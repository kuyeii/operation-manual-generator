"""Read-only desktop/mobile checks against a running generator."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    base, task_id, output = sys.argv[1:]
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        errors = []
        for width, height in [(1440, 1000), (390, 844)]:
            page = await browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            for tab in ["config", "features", "run"]:
                await page.goto(f"{base}/tasks/{task_id}/{tab}")
                await page.get_by_role("heading", name={"config": "运行方案", "features": "功能清单", "run": "功能进度"}[tab], exact=True).wait_for()
                await page.screenshot(path=directory / f"{tab}-{width}.png", full_page=True)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow: {tab} {width}"
            await page.goto(f"{base}/tasks/{task_id}/config")
            await page.get_by_role("button", name="修改", exact=True).click()
            entry = page.get_by_label("页面标识文本", exact=True)
            await entry.fill("Unsaved verification draft")
            await page.wait_for_timeout(3500)
            assert await entry.input_value() == "Unsaved verification draft"
            await page.get_by_role("button", name="取消", exact=True).click()
            await page.close()
        await browser.close()
        assert not errors, errors
        print("Desktop/mobile routes, overflow, draft retention: passed")


asyncio.run(main())
