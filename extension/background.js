const ALARM_NAME = 'pageContentSaver';
const CONFIG_KEY = 'pageContentSaverConfig';
const STATE_KEY = 'pageContentSaverState';

const DEFAULT_CONFIG = Object.freeze({
  interval: 5,
  delay: 0,
  selectors: [],
  outputType: 'auto'
});

const DEFAULT_STATE = Object.freeze({
  isRunning: false,
  lastRunAt: null,
  lastFilename: null
});

const pendingFilenameQueue = [];

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  handleMessage(request)
    .then((response) => sendResponse(response))
    .catch((error) => {
      console.error('处理消息时出错：', error);
      sendResponse({ success: false, error: error.message || String(error) });
    });
  return true;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== ALARM_NAME) {
    return;
  }

  captureCurrentTabContent().catch((error) => {
    console.error('定时抓取失败：', error);
  });
});

chrome.downloads.onDeterminingFilename.addListener((downloadItem, suggest) => {
  try {
    if (downloadItem.byExtensionId !== chrome.runtime.id) {
      suggest();
      return;
    }

    const nextFilename = pendingFilenameQueue.shift();
    if (nextFilename) {
      suggest({ filename: nextFilename, conflictAction: 'overwrite' });
    } else {
      suggest();
    }
  } catch (error) {
    console.error('设置下载文件名时出错：', error);
    suggest();
  }
});

async function handleMessage(request) {
  switch (request.command) {
    case 'start':
      return startScheduler(request);
    case 'stop':
      return stopScheduler();
    case 'getStatus':
      return getStatus();
    case 'captureOnce':
      return captureOnce(request);
    default:
      return { success: false, error: '未知指令。' };
  }
}

async function startScheduler(request) {
  const interval = Number(request.interval);
  const delay = request.delay != null ? Number(request.delay) : 0;
  const selectors = normalizeSelectors(request.selectors);
  const outputType = normalizeOutputType(request.outputType);

  if (!Number.isFinite(interval) || interval <= 0) {
    return { success: false, error: '请输入大于 0 的执行间隔。' };
  }

  if (!Number.isFinite(delay) || delay < 0) {
    return { success: false, error: '首次延迟不能为负数。' };
  }

  const config = {
    interval,
    delay,
    selectors,
    outputType
  };

  await setConfig(config);

  const previousState = await getState();
  await setState({
    ...previousState,
    isRunning: true
  });

  await callChromeApi(chrome.alarms, 'clear', ALARM_NAME);

  const alarmInfo = {
    periodInMinutes: interval
  };

  if (delay > 0) {
    alarmInfo.delayInMinutes = delay;
  }

  chrome.alarms.create(ALARM_NAME, alarmInfo);

  const alarm = await callChromeApi(chrome.alarms, 'get', ALARM_NAME).catch(() => null);

  return {
    success: true,
    config,
    nextRunAt: alarm ? alarm.scheduledTime : null
  };
}

async function stopScheduler() {
  const cleared = await callChromeApi(chrome.alarms, 'clear', ALARM_NAME).catch(() => false);
  const previousState = await getState();

  await setState({
    ...previousState,
    isRunning: false
  });

  return {
    success: true,
    message: cleared ? '定时已停止。' : '定时器不存在或已经停止。'
  };
}

async function getStatus() {
  const config = await getConfig();
  const state = await getState();
  const alarm = await callChromeApi(chrome.alarms, 'get', ALARM_NAME).catch(() => null);
  const isRunning = Boolean(alarm);

  if (state.isRunning !== isRunning) {
    await setState({
      ...state,
      isRunning
    });
  }

  return {
    success: true,
    config,
    state: {
      ...state,
      isRunning
    },
    nextRunAt: alarm ? alarm.scheduledTime : null
  };
}

async function captureOnce(request) {
  const selectors = normalizeSelectors(request.selectors);
  const outputType = typeof request.outputType === 'string'
    ? normalizeOutputType(request.outputType)
    : null;
  let config = await getConfig();

  if (selectors.length > 0) {
    config = {
      ...config,
      selectors
    };
    await setConfig(config);
  }

  if (outputType) {
    config = {
      ...config,
      outputType
    };
    await setConfig(config);
  }

  const result = await captureCurrentTabContent(config);

  return {
    success: true,
    filename: result.filename,
    capturedAt: result.capturedAt
  };
}

