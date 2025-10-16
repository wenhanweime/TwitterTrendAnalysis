#!/usr/bin/env python3
"""Summarise freshly downloaded TweetDeck CSV exports and email the trend report.

Configuration defaults are defined in this file. You can also override them via
environment variables if desired:
  NEWAPI_BASE_URL   Base URL for the chat completion endpoint (required)
  NEWAPI_API_KEY    API key used in the Authorization header (required)
  SMTP_HOST         SMTP server host (default: smtp.qq.com)
  SMTP_PORT         SMTP server port (default: 587)
  SMTP_USERNAME     SMTP username (e.g. your email address)
  SMTP_PASSWORD     SMTP password or app password.
  EMAIL_FROM        From address (defaults to SMTP_USERNAME if omitted)
  EMAIL_TO          Recipient address (required)

Run this script every 10 minutes (via cron/launchd) after the Chrome extension has
exported CSV files to ~/Downloads/tweetdeck_exports.
"""

from __future__ import annotations

import csv
import json
import os
import smtplib
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent


def load_local_env(env_path=None) -> None:
    if env_path is None:
        env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


load_local_env()

STATE_PATH = BASE_DIR / "tweet_summary_state.json"
DOWNLOAD_DIR = Path.home() / "Downloads" / "tweetdeck_exports"
PROCESSED_ROOT = DOWNLOAD_DIR / "processed"
FEED_DIR = BASE_DIR / "docs"
FEED_JSON_PATH = FEED_DIR / "feed.json"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-pro")
DEFAULT_EMAIL_TO = os.environ.get("EMAIL_TO", "")

# --- 内置 NEWAPI 配置（直接在此修改即可） ---
DEFAULT_NEWAPI_BASE_URL = os.environ.get("NEWAPI_BASE_URL", "")
DEFAULT_NEWAPI_KEY = os.environ.get("NEWAPI_API_KEY", "")

