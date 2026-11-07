# Daily Input · 每日信息输入

把完整九栏 Markdown 作者稿、期次信息和图片，制作成可离线阅读的单文件 HTML 日报。项目包含稳定 ID 入库、图片离线准备、出版、自动质量检查、可追溯归档与可选 Gmail 附件投递。

**当前范围：** Linux / WSL 上的本地出版与 pipeline 已实现；默认不发邮件。固定样例回归与每日规则检查是自动技术门禁，不是每期人工审美审阅。远端写作端无人值守交稿、真实 iPad Safari 与 Gmail 附件阅读体验仍须分别验收。

## 安装

从仓库根目录运行。支持 Python 3.10+，推荐使用已登记的 Python 3.12 conda 环境；入库与投递使用 Linux 的符号链接、原子改名和 flock，Windows 请在 WSL Linux 文件系统内运行。

```bash
# 按实际 conda 安装路径调整
source ~/miniconda3/etc/profile.d/conda.sh
conda env create -f environment.yml
conda activate daily-input
python -m playwright install chromium
# 新机器缺少浏览器系统库时，由管理员确认后执行（可能需要 sudo）
python -m playwright install-deps chromium
python -m pip check
python -m publisher.fonts verify
```

已有环境可用 `conda env update -f environment.yml` 更新，再执行校验。环境文件安装 QA 依赖及可选 OAuth 设置依赖；正式模板和固定样例从仓库读取，因此须保留完整 checkout，不能把当前 editable 安装当作可脱离仓库的 wheel。

### 字体与可复现环境

正文栈为 `"DejaVu Sans", "Maple Mono NF CN", sans-serif`。QA 校验 `config/fonts.lock.json` 中 22 个原版字体文件的 SHA-256，并比较字体锁、Playwright / Chromium、架构、fontconfig 和 FreeType 环境。版本名称相同不保证字节相同。

字体二进制不随仓库提供，也**不嵌入 HTML**。新机器应从官方来源取得锁定字体，再明确安装：

```bash
python -m publisher.fonts install --source-dir /path/to/MapleMonoNFCN --source-dir /path/to/dejavu
python -m publisher.fonts verify
```

来源、版本、安装位置与许可见[字体环境](docs/4%20自动质量检查/字体环境.md)和 `config/font-licenses/`。读者设备没有字体时会回退；固定 Linux Chromium 截图不保证其他设备同字形。依赖或字体环境变化应重新审阅，不能通过日常自动更新基线绕过检查。

## 最小运行示例

以下使用仓库中的构造示例，不是当天真实新闻；没有邮件参数，不联网发送：

```bash
conda activate daily-input
python -m publisher.pipeline   'docs/3 出版引擎/作者交稿示例/Daily_Input_2026-11-06.md'   --metadata 'docs/3 出版引擎/作者交稿示例/issue.json'   --workspace var/example-pipeline
```

成功状态为 `ARCHIVED`。查看 `var/example-pipeline/pipeline-report.json`，成品在 `var/example-pipeline/archive/2026-11-06/V3.2-stage3.1/`（实际版本以示例元数据及报告为准）。运行工作区应放在被忽略的 `var/`，不要把真实稿件、邮件或账户数据加入 Git。

## 作者交稿与图片

作者提交完整九栏 Markdown、同目录 `assets/` 和 `issue.json`。元数据包含日期、非空版本及栏目主题/提要；作者不维护 manifest 或 READY。入库分配并保存稳定 ID，生成不可变修订和完整性哈希。改稿必须递增版本，并保留既有对象标记。

- [原稿规范](docs/1.5%20Markdown%20原稿规范/Markdown_Content_Spec_V1.0.md)
- [完整示例](docs/3%20出版引擎/作者交稿示例/Daily_Input_2026-11-06.md)与[期次元数据](docs/3%20出版引擎/作者交稿示例/issue.json)
- [入库、修订、摘录与旧链接](docs/3%20出版引擎/阶段三运行说明.md)

本地 PNG/JPEG/WebP 可直接交付。远程图片须是编辑选定的直链，并在 `remote_images` 提供出处、作者、作品、许可、核验日期与 `allow_offline: true`。程序不从任意网页自动选图，不绕过登录，不自行证明版权许可。没有离线刊用许可时仅保留文字与原网页链接。

图片准备阶段校验所有许可记录后下载，拒绝内网目标、危险重定向、动画、错误格式和超限文件，记录来源与哈希；出版时内嵌图片。默认每张 8 MiB、本期 40 MiB、最多 30 张、单图 2500 万像素；成品另有 12 MiB 限制。详情见[图片离线获取](docs/1.6%20媒体内容协议/图片离线获取.md)。

## Pipeline 与质量门禁

顺序：图片准备 → 原子入库 → HTML 出版 → 固定样例回归 → 当天规则检查 → 原子归档 → 可选邮件预览/投递。

