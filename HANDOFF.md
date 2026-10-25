# 项目交接文档（给下一个维护者 / AI 助手）

> 更新时间：2026-10-25 凌晨。本文档覆盖项目全貌、当前状态、未完成事项和排查手册。
> 日常使用说明见 README.md；本文档面向维护者。

## 一、这是什么

「新闻联播备考日报」：每天自动抓取央视《新闻联播》逐条文字稿 → DeepSeek 分析（重要性排序、分点总结、领导人讲话摘录+马克思主义原理映射、考研政治701/870/公共课与省考事业编关联、自拟练习）→ 生成带视频时间戳的中文 PDF → 上传腾讯云 COS → 通过 Server酱 推送到多个接收人的微信（消息内嵌完整日报正文）。

- **运行方式**：GitHub Actions 每天北京时间 21:30 / 22:35 / 23:35 三次触发（幂等，已完成的日期跳过），也支持手动 Run workflow（可指定日期、可勾选"只发测试消息"）
- **本地目录** `F:\新闻联播` 是同一份代码的开发副本；本地还有一套 tkinter GUI（`python -m newsstudy gui`），线上不用
- **GitHub 仓库**：`yzwforever/xbaby`（Public，Private 会让 PDF 链接打不开）

## 二、关键配置位置（排查先看这里）

| 配置 | 位置 | 当前值 |
|---|---|---|
| 模型 | `config.actions.json` | DeepSeek `https://api.deepseek.com/v1` + `deepseek-chat`，max_tokens 8000，单批 1 条 |
| PDF 托管 | `config.actions.json` | `pdf_host: cos`，桶 `xwlb-daily-1257930543`，地域 `ap-shanghai`，链接 72 小时有效 |
| GitHub Secrets（4个） | 仓库 Settings → Secrets → Actions | `NEWSSTUDY_MODEL_API_KEY`、`NEWSSTUDY_SERVERCHAN_SENDKEY`（支持逗号分隔多人群发）、`NEWSSTUDY_COS_SECRET_ID`、`NEWSSTUDY_COS_SECRET_KEY` |
| 触发时间 | `.github/workflows/daily.yml` 的 cron | 13:30 / 14:35 / 15:35 UTC |

注意：Secrets 名字必须一字不差（曾因少下划线排查两小时）；桶名/地域以腾讯云控制台「访问域名」为准（截图 OCR 曾把数字认错）。

## 三、数据流与文件

```
央视网 day页(标题/时长/链接) + 条目页(正文)
  → data/YYYY-MM-DD/source.json（含 duration/start_offset，时间戳=累计时长估算）
  → newsstudy/analysis.py（分批调模型，严格校验：引文逐字匹配[忽略空白]、
      讲话摘录须含说话人姓名、topic/track/region 白名单；讲话校验3次失败自动降级为无讲话版本）
  → output/pdf/YYYY-MM-DD/{analysis.json, report.pdf}
  → delivery.publish/upload → COS 签名直链（国内秒开）+ 仓库 public/pdf/ 永久存档
  → pipeline.push_pending：每天一条完整日报消息（速读/讲话/考点内嵌正文，
      超30000字节退回要点版），多 SendKey 群发，微信消息里另有 GitHub 备用链接
  → data/state.sqlite3 记录状态（幂等/防重复推送的关键）
```

## 四、当前未完成事项 ⚠️

**Actions 的「提交采集数据、状态与 PDF」步骤推送失败**（约 2026-10-25 00:18 起）：

- 症状：管线本身全部成功（抓取/分析/COS上传/微信推送都好），只有最后把结果 commit 回仓库的 `git push` 失败，导致状态不持久化 → 每次重跑会重复推送微信
- 已排查：本地完全复现提交流程无问题（add/commit/dry-run 全过）；工作流 `permissions: contents: write` 存在；非快进冲突已用 pull 兜底
- 待办（二选一，已告知用户）：
  1. 检查仓库 Settings → Actions → General → **Workflow permissions** 是否被改成只读（若是→改回 Read and write）
  2. 或创建 PAT（classic，public_repo 权限）存为 Secret `NEWSSTUDY_PUSH_PAT`——工作流已支持，检测到该 Secret 即自动改用 PAT 推送
- 排查手段：run 的 jobs API 看 steps 结论（匿名可查，注意 60次/小时限流）；真实错误在 Actions 日志的「!!!」行附近；本机直连 github 会断，git 已配置走本机代理 127.0.0.1:7897

## 五、常见问题排查手册

- **红叉先看是哪一步**：Actions → 运行 → daily → 看步骤列表。只有最后一步红=推送问题（见上）；「生成日报」红=看日志错误文字（现在 COS 错误带真实错误码）
- **「尚未配置模型API」**：config.actions.json 的 model 字段或 Secret 缺失（工作流有「密钥自检」步骤，四个都应显示"已配置"）
- **NoSuchBucket**：桶名/地域与腾讯云控制台不符
- **讲话校验失败**：正常反幻觉机制，三次失败自动降级，不用管
- **当天 21:30 没推送**：GitHub 定时器延迟 10-30 分钟常见；后面两轮会补；文字稿未上齐不算失败
- **重复收到微信消息**：说明状态没存回仓库（提交步骤失败的副作用）
- **本地推送 GitHub 失败**：开代理软件（端口7897），git 已配 `http.https://github.com.proxy`
- **测试 27 个**：`python -m unittest discover -s tests`（用 Codex 自带 Python 或 .venv）

## 六、设计决策备忘（为什么这么做）

- 引文逐字校验、topic 白名单、拒绝押题承诺：反幻觉是本工具的核心卖点，宁可降级不编造
- 视频时间戳按分条时长累计估算（误差1-2分钟），PDF 已注明"约"
- 私有桶+72h签名链接（COS）+ 仓库永久存档（public/pdf）双轨
- 定制资料在 profile.json（上海理工2027马理论考研701/870+江浙沪皖省考事业编），改目标院校/科目改这个文件即可
- prompts/analysis.txt 是给模型的核心指令，改动会影响分析质量，谨慎

## 七、给 AI 维护者的提示

- 代码在 `newsstudy/`（Python 标准库为主 + reportlab + cos sdk），测试 `tests/test_core.py` 27 个，改动后必须跑
- `git pull` 后再 push（Actions 机器人会往 main 提交数据）；本机 git 走代理
- 用户是非技术用户，沟通要给逐步点击级指引，结论要说人话
