(() => {
  if (window.__pageContentSaverInitialized) {
    return;
  }

  window.__pageContentSaverInitialized = true;

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (!request || request.command !== 'capturePageContent') {
      return;
    }

    handleCaptureRequest(request)
      .then((payload) => {
        sendResponse({
          success: true,
          payload
        });
      })
      .catch((error) => {
        console.error('采集页面内容时出错：', error);
        sendResponse({
          success: false,
          error: error && error.message ? error.message : String(error)
        });
      });

    return true;
  });
})();

async function handleCaptureRequest(request) {
  const selectors = normalizeSelectors(request && request.selectors);
  const outputType = determineOutputType(request && request.outputType, selectors);

  if (outputType === 'tweetdeck-csv') {
    const csvPayload = buildTweetDeckPayload(selectors);
    if (csvPayload) {
      return csvPayload;
    }
    console.warn('未检测到 TweetDeck 栏目，改用文本模式抓取。');
  }

  return buildTextPayload(selectors);
}

function buildTextPayload(selectors) {
  const sections = [];

  if (!selectors || selectors.length === 0) {
    const textContent = document.body ? document.body.innerText : '';
    sections.push({
      heading: '页面正文',
      content: textContent.trim()
    });
  } else {
    selectors.forEach((selector) => {
      try {
        const nodes = Array.from(document.querySelectorAll(selector));

        if (nodes.length === 0) {
          sections.push({
            heading: `选择器：${selector}`,
            content: '[未匹配到元素]'
          });
          return;
        }

        const excerpts = nodes.map((node, index) => {
          const text = (node.innerText || node.textContent || '').trim();
          if (nodes.length === 1) {
            return text;
          }
          return `元素 ${index + 1}:\n${text}`;
        });

        sections.push({
          heading: `选择器：${selector}`,
          content: excerpts.join('\n\n') || '[空]'
        });
      } catch (error) {
        sections.push({
          heading: `选择器：${selector}`,
          content: `解析失败：${error && error.message ? error.message : String(error)}`
        });
      }
    });
  }

  return {
    format: 'text',
    title: document.title,
    url: location.href,
    capturedAt: new Date().toISOString(),
    sections
  };
}

function buildTweetDeckPayload(selectors) {
  const captureTimestamp = new Date().toISOString();
  const headers = getTweetHeaders();
  const articles = collectTweetArticles(selectors);

  const rows = [];
  const columnTitles = new Set();
  const columnIds = new Set();
  const columnPositions = new Set();

  articles.forEach((article) => {
    const { row, columnTitle, columnId, columnPosition } = extractTweetData(article, captureTimestamp);
    if (row) {
      rows.push(row);
      if (columnTitle) {
        columnTitles.add(columnTitle);
      }
      if (columnId) {
        columnIds.add(columnId);
      }
      if (columnPosition) {
        columnPositions.add(columnPosition);
      }
    }
  });

  if (rows.length === 0) {
    return null;
  }

  const fileBaseName = determineFileBaseName(columnTitles);

  return {
    format: 'csv',
    title: document.title,
    url: location.href,
    capturedAt: new Date().toISOString(),
    headers,
    rows,
    meta: {
      totalTweets: rows.length,
      columnTitles: Array.from(columnTitles),
      columnIds: Array.from(columnIds),
      columnPositions: Array.from(columnPositions),
      fileBaseName
    }
  };
}

function collectTweetArticles(selectors) {
  const articleSet = new Set();

  const pushArticle = (node) => {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) {
      return;
    }

    if (node.matches('article')) {
      articleSet.add(node);
      return;
    }

    node.querySelectorAll('article').forEach((article) => articleSet.add(article));
  };

  if (selectors && selectors.length > 0) {
    selectors.forEach((selector) => {
      try {
        document.querySelectorAll(selector).forEach(pushArticle);
      } catch (error) {
        console.warn('选择器解析失败：', selector, error);
      }
    });
  } else {
    document.querySelectorAll('.js-chirp-container article').forEach((article) => articleSet.add(article));
    document.querySelectorAll('article.stream-item').forEach((article) => articleSet.add(article));
  }

  return Array.from(articleSet);
}