默认 `--visual-policy daily`：

1. 用当前模板重建固定九栏与长文样例，核对稿件身份、环境与三视口批准基线。
2. 当天稿件检查内容、结构、媒体、链接、HTML、离线包及真实 Chromium 浏览器规则；保存 390×844、820×1180、1180×820 三张整页截图。
3. 任一固定回归或当天规则失败，停止归档与邮件处理。当天不同内容不与昨日整页比较，不创建当天基线。

报告明确保留 `human_visual_review=not_performed`。当天整页比较为 `not_applicable`，其余七项须通过；这不代表固定样例比较可跳过，也不保证规则能发现所有视觉问题。

需要对同一期已审阅 HTML 比较时，显式使用 `--visual-policy same-issue --baseline-dir PATH`。只有明确审阅后才可使用 `--update-baseline`，不得常驻每日任务。独立 QA 默认仍为同一期比较；只运行当天规则用 `--rules-only`。

归档保留原稿、素材、HTML、全部 QA、九张截图及文件哈希；同版本归档不可覆盖。相同输入重跑复用校验后的图片包。详细命令、错误恢复、状态与限制见[Pipeline 运行说明](docs/Pipeline运行说明.md)。

### GitHub 固定提交交稿

接收端支持 `--github-repo https://github.com/OWNER/REPO --commit FULL_40_CHARACTER_COMMIT_SHA`；作者稿路径和元数据路径相对于该快照。稿件、元数据和本地图片必须在同一提交。仅只读获取指定 SHA，不使用浮动分支、不修改远端、不执行交稿代码。私有仓库使用本机 Git 凭据，不把 token 写入命令。

此功能不创建写作定时任务，也不证明真实远端多文件无人值守交稿已经成功。

## Gmail：先预览，再明确发送

复制 `config/email.example.json` 为 `config/email.json`，填写自己的发件人及 To/Cc/Bcc 名单。给 pipeline 增加 `--email-config config/email.json` 只产生 EML 预览，状态为 `PREVIEW`；预览不读取 Gmail token，也不发邮件。

首次 OAuth 设置须由账号持有人明确授权，在 Google Cloud 启用 Gmail API 并准备桌面客户端文件。授权命令是：

```bash
python -m publisher.delivery --authorize secrets/gmail-client.json
```

申请 `gmail.send` 和 `gmail.readonly`，后者用于取回已发送邮件及附件核验。客户端、token、真实名单、EML 和发送数据库均不得提交 Git。只有具体收件人和内容获得发送授权后，才在 pipeline 添加 `--send --token secrets/gmail-token.json`。

所有实际投递入口应共用持久的 `var/email.sqlite3`，更换或删除记录会丢失去重保护。pipeline 对结果不明的邮件只核验，不自动补发；独立邮件 CLI 有最多两次尝试的既有恢复逻辑，两者不可混同。只有 `SENT_VERIFIED` 对应 pipeline 的 `COMPLETED`，表示 Gmail 已发送记录与附件核验通过，不代表收件箱送达或已阅读。详见[Gmail 投递说明](docs/5%20邮件投递/Gmail_Delivery_Spec_V1.0.md)。

## 验证与边界

```bash
python -m pip check
python -m publisher.fonts verify
python -m unittest discover -s tests -v
```

自动测试覆盖入库/ID、出版、图片安全、字体锁、QA、每日策略、归档及模拟 Gmail 恢复；真实浏览器回归通过最小 pipeline 示例运行。固定样例及批准基线在 `outputs/stage4/{nine,long}/`，是运行依赖，须保留。

2026-11-07 最终复验：依赖与字体校验通过，97 项测试通过；README 示例以真实 Chromium 完成固定回归、当天规则与本地归档（ARCHIVED），未发送邮件。此前真实试刊按每日策略到达 EML 预览，固定回归与当天规则通过。历史邮件模块曾单独记录一封样刊的真实 `SENT_VERIFIED`，不等于当前每日 pipeline 已验证真实发送。此次每日 pipeline 验收未发送邮件；真实远端交稿、真实 iPad Safari 和 Gmail 附件阅读体验未包含在验收中。

## 目录与发布范围

- `src/publisher/`：入库、图片、出版、QA、pipeline、邮件组件。
- `templates/`：HTML 模板及 CSS。
- `tests/`：自动测试。
- `config/`：公开样例、字体锁和第三方字体许可。
- `docs/`：格式规范、设计决策及分阶段历史记录；当前流程以本 README 和 Pipeline 说明为准。
- `outputs/stage4/`：固定公开样例、基线和检查记录。
- `var/`、`outputs/production/`、实验输出：本机工作数据，不作为发布源码。

本仓库未声明统一项目开源许可证；不要将第三方字体许可证理解为对全部源码、文章或图片的授权。样例图的来源和许可随原稿/manifest 保存，使用新图片仍需逐项核验。
