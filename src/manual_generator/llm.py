from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .schemas import BrowserAction, browser_action_adapter

SYSTEM_PROMPT = """你是 Web 用户操作手册探索代理。每次只能返回一个 JSON 动作，不要返回 Markdown。
你要完成指定功能目标，优先使用 role/name/label/text 定位，不使用坐标。每次观察的是最新页面状态。
允许动作：navigate, click, fill, select_option, press, scroll, wait_for, screenshot, finish, request_approval。
动作必须严格使用以下字段结构：
- navigate: {"action":"navigate","url":"http://站点内地址"}
- click: {"action":"click","target":{"role":"button","name":"按钮名"}}
- fill: {"action":"fill","target":{"label":"字段名"},"value":"普通内容"}
- select_option: {"action":"select_option","target":{"label":"字段名"},"value":"选项值"}
- press: {"action":"press","key":"Enter"}
- scroll: {"action":"scroll","direction":"down","amount":600}
- wait_for: {"action":"wait_for","text":"等待的文本","timeout_ms":5000}
- screenshot: {"action":"screenshot"}
- finish: {"action":"finish","summary":"完成摘要"}
登录字段必须使用 credential_ref=username 或 credential_ref=password，禁止要求或输出真实凭据。
删除、发布、授权、邀请、外发、支付、购买、订阅、上传、下载或跨域前必须 request_approval。
若页面已明确显示当前目标通过预置数据完成（如“已自动加载”“已上传”并显示对应业务对象），直接 finish 并说明页面上的完成证据；不要再次点击文件选择控件。
完成一个有意义的用户操作后使用 screenshot；确认目标功能已经得到清楚展示后 finish。
动作例：{"action":"click","target":{"role":"button","name":"新建"},"instruction":"点击“新建”按钮","reason":"进入创建流程"}
"""

FEATURE_DISCOVERY_PROMPT = """你是 Web 产品功能分析器。输入仅包含本地静态分析得到的结构化证据，不包含完整源码。
请把证据归纳为用户可以独立理解和执行的功能清单。单页应用中的上传、计算、下载等业务操作应分别列出，即使入口路径相同。
每项功能必须引用至少一个输入中的 evidence id；禁止推测证据没有支持的功能。entry_path 必须是站内绝对路径。
在 goal 中说明操作结果；存在前置依赖时直接写明。不要把泛化的“首页”与页面中的具体业务操作同时列出。
"""


class FeatureProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    entry_path: str = Field(pattern=r"^/[^?#]*$")
    goal: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class FeatureDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features: list[FeatureProposal] = Field(max_length=50)


FEATURE_DISCOVERY_SCHEMA = FeatureDiscoveryResult.model_json_schema()


@dataclass(slots=True)
class ModelOptions:
    api_key: str
    base_url: str
    model: str
    protocol: str
    vision_enabled: bool = True


