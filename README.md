# 操作手册生成器

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
UV_CACHE_DIR=.uv-cache .venv/bin/uv pip install -e '.[dev]'
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

详细约束见 [技术规范](docs/technical-specification.md)。
