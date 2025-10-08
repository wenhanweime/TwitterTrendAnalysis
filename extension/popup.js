(function () {
  const intervalInput = document.getElementById('interval');
  const delayInput = document.getElementById('delay');
  const selectorsInput = document.getElementById('selectors');
  const outputTypeSelect = document.getElementById('outputType');
  const startButton = document.getElementById('start');
  const stopButton = document.getElementById('stop');
  const captureButton = document.getElementById('capture');
  const statusEl = document.getElementById('status');
  const summaryEl = document.getElementById('summary');

  startButton.addEventListener('click', async () => {
    const interval = Number(intervalInput.value.trim());
    const delay = Number(delayInput.value.trim());
    const selectors = parseSelectors(selectorsInput.value);

    if (!Number.isFinite(interval) || interval <= 0) {
      setStatus('请输入大于 0 的执行间隔。', 'error');
      return;
    }

    if (!Number.isFinite(delay) || delay < 0) {
      setStatus('首次延迟不能为负数。', 'error');
      return;
    }

    setStatus('正在启动定时器...', '');

    try {
      const response = await sendCommand({
        command: 'start',
        interval,
        delay,
        selectors,
        outputType: outputTypeSelect.value
      });

      if (response && response.success) {
        setStatus('定时器已启动。', 'success');
        await refreshStatus();
      } else {
        setStatus(response && response.error ? response.error : '启动失败。', 'error');
      }
    } catch (error) {
      setStatus(error.message || '启动失败。', 'error');
    }
  });

  stopButton.addEventListener('click', async () => {
    setStatus('正在停止定时器...', '');

    try {
      const response = await sendCommand({ command: 'stop' });

      if (response && response.success) {
        setStatus('定时器已停止。', 'success');
        await refreshStatus();
      } else {
        setStatus(response && response.error ? response.error : '停止失败。', 'error');
      }
    } catch (error) {
      setStatus(error.message || '停止失败。', 'error');
    }
  });

  captureButton.addEventListener('click', async () => {
    const selectors = parseSelectors(selectorsInput.value);
    setStatus('正在抓取当前页面...', '');

    try {
      const response = await sendCommand({
        command: 'captureOnce',
        selectors,
        outputType: outputTypeSelect.value
      });

      if (response && response.success) {
        const when = response.capturedAt ? formatTimestamp(response.capturedAt) : '刚刚';
        setStatus(`已保存为 ${response.filename || '文本文件'}（${when}）`, 'success');
        await refreshStatus();
      } else {
        setStatus(response && response.error ? response.error : '抓取失败。', 'error');
      }
    } catch (error) {
      setStatus(error.message || '抓取失败。', 'error');
    }
  });

  init();

  async function init() {
    try {
      const status = await refreshStatus();
      await autoAdjustOutputType(status);
    } catch (error) {
      setStatus(error.message || '获取当前状态失败。', 'error');
    }
  }

  function parseSelectors(raw) {
    if (!raw) {
      return [];
    }

    return raw
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  async function refreshStatus() {
    const response = await sendCommand({ command: 'getStatus' });

    if (!response || !response.success) {
      throw new Error(response && response.error ? response.error : '获取状态失败。');
    }

    if (response.config) {
      intervalInput.value = response.config.interval;
      delayInput.value = response.config.delay;
      selectorsInput.value = (response.config.selectors || []).join('\n');
      outputTypeSelect.value = response.config.outputType || 'auto';
    }

    renderSummary(response);
    return response;
  }

  function renderSummary(response) {
    const statusLines = [];
    const isRunning = response.state && response.state.isRunning;

    statusLines.push(`状态：${isRunning ? '运行中' : '已停止'}`);

    if (response.nextRunAt) {
      statusLines.push(`下次执行：${formatTimestamp(response.nextRunAt)}`);
    } else {
      statusLines.push('下次执行：—');
    }

    if (response.state && response.state.lastRunAt) {
      statusLines.push(`上次执行：${formatTimestamp(response.state.lastRunAt)}`);
    } else {
      statusLines.push('上次执行：—');
    }

    if (response.state && response.state.lastFilename) {
      statusLines.push(`最近保存：${response.state.lastFilename}`);
    }

    const selectors = response.config && response.config.selectors && response.config.selectors.length > 0
      ? response.config.selectors.join(', ')
      : '页面正文';

    statusLines.push(`抓取选择器：${selectors}`);

    statusLines.push(`输出格式：${formatOutputType(response.config && response.config.outputType)}`);

    summaryEl.textContent = statusLines.join('\n');
  }

  function setStatus(text, type) {
    statusEl.textContent = text;
    statusEl.className = type ? type : '';
  }

  async function autoAdjustOutputType(status) {
    const activeTab = await queryActiveTab();
    if (!activeTab || !activeTab.url) {
      return;
    }

    if (!status || !status.config) {
      return;
    }

    if (status.config.outputType && status.config.outputType !== 'text' && status.config.outputType !== 'auto') {
      return;
    }

    if (isTweetDeckUrl(activeTab.url)) {
      outputTypeSelect.value = 'tweetdeck-csv';
    }
  }

  function formatTimestamp(input) {
    const date = typeof input === 'number' ? new Date(input) : new Date(String(input));

    if (!Number.isFinite(date.getTime())) {
      return '';
    }

    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function formatOutputType(value) {
    switch (value) {
      case 'auto':
        return '自动检测';
      case 'tweetdeck-csv':
        return 'TweetDeck 推文 CSV';
      case 'text':
      default:
        return '文本（TXT）';
    }
  }

  function pad(num) {
    return num.toString().padStart(2, '0');
  }

  async function sendCommand(message) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }

        resolve(response);
      });
    });
  }

  function queryActiveTab() {
    return new Promise((resolve) => {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        resolve(tabs && tabs.length ? tabs[0] : null);
      });
    });
  }

  function isTweetDeckUrl(url) {
    const lower = (url || '').toLowerCase();
    return lower.includes('tweetdeck') || lower.includes('x.com/i/tweetdeck');
  }
})();
