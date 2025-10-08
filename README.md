# Twitter Trend Analysis Toolkit

This repository bundles two pieces that work together to monitor TweetDeck activity, persist the raw data, and distribute an hourly AI-generated digest:

- **Chrome extension** (`extension/`): captures timelines or column data from Old TweetDeck and stores them locally as CSV/text.
- **Python summariser** (`summarize_tweets.py`): ingests the captured CSV files, calls your language model endpoint, and mails a structured trend report.

The workflow assumes you are using the community project **[OldTweetDeck](https://github.com/dimdenGD/OldTweetDeck)** to access the legacy UI and its richer column layout.

---

## 1. Prerequisites

| Component | Requirement |
| --- | --- |
| Browser | Chrome 112+ (or any Chromium browser with extension support) |
| TweetDeck UI | Install OldTweetDeck per the instructions in [dimdenGD/OldTweetDeck](https://github.com/dimdenGD/OldTweetDeck) so that TweetDeck columns render in the classic layout |
| Python | Python 3.9+ with `pip` |
| Python packages | `requests` (install via `pip install -r requirements.txt` if you create one, or simply `pip install requests`) |
| Email | Either SMTP credentials (QQ/Gmail/etc.) or a configured Mail.app account on macOS |
| LLM endpoint | Access to a chat-completion compatible API (NEWAPI in the original setup) |

OldTweetDeck must already be active and logged in; the capture extension relies on its DOM structure to discover column articles when exporting to CSV.

---

## 2. Configuration

1. **Clone the repository**
   ```bash
   git clone https://github.com/wenhanweime/TwitterTrendAnalysis.git
   cd TwitterTrendAnalysis
   ```

2. **Prepare secrets**
   ```bash
   cp .env.template .env
   ```
   Fill in the placeholders with your actual credentials:
   - `NEWAPI_BASE_URL` and `NEWAPI_API_KEY` for the summarisation model.
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` for outbound email (or leave username/password empty to fall back to macOS Mail.app).
   - `EMAIL_FROM` (optional) and `EMAIL_TO` (required recipient address).
   - `LLM_MODEL` if you want to override the default.

3. **Install Python dependency**
   ```bash
   pip install requests
   ```
   (If you maintain a virtual environment, activate it before running the command.)

---

## 3. Set Up the Chrome Extension

1. In Chrome, open `chrome://extensions`, toggle **Developer mode**, then choose **Load unpacked**.
2. Select the repository’s `extension/` directory.
3. Confirm the toolbar icon displays correctly (blue circle with a white “T”).
4. Open [tweetdeck.twitter.com](https://tweetdeck.twitter.com/) with OldTweetDeck enabled, ensure your columns are visible, and then click the extension icon to open the control popup.

### Extension Usage

- **Interval / Delay**: configure the automatic capture cadence (minutes) and optional first-run delay.
- **CSS 选择器**: optional list (one per line) of elements to capture when you want text snapshots outside TweetDeck CSV mode.
- **输出格式**:
  - `自动`: detect TweetDeck columns and export CSV when possible, otherwise fall back to plain text.
  - `TweetDeck CSV`: force CSV output (recommended when using OldTweetDeck columns).
  - `纯文本`: always save a text export.
- **按钮**:
  - `开始定时`: start the background alarm to capture on schedule.
  - `停止定时`: cancel the alarm.
  - `立即抓取`: perform an on-demand export using the current settings.

Exports are written under your browser download directory as:

```
~/Downloads/
    tweetdeck_exports/   # CSV files consumed by summarize_tweets.py
    page_content_exports/  # Text exports when CSV is not available
```

---

## 4. Run the Summariser

The Python script processes only CSV files in `~/Downloads/tweetdeck_exports` that were updated within the last hour and that have not yet been archived.

```bash
python3 summarize_tweets.py
```

What happens each run:

1. Load `.env` (fallback to defaults if present).
2. Collect new rows from each CSV and remove duplicate tweets.
3. Chunk tweets and call your LLM endpoint to obtain per-chunk summaries.
4. Merge the summaries into a final trend report.
5. Email the result and archive the processed CSVs into `~/Downloads/tweetdeck_exports/processed/<YYYY-MM-DD>/`.

**Testing mail only**
```bash
python3 summarize_tweets.py --test-email
```

**Scheduling tips**
- macOS `launchd`: see `SUMMARY_USAGE.md` for a sample plist that runs the script hourly.
- Cron: `0 * * * * cd /path/to/TwitterMessage && python3 summarize_tweets.py`.

---

## 5. Repository Layout

```
extension/               # Chrome extension (manifest v3)
    background.js        # Alarm + capture scheduler
    content.js           # In-page collector for TweetDeck columns or arbitrary selectors
    popup.html/js        # Control UI for capture settings
    icon-*.png           # Toolbar / store icons
summarize_tweets.py      # LLM-driven summariser and email dispatcher
merge_txt_to_csv.py      # Utility for converting text captures into CSV (optional workflow)
.env.template            # Sample environment variables (copy to .env for local use)
.gitignore               # Keeps secrets, cache, and heavy docs out of git
SUMMARY_USAGE.md         # Additional notes on scheduling and behaviour
```

---

## 6. Troubleshooting

- **Extension shows “未匹配到元素”**: verify OldTweetDeck is loaded and try switching output type to `自动` or updating column selectors.
- **CSV files not generated**: check Chrome’s download permissions and that the `tweetdeck_exports` folder exists (it is created on first successful download).
- **Summariser exits with API error**: confirm `.env` contains valid `NEWAPI_*` values and the endpoint is reachable from your network.
- **SMTP error (-1, b'\x00\x00\x00')**: benign QQ SMTP quirk; the script now logs a warning and continues if the mail already went through.

For deeper details, refer to `SUMMARY_USAGE.md` or inspect the inline comments within the extension scripts.
