# Daily Input · Digital Publication System
## 全流程调研与数字资产制作待办清单

- **文档版本：** Project Plan V1.0
- **整理日期：** 2026-11-06
- **项目目标：** 建立低摩擦、可持续、尽可能无人值守的数字日报生产与交付系统。
- **主要交付物：** 每日独立 HTML 文件，通过 Gmail 附件交付。
- **内容规范：**《每日信息输入》V3.2
- **视觉主题：** Lilac Moon（月辉紫白）V1.0

---

## 1. 项目目标与总体原则

本项目不是简单地把日报从 PDF 转换成 HTML，而是建立一条完整的数字出版流水线：

1. LLM 负责研究、资料筛选、分析和 Markdown 正文生产。
2. Markdown 是内容源文件，便于编辑、保存、归档和重新出版。
3. 云端出版引擎负责解析 Markdown、处理媒体资源、套用固定 HTML/CSS 模板并生成最终文件。
4. 自动质检负责验证内容、结构、媒体、链接和实际浏览器显示。
5. Gmail 负责将最终 HTML 作为附件发送。
6. 正常情况下，用户无需每天打开电脑、运行命令、复制内容或手动发送邮件。

### 1.1 核心原则

- **低摩擦优先：** 每天无需用户参与生产过程；异常时主动通知。
- **职责分离：** LLM 不负责最终排版；出版引擎不负责写作判断。
- **内容与呈现分离：** Markdown 保存内容，HTML/CSS 负责呈现。
- **确定性优先：** 能由普通程序稳定完成的工作，不交给 LLM 临场决定。
- **可恢复：** 每个阶段有独立状态、日志和重试策略。
- **不重复生产：** 同一日期、版本和输入不能产生重复成品或重复邮件。
- **不静默失败：** 未通过质量检查的日报不得冒充成功成品。
- **可迁移：** 将来更换 LLM 供应商，不应迫使出版引擎和内容协议推倒重来。

---

## 2. 已确认的产品决策

以下为讨论中已经明确的产品方向。

| 项目 | 已确认决策 |
|---|---|
| 主要阅读介质 | HTML |
| 每期交付形式 | 每日一个独立 HTML 文件 |
| 阅读方式 | 连续纵向滚动，不采用固定分页 |
| 文件形态 | 第一版优先单文件 HTML，必要资源内嵌 |
| 交付方式 | Gmail HTML 附件 |
| 内容源 | Markdown |
| 生产位置 | 云端运行 |
| 内容生产 | OpenAI API 路线作为目标方案 |
| 出版程序 | Python |
| 模板技术 | HTML + CSS |
| 浏览器质量检查 | Playwright 等真实浏览器自动化工具 |
| 交互功能 | 第一版静态化，除超链接外不增加交互 |
| 暂缓功能 | 抽屉目录、字体大小调整、阅读进度、收藏、笔记、跨设备同步 |
| 主要阅读设备 | 11 英寸 iPad |
| 主题 | Lilac Moon（月辉紫白）V1.0 |
| PDF | 保留为可选归档/打印输出，不再作为主要阅读介质 |

### 2.1 明确暂缓的功能

第一版不开发：
- 折叠式抽屉目录。
- 字体大小调整控件。
- 阅读进度记忆。
- 收藏、笔记和批注。
- 站点后台、用户账号和数据库。
- PWA 安装体验。
- 跨设备阅读状态同步。
- 复杂动画或交互式图表（除非具体内容确实需要且能离线工作）。

第一版允许的交互仅包括正常超链接、文章锚点和浏览器自身提供的功能。

---

## 3. 总体技术架构

### 3.1 目标架构

```text
Cloud Scheduler
      |
      v
Cloud Run Job / Python Orchestrator
      |
      +--> OpenAI API
      |      +--> 研究与资料检索
      |      +--> 选题与历史去重
      |      +--> Markdown 正文生成
      |      +--> 内容修订与核验
      |
      v
Markdown + manifest + media assets
      |
      v
Content Validation
      |
      v
Python Publication Engine
      +--> Markdown Parser
      +--> HTML Template / CSS
      +--> Image Processor
      +--> Link / Anchor Generator
      +--> Single-file Packager
      |
      v
Browser QA (Playwright)
      |
      v
Cloud Storage (staging + archive)
      |
      v
Gmail API
      |
      v
HTML Attachment
      |
      v
User's Gmail / iPad
```