async function captureCurrentTabContent(configOverride) {
  const config = configOverride ? normalizeConfig(configOverride) : await getConfig();
  const tabs = await callChromeApi(chrome.tabs, 'query', { active: true, currentWindow: true });

  if (!tabs || tabs.length === 0) {
    throw new Error('没有找到活动的标签页。');
  }

  const tabId = tabs[0].id;
  if (typeof tabId !== 'number') {
    throw new Error('活动标签页 ID 无效。');
  }

  await callChromeApi(chrome.scripting, 'executeScript', {
    target: { tabId },
    files: ['content.js']
  });

  const response = await callChromeApi(chrome.tabs, 'sendMessage', tabId, {
    command: 'capturePageContent',
    selectors: config.selectors,
    outputType: config.outputType
  });

  if (!response || !response.success) {
    throw new Error(response && response.error ? response.error : '页面内容抓取失败。');
  }

  const composed = await composeContent(response.payload);
  if (!composed) {
    console.log('没有新的内容需要保存。');
    await setState({
      ...(await getState()),
      lastRunAt: Date.now(),
      isRunning: Boolean(await callChromeApi(chrome.alarms, 'get', ALARM_NAME).catch(() => null))
    });
    return {
      filename: null,
      capturedAt: response.payload.capturedAt
    };
  }

  const filename = await saveContentToFile(
    composed.content,
    composed.extension,
    composed.suggestedFileName,
    composed.saveOptions
  );

  await setState({
    ...(await getState()),
    lastRunAt: Date.now(),
    lastFilename: filename,
    isRunning: Boolean(await callChromeApi(chrome.alarms, 'get', ALARM_NAME).catch(() => null))
  });

  return {
    filename,
    capturedAt: response.payload.capturedAt
  };
}

async function getConfig() {
  const result = await callChromeApi(chrome.storage.local, 'get', {
    [CONFIG_KEY]: DEFAULT_CONFIG
  });
  return normalizeConfig(result[CONFIG_KEY]);
}

function normalizeConfig(config) {
  const interval = Number(config && config.interval);
  const delay = Number(config && config.delay);
  const selectors = normalizeSelectors(config && config.selectors);
  const outputType = normalizeOutputType(config && config.outputType);

  return {
    interval: Number.isFinite(interval) && interval > 0 ? interval : DEFAULT_CONFIG.interval,
    delay: Number.isFinite(delay) && delay >= 0 ? delay : DEFAULT_CONFIG.delay,
    selectors,
    outputType
  };
}

async function setConfig(config) {
  await callChromeApi(chrome.storage.local, 'set', {
    [CONFIG_KEY]: normalizeConfig(config)
  });
}

async function getState() {
  const result = await callChromeApi(chrome.storage.local, 'get', {
    [STATE_KEY]: DEFAULT_STATE
  });
  return {
    ...DEFAULT_STATE,
    ...result[STATE_KEY]
  };
}

async function setState(state) {
  await callChromeApi(chrome.storage.local, 'set', {
    [STATE_KEY]: {
      ...DEFAULT_STATE,
      ...state
    }
  });
}

function normalizeSelectors(rawSelectors) {
  if (!rawSelectors) {
    return [];
  }

  let selectors;

  if (Array.isArray(rawSelectors)) {
    selectors = rawSelectors;
  } else if (typeof rawSelectors === 'string') {
    selectors = rawSelectors.split(/\r?\n|,/);
  } else {
    return [];
  }

  return selectors
    .map((selector) => selector.trim())
    .filter((selector) => selector.length > 0);
}

async function composeContent(payload) {
  if (payload && payload.format === 'csv') {
    const headers = Array.isArray(payload.headers) ? payload.headers : [];
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    const csv = generateCsv(headers, rows);

    return {
      content: csv,
      extension: 'csv',
      suggestedFileName: payload.meta && payload.meta.fileBaseName,
      saveOptions: undefined
    };
  }

  const lines = [];

  lines.push(`Title: ${payload.title}`);
  lines.push(`URL: ${payload.url}`);
  lines.push(`Captured At: ${payload.capturedAt}`);
  lines.push('');

  if (Array.isArray(payload.sections) && payload.sections.length > 0) {
    payload.sections.forEach((section) => {
      lines.push(section.heading || '内容');
      lines.push(section.content || '[空]');
      lines.push('');
    });
  } else {
    lines.push('未收集到任何内容。');
    lines.push('');
  }

  return {
    content: lines.join('\n'),
    extension: 'txt',
    suggestedFileName: null,
    saveOptions: undefined
  };
}

