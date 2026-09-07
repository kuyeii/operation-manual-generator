# 操作手册生成器技术规范

> 本文第1–8节描述原操作手册流程。新增的软著模式见第9节；其正式PDF、批次和确认机制独立于旧手册入口。

## 1. 目标与边界

本项目是单机、单用户的 Web 工具。用户上传 Web 项目源码 ZIP，系统在本地安全解压并分析项目，生成可审阅的启动方案和功能清单；用户确认后，系统启动被测项目，使用 Playwright Chromium 和 OpenAI 兼容模型逐步探索页面、记录操作并截图，最后输出中文 DOCX 用户操作手册。

首版正式支持 Docker Compose、Dockerfile、常见 Python Web 项目和 Node Web 项目。无法自动识别的项目允许用户填写参数化命令。不支持原生桌面应用，也不承诺自动处理 CAPTCHA、MFA、浏览器安全警告或生产系统副作用。

## 2. 系统架构

- 后端：Python 3.12、FastAPI、Pydantic、SQLAlchemy、SQLite。
- 前端：React、TypeScript、Vite、React Router、TanStack Query、Lucide Icons。
- 浏览器：Python Playwright，仅使用 Chromium，视口固定为 1440×900。
- 模型：OpenAI 兼容接口，支持 Chat Completions 与 Responses 两种协议；支持视觉输入，失败时自动降级为 DOM 模式。
- 文档：python-docx 生成 DOCX；LibreOffice 和 Poppler 仅在后台将其临时渲染为逐页 PNG 做版面检查，临时 PDF 不作为产物保留。
- 调度：进程内单任务执行器，同一时刻最多运行一个探索任务。

运行数据位于 `data/tasks/<task_id>/`：

```text
source.zip                 原始上传包
workspace/                 安全解压后的源码
runtime/                   被测项目独立环境和运行文件
screenshots/               Playwright 原始截图
reports/                   DOCX及内部逐页渲染结果
task.log                   过滤敏感信息后的任务日志
```

## 3. 安全模型

### 3.1 ZIP

- 上传大小默认上限 1 GiB，解压后上限 5 GiB，文件数上限 50,000。
- 忽略任意层级的 `node_modules`、Python 虚拟环境、版本库和缓存目录；其他内容拒绝绝对路径、`..` 路径穿越、NUL 字符、大小写或规范化冲突路径、文件与目录冲突、设备文件和符号链接。
- 解压前检查全部 central directory 条目，校验通过后才写入任务目录。

### 3.2 命令与进程

- 启动方案中的安装与启动命令均为字符串数组，不通过 shell 解释。
- 用户必须确认工作目录、命令、端口、访问地址和环境变量后才可运行。
- 只终止本任务创建的进程组；Compose 使用唯一 project name。
- Compose/Dockerfile 优先，其次 Python、Node；无法识别时使用用户提供的命令。
- 被测 Python 环境位于任务 `runtime/.venv`，Node 依赖位于被测项目自身目录，不污染工具 `.venv`。

### 3.3 凭据与模型

- 模型是项目级能力，从 `LLM_API_KEY`、`LLM_BASE_URL`、`MODEL` 和 `LLM_PROTOCOL` 读取，不接受任务级覆盖，不写入数据库和日志。
- 测试账号仅保存在进程内存。数据库和模型上下文只保留 `credential_ref`。
- 日志写入前过滤 API Key、密码、Authorization、Cookie 和测试账号字段。
- 页面 DOM 与截图允许发送给模型，但本地执行层在填充凭据时不把真实值加入模型请求。

### 3.4 浏览器动作

- 仅允许访问用户确认起始 URL 的同源地址。
- 动作必须通过 Pydantic 判别联合类型校验。
- 支持：`navigate`、`click`、`fill`、`select_option`、`press`、`scroll`、`wait_for`、`screenshot`、`finish`、`request_approval`。
- 删除、发布、授权、邀请、发送、支付、订阅、上传、下载、跨域等动作在执行前生成审批。
- 每功能最多 60 步/15 分钟，整任务最多 2 小时；连续 3 次页面指纹不变或相同动作重复则停止。

## 4. 数据模型

- `Task`：任务名称、状态、浏览器模式、起始 URL、错误、时间戳。
- `LaunchPlan`：项目类型、工作目录、安装/启动命令、环境变量、确认状态。
- `Feature`：功能标题、入口、目标、排序、选择状态、运行结果。
- `Run`：任务运行批次、状态、开始/结束时间和错误。
- `Step`：功能步骤、动作、目标、说明、URL、结果与页面指纹。
- `Screenshot`：步骤截图路径、尺寸、是否纳入手册。
- `Approval`：待审批动作、原因、状态和处理时间。
- `Artifact`：DOCX产物路径、MIME和大小。

状态机主路径：

```text
uploaded -> analyzing -> awaiting_review -> ready -> queued -> installing -> starting
-> authenticating -> exploring -> review_ready -> generating -> completed
```

任意可执行阶段可进入 `paused`、`failed`、`cancelled`。服务启动时将遗留的执行态任务转为 `failed`，错误码为 `interrupted`，不自动重放页面动作。