### 3.2 组件职责

| 组件 | 主要职责 | 不负责的事情 |
|---|---|---|
| OpenAI API | 研究、分析、写作、内容修订 | HTML 最终排版、邮件发送 |
| Markdown 协议 | 约束内容结构与媒体引用 | 视觉样式 |
| Python Orchestrator | 管理任务状态、调用各阶段 | 代替 LLM 写作 |
| Publication Engine | Markdown 转 HTML、模板渲染、资源封装 | 改写文章内容 |
| QA Engine | 检查内容、结构、视觉与技术质量 | 静默修复事实错误 |
| Cloud Storage | 暂存、归档输入与成品 | 作为用户主要阅读界面 |
| Gmail API | 发送附件、提供邮件 ID | 生成或修改日报 |
| Cloud Scheduler | 定时触发任务 | 判断文章质量 |

### 3.3 推荐技术组件（待验证后冻结）

| 技术 | 用途 |
|---|---|
| Python | 编排与出版主程序 |
| OpenAI Responses API | LLM 内容生产与工具调用 |
| Markdown-it-py | Markdown 解析 |
| Jinja2 | HTML 模板渲染 |
| Pillow | 图片压缩、缩放和格式处理 |
| JSON Schema | manifest 数据校验 |
| Beautiful Soup | HTML 结构检查 |
| Playwright | 浏览器渲染与视觉检查 |
| Google Cloud Scheduler | 定时触发 |
| Google Cloud Run Jobs | 云端执行 |
| Google Cloud Storage | 文件暂存与归档 |
| Google Secret Manager | 保存 API Key、OAuth 凭证 |
| Gmail API | 邮件发送与发送状态核验 |

以上为候选技术方案，正式采用前需要核对当前官方文档、服务区域、价格、配额和运行限制。

---

## 4. 分阶段待办清单

状态定义：
- `未开始`：尚未着手。
- `进行中`：正在调研或制作。
- `待验证`：已有初步成果，需要实际测试。
- `已完成`：通过验收并保存正式数字资产。
- `暂缓`：明确不进入第一版。

### 阶段 0：项目基线与范围冻结

**目标：** 固定产品方向，避免后续重复讨论已确定事项。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 0.1 | 确认 HTML 为主要阅读介质，PDF 为可选输出 | 产品基线 | 已完成 |
| 0.2 | 确认每日独立 HTML 文件 | 归档规则 | 已完成 |
| 0.3 | 确认第一版静态化，仅保留链接 | 交互范围说明 | 已完成 |
| 0.4 | 确认 Markdown 为正文源 | 内容源规则 | 已完成 |
| 0.5 | 确认 LLM 与出版引擎分离 | 架构原则 | 已完成 |
| 0.6 | 确认云端出版与 Gmail 附件交付 | 总体架构方向 | 已完成 |

**阶段验收：** 产品范围、交付形式和职责边界不再存在歧义。

### 阶段 1：出版产品与内容规范

**目标：** 确定 HTML 正文的阅读质感和机器可处理的内容协议。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 1.1 | HTML 正文灰度对照实验 | [中英文定稿及试读记录](<1.1 正文灰度/试读记录.md>) | 已验收 |
| 1.2 | 确定正文段落组织方式 | [04 定稿模板](<1.2 正文段落/04-仅首段缩进.html>)（按语意分段，每节首段首行缩进两字，后续段落齐头） | 已验收（04 版） |
| 1.3 | 确定 iPad 竖屏版心、字号、行距 | [竖屏阅读参数与定稿样张](<1.3 iPad 竖屏阅读参数/竖屏阅读参数.md>) | 已验收（20 px，1.55 倍行高） |
| 1.4 | 确定栏目、文章、弱标题的语义层级 | [内容层级规范与试读样张](<1.4 内容层级/内容层级规范.md>) | 已验收 |
| 1.5 | 制定 Markdown 内容规范 | [Markdown 原稿规范定稿](<1.5 Markdown 原稿规范/Markdown_Content_Spec_V1.0.md>) | 已验收 |
| 1.6 | 定义图片、图注、表格、代码、引用和来源标记 | [媒体内容协议与定稿样张](<1.6 媒体内容协议/Media_Asset_Spec_V1.0.md>) | 已验收 |
| 1.7 | 确定文章 ID、栏目 ID 和锚点命名规则 | [ID 与锚点规范定稿](<1.7 ID 与锚点/ID_Anchor_Spec_V1.0.md>) | 已验收 |