async function saveContentToFile(content, extension = 'txt', suggestedName = null, options = {}) {
  const { appendTimestamp = true, overwrite = false } = options;
  const safeExtension = typeof extension === 'string' && extension.trim()
    ? extension.trim().replace(/^\.+/, '')
    : 'txt';
  const mimeType = safeExtension === 'csv' ? 'text/csv;charset=utf-8' : 'text/plain;charset=utf-8';
  const blob = new Blob([content], { type: mimeType });
  const dataUrl = await blobToDataUrl(blob, mimeType);

  const now = new Date();
  const timestamp =
    now.getFullYear() +
    ('0' + (now.getMonth() + 1)).slice(-2) +
    ('0' + now.getDate()).slice(-2) +
    '_' +
    ('0' + now.getHours()).slice(-2) +
    ('0' + now.getMinutes()).slice(-2) +
    ('0' + now.getSeconds()).slice(-2);
  const sanitizedSuggestion = suggestedName ? sanitizeFileName(suggestedName) : '';
  const defaultBase = `page-content-${timestamp}`;
  const baseCore = sanitizedSuggestion || `page-content`;
  const baseName = appendTimestamp ? `${baseCore}-${timestamp}` : baseCore;
  const fileNameWithExt = baseName.endsWith(`.${safeExtension}`)
    ? baseName
    : `${baseName}.${safeExtension}`;
  const subfolder = safeExtension === 'csv' ? 'tweetdeck_exports' : 'page_content_exports';
  const relativePath = `${subfolder}/${fileNameWithExt}`;

  try {
    pendingFilenameQueue.push(relativePath);
    const downloadOptions = {
      url: dataUrl,
      filename: relativePath,
      saveAs: false
    };

    if (overwrite) {
      downloadOptions.conflictAction = 'overwrite';
    }

    const downloadId = await callChromeApi(chrome.downloads, 'download', downloadOptions);
    console.log('下载完成', { downloadId, filename: relativePath });
    return relativePath;
  } finally {
    // Nothing to revoke when using data URLs.
  }
}

function normalizeOutputType(value) {
  if (value === 'tweetdeck-csv') {
    return 'tweetdeck-csv';
  }

  if (value === 'text') {
    return 'text';
  }

  return 'auto';
}

function generateCsv(headers, rows) {
  if (!headers || headers.length === 0) {
    return '';
  }

  const headerKeys = headers.map((header) => header && header.key).filter(Boolean);

  const lines = [];
  lines.push(headers.map((header) => csvEscape(header.label || header.key || '')).join(','));

  rows.forEach((row) => {
    const line = headerKeys.map((key) => csvEscape(row && row[key] != null ? row[key] : ''));
    lines.push(line.join(','));
  });

  const csvBody = lines.join('\r\n');
  return '\ufeff' + csvBody;
}

function csvEscape(value) {
  if (value == null) {
    return '';
  }

  const stringValue = String(value).replace(/\r?\n/g, '\n');
  const shouldQuote = /[",\n]/.test(stringValue);
  const escaped = stringValue.replace(/"/g, '""');
  return shouldQuote ? `"${escaped}"` : escaped;
}

function sanitizeFileName(name) {
  if (!name) {
    return 'page-content';
  }

  return name
    .replace(/[\r\n]/g, ' ')
    .replace(/[\\/:*?"<>|]/g, '-')
    .trim()
    .substring(0, 180) || 'page-content';
}

async function blobToDataUrl(blob, mimeType) {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = '';

  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode.apply(null, chunk);
  }

  const base64 = btoa(binary);
  const finalMime = mimeType && typeof mimeType === 'string' ? mimeType : 'application/octet-stream';
  return `data:${finalMime};base64,${base64}`;
}

function callChromeApi(namespace, method, ...args) {
  return new Promise((resolve, reject) => {
    try {
      namespace[method](...args, (result) => {
        const error = chrome.runtime.lastError;
        if (error) {
          if (method === 'download') {
            pendingFilenameQueue.shift();
          }
          reject(new Error(error.message));
          return;
        }
        resolve(result);
      });
    } catch (error) {
      if (method === 'download') {
        pendingFilenameQueue.shift();
      }
      reject(error);
    }
  });
}