function extractTweetData(article, captureTimestamp) {
  if (!article) {
    return { row: null, columnTitle: '', columnId: '', columnPosition: '' };
  }

  const columnInfo = getColumnInfo(article);

  const tweetId = article.getAttribute('data-tweet-id') || '';
  const timeElement = article.querySelector('time');
  const tweetLink = timeElement ? timeElement.querySelector('a[href]') : null;
  const postedAtIso = timeElement ? timeElement.getAttribute('datetime') || '' : '';
  const postedRelative = tweetLink ? tweetLink.textContent.trim() : '';
  const tweetUrl = tweetLink ? tweetLink.href : '';

  const authorLink = article.querySelector('a.account-link[href]');
  const authorDisplayName = extractText(article.querySelector('.fullname'));
  const authorUsername = stripAtPrefix(extractText(article.querySelector('.username')));
  const authorProfileUrl = authorLink ? authorLink.href : '';
  const isVerified = Boolean(article.querySelector('.sprite-verified-mini'));

  const tweetTextElement = article.querySelector('.js-tweet-text');
  const tweetText = tweetTextElement ? tweetTextElement.innerText.trim() : '';
  const language = tweetTextElement ? tweetTextElement.getAttribute('lang') || '' : '';
  const hashtags = extractTokens(tweetText, /#[\p{L}0-9_]+/gu);
  const cashtags = extractTokens(tweetText, /\$[A-Za-z0-9_]+/g);
  const mentions = extractMentionTokens(tweetTextElement);

  const quoteTextElement = article.querySelector('.js-quoted-tweet-text');
  const quoteText = quoteTextElement ? quoteTextElement.innerText.trim() : '';
  const quoteDetail = article.querySelector('.js-quote-detail');
  const quotedTweetId = quoteDetail ? quoteDetail.getAttribute('data-tweet-id') || '' : '';
  const quotedAuthorDisplayName = quoteDetail ? extractText(quoteDetail.querySelector('.fullname')) : '';
  const quotedAuthorUsername = quoteDetail ? stripAtPrefix(extractText(quoteDetail.querySelector('.username'))) : '';
  const quotedTweetLink = quoteDetail ? quoteDetail.querySelector('a[href*="status"]') : null;
  const quotedTweetUrl = quotedTweetLink ? quotedTweetLink.href : (quotedTweetId ? `https://x.com/i/web/status/${quotedTweetId}` : '');

  const replyingLinks = Array.from(article.querySelectorAll('.other-replies-link'))
    .map((link) => extractText(link))
    .filter(Boolean);

  const replyCount = readCount(article.querySelector('.js-reply-count'));
  const retweetCount = readCount(article.querySelector('.js-retweet-count'));
  const likeCount = readCount(article.querySelector('.js-like-count'));
  const quoteCount = readCount(article.querySelector('.js-quote-count'));

  const isQuoteTweet = article.classList.contains('is-quote-tweet') ||
    (article.querySelector('.tweet') && article.querySelector('.tweet').classList.contains('is-quote-tweet'));

  const mediaUrls = collectMediaUrls(article);

  const fallbackId = tweetUrl || `${captureTimestamp}-${columnInfo.id || ''}-${tweetText.slice(0, 16)}`;
  const row = {
    tweet_id: tweetId || fallbackId,
    date: postedAtIso || captureTimestamp,
    author_display_name: authorDisplayName,
    tweet_text: tweetText
  };

  return {
    row,
    columnTitle: columnInfo.title,
    columnId: columnInfo.id,
    columnPosition: columnInfo.position
  };
}

function getColumnInfo(article) {
  const section = article.closest('section[data-column]');
  if (!section) {
    return { title: '', id: '', position: '' };
  }

  const columnId = section.getAttribute('data-column') || '';
  let title = '';

  const titleInput = section.querySelector('.js-column-title-edit-box');
  if (titleInput && typeof titleInput.value === 'string') {
    title = titleInput.value.trim();
  }

  if (!title) {
    const headerTitle = section.querySelector('.column-header-title');
    if (headerTitle) {
      title = headerTitle.textContent.replace(/\s+/g, ' ').trim();
    }
  }

  let position = '';
  const parent = section.parentElement;
  if (parent) {
    const siblings = Array.from(parent.querySelectorAll('section[data-column]'));
    const index = siblings.indexOf(section);
    if (index !== -1) {
      position = String(index + 1);
    }
  }

  return {
    title,
    id: columnId,
    position
  };
}

function determineFileBaseName(columnTitles) {
  if (!columnTitles || columnTitles.size === 0) {
    return 'tweetdeck-export';
  }

  if (columnTitles.size === 1) {
    const [title] = Array.from(columnTitles);
    if (title) {
      return `tweetdeck-${title}`;
    }
  }

  return 'tweetdeck-multi-columns';
}

function readCount(element) {
  if (!element) {
    return '';
  }

  const text = element.textContent.replace(/\s+/g, '').trim();
  return text || '';
}

function extractText(element) {
  if (!element) {
    return '';
  }

  return element.textContent.replace(/\s+/g, ' ').trim();
}

function stripAtPrefix(text) {
  if (!text) {
    return '';
  }

  return text.startsWith('@') ? text.slice(1) : text;
}

function determineOutputType(requestedType, selectors) {
  const normalized = typeof requestedType === 'string' ? requestedType : '';

  if (normalized === 'tweetdeck-csv') {
    return 'tweetdeck-csv';
  }

  if (normalized === 'text') {
    if (!selectors || selectors.length === 0) {
      return isLikelyTweetDeckPage(selectors) ? 'tweetdeck-csv' : 'text';
    }
    return 'text';
  }

  if (normalized === 'auto' || normalized === '') {
    return isLikelyTweetDeckPage(selectors) ? 'tweetdeck-csv' : 'text';
  }

  return 'text';
}

function isLikelyTweetDeckPage(selectors) {
  const host = (location.hostname || '').toLowerCase();
  const path = (location.pathname || '').toLowerCase();
  const looksLikeTweetDeckHost = host.includes('tweetdeck') || host.includes('x.com');
  const looksLikeTweetDeckPath = path.includes('tweetdeck');

  if (looksLikeTweetDeckHost || looksLikeTweetDeckPath) {
    if (document.querySelector('section[data-column]')) {
      return true;
    }
    if (selectors && selectors.some((selector) => /data-column/.test(selector))) {
      return true;
    }
  }
  return false;
}

function normalizeSelectors(rawSelectors) {
  if (!rawSelectors) {
    return [];
  }

  if (Array.isArray(rawSelectors)) {
    return rawSelectors.map((selector) => selector.trim()).filter(Boolean);
  }

  if (typeof rawSelectors === 'string') {
    return rawSelectors
      .split(/\r?\n|,/)
      .map((selector) => selector.trim())
      .filter(Boolean);
  }

  return [];
}

function getTweetHeaders() {
  return [
    { key: 'date', label: '日期' },
    { key: 'author_display_name', label: 'Author Display Name' },
    { key: 'tweet_text', label: 'Tweet Text' }
  ];
}

function collectMediaUrls(article) {
  const urls = new Set();

  article.querySelectorAll('a.js-media-image-link').forEach((link) => {
    const largeUrl = link.getAttribute('data-resolved-url-large');
    if (largeUrl) {
      urls.add(largeUrl);
    } else if (link.href) {
      urls.add(link.href);
    }
  });

  article.querySelectorAll('video source').forEach((source) => {
    if (source.src) {
      urls.add(source.src);
    }
  });

  return Array.from(urls);
}

function extractTokens(text, pattern) {
  if (!text) {
    return [];
  }
  const matches = text.match(pattern);
  if (!matches) {
    return [];
  }
  return Array.from(new Set(matches.map((token) => token.trim())));
}

function extractMentionTokens(tweetTextElement) {
  if (!tweetTextElement) {
    return [];
  }

  const mentions = new Set();
  tweetTextElement.querySelectorAll('a').forEach((link) => {
    const text = (link.textContent || '').trim();
    if (text.startsWith('@')) {
      mentions.add(stripAtPrefix(text));
    }
  });

  return Array.from(mentions);
}
