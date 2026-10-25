# 新闻联播备考日报

两种运行方式，同一套代码：

- **GitHub Actions 全自动（推荐）**：每天北京时间 21:30/22:35/23:35 三次触发，自动抓取、AI 分析、生成 PDF，PDF 提交到仓库 `public/pdf/` 形成长期链接，经 Server酱 推送微信（要点 + PDF 链接 + 央视完整版视频链接）。不依赖本机开机；仓库需设为 Public，否则微信里打不开 PDF 链接。
- 本地 Windows 工具：晚上登录电脑后补跑，生成中文 PDF，按需上传腾讯云 COS，再通过 Server酱发送微信摘要和下载链接。不下载视频。

## 当前能做什么

- 无需 API：抓取指定日期央视逐条文字稿（含每条在整期视频中的约略起点和时长），生成带原文节选、讲话候选段落、来源链接的“原文阅读版”。此版没有 AI 考点分析，也不自动推送。
- 配置模型后：按重要性排序，逐条总结，记录讲话，生成考研/考公考编关联与自拟题。每个新闻必须有结果，讲话摘录须匹配原文。
- 配置推送后（Actions 与本地版均可选 COS，Actions 同时保存仓库备用 PDF）：微信收到要点摘要（含“视频约 XX:XX 起”定位）、PDF 链接和视频链接。
- 配置自动任务后：登录时检查、每日配置时间开始处理当天新闻，之后每30分钟检查，失败或漏掉的日期下次继续。默认21:30，每轮最多7天。电脑须联网，运行期间不能休眠。

## GitHub Actions 全自动部署

一次性配置，之后每天全自动，不需要本机开机：

1. 安装 git，在 GitHub 新建一个 **Public** 空仓库（Private 仓库的 PDF 链接需要登录，微信里打不开）。
2. 在本项目目录执行（首次）：
   ```
   git init -b main
   git add -A
   git commit -m "新闻联播备考日报"
   git remote add origin https://github.com/<你的用户名>/<仓库名>.git
   git push -u origin main
   ```
   首次 push 前确认没有把 config.json、secrets.dat、logs 一起提交（.gitignore 已排除）。
3. 仓库 Settings → Secrets and variables → Actions，添加两个 Secret：
   - `NEWSSTUDY_MODEL_API_KEY`：模型 API Key
   - `NEWSSTUDY_SERVERCHAN_SENDKEY`：Server酱 SendKey（https://sct.ftqq.com 扫码获取）。**多人推送**：每个接收人自己扫码获取 SendKey 给你，多个 Key 用英文逗号分隔填入同一个 Secret，每天各自微信都会收到。
4. 编辑 `config.actions.json`，填写两个模型字段并提交推送（不含密钥）：
   - DeepSeek：`https://api.deepseek.com/v1` + `deepseek-chat`
   - 智谱 GLM：`https://open.bigmodel.cn/api/paas/v4` + 模型名如 `glm-4.5`
5. 仓库 Actions 页启用 `daily` 工作流。可用 Run workflow 手动指定日期试跑。

运行机制：每晚 21:30 首跑、22:35/23:35 补跑；已完成的日期自动跳过（不重复调用模型）；采集数据、安全投递快照和 PDF 自动提交回仓库（data/、output/、public/）；SQLite、锁、日志和签名 URL 不提交。视频时间戳按当日分条时长累计估算，未计入片头与转场，仅供定位参考。

公开性说明：Public 仓库里的文字稿与 PDF 任何人都可访问（内容均来自公开报道，无密钥；密钥只存放在 GitHub Secrets）。

## 本地 Windows 使用

双击“启动工具.cmd”。当前电脑可使用 Codex 附带 Python 和 ReportLab；换电脑时建议安装 Python 3.11+（包含 tkinter），再双击“安装依赖.cmd”。

