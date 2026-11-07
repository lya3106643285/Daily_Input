# Pipeline 运行说明

在 WSL Ubuntu 项目根目录激活 `daily-input` conda 环境后，使用 `python -m publisher.pipeline`。交稿由完整九栏 Markdown、`issue.json` 和本地 assets 组成；远程图片须在 `remote_images` 中提供经人工确认的离线许可，详见图片离线获取文档。下载器消费已选定图片直链，不自动从任意网页挑图。

## 每日自动门禁（默认）

用户已批准：固定样例回归＋当天规则检查作为自动交付门禁。这不等于每期人工审美验收，也不保证所有视觉问题都能由规则发现。报告固定标记 `human_visual_review=not_performed`。

```bash
python -m publisher.pipeline path/to/draft.md \
  --metadata path/to/issue.json \
  --workspace var/pipeline \
  --email-config config/email.json
```

默认只生成 EML 预览，不读取 Gmail token、不发送邮件。不需要邮件预览时省略 `--email-config`。

执行顺序：图片离线准备 → 原子入库 → HTML 出版 → 固定样例回归 → 当天规则检查及截图 → 原子归档 → 邮件预览或显式投递。

固定样例始终是 `outputs/stage4/nine` 的 2026-11-06 九栏示例和 `outputs/stage4/long` 的 2026-11-07 长文示例。程序每次都用当前模板重新出版这两份已登记稿件，与各自已批准基线比较，核对输入身份、截图哈希和字体/浏览器环境。缺少任何样例、输入变化、环境变化、规则失败或截图差异超限，都阻止下游归档和投递；没有跳过门禁的 CLI 选项。

当天稿件检查正文和链接一致性、结构、图片、HTML、离线包及真实 Chromium 浏览器规则。390×844、820×1180、1180×820 三视口检查离线图片、页内锚点、横向溢出、正文 20px/31px、脚本错误和外部资源请求，并保存整页截图。当天新内容不与昨日或其他期次整页截图比较，也不创建本期基线。报告的 `visual` 项明确为 `not_applicable`，其余七项必须通过，整体才为 `passed`。

两份固定样例的完整报告、HTML 和六张截图放在本次运行的 `qa/template-regressions/`；当天报告与三张截图放在 `qa/`。归档复制全部材料，保留哈希。11 月 4 日旧生产基线及所有历史诊断目录保持原样。

单独执行当天规则检查可用：

```bash
python -m publisher.qa path/to/ingested-issue \
  --html path/to/Daily_Input_YYYY-MM-DD.html \
  --output-dir var/daily-qa --rules-only
```

该命令只检查当天规则；完整 pipeline 还会强制检查两份固定样例。

## 同一期基线比较

需要对已审阅的同一期复验时，显式选择：

```bash
python -m publisher.pipeline path/to/draft.md \
  --metadata path/to/issue.json \
  --visual-policy same-issue \
  --baseline-dir path/to/this-issue-baseline \
  --workspace var/issue-comparison
```

此模式仍必须通过两份固定样例回归，并额外比较本期基线。明确创建或更新本期基线时才加 `--update-baseline`；该选项不能与每日模式一起使用，不应常驻每日任务。基线创建不是人工审批证据，不会自动批准 11 月 4 日旧基线更新。独立 QA 的默认模式仍为同一期比较，原有 `--baseline-dir` 用法保持兼容。

## GitHub 交稿

```bash
python -m publisher.pipeline handoff/draft.md \
  --metadata handoff/issue.json \
  --github-repo https://github.com/OWNER/REPO \
  --commit FULL_40_CHARACTER_COMMIT_SHA \
  --workspace var/pipeline
```

Markdown、元数据和本地图片须在同一个 commit。接收端只读获取明确的完整 SHA，不使用浮动分支，不修改远端；私有仓库使用本机已配置的 Git 凭据，不在命令中填写 token。缓存复用时检查 HEAD 和工作区完整性，记录仓库、commit、文件路径和输入哈希。获取失败或路径越界即停止。该入口不创建上游写作定时任务，也不证明写入端已具备无人值守交稿能力。

## 报告、归档和重跑

| 状态 | 含义 |
| --- | --- |
| `RUNNING` | 正在执行；硬中断后可重跑 |
| `FAILED` | 查看失败阶段、error 和相应 QA 报告 |
| `ARCHIVED` | 技术门禁通过并归档，没有请求邮件处理 |
| `PREVIEW` | 已生成 EML，未发送 |
| `DELIVERY_PENDING` | 邮件被拒绝、接受待核验或发送结果不明 |
| `COMPLETED` | Gmail 邮件身份、收件人和附件核验通过，不代表已阅读 |

最近状态写入 `工作目录/pipeline-report.json`，每个输入也保存于 `runs/输入哈希/pipeline-report.json`。同一输入重跑更新其报告，不保存每次尝试的独立历史。报告包含 `visual_policy`、固定回归和当天 QA 报告路径。任何失败都不能进入邮件步骤。

归档位于 `archive/日期/版本/`，包含 HTML、原稿、manifest/READY、素材、全部 QA 及 `archive.json` 文件哈希。已有归档不可覆盖；同版本 HTML 或输入变化须递增版本，或在明确的隔离验证工作目录运行。工作目录锁串行化 pipeline，图片准备包逐文件校验后复用；下游重跑不重新下载。缓存损坏会停止；极端中断留下 prepared 但缺少 prepared.json 时，需检查后移走该输入的 runs 目录再重跑。

只有针对具体收件人和内容获得授权后才添加 `--send --token secrets/gmail-token.json`。CLI 默认共用原邮件组件的 `var/email.sqlite3`；自定义路径用 `--email-database`，必须与所有实际投递入口一致。更换或丢失记录无法保证跨运行去重。已核验邮件直接跳过；发送结果不明时先核验，pipeline 不自动补发。独立邮件 CLI 的既有补发行为保持兼容。

## 实测证据（2026-11-07）

新策略完整测试 97 项通过，日志为 `var/daily-visual-gate-tests.log`。新增 11 项策略测试覆盖基线输出保护、当天不比较基线、截图留存、失败规则阻断、固定样例任一失败阻断邮件，以及保留同一期比较。

真实试刊 2026-11-04、3.2 已按每日策略走到 `PREVIEW`，两份固定样例回归通过，当天七项规则通过，截图保留；没有发送真实邮件。成品 SHA-256 为 `827b73690eb21b5d7471d09bd5c869b2fd9507888cf1ab06e4f205c3c787670b`，运行目录为 `var/pipeline-daily-20261107/`。这表示用户批准的自动技术门禁通过，未进行该期人工审美验收。

历史资料仍在 `var/pipeline-postdeps-verification.json`、`var/pipeline-fontlocked-20261107/` 和 `outputs/visual-environment-20261107/`。其中旧的整期基线失败是策略改变前的记录；没有修改或追认旧生产基线。真实远端多文件交稿、真实 Gmail 投递、实际 iPad Safari 和 Gmail 附件预览不属于此次端到端测试范围。

汇总证据为 var/pipeline-daily-20261107/verification.json：归档中 26 个文件哈希一致、九张截图齐全、六张固定回归截图差异均为零，EML 附件与成品相同；两份正式基线及旧生产基线全部保持原样。
