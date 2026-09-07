from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40), default="uploaded", index=True)
    browser_mode: Mapped[str] = mapped_column(String(20), default="headed")
    start_url: Mapped[str | None] = mapped_column(String(2048))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    launch_plan: Mapped[LaunchPlan | None] = relationship(back_populates="task", cascade="all, delete-orphan", uselist=False)
    features: Mapped[list[Feature]] = relationship(back_populates="task", cascade="all, delete-orphan", order_by="Feature.position")
    runs: Mapped[list[Run]] = relationship(back_populates="task", cascade="all, delete-orphan")
    approvals: Mapped[list[Approval]] = relationship(back_populates="task", cascade="all, delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="task", cascade="all, delete-orphan")
    copyright_case: Mapped[CopyrightCase | None] = relationship(cascade="all, delete-orphan", uselist=False)
    copyright_batches: Mapped[list[CopyrightBatch]] = relationship(cascade="all, delete-orphan")
    runtime_config: Mapped[RuntimeConfig | None] = relationship(cascade="all, delete-orphan", uselist=False)
    test_files: Mapped[list[TestFile]] = relationship(cascade="all, delete-orphan")
    execution_records: Mapped[list[ExecutionRecord]] = relationship(cascade="all, delete-orphan")


class LaunchPlan(Base):
    __tablename__ = "launch_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    project_type: Mapped[str] = mapped_column(String(40))
    working_directory: Mapped[str] = mapped_column(String(1024), default=".")
    install_command: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_command: Mapped[list[str]] = mapped_column(JSON, default=list)
    environment: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    detected_files: Mapped[list[str]] = mapped_column(JSON, default=list)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    task: Mapped[Task] = relationship(back_populates="launch_plan")


class Feature(Base):
    __tablename__ = "features"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    title: Mapped[str] = mapped_column(String(240))
    entry_path: Mapped[str] = mapped_column(String(2048), default="/")
    goal: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    task: Mapped[Task] = relationship(back_populates="features")
    steps: Mapped[list[Step]] = relationship(back_populates="feature", cascade="all, delete-orphan", order_by="Step.position")


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    task: Mapped[Task] = relationship(back_populates="runs")


class Step(Base):
    __tablename__ = "steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    feature_id: Mapped[str] = mapped_column(ForeignKey("features.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(500))
    instruction: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(2048))
    result: Mapped[str] = mapped_column(String(40), default="success")
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    feature: Mapped[Feature] = relationship(back_populates="steps")
    screenshot: Mapped[Screenshot | None] = relationship(back_populates="step", cascade="all, delete-orphan", uselist=False)


class Screenshot(Base):
    __tablename__ = "screenshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    step_id: Mapped[str] = mapped_column(ForeignKey("steps.id"), unique=True)
    path: Mapped[str] = mapped_column(String(2048))
    width: Mapped[int] = mapped_column(Integer, default=1440)
    height: Mapped[int] = mapped_column(Integer, default=900)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    step: Mapped[Step] = relationship(back_populates="screenshot")


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    action: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task: Mapped[Task] = relationship(back_populates="approvals")


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(2048))
    mime_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    task: Mapped[Task] = relationship(back_populates="artifacts")


class CopyrightCase(Base):
    __tablename__ = "copyright_cases"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="idle")
    operation: Mapped[str | None] = mapped_column(String(30))
    progress: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmations: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class CopyrightBatch(Base):
    __tablename__ = "copyright_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="generating")
    manifest: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RuntimeConfig(Base):
    __tablename__ = "runtime_configs"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    execution: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmations: Mapped[dict] = mapped_column(JSON, default=dict)


class TestFile(Base):
    __tablename__ = "task_test_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    path: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(120))


class ExecutionRecord(Base):
    __tablename__ = "execution_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="starting")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