#### 1.1 正文灰度实验原计划

实际试读先比较行距，再调整页边距。用户已选定「A 版 · BBC 宽边距」。下表保留原计划以供追溯，验收结果见上方试读记录。

使用同一篇约 1500–2000 字的真实技术文章，制作三种对照：

| 变量 | A：传统印刷式 | B：网页长文式 | C：混合式 |
|---|---|---|---|
| 首行缩进 | 两个汉字 | 无 | 两个汉字 |
| 段间距 | 极小 | 约 0.8–1em | 当前值约一半 |
| 正文对齐 | 两端对齐 | 左对齐 | 左对齐 |
| 字体字号 | 相同 | 相同 | 相同 |
| 行距 | 相同 | 相同 | 相同 |
| 文章结构 | 相同 | 相同 | 相同 |

**验收：** 在 iPad 竖屏模拟环境中比较连续阅读体验、文字灰度、段落边界和阅读疲劳。此阶段不预设最终胜者。

### 阶段 2：HTML 设计系统与模板资产

**目标：** 制作可被出版引擎重复调用的正式模板。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 2.1 | Lilac Moon HTML 色彩映射 | [共用样式](../templates/css/theme.css) / [色彩规范与校样](<2.1 HTML 色彩映射/HTML_色彩映射.md>) | 已验收 |
| 2.2 | 字体与字体回退方案 | [Typography Spec](<2 HTML 设计系统/HTML_Design_System_V1.0.md>) | 已完成 |
| 2.3 | 刊头与首页结构 | [整期刊头、分层目录与版式](../templates/daily.html.j2) | 已通过 iPad 试读 |
| 2.4 | 栏目与独立文章样式 | [Section / Article Components](<../templates/components/content.html.j2>) | 已完成 |
| 2.5 | 正文、弱标题、引文、证据组件 | [Content Components](<../templates/components/content.html.j2>) | 已完成 |
| 2.6 | 图片、图注、表格、代码组件 | [Media Components](<../templates/components/media.html.j2>) | 已完成 |
| 2.7 | iPad 竖屏与横屏响应式布局 | [Responsive CSS](<../templates/css/publication.css>) | 已完成 |
| 2.8 | 单文件 HTML 封装方案 | [HTML Packaging Spec](<2 HTML 设计系统/HTML_Design_System_V1.0.md>) | 已完成 |
| 2.9 | 完整示范日报 | [V3.2 历史整期试排](<2 HTML 设计系统/正式日报试排_2026-10-31.html>) | 已通过 iPad 试读 |

#### 设计约束

- 继承 Lilac Moon 六色语义，不擅自改变核心色职责。
- 允许受控衍生紫色。
- 保留栏目、文章、文内弱标题三级结构。
- 文章之间的视觉区分必须明显强于文内弱标题。
- 第一版不加入抽屉目录、字体调整和其他交互控件。
- 不把 PDF 的固定分页和 A4 页面宽度直接搬到 HTML。
- 正文灰度参数以阶段 1 实验结果为准。

### 阶段 3：Python 出版引擎

**目标：** 将 Markdown 和媒体资源稳定转换为单文件 HTML。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 3.1 | Python 工程结构 | 可维护的项目目录 | 已有本地入口，待接入入库程序 |
| 3.2 | Markdown 解析 | Markdown Parser | 已有解析原型，待协议回归 |
| 3.3 | JSON manifest 解析 | Manifest Parser | 已能读取，待入库生成与版本维护 |
| 3.4 | HTML 模板渲染 | HTML Renderer | 已生成含图样刊，待九栏回归 |
| 3.5 | 图片压缩、缩放和内嵌 | Asset Processor | JPEG 已联调，待多格式验收 |
| 3.6 | 目录、文章 ID 和锚点生成 | Navigation / Anchor Generator | 已有目录与锚点检查，待稳定 ID 入库 |
| 3.7 | 外链、引用与来源处理 | Link Processor | 已有基础处理，待摘录与旧链接验收 |
| 3.8 | CSS、脚本和资源内嵌 | Single-file Packager | 已有单文件样刊，待多图离线验收 |
| 3.9 | 重复执行与错误恢复 | Idempotent Renderer | 成品重跑已验证，待入库并发与恢复 |
| 3.10 | 文件体积控制 | Size Optimizer | 已有体积上限，待多图质量与体积验收 |

