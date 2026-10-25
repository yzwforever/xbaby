# 项目交接文档（给下一个维护者 / AI 助手）

> 更新时间：2026-10-25 凌晨。本文档覆盖项目全貌、当前状态、未完成事项和排查手册。
> 日常使用说明见 README.md；本文档面向维护者。

## 一、这是什么

「新闻联播备考日报」：每天自动抓取央视《新闻联播》逐条文字稿 → DeepSeek 分析（重要性排序、分点总结、领导人讲话摘录+马克思主义原理映射、考研政治701/870/公共课与省考事业编关联、自拟练习）→ 生成带视频时间戳的中文 PDF → 上传腾讯云 COS → 通过 Server酱 推送到多个接收人的微信（消息内嵌完整日报正文）。

- **运行方式**：GitHub Actions 每天北京时间 21:30 / 22:35 / 23:35 三次触发（幂等，已完成的日期跳过），也支持手动 Run workflow（可指定日期、可勾选“只检查推送配置”）
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
  → 本地 data/state.sqlite3 + 远端 data/delivery-state.json（逐接收人投递账本）
```

## 四、2026-10-25 修复：推送保护、远端屏障与逐人状态

实际失败证据：Actions run 40113929884 / job 120931188003，GitHub 返回 GH013 / GITHUB PUSH PROTECTION，指出 data/state.sqlite3 含 Tencent Cloud Secret ID。日志同时明确 Contents: write。此前“改权限或换 PAT”的推测不能解释此次拒绝；不要绕过推送保护。

原因：COS 下载签名含 Secret ID，原流程将完整签名 URL 写入 SQLite，然后强制提交数据库。现在 SQLite 仅保留在本机/当前 runner；远端只保存 data/delivery-state.json 的白名单字段（日期、阶段、通知汇总、SHA-256 接收人指纹、逐人投递状态）。快照不含 URL、错误文本、SendKey、密钥或本机路径。

- 新增 newsstudy/persistence.py：只暂存白名单日报产物；从 Git 索引移除 SQLite/边车/锁；扫描产物中配置密钥、签名和 Secret ID；固定 main，拒绝旧基线、非快进和混入暂存区的代码。普通 push 后还须读回远端 HEAD 确认。不会强推、自动合并状态或覆盖并发更新。
- daily.yml：checkout main + 完整历史；可选 PAT 在 checkout 时统一配置认证，未设置则用 github.token，不把 Token 拼进 URL。先 prepare-state，发消息前保存 uncertain、收到服务确认后保存 sent，最后 sync-state。远端不可达或拒绝提交时停止发送。
- pipeline.py/state.py：按日期+接收人指纹保存 uncertain/sent/retry-approved；成功的人不重发，不确定的人须人工核对；GUI 原有两项核对按钮仍可用，命令行支持按日期/指纹核对。
- COS 错误只保留已知错误码，不再保存 SDK 原始异常文字。COS 模式也归档 PDF 到 public/pdf，备用链接在发送前随屏障提交。
- 工作流旧 test_push 输入改为仅检查 SendKey 格式和人数，**不会发送真实测试消息**。

首次迁移：数据库中的已生成日期及已存在的日报，若没有可信投递记录则保守标记为待核对，禁止自动补发。2026-10-23/24 在失败运行日志中显示服务已接受；应核对手机后再人工确认，不能仅因数据库显示未发送就重发。

退出状态修正：仅存在历史待核对记录时属于正常保守跳过，不返回失败，不重新上传其 PDF；当轮新发送失败、链接未就绪或 Git 屏障失败仍返回失败。没有 SQLite 的新检出目录（含 GUI）会自动读取安全快照，保留历史待核对记录。

恢复方法详见 README 的“远端投递恢复”。不要在工作副本 main 落后远端时运行同步命令；先更新代码。不要盲目更换 SendKey 后批准重试。尚不能保证网络层严格一次：发前记录成功、发请求前被终止会导致漏发；必须核对后批准重试。

## 五、常见问题排查手册

- **红叉先看是哪一步**：Actions → 运行 → daily → 看步骤列表。同步步骤红=先查推送保护、权限、远端基线或网络（见上）；「生成日报」红=看日志错误文字（COS 只记录白名单错误码）
- **「尚未配置模型API」**：config.actions.json 的 model 字段或 Secret 缺失（工作流有「密钥自检」步骤，四个都应显示"已配置"）
- **NoSuchBucket**：桶名/地域与腾讯云控制台不符
- **讲话校验失败**：正常反幻觉机制，三次失败自动降级，不用管
- **当天 21:30 没推送**：GitHub 定时器延迟 10-30 分钟常见；后面两轮会补；文字稿未上齐不算失败
- **重复收到微信消息**：说明状态没存回仓库（提交步骤失败的副作用）
- **本地推送 GitHub 失败**：开代理软件（端口7897），git 已配 `http.https://github.com.proxy`
- **回归测试**：`python -m unittest discover -s tests`（用 Codex 自带 Python 或 .venv）

## 六、设计决策备忘（为什么这么做）

- 引文逐字校验、topic 白名单、拒绝押题承诺：反幻觉是本工具的核心卖点，宁可降级不编造
- 视频时间戳按分条时长累计估算（误差1-2分钟），PDF 已注明"约"
- 私有桶+72h签名链接（COS）+ 仓库永久存档（public/pdf）双轨
- 定制资料在 profile.json（上海理工2027马理论考研701/870+江浙沪皖省考事业编），改目标院校/科目改这个文件即可
- prompts/analysis.txt 是给模型的核心指令，改动会影响分析质量，谨慎

## 七、给 AI 维护者的提示

- 代码在 `newsstudy/`（Python 标准库为主 + reportlab + cos sdk），测试 `tests/test_core.py` 与 `tests/test_persistence.py`，改动后必须跑
- `git pull` 后再 push（Actions 机器人会往 main 提交数据）；本机 git 走代理
- 用户是非技术用户，沟通要给逐步点击级指引，结论要说人话
