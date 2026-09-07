from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, TypeAdapter


class FeatureInput(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    entry_path: str = "/"
    goal: str = ""
    selected: bool = True


class LaunchPlanInput(BaseModel):
    project_type: str
    working_directory: str = "."
    install_command: list[str] = Field(default_factory=list)
    start_command: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    start_url: HttpUrl
    browser_mode: Literal["headed", "headless"] = "headed"
    launch_plan: LaunchPlanInput
    features: list[FeatureInput]


class CredentialsRequest(BaseModel):
    username: str = ""
    password: str = ""
    username_label: str = "用户名"


class ApprovalDecision(BaseModel):
    approved: bool


class ScreenshotUpdate(BaseModel):
    included: bool


class ActionBase(BaseModel):
    reason: str = ""
    instruction: str = ""


class NavigateAction(ActionBase):
    action: Literal["navigate"]
    url: str


class Locator(BaseModel):
    role: str | None = None
    name: str | None = None
    text: str | None = None
    label: str | None = None
    placeholder: str | None = None
    test_id: str | None = None
    selector: str | None = None
    index: int = Field(default=0, ge=0, le=100)


class UploadAction(ActionBase):
    action: Literal["upload"]
    target: Locator
    file_id: str


class DownloadAction(ActionBase):
    action: Literal["download"]
    target: Locator


class ClickAction(ActionBase):
    action: Literal["click"]
    target: Locator


class FillAction(ActionBase):
    action: Literal["fill"]
    target: Locator
    value: str | None = None
    credential_ref: Literal["username", "password"] | None = None


class SelectAction(ActionBase):
    action: Literal["select_option"]
    target: Locator
    value: str


class PressAction(ActionBase):
    action: Literal["press"]
    key: str


class ScrollAction(ActionBase):
    action: Literal["scroll"]
    direction: Literal["up", "down"] = "down"
    amount: int = Field(default=600, ge=100, le=3000)


class WaitAction(ActionBase):
    action: Literal["wait_for"]
    text: str | None = None
    timeout_ms: int = Field(default=5000, ge=100, le=30000)


class ScreenshotAction(ActionBase):
    action: Literal["screenshot"]


class FinishAction(ActionBase):
    action: Literal["finish"]
    summary: str = "已完成当前功能探索"


class RequestApprovalAction(ActionBase):
    action: Literal["request_approval"]
    proposed_action: dict


BrowserAction = Annotated[
    NavigateAction
    | ClickAction
    | FillAction
    | SelectAction
    | PressAction
    | ScrollAction
    | WaitAction
    | ScreenshotAction
    | FinishAction
    | RequestApprovalAction
    | UploadAction
    | DownloadAction,
    # File IDs are resolved only against confirmed task bindings.
    Field(discriminator="action"),
]

browser_action_adapter = TypeAdapter(BrowserAction)


class EventPayload(BaseModel):
    event: str
    data: dict