class OpenAICompatibleClient:
    def __init__(self, options: ModelOptions):
        self.options = options

    async def next_action(
        self,
        *,
        goal: str,
        url: str,
        page_text: str,
        history: list[dict],
        screenshot: Path | None,
    ) -> BrowserAction:
        prompt = (
            f"功能目标：{goal}\n当前 URL：{url}\n"
            f"最近动作：{json.dumps(history[-8:], ensure_ascii=False)}\n"
            f"页面可访问文本：\n{page_text[:24000]}"
        )
        return await self._request(prompt, screenshot)

    async def discover_features(self, evidence: list[dict]) -> list[dict]:
        prompt = (
            "请根据以下结构化证据生成功能清单。输入字段只描述控件和调用关系：\n"
            + json.dumps({"evidence": evidence}, ensure_ascii=False)
        )
        data = await self._request_feature_discovery(prompt, structured=True)
        content = self._extract_content(data)
        result = FeatureDiscoveryResult.model_validate(_json_object(content))
        return [item.model_dump() for item in result.features]

    async def _request_feature_discovery(self, prompt: str, *, structured: bool) -> dict:
        if self.options.protocol == "chat_completions":
            endpoint = f"{self.options.base_url.rstrip('/')}/chat/completions"
            format_value = (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "feature_inventory",
                        "strict": True,
                        "schema": FEATURE_DISCOVERY_SCHEMA,
                    },
                }
                if structured
                else {"type": "json_object"}
            )
            payload = {
                "model": self.options.model,
                "temperature": 0,
                "response_format": format_value,
                "messages": [
                    {"role": "system", "content": FEATURE_DISCOVERY_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }
        else:
            endpoint = f"{self.options.base_url.rstrip('/')}/responses"
            format_value = (
                {
                    "type": "json_schema",
                    "name": "feature_inventory",
                    "strict": True,
                    "schema": FEATURE_DISCOVERY_SCHEMA,
                }
                if structured
                else {"type": "json_object"}
            )
            payload = {
                "model": self.options.model,
                "instructions": FEATURE_DISCOVERY_PROMPT,
                "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                "text": {"format": format_value},
            }
        headers = {"Authorization": f"Bearer {self.options.api_key}"}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
        if structured and response.status_code in {400, 415, 422}:
            return await self._request_feature_discovery(prompt, structured=False)
        response.raise_for_status()
        return response.json()

    async def _request(self, prompt: str, screenshot: Path | None) -> BrowserAction:
        if self.options.protocol == "chat_completions":
            payload = self._chat_payload(prompt, screenshot)
            endpoint = f"{self.options.base_url.rstrip('/')}/chat/completions"
        else:
            payload = self._responses_payload(prompt, screenshot)
            endpoint = f"{self.options.base_url.rstrip('/')}/responses"
        headers = {"Authorization": f"Bearer {self.options.api_key}"}
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            should_fallback = (
                response.status_code in {400, 415, 422}
                and screenshot is not None
                and self.options.vision_enabled
            )
            if not should_fallback:
                response.raise_for_status()
                data = response.json()
        if should_fallback:
            self.options.vision_enabled = False
            return await self._request(prompt, None)
        content = self._extract_content(data)
        return browser_action_adapter.validate_python(_normalize_action(_json_object(content)))

    def _chat_payload(self, prompt: str, screenshot: Path | None) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        image = _image_data(screenshot) if self.options.vision_enabled else None
        if image:
            content.append({"type": "image_url", "image_url": {"url": image}})
        return {
            "model": self.options.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }

    def _responses_payload(self, prompt: str, screenshot: Path | None) -> dict:
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        image = _image_data(screenshot) if self.options.vision_enabled else None
        if image:
            content.append({"type": "input_image", "image_url": image})
        return {
            "model": self.options.model,
            "instructions": SYSTEM_PROMPT,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_object"}},
        }

    @staticmethod
    def _extract_content(data: dict) -> str:
        if data.get("choices"):
            return data["choices"][0]["message"]["content"]
        if data.get("output_text"):
            return data["output_text"]
        parts: list[str] = []
        for output in data.get("output", []):
            for item in output.get("content", []):
                if item.get("type") in {"output_text", "text"}:
                    parts.append(item.get("text", ""))
        if not parts:
            raise ValueError("模型响应中没有文本动作")
        return "".join(parts)


class DeterministicClient:
    """无 API Key 时使用的安全演示代理，只导航并截图。"""

    async def next_action(self, *, history: list[dict], **_: object) -> BrowserAction:
        if not history:
            return browser_action_adapter.validate_python(
                {"action": "screenshot", "instruction": "记录功能页面", "reason": "演示模式"}
            )
        return browser_action_adapter.validate_python(
            {"action": "finish", "summary": "已记录当前功能页面", "instruction": "完成"}
        )


def _image_data(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/jpeg;base64,{encoded}"


def _json_object(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型未返回 JSON 对象")
    value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型动作必须是 JSON 对象")
    return value


def _normalize_action(action: dict) -> dict:
    if action.get("action") != "navigate" or action.get("url"):
        return action
    target = action.get("target")
    if isinstance(target, str):
        return {**action, "url": target}
    if isinstance(target, dict):
        url = target.get("url") or target.get("href") or target.get("value")
        if isinstance(url, str) and url:
            return {**action, "url": url}
    return action