# --- 内置 SMTP 配置（直接在此修改或通过环境变量覆盖） ---
DEFAULT_SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")
DEFAULT_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
DEFAULT_SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
DEFAULT_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# --- 分段与重试配置 ---
CHUNK_CHAR_LIMIT = 8000  # 每个分段的最大字符数
LLM_MAX_RETRIES = 3
LLM_RETRY_BACKOFF = 5.0  # 秒
SUMMARY_WINDOW_SECONDS = 3600  # 只处理最近 1 小时内更新的文件
OVERALL_SUMMARY_GROUP_LIMIT = 5  # 汇总前每批包含的摘要数量上限
FEED_MAX_ENTRIES = 48  # GitHub Pages feed 最多保留的条目数量


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"processed_files": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))  # type: ignore[return-value]
    except json.JSONDecodeError:
        state = {}

    processed = state.get("processed_files", {})
    if isinstance(processed, list):
        state["processed_files"] = {
            path: {"processed_rows": 0, "mtime": 0.0} for path in processed
        }
    elif isinstance(processed, dict):
        normalized = {}
        for path, info in processed.items():
            if isinstance(info, dict):
                normalized[path] = {
                    "processed_rows": int(info.get("processed_rows", 0)),
                    "mtime": float(info.get("mtime", 0.0)),
                }
            else:
                normalized[path] = {
                    "processed_rows": int(info) if isinstance(info, int) else 0,
                    "mtime": 0.0,
                }
        state["processed_files"] = normalized
    else:
        state["processed_files"] = {}

    last_info = state.get("last_processed")
    if not isinstance(last_info, dict):
        last_info = {}

    raw_timestamp = last_info.get("timestamp", "")
    if isinstance(raw_timestamp, str):
        timestamp_str = raw_timestamp.strip()
    else:
        timestamp_str = str(raw_timestamp).strip() if raw_timestamp else ""

    raw_ids = last_info.get("ids", [])
    if isinstance(raw_ids, list):
        ids_list = [str(item) for item in raw_ids if str(item)]
    elif isinstance(raw_ids, (str, int)):
        ids_list = [str(raw_ids)]
    else:
        ids_list = []

    raw_texts = last_info.get("texts", [])
    if isinstance(raw_texts, list):
        texts_list = [str(item) for item in raw_texts if str(item)]
    elif isinstance(raw_texts, (str, int)):
        texts_list = [str(raw_texts)]
    else:
        texts_list = []

    state["last_processed"] = {
        "timestamp": timestamp_str,
        "ids": ids_list,
        "texts": texts_list,
    }

    raw_last_file_mtime = state.get("last_processed_file_mtime", 0.0)
    try:
        last_file_mtime = float(raw_last_file_mtime)
    except (TypeError, ValueError):
        last_file_mtime = 0.0
    state["last_processed_file_mtime"] = last_file_mtime

    return state


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_tweet_timestamp(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def collect_new_tweets(state: dict) -> Tuple[List[str], List[Path]]:
    tweets: List[str] = []
    touched_files: List[Path] = []

    if not DOWNLOAD_DIR.is_dir():
        return tweets, touched_files

    processed_map = state.setdefault("processed_files", {})

    last_processed_info = state.setdefault(
        "last_processed", {"timestamp": "", "ids": [], "texts": []}
    )
    last_file_mtime = float(state.get("last_processed_file_mtime", 0.0))
    max_processed_file_mtime = last_file_mtime

    last_timestamp_str = ""
    if isinstance(last_processed_info, dict):
        last_timestamp_str = last_processed_info.get("timestamp", "") or ""
        last_ids_source = last_processed_info.get("ids", [])
        last_texts_source = last_processed_info.get("texts", [])
    else:
        last_ids_source = []
        last_texts_source = []

    last_timestamp = (
        parse_tweet_timestamp(last_timestamp_str)
        if isinstance(last_timestamp_str, str) and last_timestamp_str.strip()
        else None
    )
    last_ids = {str(item) for item in last_ids_source if str(item)}
    last_texts = {str(item) for item in last_texts_source if str(item)}

    max_timestamp: Optional[datetime] = last_timestamp
    ids_for_max_timestamp: set[str] = set(last_ids) if last_timestamp else set()
    texts_for_max_timestamp: set[str] = set(last_texts) if last_timestamp else set()
    processed_any_timestamped = False

    files = sorted(DOWNLOAD_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    existing_paths = {str(path.resolve()) for path in files}
    time_based_cutoff = time.time() - SUMMARY_WINDOW_SECONDS
    effective_cutoff_ts = max(time_based_cutoff, last_file_mtime)

    for path in files:
        key = str(path.resolve())
        try:
            file_mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue

        if file_mtime + 1e-6 < effective_cutoff_ts:
            continue

        if file_mtime > max_processed_file_mtime:
            max_processed_file_mtime = file_mtime

        info = processed_map.get(key, {"processed_rows": 0, "mtime": 0.0})
        last_mtime = float(info.get("mtime", 0.0))
        processed_rows = int(info.get("processed_rows", 0))

        if file_mtime > last_mtime + 1e-3:
            processed_rows = 0

        rows = extract_tweets(path)
        total_rows = len(rows)

        if total_rows <= processed_rows:
            processed_map[key] = {"processed_rows": total_rows, "mtime": file_mtime}
            continue

        new_rows = rows[processed_rows:]
        recent_texts: List[str] = []
        for tweet_text, posted_at, tweet_id in new_rows:
            if posted_at:
                if last_timestamp:
                    if posted_at < last_timestamp:
                        continue
                    if posted_at == last_timestamp:
                        if (tweet_id and tweet_id in last_ids) or tweet_text in last_texts:
                            continue
            elif last_timestamp and tweet_text in last_texts:
                # 没有时间戳时，保守跳过已处理过的文本以避免重复
                continue

            recent_texts.append(tweet_text)

            if posted_at:
                processed_any_timestamped = True
                if max_timestamp is None or posted_at > max_timestamp:
                    max_timestamp = posted_at
                    ids_for_max_timestamp = {tweet_id} if tweet_id else set()
                    texts_for_max_timestamp = {tweet_text}
                elif posted_at == max_timestamp:
                    if tweet_id:
                        ids_for_max_timestamp.add(tweet_id)
                    texts_for_max_timestamp.add(tweet_text)

        if recent_texts:
            tweets.extend(recent_texts)
            touched_files.append(path)
        else:
            print(
                f"{path.name} 中未找到晚于已处理记录的推文，已跳过。"
            )
        processed_map[key] = {"processed_rows": total_rows, "mtime": file_mtime}

    # 清理已删除的文件记录
    for key in list(processed_map.keys()):
        if key not in existing_paths:
            del processed_map[key]

    if max_timestamp is not None and (processed_any_timestamped or max_timestamp != last_timestamp):
        last_processed_snapshot = {
            "timestamp": max_timestamp.isoformat(),
            "ids": sorted(ids_for_max_timestamp),
            "texts": sorted(texts_for_max_timestamp),
        }
        state["last_processed"] = last_processed_snapshot
    elif processed_any_timestamped and max_timestamp is not None:
        # max_timestamp 与 last_timestamp 相同，但新增了同时间戳的推文
        last_processed_snapshot = {
            "timestamp": max_timestamp.isoformat() if max_timestamp else "",
            "ids": sorted(ids_for_max_timestamp),
            "texts": sorted(texts_for_max_timestamp),
        }
        state["last_processed"] = last_processed_snapshot

    state["last_processed_file_mtime"] = max_processed_file_mtime

    return tweets, touched_files


def extract_tweets(csv_path: Path) -> List[Tuple[str, Optional[datetime], str]]:
    tweets: List[Tuple[str, Optional[datetime], str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            tweet = row.get("tweet_text") or row.get("Tweet Text") or ""
            tweet = tweet.strip()
            if tweet:
                posted_raw = (
                    row.get("Posted At (ISO)")
                    or row.get("Posted At")
                    or row.get("Posted at")
                    or row.get("Captured At (ISO)")
                    or row.get("Captured At")
                    or row.get("captured_at")
                    or row.get("日期")
                    or row.get("Date")
                    or row.get("date")
                    or ""
                )
                posted_at = parse_tweet_timestamp(posted_raw)
                raw_tweet_id = (
                    row.get("Tweet ID")
                    or row.get("tweet_id")
                    or row.get("TweetId")
                    or row.get("tweetId")
                    or ""
                )
                tweet_id = str(raw_tweet_id).strip()
                tweets.append((tweet, posted_at, tweet_id))
    return tweets


def deduplicate_tweets(tweets: Sequence[str]) -> List[str]:
    seen = set()
    unique: List[str] = []
    for tweet in tweets:
        if tweet in seen:
            continue
        seen.add(tweet)
        unique.append(tweet)
    return unique


def build_chunk_prompt(tweets: Sequence[str], chunk_index: int, total_chunks: int) -> List[dict]:
    bullet_list = "\n".join(f"- {tweet}" for tweet in tweets)
    prompt = textwrap.dedent(
        f"""
        你是一个专业的推特内容分析师，正在处理第 {chunk_index}/{total_chunks} 个推文分段。
        请阅读本分段的推文正文，提炼与 AI 相关的重点信息，尤其关注：
        - 可能出现的新 AI 产品、工具或用法，以及它们的亮点；
        - 社区正在讨论的主题、趋势或情绪变化；
        - 值得收藏的技巧、教程、案例或洞察；
        - 适合列入最终 TOP10 的高价值推文候选。

        输出要求：
        - 使用不超过 6 条的项目符号，每条以 “•” 开头；
        - 每条按照 “主题/产品 —— 结论；原文：「关键句」” 的结构书写；
        - “原文” 必须引用本分段推文中的核心句子，可酌情截取并用省略号保持语义完整。

        推文列表：
        {bullet_list}
        """
    ).strip()
    return [{"role": "user", "content": prompt}]


def build_overall_prompt(chunk_summaries: Sequence[str]) -> List[dict]:
    summaries_text = "\n\n".join(
        f"分段 {idx + 1} 摘要：\n{summary}" for idx, summary in enumerate(chunk_summaries)
    )
    prompt = textwrap.dedent(
        f"""
        你是一个专业的推特内容分析师，你重点关注 AI 领域的最新消息。写作需遵循以下原则：
        - 表述务必具体，避免使用“AI”“Web3”等笼统词汇，除非紧接着给出具体上下文；
        - 每次引用推文内容时都要逐字呈现关键句，必要时使用省略号保持语义完整；
        - 结论要聚焦可执行信息或明确洞察。

        请综合以下分段摘要，完成最近一小时的趋势分析，必须严格按照下述模板输出：

        1. top趋势关键词：列出 3~5 个具体热门词，格式 “关键词（出现原因/关联产品）”。
        2. 提到的 AI 产品及亮点：列出 3~5 条，格式 “产品名称｜估计提及次数｜亮点评述（包含功能或结果）”。
        3. 使用技巧 / 教程 / 实战案例：用 “分享者｜方法步骤｜带来价值（引用原文要点）” 的结构归纳 3~5 条。
        4. 最有价值推文 TOP10：
           1. 标题：概括主题或产品
              推文原文：「逐字引用核心句，必要时使用省略号」
              关键信息：概述观点/方法/数据
              应用价值：说明适用场景或行动建议
           2. …
           3. …
           …
           10. …

        要求：
        - 语言使用中文；
        - 如果信息不足，请根据已有摘要合理整合，不可杜撰；
        - 若无法列满 10 条 TOP 推文，可列出现有数量并在末尾说明原因；
        - 请不要使用markdown格式，邮件是不支持的。

        以下是各分段的摘要，请据此完成上述模板：
        {summaries_text}
        """
    ).strip()
    return [{"role": "user", "content": prompt}]


def build_intermediate_prompt(
    summaries: Sequence[str], stage: int, group_index: int, total_groups: int
) -> List[dict]:
    summaries_text = "\n\n".join(
        f"摘要 {idx + 1}：\n{summary}" for idx, summary in enumerate(summaries)
    )
    prompt = textwrap.dedent(
        f"""
        你正在为一份 AI 趋势日报执行第 {stage} 层的分层汇总（共 {total_groups} 个组合，当前为第 {group_index} 个）。
        请阅读以下多个摘要，保留其中不重复的核心事实，将关键信息浓缩为 4~6 条项目符号，遵循：
        - 每条以 “• ” 开头，长度不超过 120 字；
        - 指出涉及的产品 / 主题，并说明主要洞察或数字；
        - 如需引用原文，请保留逐字片段或使用省略号；
        - 不要捏造未出现的信息。

        待合并的摘要如下：
        {summaries_text}
        """
    ).strip()
    return [{"role": "user", "content": prompt}]


def compress_summaries_for_overall(summaries: Sequence[str]) -> List[str]:
    """
    递归压缩分段摘要，确保最终传入总汇总模型的摘要数量受限。
    """
    current = list(summaries)
    if len(current) <= OVERALL_SUMMARY_GROUP_LIMIT:
        return current

    stage = 1
    while len(current) > OVERALL_SUMMARY_GROUP_LIMIT:
        print(f"正在执行第 {stage} 次分层汇总，当前摘要数量：{len(current)}")
        next_level: List[str] = []
        total_groups = (len(current) + OVERALL_SUMMARY_GROUP_LIMIT - 1) // OVERALL_SUMMARY_GROUP_LIMIT
        for group_index, start in enumerate(
            range(0, len(current), OVERALL_SUMMARY_GROUP_LIMIT), start=1
        ):
            group = current[start : start + OVERALL_SUMMARY_GROUP_LIMIT]
            try:
                combined = call_llm(
                    build_intermediate_prompt(group, stage, group_index, total_groups)
                )
            except Exception as exc:
                print(
                    f"分层汇总第 {stage} 层第 {group_index}/{total_groups} 组失败：{exc}，"
                    "将使用原始摘要拼接作为替代。"
                )
                combined = "\n\n".join(group)
            next_level.append(combined)
        current = next_level
        stage += 1

    return current


def update_static_feed(
    summary_text: str,
    touched_files: Sequence[Path],
    tweet_count: int,
    chunk_count: int,
    generated_at_iso: str,
) -> None:
    """
    将最新摘要写入 docs/feed.json，供 GitHub Pages 展示。
    """
    try:
        FEED_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"更新静态 feed 失败：创建目录 {FEED_DIR} 时出错：{exc}")
        return

    data: dict = {"entries": []}
    if FEED_JSON_PATH.exists():
        try:
            loaded = json.loads(FEED_JSON_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            print("警告：feed.json 内容解析失败，将重新生成。")

    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []

    entry = {
        "generated_at": generated_at_iso,
        "tweet_count": int(tweet_count),
        "chunk_count": int(chunk_count),
        "summary": summary_text,
        "source_files": [path.name for path in touched_files],
    }
    entries.insert(0, entry)
    data["entries"] = entries[:FEED_MAX_ENTRIES]
    data["last_updated"] = generated_at_iso

    try:
        FEED_JSON_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"警告：写入 {FEED_JSON_PATH} 失败：{exc}")


def load_api_credentials() -> Tuple[str, str]:
    base_url = os.environ.get("NEWAPI_BASE_URL") or DEFAULT_NEWAPI_BASE_URL
    api_key = os.environ.get("NEWAPI_API_KEY") or DEFAULT_NEWAPI_KEY
    if not base_url or not api_key:
        raise RuntimeError("请在 .env 中配置 NEWAPI_BASE_URL 与 NEWAPI_API_KEY")
    return base_url.rstrip('/'), api_key


def call_llm(messages: Sequence[dict]) -> str:
    base_url, api_key = load_api_credentials()
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": DEFAULT_MODEL,
        "messages": list(messages),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    delay = LLM_RETRY_BACKOFF
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == LLM_MAX_RETRIES - 1:
                raise RuntimeError(f"调用 NEWAPI 超时/连接失败: {exc}")
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code == 429:
            if attempt == LLM_MAX_RETRIES - 1:
                detail = response.text.strip()
                raise RuntimeError(
                    f"调用 NEWAPI 失败: 429 Too Many Requests — 响应内容: {detail or '无'}"
                )
            time.sleep(delay)
            delay *= 2
            continue
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip()
            raise RuntimeError(
                f"调用 NEWAPI 失败: {exc} — 响应内容: {detail or '无'}"
            ) from None

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM 返回结果为空")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not content:
            raise RuntimeError("LLM 返回内容为空")
        return content.strip()

    raise RuntimeError("调用 NEWAPI 失败：超过最大重试次数")


def chunk_tweets(tweets: Sequence[str], max_chars: int = CHUNK_CHAR_LIMIT) -> List[List[str]]:
    if not tweets:
        return []

    chunks: List[List[str]] = []
    current_chunk: List[str] = []
    current_length = 0

    for tweet in tweets:
        tweet_length = len(tweet)
        if current_chunk and current_length + tweet_length > max_chars:
            chunks.append(current_chunk)
            current_chunk = []
            current_length = 0

        current_chunk.append(tweet)
        current_length += tweet_length

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def send_email(summary: str, files: Iterable[Path]) -> None:
    smtp_host = os.environ.get("SMTP_HOST", DEFAULT_SMTP_HOST)
    smtp_port = int(os.environ.get("SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    smtp_username = os.environ.get("SMTP_USERNAME", DEFAULT_SMTP_USERNAME)
    smtp_password = os.environ.get("SMTP_PASSWORD", DEFAULT_SMTP_PASSWORD)

    email_from = os.environ.get("EMAIL_FROM", smtp_username or "")
    email_to = os.environ.get("EMAIL_TO", DEFAULT_EMAIL_TO)

    if not email_to:
        raise RuntimeError("邮件收件人地址未配置，请在 .env 中设置 EMAIL_TO。")

    tz = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")
    subject = f"TweetDeck 趋势总结 - {timestamp}"

    attachments_info = "\n".join(f"  - {path.name}" for path in files)
    body = (
        f"以下是最近一小时的推文趋势总结：\n\n{summary}\n\n"
        f"已处理文件：\n{attachments_info or '（本次无新文件）'}\n"
    )

    if not smtp_username or not smtp_password:
        send_email_via_mail_app(subject, body, email_to)
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from or smtp_username
    message["To"] = email_to
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    except smtplib.SMTPResponseException as exc:
        if exc.smtp_code == -1 and exc.smtp_error in {b"", b"\x00\x00\x00"}:
            print("警告: SMTP 服务器在关闭连接时返回 (-1, b'\\x00\\x00\\x00')，已忽略。")
        else:
            raise


def send_test_email() -> None:
    send_email("这是一封来自 summarize_tweets.py 的测试邮件。", [])
    print("测试邮件已发送。")


def send_email_via_mail_app(subject: str, body: str, recipient: str) -> None:
    script = textwrap.dedent(
        f"""
        tell application "Mail"
            set newMessage to make new outgoing message with properties {{subject:"{_escape_applescript(subject)}", content:"{_escape_applescript(body)}", visible:false}}
            tell newMessage
                make new to recipient with properties {{address:"{_escape_applescript(recipient)}"}}
                send
            end tell
        end tell
        """
    )
    subprocess.run(["osascript", "-e", script], check=True)


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def archive_files(files: Iterable[Path]) -> None:
    if not files:
        return
    now = datetime.now()
    dated_folder = PROCESSED_ROOT / now.strftime("%Y-%m-%d")
    dated_folder.mkdir(parents=True, exist_ok=True)
    for path in files:
        if not path.exists():
            print(f"警告: 待归档文件 {path} 已不存在，跳过。")
            continue
        target = dated_folder / path.name
        counter = 1
        while target.exists():
            target = dated_folder / f"{path.stem}_{counter}{path.suffix}"
            counter += 1
        try:
            path.rename(target)
        except FileNotFoundError:
            print(f"警告: 归档时文件 {path} 消失，已跳过。")
            continue
        except OSError as exc:
            print(f"警告: 归档文件 {path} 失败：{exc}，已跳过。")
            continue


def main() -> None:
    state = load_state()
    touched_files: List[Path] = []
    try:
        tweets, touched_files = collect_new_tweets(state)

        if not tweets:
            print("没有新的推文正文，退出。")
            return

        original_count = len(tweets)
        tweets = deduplicate_tweets(tweets)
        if original_count != len(tweets):
            print(
                f"去重后推文数：{len(tweets)}（移除 {original_count - len(tweets)} 条重复推文）"
            )

        if not tweets:
            print("新的推文在去重后为空，跳过。")
            return

        chunks = chunk_tweets(tweets)
        chunk_summaries: List[str] = []

        for idx, chunk in enumerate(chunks, start=1):
            if not chunk:
                continue
            print(f"正在总结分段 {idx}/{len(chunks)}，推文数量：{len(chunk)}")
            try:
                chunk_summary = call_llm(build_chunk_prompt(chunk, idx, len(chunks)))
                chunk_summaries.append(chunk_summary)
            except Exception as exc:
                print(f"分段 {idx} 处理失败：{exc}")
                continue

        if not chunk_summaries:
            print("所有分段均失败或为空，发送失败提示邮件。")
            failure_message = "本小时推文总结失败：所有分段分析均未成功，请稍后重试。"
            generated_at = datetime.now(timezone.utc).isoformat()
            update_static_feed(
                failure_message,
                touched_files,
                len(tweets),
                0,
                generated_at,
            )
            send_email(failure_message, touched_files)
            archive_files(touched_files)
            return

        if len(chunk_summaries) == 1:
            final_summary = chunk_summaries[0]
        else:
            compressed_summaries = compress_summaries_for_overall(chunk_summaries)
            print("正在汇总所有分段…")
            try:
                final_summary = call_llm(build_overall_prompt(compressed_summaries))
            except Exception as exc:
                print(f"汇总阶段失败：{exc}，将以合并摘要拼接发送。")
                combined = "\n\n".join(
                    f"合并摘要 {idx + 1}：\n{summary}" for idx, summary in enumerate(compressed_summaries)
                )
                final_summary = (
                    "【注意】自动汇总失败，下方为合并摘要拼接，请人工复核。\n\n"
                    f"{combined}"
                )

        generated_at = datetime.now(timezone.utc).isoformat()
        update_static_feed(
            final_summary,
            touched_files,
            len(tweets),
            len(chunk_summaries),
            generated_at,
        )
        send_email(final_summary, touched_files)
        archive_files(touched_files)
    finally:
        save_state(state)

    print("已生成总结并发送邮件。")


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--test-email":
            send_test_email()
            sys.exit(0)
        main()
    except Exception as exc:  # pragma: no cover - operational feedback
        sys.stderr.write(f"运行失败: {exc}\n")
        sys.exit(1)
