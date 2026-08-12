import pytest
from pydantic import ValidationError

from manual_generator.explorer import _fallback_chromium, same_origin
from manual_generator.schemas import browser_action_adapter
from manual_generator.security import risky_action


def test_action_schema() -> None:
    action = browser_action_adapter.validate_python({
        "action": "click",
        "target": {"role": "button", "name": "新建"},
        "instruction": "点击新建",
    })
    assert action.action == "click"


def test_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        browser_action_adapter.validate_python({"action": "execute_script", "script": "alert(1)"})


def test_finish_action_accepts_missing_summary() -> None:
    action = browser_action_adapter.validate_python(
        {"action": "finish", "instruction": "功能已展示"}
    )
    assert action.summary == "已完成当前功能探索"


def test_risky_action_detection() -> None:
    assert risky_action({"action": "click", "instruction": "点击删除按钮"})
    assert risky_action({"action": "click", "instruction": "打开详情"}) is None


def test_same_origin() -> None:
    assert same_origin("http://127.0.0.1:8000/app", "/users")
    assert not same_origin("http://127.0.0.1:8000", "https://example.com")


def test_chromium_override(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "chrome"
    executable.write_text("")
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", str(executable))
    assert _fallback_chromium() == executable
