from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessFeature(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class Business(StrictModel):
    positioning: str = Field(min_length=1, max_length=3000)
    industry: str = Field(min_length=1, max_length=300)
    users: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=1000)
    features: list[BusinessFeature] = Field(min_length=1, max_length=100)
    technical_notes: list[BusinessFeature] = Field(default_factory=list, max_length=100)
    development_environment: str = ""
    runtime_environment: str = ""


class Registration(StrictModel):
    software_name: str = Field(default="", max_length=200)
    short_name: str = Field(default="", max_length=100)
    version: str = Field(default="", max_length=50)
    software_category: str = "应用软件"
    owner_type: Literal["", "自然人", "法人", "其他组织"] = ""
    owner_name: str = ""
    owner_country: str = "中国"
    owner_region: str = ""
    certificate_type: str = ""
    certificate_number: str = ""
    completion_date: str = ""
    development_method: Literal["", "单独开发", "合作开发", "委托开发", "下达任务开发"] = ""
    originality: Literal["", "原创", "修改"] = ""
    publication_status: Literal["", "已发表", "未发表"] = ""
    publication_date: str = ""
    publication_place: str = ""
    rights_acquisition: Literal["", "原始取得", "继受取得"] = ""
    rights_scope: str = "全部权利"
    ownership_notes: str = ""
    development_hardware: str = ""
    runtime_hardware: str = ""
    development_os: str = ""
    development_tools: str = ""
    runtime_os: str = ""
    supporting_software: str = ""
    languages: str = ""
    purpose: str = ""
    industry: str = ""
    main_functions: str = ""
    technical_features: str = ""

    def blockers(self) -> list[str]:
        required = [key for key in type(self).model_fields if key not in {
            "short_name", "publication_date", "publication_place", "ownership_notes",
        }]
        errors = [f"请填写{FIELD_LABELS.get(key, key)}" for key in required if not getattr(self, key).strip()]
        completed = None
        if self.completion_date:
            try:
                completed = date.fromisoformat(self.completion_date)
                if completed > date.today():
                    errors.append("开发完成日期不能晚于今天")
            except ValueError:
                errors.append("开发完成日期格式应为 YYYY-MM-DD")
        if self.publication_status == "已发表":
            try:
                published = date.fromisoformat(self.publication_date)
                if published > date.today() or (completed and published < completed):
                    errors.append("首次发表日期应不早于开发完成日期且不晚于今天")
            except ValueError:
                errors.append("已发表的软件必须填写有效的首次发表日期")
            if not self.publication_place.strip():
                errors.append("已发表的软件必须填写首次发表地点")
        elif self.publication_date or self.publication_place:
            errors.append("未发表的软件应清空首次发表日期和地点")
        if (self.development_method != "单独开发" or self.originality == "修改" or self.rights_acquisition == "继受取得") and not self.ownership_notes.strip():
            errors.append("请补充开发关系、原软件或权利取得的说明及证明材料情况")
        return errors


class Section(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    paragraphs: list[str] = Field(min_length=1, max_length=40)
    evidence_ids: list[str] = Field(min_length=1)
    feature_id: str | None = None
    screenshot_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def nonempty(self):
        if any(not item.strip() or len(item) > 12000 for item in self.paragraphs):
            raise ValueError("章节段落不能为空或超过12000字")
        return self


class Drafts(StrictModel):
    manual: list[Section] = Field(min_length=1, max_length=110)
    design: list[Section] = Field(min_length=1, max_length=110)


class SourceSelection(StrictModel):
    paths: list[str] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique(self):
        if len(self.paths) != len(set(self.paths)):
            raise ValueError("源码文件不能重复")
        return self


class RevisionRequest(StrictModel):
    revision: int = Field(ge=1)


class StageUpdate(RevisionRequest):
    value: dict


class ReviewResolution(RevisionRequest):
    issue: str = Field(min_length=1, max_length=5000)
    note: str = Field(min_length=20, max_length=4000)


FIELD_LABELS = {
    "software_name": "软件全称", "short_name": "软件简称", "version": "版本号",
    "software_category": "软件分类", "owner_type": "著作权人类型", "owner_name": "著作权人名称",
    "owner_country": "国家", "owner_region": "省市", "certificate_type": "证件类型",
    "certificate_number": "证件号码", "completion_date": "开发完成日期", "development_method": "开发方式",
    "originality": "软件说明", "publication_status": "发表状态", "publication_date": "首次发表日期",
    "publication_place": "首次发表地点", "rights_acquisition": "权利取得方式", "rights_scope": "权利范围",
    "ownership_notes": "权属及证明材料说明", "development_hardware": "开发硬件环境",
    "runtime_hardware": "运行硬件环境", "development_os": "开发操作系统", "development_tools": "开发环境与工具",
    "runtime_os": "运行平台与操作系统", "supporting_software": "运行支撑环境", "languages": "编程语言",
    "purpose": "开发目的", "industry": "面向领域", "main_functions": "主要功能", "technical_features": "技术特点",
}

STAGES = ("business", "registration", "sources", "drafts")