#### 建议文件交接结构

```text
inbox/
└── 2026-11-06/
    ├── Daily_Input_2026-11-06.md
    ├── manifest.json
    ├── READY
    └── assets/
        ├── image_01.jpg
        └── image_02.png
```

- Markdown 是正文源文件。
- manifest 保存日期、内容版本、栏目、文章 ID 和媒体清单。
- assets 保存需要嵌入的图片。
- `READY` 只在全部文件写入完成后创建。
- 出版引擎只处理具备有效 READY 标记的任务。
- 原稿不得被渲染程序直接修改。

### 阶段 4：自动质量检查

**目标：** 生成成功不等于出版成功，必须进行实际浏览器检查。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 4.1 | 内容完整性检查 | Content Validator | 未开始 |
| 4.2 | 标题层级与锚点检查 | Structure Validator | 未开始 |
| 4.3 | 图片和媒体完整性检查 | Asset Validator | 未开始 |
| 4.4 | 外部链接格式检查 | Link Validator | 未开始 |
| 4.5 | HTML 语法和结构检查 | HTML Validator | 未开始 |
| 4.6 | 浏览器实际渲染 | Playwright QA | 未开始 |
| 4.7 | 截图与视觉回归检查 | Visual QA Baseline | 未开始 |
| 4.8 | 文件体积和离线打开检查 | Package Validator | 未开始 |

#### 测试样例

- 短文章。
- 连续五页长度的长文章。
- 多个文内弱标题。
- 多张图片及图注。
- 长表格。
- 代码块。
- 中英文混排和数学公式。
- 缺失图片、错误链接、空内容和截断输入。

#### 重点验收设备

1. iPad Safari 竖屏。
2. iPad Safari 横屏。
3. Windows Chrome。

**验收：** 正文、媒体和样式离线完整；目录锚点和外链有效；无严重溢出、裁切、乱码或结构错误。

### 阶段 5：云端基础设施

**目标：** 建立不依赖本地电脑持续运行的生产环境。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 5.1 | 核实 Pro API Credits 资格、激活状态和余额 | API Credit Record | 待验证 |
| 5.2 | 比较模型能力与价格 | Model Routing Spec | 未开始 |
| 5.3 | 调研 API 研究、搜索和来源核验能力 | Research Pipeline Spec | 未开始 |
| 5.4 | 建立 Google Cloud 项目及计费设置 | Cloud Project | 未开始 |
| 5.5 | 配置 Cloud Run Jobs | Cloud Execution Environment | 未开始 |
| 5.6 | 配置 Cloud Scheduler | Schedule Configuration | 未开始 |
| 5.7 | 配置 Cloud Storage | Staging / Archive Bucket | 未开始 |
| 5.8 | 配置 Secret Manager | Secret Configuration | 未开始 |
| 5.9 | 配置 Cloud Logging | Logging / Monitoring | 未开始 |

#### 重要调研事项

不能假设 OpenAI API 自动拥有 ChatGPT 网页端相同的搜索和研究能力。必须验证：

- 可用模型与工具。
- Responses API 的搜索能力和调用方式。
- 如何保留来源 URL、标题和引用信息。
- 搜索失败与来源失效的处理。
- 如何控制研究深度和 Token 成本。
- 是否需要单独的搜索供应商。

#### API 费用原则

- 优先使用已激活且符合资格的 API Credits。
- 不默认启用无限额自动扣费。
- 每期统计输入、输出 Token 和工具调用费用。
- 为单期与月度成本设置预算阈值。
- 额度耗尽时暂停生产并通知，不自动扩大支出。

### 阶段 6：Gmail 自动投递

