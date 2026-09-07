from pathlib import Path

import pytest
from docx import Document

from manual_generator.models import Feature, Step, Task
from manual_generator.reports import _render_and_verify_pdf, build_report


@pytest.mark.parametrize("enabled", [False, True])
def test_report_pdf_switch(tmp_path, monkeypatch, enabled):
    from types import SimpleNamespace

    monkeypatch.setattr("manual_generator.reports.get_settings", lambda: SimpleNamespace(pdf_enabled=enabled))
    calls = []
    monkeypatch.setattr("manual_generator.reports._render_and_verify_docx", lambda *args: calls.append(args))
    task = Task(name="测试", status="review_ready", features=[])
    assert build_report(task, tmp_path).exists()
    assert len(calls) == int(enabled)


def test_build_report_without_screenshots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("manual_generator.reports._render_and_verify_docx", lambda *_: [])
    task = Task(name="演示系统", status="review_ready", start_url="http://localhost:8001")
    feature = Feature(title="用户管理", goal="查看用户列表", status="completed", selected=True, position=0)
    feature.steps = [Step(position=1, action="click", instruction="打开用户管理", result="success")]
    task.features = [feature]
    docx = build_report(task, tmp_path)
    assert docx.exists()
    document = Document(docx)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "演示系统" in text
    assert "用户管理" in text
    assert "请在 Word 中更新目录字段" not in text


def test_render_verifier_requires_pdf_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("manual_generator.reports.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="pdftoppm"):
        _render_and_verify_pdf(tmp_path / "missing.pdf", tmp_path / "rendered")


def test_render_verifier_removes_stale_pages(tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "rendered"
    output_dir.mkdir()
    stale_page = output_dir / "page-99.png"
    stale_page.write_bytes(b"stale")
    monkeypatch.setattr("manual_generator.reports.shutil.which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **_):
        if command[0].endswith("pdftoppm"):
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (20, 20), "white")
            ImageDraw.Draw(image).rectangle((5, 5, 15, 15), fill="black")
            image.save(output_dir / "page-1.png")
            return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": "Pages: 1\n"})()

    monkeypatch.setattr("manual_generator.reports.subprocess.run", fake_run)
    pages = _render_and_verify_pdf(tmp_path / "report.pdf", output_dir)
    assert pages == [output_dir / "page-1.png"]
    assert not stale_page.exists()


def test_pdf_conversion_requires_cjk_font(tmp_path: Path, monkeypatch) -> None:
    from manual_generator.reports import _convert_pdf

    monkeypatch.setenv("MANUAL_GENERATOR_FONT_DIR", str(tmp_path / ".fonts"))
    monkeypatch.setattr("manual_generator.reports.shutil.which", lambda _: "/bin/true")
    with pytest.raises(RuntimeError, match="中文字体"):
        _convert_pdf(tmp_path / "input.docx", tmp_path / "reports")
