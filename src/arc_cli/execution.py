from __future__ import annotations

import contextvars
import os
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .progress import console


@dataclass(slots=True)
class ExecutionStage:
    kind: str
    argv: list[str]
    description: str = ""
    pipeline: list[list[str]] = field(default_factory=list)
    stdin_from: str | None = None
    stdout_to: str | None = None
    redact: list[str] = field(default_factory=list)
    implementation_paths: list[str] = field(default_factory=list)

    def _safe_value(self, value: str, *, reproducible: bool) -> str:
        item = value
        for secret in self.redact:
            if secret and secret in item:
                item = item.replace(secret, "<redacted>")
        if reproducible:
            for path in self.implementation_paths:
                if path and path in item:
                    item = item.replace(path, "<manifest>")
            if "arc-remote-stage-" in item or "arc-remote-inputs-" in item:
                item = "<staged-archive>"
            elif ".arc-tmp-" in item:
                item = item.split(".arc-tmp-", 1)[0] + ".arc-tmp-<id>"
            elif "arc-stdout-archive-" in item:
                item = "<archive-temp>"
        return item

    def safe_argv(self, *, reproducible: bool = False) -> list[str]:
        return [self._safe_value(item, reproducible=reproducible) for item in self.argv]

    def safe_pipeline(self, *, reproducible: bool = False) -> list[list[str]]:
        return [
            [self._safe_value(item, reproducible=reproducible) for item in command]
            for command in self.pipeline
        ]

    def display(self, *, reproducible: bool = False) -> str:
        commands = [shlex.join(self.safe_argv(reproducible=reproducible))]
        commands.extend(shlex.join(command) for command in self.safe_pipeline(reproducible=reproducible))
        text = " | ".join(commands)
        if self.stdin_from:
            text = f"{self.stdin_from} | {text}"
        if self.stdout_to:
            target = self._safe_value(self.stdout_to, reproducible=reproducible)
            if reproducible and target.startswith(("/tmp/", os.fspath(Path.home() / ".cache"))):
                target = "<output>"
            text = f"{text} > {shlex.quote(target)}"
        return text

    def to_dict(self) -> dict:
        data = asdict(self)
        data["argv"] = self.safe_argv(reproducible=False)
        data["pipeline"] = self.safe_pipeline(reproducible=False)
        data["display"] = self.display(reproducible=False)
        data["reproducible"] = self.display(reproducible=True)
        data.pop("redact", None)
        data.pop("implementation_paths", None)
        return data


@dataclass(slots=True)
class ExecutionPlan:
    operation: str = ""
    stages: list[ExecutionStage] = field(default_factory=list)

    def add(self, stage: ExecutionStage) -> None:
        self.stages.append(stage)

    def to_dict(self) -> dict:
        return {"operation": self.operation, "stages": [stage.to_dict() for stage in self.stages]}

    def render(self, *, style: str = "reproducible") -> list[str]:
        reproducible = style != "exact"
        return [stage.display(reproducible=reproducible) for stage in self.stages]


_plan_var: contextvars.ContextVar[ExecutionPlan | None] = contextvars.ContextVar("arc_execution_plan", default=None)
_mode_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("arc_native_mode", default=None)
_style_var: contextvars.ContextVar[str] = contextvars.ContextVar("arc_native_style", default="reproducible")


def begin_plan(operation: str, *, mode: str | None = None, style: str = "reproducible") -> ExecutionPlan:
    plan = ExecutionPlan(operation=operation)
    _plan_var.set(plan)
    _mode_var.set(mode)
    _style_var.set(style)
    return plan


def current_plan() -> ExecutionPlan:
    plan = _plan_var.get()
    if plan is None:
        plan = ExecutionPlan()
        _plan_var.set(plan)
    return plan


def record_stage(
    kind: str,
    argv: Iterable[str],
    *,
    description: str = "",
    pipeline: Sequence[Sequence[str]] = (),
    stdin_from: str | None = None,
    stdout_to: str | os.PathLike[str] | None = None,
    redact: Iterable[str] = (),
    implementation_paths: Iterable[str | os.PathLike[str]] = (),
) -> ExecutionStage:
    stage = ExecutionStage(
        kind=kind,
        argv=[os.fspath(x) for x in argv],
        description=description,
        pipeline=[[os.fspath(x) for x in command] for command in pipeline],
        stdin_from=stdin_from,
        stdout_to=os.fspath(stdout_to) if stdout_to is not None else None,
        redact=[str(x) for x in redact if x],
        implementation_paths=[os.fspath(x) for x in implementation_paths if x],
    )
    current_plan().add(stage)
    if _mode_var.get() in {"before", "both"}:
        _print_stage(stage, style=_style_var.get())
    return stage


def record_backend(cmd: list[str], meta: dict) -> None:
    cleanup = [os.fspath(x) for x in meta.get("cleanup", [])]
    redact = [str(x) for x in meta.get("redact", []) if x]
    preprocess = meta.get("preprocess")
    pipeline = meta.get("pipeline")
    stdout_file = meta.get("stdout_file")
    if preprocess:
        record_stage(
            "backend-pipeline",
            preprocess,
            description="archive input preprocessing and backend",
            pipeline=[cmd],
            stdout_to=stdout_file,
            redact=redact,
            implementation_paths=cleanup,
        )
    elif pipeline:
        record_stage(
            "backend-pipeline",
            cmd,
            description="archive backend and compression pipeline",
            pipeline=[pipeline],
            stdout_to=stdout_file,
            redact=redact,
            implementation_paths=cleanup,
        )
    else:
        record_stage(
            "backend",
            cmd,
            description="archive backend",
            stdout_to=stdout_file,
            redact=redact,
            implementation_paths=cleanup,
        )


def _print_stage(stage: ExecutionStage, *, style: str) -> None:
    console.print("[dim]native:[/] " + stage.display(reproducible=style != "exact"))


def emit_after(*, json_mode: bool = False) -> None:
    if _mode_var.get() not in {"after", "both"}:
        return
    plan = current_plan()
    if not plan.stages:
        return
    style = _style_var.get()
    if json_mode:
        # Preserve the operation's stdout JSON contract. Native-plan metadata is
        # still structured JSON, but remains diagnostic output on stderr.
        import json

        console.print(json.dumps({"native_plan": plan.to_dict()}, ensure_ascii=False))
        return
    console.print("[bold cyan]Native equivalent[/]")
    for stage in plan.stages:
        _print_stage(stage, style=style)


def plan_dict() -> dict:
    return current_plan().to_dict()
