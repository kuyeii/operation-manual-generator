# 操作手册与软著材料生成器

本地运行的 AI Web 操作手册生成工具。上传源码 ZIP 后，工具会识别启动方式与功能入口；用户确认方案后，Playwright Chromium 自动探索页面、记录步骤与截图，并输出中文 DOCX 手册。

## 环境准备

- macOS ARM64
- Python 3.12
- Node.js 22+
- Docker Engine（多服务运行必需；macOS 使用 Docker Desktop 或部署到 Linux）
- LibreOffice/Poppler（后台渲染并检查 DOCX 版面）

所有工具 Python 依赖安装到根目录 `.venv`，前端依赖由根目录 npm workspace 管理。被测项目依赖存放在独立任务目录。

```bash
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.uv-python uv python install 3.12
UV_CACHE_DIR=.uv-cache uv venv --python 3.12 .venv
UV_CACHE_DIR=.uv-cache uv pip install --python .venv/bin/python -e '.[dev]'
npm install
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers .venv/bin/playwright install chromium
mkdir -p .fonts
curl -L --fail -o .fonts/NotoSansCJKsc-Regular.otf \
  https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf
```

国内网络可为 Playwright 安装命令增加 `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright`。

## 启动

开发模式分别启动后端与前端：

```bash
.venv/bin/uvicorn manual_generator.main:app --host 127.0.0.1 --port 8000 --reload
npm run dev
```

生产式本地运行：

```bash
npm run build
.venv/bin/python -m manual_generator
```

访问 `http://127.0.0.1:8000`。模型是项目级能力，通过 `.env` 中的 `LLM_API_KEY`、`LLM_BASE_URL`、`MODEL` 和 `LLM_PROTOCOL` 统一配置；任务页面不接收模型密钥。

### Docker 启动

Docker 镜像会构建前端并由 FastAPI 统一提供页面和 API，同时安装 Playwright Chromium、Node.js 22 和中文字体。先根据 `.env.example` 准备 `.env`，再执行：

```bash
docker compose up --build -d
docker compose ps
```

访问 `http://127.0.0.1:8000`。任务数据库、上传源码、截图和生成材料保存在宿主机 `data/`；更新镜像或重建容器不会清空这些内容。查看日志和停止服务：

```bash
docker compose logs -f app
docker compose down
```

Compose 会覆盖 `.env` 中的监听地址和数据目录，使容器监听 `0.0.0.0:8000` 并写入 `/app/data`，其余模型及 PDF 开关继续读取 `.env`。

镜像包含 Docker CLI，Compose 挂载宿主机 `/var/run/docker.sock`。这等同于授予宿主机 Docker 控制权限，仅可用于可信源码和受控网络，不可作为不可信上传的安全沙箱。每次任务使用独立网络和网络命名空间，各被测服务共享该任务网络，生成器接入后通过内部 IP 访问，不向宿主机发布业务端口。服务器没有显示服务时自动使用无界面浏览器。

Compose 配置保留启动依赖，运行时移除宿主机端口发布并使用任务专属卷。不接受宿主机绑定、特权、外部卷、外部配置和环境插值；这些配置必须先改成明确的任务内方案。浏览器仅允许访问已确认的入口站点。使用外部 CDN 的项目需先将必要资源放到项目内。

## 使用流程

1. 上传源码 ZIP。
2. 在“配置”页查看识别依据、服务列表、构建步骤、端口和业务页面就绪条件，点击“分析功能”。
3. 按需要上传测试文件，在“功能清单”页绑定文件、前置功能和成功校验条件；保存后分别确认运行方案与文件用途。
4. 在“运行”页开始探索，处理需要人工确认的高风险动作。
5. 在“截图审核”页排除不需要的截图。
6. 在“报告”页生成并下载 DOCX。

源码扫描忽略 macOS 元数据和依赖目录；未知入口不会自动回退到目录服务器。HTTP 200 只通过服务层检查，目录列表、空白应用根节点、资源错误及不满足页面标识的页面均阻止探索。修改源码、文件或配置后原确认失效。新运行会重新建立浏览器状态和功能依赖，不沿用旧成功标记。

