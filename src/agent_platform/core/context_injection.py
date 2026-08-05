"""Explicit, read-only context injection for cognitive loops."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .contracts import AgentRequest
from .long_term_memory import (
    LongTermMemory,
    LongTermMemoryEntry,
    MemoryNamespace,
    MemoryScope,
)


class ContextInjectionError(ValueError):
    """Configured context sources are invalid or inconsistent."""


@dataclass(frozen=True)
class SkillContext:
    """One selected skill instruction made visible to an agent."""

    name: str
    content: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "skill name", 128))
        object.__setattr__(
            self,
            "content",
            _required_text(self.content, "skill content", 20_000),
        )
        object.__setattr__(
            self,
            "source",
            _required_text(self.source, "skill source", 512),
        )


@dataclass(frozen=True)
class InjectedContext:
    """Immutable context visible during planning, action, and reflection."""

    skills: tuple[SkillContext, ...] = ()
    project_instructions: tuple[str, ...] = ()
    task_context: Mapping[str, Any] = field(default_factory=dict)
    project_memory: tuple[LongTermMemoryEntry, ...] = ()
    organization_memory: tuple[LongTermMemoryEntry, ...] = ()

    def __post_init__(self) -> None:
        skills = tuple(self.skills)
        if any(not isinstance(skill, SkillContext) for skill in skills):
            raise ContextInjectionError("skills must contain SkillContext values")
        names = tuple(skill.name for skill in skills)
        if len(names) != len(set(names)):
            raise ContextInjectionError("skill names must be unique")
        instructions = tuple(
            _required_text(value, "project instruction", 10_000)
            for value in self.project_instructions
        )
        if not isinstance(self.task_context, Mapping):
            raise ContextInjectionError("task context must be a mapping")
        if any(not isinstance(key, str) for key in self.task_context):
            raise ContextInjectionError("task context keys must be strings")
        project_memory = tuple(self.project_memory)
        organization_memory = tuple(self.organization_memory)
        if any(
            not isinstance(entry, LongTermMemoryEntry)
            or entry.namespace.scope is not MemoryScope.PROJECT
            for entry in project_memory
        ):
            raise ContextInjectionError(
                "project memory must contain project-scoped entries"
            )
        if any(
            not isinstance(entry, LongTermMemoryEntry)
            or entry.namespace.scope is not MemoryScope.ORGANIZATION
            for entry in organization_memory
        ):
            raise ContextInjectionError(
                "organization memory must contain organization-scoped entries"
            )
        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "project_instructions", instructions)
        object.__setattr__(
            self,
            "task_context",
            MappingProxyType(
                {
                    key: _freeze_value(value)
                    for key, value in self.task_context.items()
                }
            ),
        )
        object.__setattr__(self, "project_memory", project_memory)
        object.__setattr__(self, "organization_memory", organization_memory)


class ContextInjector:
    """Select and combine explicit context sources behind one small interface."""

    def __init__(
        self,
        *,
        memory: LongTermMemory | None = None,
        project_namespace: MemoryNamespace | None = None,
        organization_namespace: MemoryNamespace | None = None,
        project_memory_keys: Sequence[str] = (),
        organization_memory_keys: Sequence[str] = (),
        skills: Sequence[SkillContext] = (),
        project_instructions: Sequence[str] = (),
        task_context: Mapping[str, Any] | None = None,
    ) -> None:
        if memory is not None and not isinstance(memory, LongTermMemory):
            raise ContextInjectionError("memory must be LongTermMemory")
        if project_namespace is not None and (
            not isinstance(project_namespace, MemoryNamespace)
            or project_namespace.scope is not MemoryScope.PROJECT
        ):
            raise ContextInjectionError(
                "project_namespace must use project scope"
            )
        if organization_namespace is not None and (
            not isinstance(organization_namespace, MemoryNamespace)
            or organization_namespace.scope is not MemoryScope.ORGANIZATION
        ):
            raise ContextInjectionError(
                "organization_namespace must use organization scope"
            )
        project_keys = _validated_keys(project_memory_keys, "project memory key")
        organization_keys = _validated_keys(
            organization_memory_keys,
            "organization memory key",
        )
        if memory is None and (
            project_namespace is not None
            or organization_namespace is not None
            or project_keys
            or organization_keys
        ):
            raise ContextInjectionError(
                "long-term memory namespaces require a memory module"
            )
        if project_keys and project_namespace is None:
            raise ContextInjectionError(
                "project memory keys require a project namespace"
            )
        if organization_keys and organization_namespace is None:
            raise ContextInjectionError(
                "organization memory keys require an organization namespace"
            )
        base_context = {} if task_context is None else task_context
        if not isinstance(base_context, Mapping):
            raise ContextInjectionError("task_context must be a mapping")
        if any(not isinstance(key, str) for key in base_context):
            raise ContextInjectionError("task_context keys must be strings")
        self._memory = memory
        self._project_namespace = project_namespace
        self._organization_namespace = organization_namespace
        self._project_keys = project_keys
        self._organization_keys = organization_keys
        self._skills = tuple(skills)
        self._project_instructions = tuple(project_instructions)
        self._task_context = dict(base_context)
        InjectedContext(
            skills=self._skills,
            project_instructions=self._project_instructions,
            task_context=self._task_context,
        )

    def build(self, request: AgentRequest) -> InjectedContext:
        if not isinstance(request, AgentRequest):
            raise ContextInjectionError("context injection requires AgentRequest")
        project_memory = ()
        organization_memory = ()
        if self._memory is not None:
            if self._project_namespace is not None and self._project_keys:
                project_memory = self._memory.query(
                    self._project_namespace,
                    keys=self._project_keys,
                )
            if self._organization_namespace is not None and self._organization_keys:
                organization_memory = self._memory.query(
                    self._organization_namespace,
                    keys=self._organization_keys,
                )
        task_context = {**self._task_context, **dict(request.context)}
        return InjectedContext(
            skills=self._skills,
            project_instructions=self._project_instructions,
            task_context=task_context,
            project_memory=project_memory,
            organization_memory=organization_memory,
        )


def _validated_keys(values: Sequence[str], name: str) -> tuple[str, ...]:
    keys = tuple(_required_text(value, name, 128) for value in values)
    if len(keys) != len(set(keys)):
        raise ContextInjectionError(f"{name}s must be unique")
    return keys


def _required_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextInjectionError(f"{name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise ContextInjectionError(
            f"{name} must not exceed {maximum} characters"
        )
    return text


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value