**目标：** 将 HTML 作为普通附件发送，并确认 iPad 实际打开体验。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 6.1 | 小型 HTML 附件发送测试 | Gmail Test | 未开始 |
| 6.2 | 带图片、CSS、链接的完整 HTML 测试 | Attachment Compatibility Report | 未开始 |
| 6.3 | Google Cloud OAuth 项目配置 | OAuth Client | 未开始 |
| 6.4 | `gmail.send` 最小权限授权 | OAuth Authorization | 未开始 |
| 6.5 | Python Gmail Sender | Gmail Sender Module | 未开始 |
| 6.6 | 附件大小、文件名与 MIME 规则 | Delivery Spec | 未开始 |
| 6.7 | Message ID 与已发送记录核验 | Delivery Verification | 未开始 |
| 6.8 | iPad Gmail 保存与 Safari 打开测试 | iPad Compatibility Report | 未开始 |

#### Gmail 技术方向

优先采用 Gmail API + Python，不采用浏览器 UI 自动点击作为正式发送方式。

建议附件参数：

```text
Filename:
Daily_Input_YYYY-MM-DD_V3.2.html

MIME Type:
text/html

Content-Disposition:
attachment
```

建议只申请：

```text
https://www.googleapis.com/auth/gmail.send
```

#### OAuth 风险

- 不将 OAuth 应用长期停留在 Testing 状态。
- 核实个人使用场景下的发布与验证要求。
- Refresh Token 放入 Secret Manager。
- 不将凭证写入代码仓库、Markdown、HTML 或日志。

#### 邮件发送状态

| 状态 | 意义 |
|---|---|
| `SEND_ACCEPTED` | Gmail API 接受发送请求并返回 Message ID |
| `SENT_VERIFIED` | 在 Gmail 已发送邮件中确认对应邮件和附件 |
| `DELIVERY_CONFIRMED` | 获得可靠投递证据；普通 Gmail API 本身不保证此状态 |

#### 重复发送保护

- 使用日期、内容版本和收件人生成唯一任务键。
- 保存 HTML SHA-256。
- 发送前检查当天是否已有相同邮件。
- 发送结果不明确时，先查询已发送记录，再决定是否重试。
- 不因网络超时直接盲目重发。

#### iPad 交付验收

必须实际验证：

1. Gmail 收到 HTML 附件。
2. 可以保存到 Files。
3. 可以使用 Safari 或兼容方式打开。
4. 内嵌图片正常显示。
5. CSS 样式完整。
6. 锚点和外部链接有效。
7. 离线打开时正文和内嵌媒体仍可用。

### 阶段 7：自动化编排系统

**目标：** 将内容生产、出版、质检、归档和邮件发送连接成可恢复流水线。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 7.1 | 设计生产状态机 | Pipeline State Machine | 未开始 |
| 7.2 | 每日任务唯一 ID | Job ID Spec | 未开始 |
| 7.3 | Markdown 交接协议 | Content Handoff Protocol | 未开始 |
| 7.4 | READY 文件协议 | READY Protocol | 未开始 |
| 7.5 | 各阶段独立重试 | Retry System | 未开始 |
| 7.6 | 防止重复生产和发送 | Idempotency System | 未开始 |
| 7.7 | Token 与费用统计 | Cost Monitor | 未开始 |
| 7.8 | 失败通知 | Alert System | 未开始 |
| 7.9 | 自动归档 | Archive Manager | 未开始 |
| 7.10 | 全流程日志与恢复 | Recovery Runbook | 未开始 |

#### 建议状态机

```text
SCHEDULED
    ↓
RESEARCH
    ↓
WRITING
    ↓
CONTENT_VALIDATION
    ↓
PUBLISH
    ↓
HTML_QA
    ↓
ARCHIVE
    ↓
GMAIL_SEND
    ↓
DELIVERY_VERIFY
    ↓
COMPLETE
```

每个阶段保存独立状态、输入引用、输出文件、开始/结束时间、错误信息和重试次数。

失败恢复原则：

- 写作失败：只重试内容生产阶段。
- HTML 渲染失败：不重新调用 LLM。
- 浏览器质检失败：保留失败样本，不覆盖上一个有效成品。
- Gmail 发送失败：不重新生成正文和 HTML。
- 发送状态不明确：先核验 Gmail，再决定是否重试。

### 阶段 8：全流程验收与上线

**目标：** 证明系统能持续、无人值守地运行。