1. 在“运行与报告”选择日期，生成原文阅读版，确认采集和PDF正常。
2. 在“本地配置”填写模型基础URL、模型名称、API Key。URL要包含供应商要求的前缀，例如 /v1，程序在后面添加 /chat/completions。需兼容 Chat Completions JSON 接口。模型应支持至少14,000输出token及足够上下文；若供应商限额不同，可编辑 config.json 的 model_max_tokens。API未开通时不会伪装成AI报告。
3. 若需要手机上的PDF链接，开通腾讯云 COS，建立私有桶，填写区域、完整桶名、SecretId/SecretKey，并勾选上传。安装依赖脚本包含 COS SDK。仅PDF上传；完整正文和分析JSON保存在本地。
4. 在 https://sct.ftqq.com 注册并配置微信接收通道，将 SendKey 填入配置。勾选微信通知。当前同时支持 SCT 和 sctp 格式。
5. 保存，然后“执行自动补跑”。成功生成、上传和微信接收后，再点“安装/更新开机自动任务”。首次日期默认2026-10-23；想补之前的新闻可自行提前。

程序不代替你开通付费账号，也不含免费模型额度。模型、COS存储/流量以及通知通道按供应商计费。请先跑一天确认成本和效果。

密钥使用 Windows DPAPI 加密后保存到 secrets.dat，仅当前Windows用户可读取。不要分享该文件；不要把密钥发到聊天、提交到Git。也可用 NEWSSTUDY_MODEL_API_KEY、NEWSSTUDY_COS_SECRET_ID、NEWSSTUDY_COS_SECRET_KEY、NEWSSTUDY_SERVERCHAN_SENDKEY 环境变量覆盖。运行日志不记录密钥、请求体或带签名下载链接。

## 日常使用

- 21:30前开机：先处理以前遗漏的日期，当天到21:30后处理。
- 21:30后开机：登录后补跑。午夜后的凌晨运行，会处理前一天。
- 当日内容不全或网络失败：不发布不完整分析，保留待重试状态。采集完成前会再次比较央视目录。
- 同一天重复运行：复用已保存分析，不重复支付模型费用；上传失败从上传继续；通知正常确认后不重复发送。
- 推送超时或进程突然退出：标记“结果待核对”。核对手机/Server酱后台后，点击“我已收到”或“允许重试”。不宣称实现网络层严格一次投递。
- PDF默认链接有效72小时。已收到的链接过期后，本地报告仍在；如需重新发送，可在SQLite状态之外使用COS控制台为该PDF重新生成授权链接。第一版不提供在线永久下载页。
- 自动任务仅当前用户登录时运行，不存Windows密码，不唤醒关机电脑。更改每日时间后重新安装任务。移除按钮只移除本工具任务，不删除报告。

## 远端投递恢复（Actions）

2026-10-25 已确认旧提交失败源于 GitHub 推送保护：SQLite 内 COS 签名链接含腾讯云 Secret ID。更换推送令牌或关闭保护不能解决数据泄露问题。新工作流保留写权限，但不再把 SQLite 提交到公开仓库。可选 NEWSSTUDY_PUSH_PAT 仅作为 checkout 的认证来源，无需为此次错误专门申请 PAT。

每个接收人发送前必须先将 uncertain 状态保存到远端；服务确认后保存 sent。断网、提交被拒、远端 main 被其他操作更新、无法确认保存结果时停止发送。已成功的人不会因其他人超时而收到重发。发现远端更新时请从最新 main 重跑；不会强制推送或自动合并投递状态。

首次升级将旧的“已生成但不知是否发过”日期置为待核对。核对手机与 Server酱后台后再确认；不能通过直接重跑来消除不确定状态。推送接口不支持本工具的幂等键，程序不保证网络层严格一次；可能宁可漏发也不重复，需要人工恢复。

历史待核对状态只提示并跳过，不使本轮任务失败，也不重复上传其 PDF。新的发送失败、缺失有效下载链接、远端保存失败仍会返回失败。新检出的本地工作区会从安全快照恢复状态，GUI 同样保留这些待核对记录。

维护者在已更新到最新 main、工作区干净、具有正常 Git 写权限的开发副本执行：

    python -m newsstudy prepare-state --remote-persist
    python -m newsstudy status