## 5. API

- `POST /api/tasks`：上传 ZIP。
- `GET /api/tasks`、`GET /api/tasks/{id}`：任务集合与详情。
- `POST /api/tasks/{id}/analyze`：静态分析。
- `PUT /api/tasks/{id}/review`：确认启动方案与功能清单。
- `PUT /api/tasks/{id}/credentials`：设置内存凭据。
- `POST /api/tasks/{id}/run|pause|resume|cancel`：控制任务。
- `POST /api/tasks/{id}/approvals/{approval_id}`：处理审批。
- `POST /api/tasks/{id}/features/{feature_id}/retry`：重试功能。
- `PUT /api/tasks/{id}/screenshots/{screenshot_id}`：设置是否纳入报告。
- `POST /api/tasks/{id}/report`：生成报告。
- `GET /api/tasks/{id}/artifacts/{artifact_id}`：下载报告。
- `GET /api/tasks/{id}/events`：SSE任务事件。
- `DELETE /api/tasks/{id}`：停止进程并删除任务。

## 6. 前端信息架构

首屏是任务集合与上传入口。任务工作区包含“配置、功能清单、运行、截图审核、报告”五个视图。运行视图采用三栏：功能进度、当前页面截图、结构化事件与审批。移动端只承担状态查看、审批和下载。

统一状态词为 `loading`、`refreshing`、`ready`、`empty`、`processing`、`queued`、`error`。长任务在对象行内显示状态，不使用全屏旋转加载器。所有操作必须有真实 API 和明确恢复路径。

## 7. 文档规范

使用 A4 中文专业操作手册模板：封面、静态章节目录、生成环境、功能章节、编号步骤与 1440×900 原始截图、未覆盖/失败附录。正文使用易读中文字体，截图保持比例并与说明同页。目录直接写入已选择的功能章节，打开 DOCX 后无需手动更新字段。DOCX 生成后在临时目录转换为 PDF，再把全部页面渲染为 PNG，校验页数、非空页面和图片边界；临时 PDF 在验收后删除，最终只登记 DOCX 产物。

## 8. 验收标准

1. ZIP攻击样本全部拒绝且不在任务目录外产生文件。
2. Compose、Python、Node样例能生成可编辑启动方案与功能清单。
3. 未确认方案不得执行任何安装或启动命令。
4. 凭据不出现在 SQLite、任务日志、模型请求记录和异常文本中。
5. 高风险动作在 Playwright执行前进入审批，拒绝后页面无副作用。
6. 有头/无头模式均可完成同源页面探索，失败后可重试单个功能。
7. 用户可审核步骤、排除截图并下载 DOCX。
8. DOCX 的全部渲染页非空且无明显裁切、重叠、缺字或失真。

## 9. 软著材料生成

新增 `CopyrightCase` 和 `CopyrightBatch` 关联表，既有任务及表结构保持兼容。Case 持久化修订号、业务证据、登记事实、源码顺序、章节草稿、阶段确认、检查点和执行状态。每次修改使本阶段及下游确认失效；确认绑定版本号及内容摘要。软件名称和版本以登记事实为准。

接口前缀 `/api/tasks/{id}/copyright`：

- `GET /`：状态、资料、字段标签、阻断项、批次及下载元数据。
- `PUT /stages/{business|registration|sources|drafts}`：提交 `{revision, value}` 编辑草稿。
- `POST /stages/{stage}/confirm`：提交 `{revision}` 确认当前内容。
- `POST /generate/{analyze|draft|review|publish}`：提交 `{revision}`，返回202并后台执行。
- `GET /source-preview`：真实源码分页与原始行号。
- `GET /draft-download`：下载明确标识的 Markdown 草稿。
- `POST /review-resolutions`：提交 `{revision, issue, note}`，对模型审查问题记录至少20字的人工核对依据。绑定草稿摘要，不能豁免源码变化、截图缺失、登记信息或分页等确定性校验。

生成进度通过既有 SSE 的 `copyright` 事件通知。重复生成返回409，旧版本写入返回409，错误保留已完成阶段。服务启动将遗留生成态标为中断，可从检查点重试。

源码证据按模块分批归纳业务及技术事实，章节引用必须属于实际项目。复核问题必须定位至文档原句及源码原句；有依据的矛盾修正一次后复核。外部模型直接使用 `.env` 配置，密钥、证件信息和权属资料不加入模型请求。

正式产物写入 `data/tasks/<id>/copyright/batches/<batch-id>/`，其中“正式资料”保存申请TXT、操作手册、技术设计说明书和源码的DOCX/PDF，ZIP仅包含正式文件。证据清单和校验报告单独登记。PDF由LibreOffice实际转换并逐页渲染，源码实际页数必须符合预期；全部通过后一次提交产物记录，否则不替换旧批次。

普通手册仍使用原 `reports/` 目录及接口，重建时只替换同一路径的旧手册，不删除软著产物。任务删除先等待本任务的生成和文件写入结束，再清理关联记录及目录。