| ID | 任务 | 交付成果 | 状态 |
|---|---|---|---|
| 8.1 | 单次全流程测试 | End-to-end Test Report | 未开始 |
| 8.2 | 网络/API/媒体异常模拟 | Failure Test Report | 未开始 |
| 8.3 | 重复运行测试 | Idempotency Test Report | 未开始 |
| 8.4 | 连续运行至少 7 天 | 7-day Stability Report | 未开始 |
| 8.5 | iPad 实际阅读测试 | Reading Compatibility Report | 未开始 |
| 8.6 | 成本复盘 | Cost Report | 未开始 |
| 8.7 | 迁移旧 Scheduled Task | Migration Record | 未开始 |
| 8.8 | 部署和恢复文档 | Operations Manual | 未开始 |

### 正式上线验收标准

- 每天按计划自动启动。
- 正文由 LLM 完成，内容符合 V3.2。
- HTML 由固定出版引擎生成。
- 通过结构和浏览器质检。
- Markdown 原稿与 HTML 成品均归档。
- Gmail 自动发送 HTML 附件。
- 已发送状态得到核验。
- iPad 能够正常保存和打开。
- 正常情况下用户零操作。
- 失败时主动通知，且不会发送不完整文件。
- 连续运行至少 7 天无未处理的严重故障。

---

## 5. 数字资产清单

所有正式资产应保存版本号、用途、依赖和验收记录。

### 5.1 规范文件

| 文件 | 用途 | 状态 |
|---|---|---|
| `Product_Spec_V1.0.md` | 产品定位与交互范围 | 待制作 |
| `Markdown_Content_Spec_V1.0.md` | LLM 内容交接规范 | 已验收 |
| `HTML_Design_System_V1.0.md` | HTML 视觉规范 | 已完成 |
| `Media_Asset_Spec_V1.0.md` | 图片、图表和媒体规范 | 已验收 |
| `Automation_Architecture_V1.0.md` | 全流程架构 | 待制作 |
| `Gmail_Delivery_Spec_V1.0.md` | 邮件投递规范 | 待制作 |
| `Operations_Runbook_V1.0.md` | 运维与故障恢复 | 待制作 |

### 5.2 设计资产

- HTML 主模板。
- Lilac Moon HTML CSS。
- 字体与回退配置。
- 栏目和文章组件。
- 弱标题和正文组件。
- 图片与图注组件。
- 表格、代码、引用和证据组件。
- 完整 HTML 示例日报。
- 浏览器截图基准。

### 5.3 软件资产

- Python Renderer。
- Markdown Parser。
- Manifest Validator。
- Asset Processor。
- HTML Packager。
- HTML Validator。
- Playwright QA。
- Gmail Sender。
- Pipeline Orchestrator。
- Cost Monitor。
- Archive Manager。

### 5.4 测试资产

- 标准 Markdown 测试样例。
- 长文与短文样例。
- 多级标题样例。
- 图片与图注样例。
- 长表格样例。
- 代码与数学公式样例。
- 错误链接和缺失图片样例。
- 浏览器截图基准。
- 全流程测试报告。

### 5.5 部署资产

- Cloud Run Job 配置。
- Cloud Scheduler 配置。
- Cloud Storage 配置。
- IAM 权限说明。
- Secret Manager 配置说明。
- 环境变量模板（不含真实密钥）。
- OAuth 授权说明。
- 故障恢复手册。

---

## 6. 关键风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| API Credits 未激活或额度不足 | 内容生产停止 | 先核实资格与余额，设置预算阈值 |
| API 研究能力与 ChatGPT 不一致 | 内容质量下降 | 独立验证搜索工具和来源流程 |
| OAuth Refresh Token 失效 | Gmail 无法发送 | 正确配置 OAuth 发布状态，监控授权 |
| HTML 附件在 iPad 打开不便 | 最终阅读路径失败 | 提前进行真实设备测试 |
| 单文件图片过大 | 邮件附件超限 | 图片压缩、尺寸控制、体积检查 |
| 云端任务超时 | 日报未完成 | 分阶段执行、独立重试 |
| 重试造成重复邮件 | 用户收到重复日报 | 幂等键、Message ID、已发送核验 |
| 浏览器与本地预览不一致 | 视觉缺陷 | Playwright 真实浏览器验收 |
| 模板修改造成历史样式漂移 | 归档一致性受损 | 模板版本记录，历史成品不自动重写 |
| 云端服务成本不可控 | 产生额外费用 | API 与云资源分别设置预算和告警 |