status 显示日期和接收人完整指纹。只处理已经核对的某位接收人：

    python -m newsstudy resolve --date 2026-10-24 --recipient <完整指纹> --received --remote-persist

确实没有收到才批准重试（可能重复，须先核对）：

    python -m newsstudy resolve --date 2026-10-24 --recipient <完整指纹> --retry --remote-persist

旧版记录没有指纹时，省略 --recipient 可处理指定日期。之后重新运行 Actions 即可。恢复命令只修改记录，不发送消息；已确认 sent 的接收人不会被 retry 改回未发送。Windows GUI 的“我已收到 / 允许重试”继续操作本地待核对记录，不自动同步线上状态。

prepare-state 与 sync-state 会提交并推送安全快照与白名单产物，不要将它们当作只读检查命令。不会暂存业务代码；如暂存区有其他修改会拒绝运行。test_push 手动输入现仅检查配置格式，**不发微信**。

## 报告与知识边界

已核对上海理工大学2027年招生目录和参考书目：030500马克思主义理论，01马克思主义基本原理；701马克思主义基本原理、870中国化的马克思主义。完整来源在 profile.json。

四本书：2023《马克思主义基本原理》、2020第二版《马克思恩格斯列宁哲学经典著作导读》、2023《毛泽东思想和中国特色社会主义理论体系概论》、2023《习近平新时代中国特色社会主义思想概论》。主题索引是本工具整理的学习辅助，不是教材目录；未内置教材全文，不生成教材页码或原著引语。

江苏/上海/浙江/安徽省考和事业编：当前给出全国通用或直接涉及当地的素材关联；具体年度和岗位大纲尚未内置。相关度不是命中概率，自拟题不是历年真题。

讲话来自央视报道，保留出处和原始文字稿。报道转述与直接引语分开，不能视为讲话全文；第一版没有自动全网搜索补齐讲话全文。自动校验可防止模型改写引文，但事实摘要、讲话归属和原理解释仍需人工复核。

## 文件位置

- config.json：非敏感配置；secrets.dat：当前Windows账号加密凭据。
- profile.json / prompts/analysis.txt：学校资料、知识主题和分析要求。
- data/YYYY-MM-DD/source.json：完整文字稿；speech-candidates.json：讲话候选原文。
- output/pdf/YYYY-MM-DD/report.pdf：完整AI报告，旁边是分析和来源JSON。
- output/pdf/YYYY-MM-DD-reading.pdf：原文阅读版。
- public/pdf/YYYY-MM-DD.pdf：仓库托管版日报 PDF（Actions 模式，微信推送的长期链接指向这里）。
- data/state.sqlite3：本地完整状态（含临时链接），仅保存在本机或当前 runner，不提交 Git。
- data/delivery-state.json：公开的安全投递快照，保存日期、阶段、接收人指纹与发送状态。
- logs/：本地月度日志，不提交 Git。

## 命令行

在项目目录用已安装依赖的Python运行：

    python -m newsstudy gui
    python -m newsstudy reading --date 2026-10-22
    python -m newsstudy run
    python -m newsstudy run --date 2026-10-22
    python -m newsstudy status
    python -m unittest discover -s tests -v

手动安装自动任务（使用实际Python绝对路径）：

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/task.ps1 -PythonPath "C:\路径\python.exe" -At "21:30"

移除时追加 -Remove。任务名称包含项目路径哈希，仅覆盖本目录对应任务。

## 外部依赖

ReportLab（中文PDF，替代前期候选Playwright以减少浏览器依赖）；腾讯云官方cos-python-sdk-v5（私有文件上传/签名）；Windows任务计划（本地定时）。采集逻辑参考MIT项目DuckBurnIncense/xin-wen-lian-bo，归属见THIRD_PARTY_NOTICES.md。

网络配置使用Python标准库代理环境变量。不要关闭HTTPS证书校验。单位代理拦截时，请使用可信网络或配置正确的证书链。
