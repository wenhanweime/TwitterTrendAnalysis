# 推特趋势分析工具集

[English Version](README.md) · 中文说明

本项目包含两部分协同工作，帮助你收集 TweetDeck（配合 OldTweetDeck）导出的原始数据，并在每小时自动生成 AI 趋势摘要：

- **Chrome 插件**（`extension/`）：在 Old TweetDeck 页面中抓取列或任意 DOM 元素，将结果保存为本地 CSV/文本文件。
- **Python 总结脚本**（`summarize_tweets.py`）：读取前述 CSV，调用你的 LLM 接口生成趋势报告，并通过邮件发送。

整个流程依赖社区项目 **[OldTweetDeck](https://github.com/dimdenGD/OldTweetDeck)**，以便恢复旧版的列布局，让插件能提取到完整数据。

---

## 1. 环境依赖

| 组件 | 说明 |
| --- | --- |
| 浏览器 | Chrome 112+（或任何支持扩展的 Chromium 浏览器） |
| TweetDeck UI | 按 [dimdenGD/OldTweetDeck](https://github.com/dimdenGD/OldTweetDeck) 指南安装旧版界面 |
| Python | Python 3.9+，需可使用 `pip` |
| Python 库 | `requests` |
| 邮件服务 | 可使用 SMTP（QQ、Gmail 等）或 macOS 上已配置好的 Mail.app |
| LLM 接口 | 与 Chat Completion 兼容的 API（示例中使用 NEWAPI） |

请确保 OldTweetDeck 处于登录状态；插件会依赖其 DOM 结构来定位列数据。

---

## 2. 配置步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/wenhanweime/TwitterTrendAnalysis.git
   cd TwitterTrendAnalysis
   ```

2. **准备环境变量**
   ```bash
   cp .env.template .env
   ```
   按实际情况填写 `.env` 中的内容：
   - `NEWAPI_BASE_URL` 与 `NEWAPI_API_KEY`：LLM 服务地址与密钥。
   - `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`：外发邮件所需的 SMTP 配置；若暂不填写用户名/密码，脚本会退回到 macOS Mail.app 发送。
   - `EMAIL_FROM`（可选）与 `EMAIL_TO`（必填）：发件人与收件地址。
   - `LLM_MODEL`：若需要自定义模型 ID，可在此覆盖默认值。

3. **安装 Python 依赖**
   ```bash
   pip install requests
   ```
   建议在虚拟环境（如 `python -m venv venv`）中执行。

---

## 3. 加载 Chrome 插件

1. 打开 `chrome://extensions`，启用 **开发者模式**，点击 **加载已解压的扩展程序**。
2. 选择仓库中的 `extension/` 目录。
3. 确认工具栏图标为蓝底白 “T” 圆形标志。
4. 打开 [tweetdeck.twitter.com](https://tweetdeck.twitter.com/)，确保 OldTweetDeck 已启用并显示多个列，点击插件图标调出控制面板。

### 控制面板说明

- **定时间隔 / 首次延迟**：输入分钟数，用于设定定时抓取频率和首次执行前的延迟。
- **CSS 选择器**：每行一个，可对非 TweetDeck 页面进行自定义文本抓取；为空时默认抓取整页正文。
- **输出格式**：
  - `自动`：优先尝试识别 TweetDeck 列生成 CSV，失败时回退到纯文本。
  - `TweetDeck CSV`：强制按 TweetDeck 列导出 CSV。
  - `纯文本`：始终保存文本内容。
- **按钮**：
  - `开始定时`：启动后台闹钟，按设定频率自动抓取。
  - `停止定时`：停止闹钟。
  - `立即抓取`：立刻执行一次抓取。

导出文件默认保存到浏览器下载目录的子文件夹：

```
~/Downloads/
    tweetdeck_exports/      # 提供给 summarize_tweets.py 的 CSV
    page_content_exports/   # 当以文本模式保存时使用
```

---

## 4. 运行总结脚本

脚本仅处理最近一小时内更新、且尚未归档到 `~/Downloads/tweetdeck_exports/processed/` 的 CSV 文件。

```bash
python3 summarize_tweets.py
```

运行流程：

1. 读取 `.env`（若某项缺失则使用默认值或报错）。
2. 扫描 CSV 新增行并去重。
3. 按字符数自动分段，调用 LLM 接口获取每段摘要。
4. 汇总为最终趋势报告。
5. 发送邮件，并将已处理的 CSV 移动至 `processed/<日期>/` 目录。

**仅测试邮件发送**
```bash
python3 summarize_tweets.py --test-email
```

**定时运行建议**
- macOS `launchd`：可参考 `SUMMARY_USAGE.md` 中提供的 plist 示例，实现每小时自动执行。
- Cron：`0 * * * * cd /path/to/TwitterMessage && python3 summarize_tweets.py`。

---

## 5. 目录结构

```
extension/               # Chrome 插件（Manifest v3）
    background.js        # 定时任务 & 下载逻辑
    content.js           # 页面内容采集脚本
    popup.html/js        # 插件控制面板
    icon-*.png           # 图标资源
summarize_tweets.py      # LLM 摘要脚本 + 邮件发送
merge_txt_to_csv.py      # 文本导出的辅助转换脚本
.env.template            # 环境变量模板（复制为 .env）
.gitignore               # 忽略敏感文件与缓存
SUMMARY_USAGE.md         # 运行与调度补充说明
README.md                # 英文说明
README_CN.md             # 中文说明（本文）
```

---

## 6. 常见问题

- **插件提示“未匹配到元素”**：确认 OldTweetDeck 列已加载，或尝试切换输出格式为 `自动`/`纯文本`。
- **未生成 CSV 文件**：检查浏览器下载权限，首次保存时会自动创建 `tweetdeck_exports` 目录。
- **摘要脚本报 API 错误**：确认 `.env` 中 `NEWAPI_*` 设置正确且网络可访问目标接口。
- **SMTP 出现 (-1, b'\x00\x00\x00')**：这是 QQ SMTP 的常见连接关闭行为，脚本已捕捉并忽略此情况；若邮件未收到，请检查凭据是否正确。

更多细节可阅读 `SUMMARY_USAGE.md` 或直接查看源码中的注释。