---

## 7. 项目执行规则

1. 一次只推进一个主要待办事项。
2. 调研任务先输出资料来源、事实、推断和未确定事项。
3. 设计任务必须形成可复用的规范文件。
4. 制作任务必须产生真实文件或可运行程序。
5. 验证任务必须有实际测试记录。
6. 用户确认后才冻结版本。
7. 不因后续技术实现方便而擅自改变已经确认的产品决策。
8. 不修改现有《每日信息输入》Scheduled Task，直到新系统通过全流程验收。
9. 不把 API Credits、Google Cloud 免费额度或任何服务配额视为永久、无条件可用。
10. 所有真实凭证必须保存在安全凭证管理系统中，不进入普通项目资产。

---

## 8. 当前执行队列

建议按以下顺序推进：

| 顺序 | 任务 | 目的 | 状态 |
|---|---|---|---|
| 1 | HTML 正文灰度实验 | 确定主要阅读体验 | 已验收 |
| 2 | 正文段落组织（1.2） | 确定按语意分段和中文节首缩进规则 | 已验收（04 版） |
| 3 | iPad 竖屏阅读参数（1.3） | 验证版心、字号与行高 | 已验收（20 px，1.55 倍行高） |
| 4 | 栏目、文章与文内弱标题（1.4） | 验证三种内容边界 | 已验收 |
| 5 | Markdown 内容协议 | 定义 LLM 与出版引擎接口 | 已验收 |
| 6 | 媒体内容协议（1.6） | 确认材料说明和出处的归属 | 已验收 |
| 7 | 栏目、文章 ID 与锚点（1.7） | 保持跨修订定位稳定 | 已验收 |
| 8 | Gmail HTML 附件兼容性测试 | 提前验证最终交付路径 | iOS Gmail 附件预览已实测，其余待验 |
| 9 | HTML 设计系统 | 制作正式视觉资产 | 已完成，整期版式已通过 iPad 试读 |
| 10 | Python 出版引擎 | 建立确定性出版能力 | 进行中（含图样刊已跑通，待入库） |
| 11 | 自动质量检查 | 保证交付质量 | 未开始 |
| 12 | OpenAI API 与云端基础设施 | 建立无人值守内容生产环境 | 未开始 |
| 13 | Gmail API 与 OAuth | 实现邮件自动发送 | 未开始 |
| 14 | 自动化编排 | 连接全流程 | 未开始 |
| 15 | 连续运行验收 | 验证稳定性 | 未开始 |

Gmail 附件测试可以与 HTML 正文实验并行进行，因为它不依赖最终正文样式。

### 下一项

**阶段三：入库程序接入出版原型**。见 [Codex Build 交接](<3 出版引擎/Codex_Build_交接.md>)。[游戏与每日好图的素材来源调研](<2 HTML 设计系统/图片来源调研.md>)已完成，随期图片处理由 3.5 接入。

### 最近完成

**阶段 2 HTML 设计系统与整期模板**已完成。用户于 2026-11-06 在 iPad 试读并接受 10 月 31 日整期版式。[设计规范、示范日报与浏览器核验记录](<2 HTML 设计系统/HTML_Design_System_V1.0.md>)

---

## 9. 项目完成定义

当以下条件全部满足时，项目才视为正式完成：

- [ ] HTML 产品规范与正文视觉基准定稿。
- [x] Markdown 内容协议定稿。
- [x] HTML/CSS 设计系统完成。
- [ ] Python 出版引擎可稳定运行。
- [ ] 图片和媒体资源可以完整内嵌。
- [ ] 自动质量检查通过。
- [ ] 云端生产环境部署完成。
- [ ] OpenAI API 内容生产稳定。
- [ ] Gmail OAuth 与附件发送通过。
- [x] iPad 实际打开和阅读通过（10 月 31 日整期试排）。
- [ ] 自动化状态机、重试和幂等机制通过。
- [ ] 连续 7 天无人值守测试通过。
- [ ] 运维和恢复文档完成。
- [ ] 旧日报任务完成迁移或明确保留策略。

**最终验收目标：每天无需用户参与生产，Gmail 自动收到一份完整、可离线阅读的 HTML 数字刊物。**
