from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from manual_generator.explorer import (
    Explorer,
    action_instruction,
    early_finish_without_interaction,
    has_interactive_controls,
    meaningful_interaction,
    page_shows_completed_feature,
    passive_stale_action,
)
from manual_generator.llm import SYSTEM_PROMPT
from manual_generator.schemas import browser_action_adapter


class TimeoutLocator:
    first = None

    def __init__(self):
        self.first = self

    async def wait_for(self, **_):
        raise PlaywrightTimeoutError("timeout")


class TimeoutPage:
    def get_by_text(self, *_args, **_kwargs):
        return TimeoutLocator()


class ScreenshotPage:
    async def screenshot(self, *, path):
        Path(path).write_bytes(b"screenshot")


@pytest.mark.asyncio
async def test_wait_for_timeout_is_returned_to_model(tmp_path: Path):
    action = browser_action_adapter.validate_python(
        {"action": "wait_for", "text": "加载", "timeout_ms": 100}
    )

    result, screenshot = await Explorer(None, None)._execute(
        TimeoutPage(), action, tmp_path, "feature", 1
    )

    assert result == "等待超时，未出现文本：加载"
    assert screenshot is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"action": "finish"}, False),
        ({"action": "click", "target": {"text": "继续"}}, False),
        ({"action": "screenshot"}, True),
        ({"action": "wait_for", "timeout_ms": 100}, True),
        ({"action": "navigate", "url": "http://localhost/"}, True),
        ({"action": "navigate", "url": "http://localhost/next"}, False),
    ],
)
def test_passive_stale_action(payload, expected):
    action = browser_action_adapter.validate_python(payload)

    assert passive_stale_action(action, "http://localhost/") is expected


def test_finish_requires_a_meaningful_interaction_on_interactive_page():
    finish = browser_action_adapter.validate_python({"action": "finish"})
    assert has_interactive_controls("医保上传\n选择文件\n计算得分")
    assert not meaningful_interaction([{"action": {"action": "screenshot"}}])
    assert meaningful_interaction([{"action": {"action": "click"}}])
    assert early_finish_without_interaction(finish, [], "医保上传\n选择文件")
    assert not early_finish_without_interaction(
        finish, [{"action": {"action": "click"}}], "医保上传\n选择文件"
    )


def test_finish_accepts_explicit_preloaded_completion_evidence():
    finish = browser_action_adapter.validate_python({"action": "finish"})
    page_text = "测试数据已自动加载\n医保上传（明文 Excel）\n文件：medical.xlsx\n行数：5"

    assert page_shows_completed_feature("上传医保明文 Excel 文件", page_text)
    assert not early_finish_without_interaction(
        finish, [], page_text, "上传医保明文 Excel 文件"
    )


def test_finish_rejects_unrelated_completion_banner():
    finish = browser_action_adapter.validate_python({"action": "finish"})
    page_text = "测试数据已自动加载\n银行上传\n选择文件\n医保上传"

    assert not page_shows_completed_feature("上传客户名单", page_text)
    assert early_finish_without_interaction(finish, [], page_text, "上传客户名单")


def test_model_is_told_to_use_preloaded_completion_evidence():
    assert "已自动加载" in SYSTEM_PROMPT
    assert "不要再次点击文件选择控件" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"action": "finish", "summary": "负责人已成功分配"}, "负责人已成功分配"),
        ({"action": "finish", "summary": ""}, "确认功能操作已完成"),
        (
            {"action": "click", "target": {"text": "提交"}, "instruction": "提交表单"},
            "提交表单",
        ),
    ],
)
def test_action_instruction_uses_user_facing_finish_summary(payload, expected):
    action = browser_action_adapter.validate_python(payload)

    assert action_instruction(action) == expected


@pytest.mark.asyncio
async def test_finish_action_captures_final_page(tmp_path: Path):
    action = browser_action_adapter.validate_python(
        {"action": "finish", "summary": "首页已展示"}
    )

    result, screenshot = await Explorer(None, None)._execute(
        ScreenshotPage(), action, tmp_path, "feature", 4
    )

    assert result == "success"
    assert screenshot == tmp_path / "screenshots" / "feature-004.png"
    assert screenshot.read_bytes() == b"screenshot"
