# 操作手册与软著材料生成器

本地运行的 AI Web 操作手册生成工具。上传源码 ZIP 后，工具会识别启动方式与功能入口；用户确认方案后，Playwright Chromium 自动探索页面、记录步骤与截图，并输出中文 DOCX 手册。

## 环境准备

- macOS ARM64
- Python 3.12
- Node.js 22+
- Docker Desktop（仅被测项目需要时）
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

## 使用流程

1. 上传源码 ZIP。
2. 点击“开始分析”，检查识别出的命令、访问地址与功能清单。
3. 配置测试账号并确认启动方案。
4. 在“运行”页开始探索，处理需要人工确认的高风险动作。
5. 在“截图审核”页排除不需要的截图。
6. 在“报告”页生成并下载 DOCX。

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
