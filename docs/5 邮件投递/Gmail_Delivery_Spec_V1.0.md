# Gmail 邮件投递

> 当前完整流程以根目录 README 和 [Pipeline 运行说明](../Pipeline运行说明.md) 为准。本页保留该组件的接口与分阶段验证记录；以下命令须先激活 daily-input conda 环境。

本模块发送已经出版的完整单文件 HTML，不重新生成正文。默认生成 EML 预览，只有显式提供 `--send` 才调用 Gmail。当前本地名单只配置用户自己的 Gmail；真实名单保存在被 Git 忽略的 `config/email.json`，公开样例见 `config/email.example.json`。

## 收件人配置

从仓库根目录运行命令。新环境先复制样例：

```bash
cp config/email.example.json config/email.json
```

配置字段：`sender` 为授权账号或该账号已验证的发件别名；`to`、`cc`、`bcc` 为纯邮箱地址数组；`subject_prefix` 为邮件主题前缀。同一地址跨名单重复时只保留首次出现，优先顺序 To、Cc、Bcc。支持仅 Bcc，拒绝空名单、换行注入和未知字段。改动名单后，下次运行生效；同一期的新名单视作一个新的发送任务，会发送给新名单内所有人。

## 一次性 OAuth 设置

在 Google Cloud 项目启用 Gmail API，并为桌面应用创建 OAuth 客户端，下载到 `secrets/gmail-client.json`。使用自己的账号完成 Google 同意页面授权。不要将客户端文件、token 或名单提交仓库。

```bash
python -m pip install -e '.[email-setup]'
python -m publisher.delivery --authorize secrets/gmail-client.json
```

命令打印授权网址，浏览器打开后回调本机 8765 端口；WSL 环境需确保 Windows 浏览器能访问该端口。授权等待最多 300 秒。授权文件默认保存到 `secrets/gmail-token.json`，文件权限 0600，不覆盖已有文件。可通过 `--token PATH` 或环境变量 `GMAIL_TOKEN_FILE` 改路径。也可直接提供已有的 Google `authorized_user` JSON，须含 `client_id`、`client_secret`、`refresh_token`。

申请 `gmail.send` 和 `gmail.readonly`：前者发送，后者搜索已发送邮件并取回附件核验。仅有发送权限时最多得到 `SEND_ACCEPTED`，不能声称完成已发送核验。此处补充了项目计划中“建议仅申请发送权限”无法支持核验的缺口。

项目运行只使用 Python 标准库访问固定 Google HTTPS 端点；可选依赖仅用于首次浏览器授权。访问令牌每次运行由 refresh token 获取，不写日志。长期运行前须核对 Google OAuth 应用发布状态及 refresh token 有效性。

## 预览与发送

```bash
python -m publisher.delivery \
  'docs/3 出版引擎/阶段三成品/Daily_Input_2026-11-06.html' \
  --date 2026-11-06 --version 1
```

预览文件为 `outputs/email/preview.eml`，可在邮件客户端打开，含名单、正文和原始 HTML 附件。预览不读取 OAuth，不创建发送记录，不联网。支持 `--preview PATH` 自定义路径。

确认生产文件路径、日期、版本和名单后，在同一命令追加 `--send`。附件名为 `Daily_Input_YYYY-MM-DD_V版本.html`，类型 `text/html`，以 attachment 发送。默认上限 12 MiB；文件须为 UTF-8 且含 HTML 和 body 闭合标记。本模块不替代出版阶段和阶段四 QA，生产流水线应先通过 QA，再传入归档成品。

## 状态、核验和恢复

SQLite 默认路径为 `var/email.sqlite3`，可用 `--database PATH` 覆盖。保存任务键、附件 SHA-256、不可变原邮件和每次尝试的 MIME、状态、Gmail ID、故障和更新时间。记录含收件地址及附件，应按私有数据管理；数据库和旁边的锁文件须放在本机同一持久目录并一起保留。删除记录或在另一台机器用独立数据库运行会失去重复发送保护。当前锁机制为 Linux/WSL 的 `flock`，并非跨机器锁。

| 状态 | 含义与后续动作 |
| --- | --- |
| `PREVIEW` | 只写 EML，未调用发送接口 |
| `SEND_UNKNOWN` | 已持久化发送意图，但请求结果不明或进程中断；重跑先搜索原 Message-ID |
| `SEND_REJECTED` | Gmail 返回明确 4xx 拒绝（408 按结果不明处理）；修复凭证、权限或请求后可重跑 |
| `SEND_ACCEPTED` | Gmail 返回 ID；重跑仅核验，绝不重新发送 |
| `SENT_VERIFIED` | 取回邮件含 SENT 标签，已记录的 Gmail API ID、主题、发件人、To/Cc/Bcc、附件名、类型和附件 SHA-256 均匹配；结果不明且没有 API ID 时按原 Message-ID 核验；后续跳过 |

只有 `SENT_VERIFIED` 返回退出码 0；其他实际发送状态返回 1，供调用方安排恢复。Gmail 已发送核验不等于收件人收件箱投递确认，不产生 `DELIVERY_CONFIRMED` 状态。

发送键由日期、版本、发件人及按 To/Cc/Bcc 分类排序的地址生成。同一键若附件不同则拒绝，需递增版本。并发进程在持有本地文件锁期间执行核验和发送；在调用 Gmail 前提交 `SEND_UNKNOWN`，防止崩溃后丢失尝试记录。

HTTP 层不自动重试发送。首次结果不明时，下次显式运行相同命令先搜索；核验找到则直接完成，查询失败则停止。若查询成功仍找不到，使用同一份归档附件补发一次，正文包含故障说明、前封附件可能不完整的提醒及请收件人核对两封附件的说明。Gmail 搜索可能有索引延迟，因此仍可能收到两封。每个键最多两次发送尝试；第二次结果不明时继续重跑只核验，不再补发。明确拒绝也计入尝试上限。达到上限后需人工检查并处理，不要通过删除数据库强行重试。

## 验证

```bash
python -m unittest discover -s tests -p test_email_delivery.py -v
```

测试使用模拟 Gmail，覆盖收件名单、附件字节、并发去重、崩溃恢复、接受后核验失败、响应丢失后找回、补发说明与次数上限、附件变更和配置拒绝。真实 OAuth、真实 Gmail 及收件箱附件体验仍需授权后完成验收。

接口依据：[Gmail 发送](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)、[邮件读取与权限](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)、[邮件查询](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)、[桌面 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)。

## 2026-11-07 实际测试记录

已完成 OAuth、令牌刷新、授权账号核对及一封样刊的真实发送。状态为 `SENT_VERIFIED`，附件名 `Daily_Input_2026-11-06_V1.html`，378712 字节，归档与已发送邮件附件的 SHA-256 一致。Gmail 在这次发送中改写了 RFC Message-ID，初版严格比较该头部造成误判；已改为在有已记录 API ID 时核对 API ID，并保留主题、地址及附件的严格比对。回归测试共 21 项通过，没有重复发送。用户收件箱及浏览器打开附件的体验仍待用户确认。
