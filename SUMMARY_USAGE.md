# Tweet Trend Summariser 使用指南

脚本 `summarize_tweets.py` 会在每次运行时：

1. 扫描 `~/Downloads/tweetdeck_exports` 目录中尚未处理的 CSV（由扩展导出的文件）。
2. 读取其中的推文正文（忽略日期、用户名列）。
3. 调用从环境变量或 `api文档.md` 解析到的 NEWAPI 接口获取趋势总结。
4. 通过 SMTP 将总结邮件发送至 `wenhanwei.me@gmail.com`。
5. 将已处理的 CSV 归档到 `~/Downloads/tweetdeck_exports/processed/<日期>/`。

## 运行前准备

1. **API 配置**
   - `summarize_tweets.py` 文件顶部写死了默认的 `DEFAULT_NEWAPI_BASE_URL` 与 `DEFAULT_NEWAPI_KEY`，直接在该文件中修改即可。
   - 若希望临时覆盖，可设置以下环境变量：

```bash
export NEWAPI_BASE_URL="https://你的newapi服务器地址"
export NEWAPI_API_KEY="sk-xxxx"             # API key
```

2. **邮件发送配置**

```bash
export SMTP_HOST="smtp.gmail.com"           # 可按需调整
export SMTP_PORT="587"
export SMTP_USERNAME="你的邮箱地址"
export SMTP_PASSWORD="邮箱的应用专用密码"
export EMAIL_FROM="可选：发件人显示地址"
export EMAIL_TO="wenhanwei.me@gmail.com"    # 可改为其他收件人
```

> 如果未设置 `SMTP_USERNAME`/`SMTP_PASSWORD`，脚本会自动调用 macOS 的 Mail.app 来发送邮件，前提是 Mail 已配置可用的账号。

3. **首次运行测试**

```bash
cd /Users/pot/Documents/TwitterMessage
python3 summarize_tweets.py
```

确认终端输出 “已生成总结并发送邮件。” 并查收邮件。

- 若想单独测试 SMTP 是否配置正确，可运行：

```bash
python3 summarize_tweets.py --test-email
```

成功后会收到一封测试邮件，脚本不会处理 CSV。

## 定时执行（每 1 小时）

### macOS `launchd`
1. 创建 `~/Library/LaunchAgents/com.tweetdeck.summary.plist`，内容示例：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tweetdeck.summary</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd /Users/pot/Documents/TwitterMessage && python3 summarize_tweets.py</string>
  </array>
  <key>StartInterval</key><integer>3600</integer> <!-- 每 3600 秒 = 1 小时 -->
  <key>StandardOutPath</key><string>/tmp/tweetdeck_summary.log</string>
  <key>StandardErrorPath</key><string>/tmp/tweetdeck_summary.err</string>
</dict>
</plist>
```

2. 载入任务：
```bash
launchctl load ~/Library/LaunchAgents/com.tweetdeck.summary.plist
```

### 或者使用 `cron`

```bash
0 * * * * cd /Users/pot/Documents/TwitterMessage && python3 summarize_tweets.py >> /tmp/tweetdeck_summary.log 2>&1
```

## 注意事项
- 扩展需保持在 TweetDeck 页面运行，并以 CSV 模式导出。
- 如果某次运行未生成推文正文，脚本会直接归档 CSV 并记录在状态文件 `tweet_summary_state.json` 中，避免重复处理。
- 调试时可清空 `tweet_summary_state.json` 或删除归档目录以重新处理历史 CSV。
- 若 API 或 SMTP 出错，脚本会在终端输出错误并以非零状态退出，可通过日志排查。