测试文件默认单文件 20 MB、每任务 100 MB，可通过 `MAX_TEST_FILE_BYTES`、`MAX_TEST_FILES_BYTES` 调整。上传只允许当前任务绑定的文件；成功必须有确认的页面提示或接口结果。下载必须完成、非空且类型一致，XLSX 检查工作簿结构。运行结果、服务日志、接口证据和已下载文件均持久化，只有成功步骤的截图默认纳入手册。停止和失败回收本批次资源，重启不自动重放业务操作。

运行接口位于 `/api/tasks/{id}/runtime-plan`、`execution-config`、`preflight`、`test-files`；更新与确认均使用当前 `revision`。功能编辑通过 `PUT /features?revision=...` 原位保存，保留功能 ID 和历史证据。历史任务首次新运行需补充并确认页面就绪条件。

项目私密测试文件放在任务目录的 `runtime-private/`，运行时通过专属卷只读挂载至 `/run/test-private`。该目录不进入源码工作副本、模型输入或正式材料；请勿在服务环境变量表内填写密钥。

### 合成样例验收

`scripts/bank_fixture.go` 仅适用于已确认的 bank 项目：在其后端模块临时运行，复用项目加密函数生成三行虚构数据，密钥单独保存，预期评分为 4、5、8。`scripts/bank_acceptance.py` 配置四功能依赖并启动真实模型探索；`scripts/verify_bank_result.py` 校验接口业务任务 ID、下载字段、行数与评分。目标项目修复记录在 `scripts/patches/bank-runtime.patch`，不修改评分算法或升级原直接依赖。

`scripts/verify_runtime_matrix.py` 在 Linux Docker 中验证 Go、FastAPI、Express 与 Vite 组合；`scripts/verify_runtime_ui.py` 检查桌面/手机页面及编辑草稿保留。测试目录与临时密钥存放在 `.runtime/`，已加入 Git 忽略。

## 软件著作权资料

在任务的“软著资料”页执行以下流程：

1. 分析软著资料，审阅并确认业务理解及源码证据。
2. 编辑登记信息，补全软件名称、版本、权属、日期及环境并确认。
3. 审核源码选择，可排除文件、调整顺序、查看前后卷分页后确认。
4. 使用原有“运行”和“截图审核”流程完成所有选中功能，每项至少保留一张真实截图。
5. 生成操作手册和技术设计说明书草稿；可按章编辑，修改后重新复核并确认全部草稿。模型误报可逐项记录源码核对依据与处理理由，结论绑定当前草稿，修改后失效。
6. 生成正式资料，下载申请填报 TXT、手册/设计说明书/源码的 DOCX，以及正式材料 ZIP。

源码超过60页时输出连续前后各30页，不超过60页时输出全部。源文件、原始行号、哈希、章节证据及校验报告单独留档。正式包不包含内部核验记录。

每次修改上游资料都会使相关下游确认失效。长任务进度保存于 SQLite，失败可重试；新批次只有完整通过校验后才发布，上一批正式材料仍可下载。软著模型直接复用 `.env` 配置，不依赖 Codex/Claude 或任何 Skill 运行器。技术事实分析仅发送脱敏源码证据，权属和证件信息仅用于本地填报文件。

PDF 生成及版面验收暂时默认关闭，历史 PDF 下载项隐藏，原文件与实现保留；新批次 ZIP 不含 PDF。校验报告会注明未执行 PDF 验收，因此不会验证实际渲染页数及长行截断。需要恢复时在 `.env` 设置 `MANUAL_GENERATOR_PDF_ENABLED=true` 并重启服务；届时需要 `pypdf`（已包含于 `pyproject.toml`）、LibreOffice、Poppler 的 `pdfinfo`/`pdftoppm` 及完整中文字体。历史 ZIP 保留原内容，不重新打包。

普通回归测试：`.venv/bin/python -m pytest`。真实模型与浏览器端到端验收：`.venv/bin/python scripts/verify_copyright_sample.py`，只使用仓库公开工单样例及明确标注的测试登记信息，会调用 `.env` 中的模型服务并创建一个验收任务。可追加已创建任务 ID 继续失败的验收。

参考来源和许可见 [第三方说明](docs/third-party-notices.md)。

详细约束见 [技术规范](docs/technical-specification.md)。
