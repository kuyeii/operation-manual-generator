from __future__ import annotations

import re


def page_problem(text: str, html: str, failures: list[str]) -> str | None:
    if re.search(r"(?im)^\s*(Directory listing for|Index of)\s+/", text):
        return "当前页面是文件目录列表，业务系统未启动"
    if "vite-error-overlay" in html or "Internal Server Error" in text or "Error compiling" in text:
        return "页面显示开发服务器或编译错误"
    if not text.strip() and re.search(r'id=["\'](?:root|app|__next)["\']', html):
        return "前端应用根节点为空，页面未渲染"
    if failures:
        return "页面关键资源加载失败：" + "；".join(failures[:3])
    return None


async def verify_page(page, plan, diagnostic):
    failures = []
    def failed(request):
        if request.resource_type in {"script", "stylesheet", "document"}:
            failures.append(request.url)
    def response(item):
        if item.status >= 400 and item.request.resource_type in {"script", "stylesheet", "document"}:
            failures.append(item.url)
    page.on("requestfailed", failed)
    page.on("response", response)
    try:
        await page.reload(wait_until="domcontentloaded")
        if plan.page_text:
            await page.get_by_text(plan.page_text, exact=False).first.wait_for(timeout=15000)
        if plan.page_selector:
            await page.locator(plan.page_selector).first.wait_for(timeout=15000)
        problem = page_problem(await page.locator("body").inner_text(), await page.content(), failures)
        if problem:
            raise ValueError(problem)
    except Exception as exc:
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=diagnostic)
        problem = page_problem(await page.locator("body").inner_text(), await page.content(), failures)
        raise ValueError(problem or f"业务页面就绪条件未满足：{exc}") from exc
    finally:
        page.remove_listener("requestfailed", failed)
        page.remove_listener("response", response)
