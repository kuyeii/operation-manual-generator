from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

CODE_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".go", ".java", ".kt", ".rs", ".cs", ".cpp", ".c", ".h", ".php", ".rb", ".swift", ".html", ".css", ".scss", ".sql", ".wxml", ".wxss"}
IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "target", "vendor", "__pycache__", "coverage", "tests", "test", "fixtures", "samples", "examples", "runtime", "reports", "软件著作权申请资料"}
SECRET = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-|ghp_|github_pat_)[\w-]{16,}|"
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|https?)://[^\s/:]+:[^\s/@]+@|"
    r"\bBearer\s+[a-zA-Z0-9._-]{16,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|authorization)\s*[=:]\s*[\"'][^\"'\n]{4,}[\"'])",
    re.IGNORECASE,
)


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def redact(text: str) -> str:
    return SECRET.sub("[已脱敏]", text)


def scan_sources(root: Path) -> dict:
    root = root.resolve()
    files, excluded, docs = [], [], []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if name.startswith(".") or path.suffix in {".pem", ".key", ".p12"}:
                excluded.append({"path": relative, "reason": "配置或敏感文件"})
                continue
            is_doc = path.suffix.lower() in {".md", ".rst"} or name.lower().startswith("readme") or name in {"package.json", "pyproject.toml", "go.mod", "pom.xml"}
            if path.suffix.lower() not in CODE_EXTENSIONS and not is_doc:
                continue
            reason = None
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                reason = "符号链接或路径越界"
            elif path.stat().st_size > 512_000:
                reason = "文件超过500KB，需拆分或人工核查"
            elif ".min." in name or ".test." in name or ".spec." in name or name.endswith((".d.ts", ".generated.ts")):
                reason = "测试、压缩或生成代码"
            if reason:
                excluded.append({"path": relative, "reason": reason})
                continue
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8")
                if "\x00" in text:
                    raise UnicodeError()
            except UnicodeError:
                excluded.append({"path": relative, "reason": "非UTF-8文本"})
                continue
            if SECRET.search(text):
                excluded.append({"path": relative, "reason": "疑似包含硬编码凭据，排除整文件"})
                continue
            lines = text.splitlines()
            if not any(line.strip() for line in lines):
                continue
            # Keep original line numbers in model evidence; extraction never uses these excerpts.
            if len(text) <= 14000:
                indices = list(range(len(lines)))
            else:
                indices = list(range(min(30, len(lines))))
                anchors = [i for i, line in enumerate(lines) if re.search(r"\b(class|def|function|interface|CREATE TABLE|export|router|app\.(get|post))\b", line)][:24]
                for anchor in anchors:
                    indices.extend(range(anchor, min(anchor + 9, len(lines))))
            excerpt = "\n".join(f"{i + 1}: {lines[i][:500]}" for i in sorted(set(indices)))[:18000]
            item = {"id": relative, "path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "lines": len(lines), "effective_lines": sum(bool(line.strip()) for line in lines), "excerpt": redact(excerpt), "language": path.suffix.lstrip(".")}
            (docs if is_doc else files).append(item)
            if len(files) + len(docs) > 10000:
                raise ValueError("源码文件超过10000个，请缩小本次申请项目范围")
    files.sort(key=lambda item: (0 if Path(item["path"]).stem in {"main", "app", "index", "App"} else 1, item["path"]))
    if not files:
        raise ValueError("未发现可交存的源码，请检查项目范围、编码和敏感文件排除清单")
    return {"files": files, "documents": docs, "excluded": excluded,
            "source_lines": sum(item["lines"] for item in files),
            "languages": sorted({item["language"] for item in files}),
            "fingerprint": digest([(item["path"], item["sha256"]) for item in sorted(files + docs, key=lambda x: x["path"])])}


def verify_sources(root: Path, inventory: dict) -> None:
    if scan_sources(root)["fingerprint"] != inventory["fingerprint"]:
        raise ValueError("项目源码已变化，请重新分析并确认业务、登记信息和源码选择")


def wrap_code(line: str, columns: int = 88) -> list[str]:
    line = line.expandtabs(4)
    parts, current, width = [], "", 0
    for char in line:
        size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if width + size > columns and current:
            parts.append(current)
            current, width = "", 0
        current += char
        width += size
    if current:
        parts.append(current)
    return parts


def source_pages(root: Path, inventory: dict, paths: list[str]) -> dict:
    candidates = {item["path"]: item for item in inventory["files"]}
    if not paths or len(paths) != len(set(paths)) or any(path not in candidates for path in paths):
        raise ValueError("源码选择为空、重复或包含非候选文件")
    rows = []
    for relative in paths:
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("源码路径越界")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != candidates[relative]["sha256"]:
            raise ValueError("源码已变化，请重新分析")
        text = raw.decode("utf-8")
        if SECRET.search(text):
            raise ValueError("源码包含疑似凭据")
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            for part, display in enumerate(wrap_code(line)):
                rows.append({"path": relative, "line": number, "part": part, "text": display})
    if not rows:
        raise ValueError("选定源码没有有效内容")
    pages = [{"number": i // 50 + 1, "rows": rows[i:i + 50]} for i in range(0, len(rows), 50)]
    volumes = [{"name": "全部", "pages": pages}] if len(pages) <= 60 else [
        {"name": "前30页", "pages": pages[:30]}, {"name": "后30页", "pages": pages[-30:]},
    ]
    return {"total_pages": len(pages), "selected_lines": len({(row["path"], row["line"]) for row in rows}), "volumes": volumes}
