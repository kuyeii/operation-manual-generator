from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error

from manual_generator import explorer


@pytest.mark.parametrize("platform,display,mode,expected", [
    ("linux", "", "headed", "headless"),
    ("linux", ":99", "headed", "headed"),
    ("linux", ":99", "headless", "headless"),
    ("darwin", "", "headed", "headed"),
])
def test_effective_mode(monkeypatch, platform, display, mode, expected):
    monkeypatch.setattr(explorer.sys, "platform", platform)
    monkeypatch.setenv("DISPLAY", display)
    assert explorer.effective_browser_mode(mode) == expected


@pytest.mark.asyncio
async def test_no_display_uses_headless(monkeypatch):
    monkeypatch.setattr(explorer.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    browser = object()
    chromium = SimpleNamespace(launch=AsyncMock(return_value=browser))
    assert await explorer.launch_browser(chromium, "headed") is browser
    chromium.launch.assert_awaited_once_with(headless=True)


@pytest.mark.asyncio
async def test_launch_failure_preserves_real_cause():
    chromium = SimpleNamespace(launch=AsyncMock(side_effect=Error("XServer unavailable")))
    with pytest.raises(RuntimeError, match="Chromium启动失败：XServer unavailable"):
        await explorer.launch_browser(chromium, "headless")


@pytest.mark.asyncio
async def test_missing_browser_has_install_instruction(monkeypatch):
    monkeypatch.setattr(explorer, "_fallback_chromium", lambda: None)
    chromium = SimpleNamespace(launch=AsyncMock(side_effect=Error("Executable doesn't exist at /missing")))
    with pytest.raises(RuntimeError, match="playwright install chromium"):
        await explorer.launch_browser(chromium, "headless")


@pytest.mark.asyncio
async def test_missing_browser_can_use_fallback(monkeypatch, tmp_path):
    executable = tmp_path / "chrome"
    monkeypatch.setattr(explorer, "_fallback_chromium", lambda: executable)
    browser = object()
    chromium = SimpleNamespace(launch=AsyncMock(side_effect=[Error("Executable doesn't exist"), browser]))
    assert await explorer.launch_browser(chromium, "headless") is browser
    chromium.launch.assert_awaited_with(headless=True, executable_path=str(executable))
