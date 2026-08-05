"""Offline end-to-end demonstration for A3 Loop Engineering."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    Action,
    AgentRequest,
    CognitiveLoopRunner,
    CognitiveLoopTaskHandler,
    ContextInjector,
    CronExpression,
    CronLoop,
    GoalLoop,
    HeartbeatLoop,
    HookEvent,
    HookLoop,
    HookSubscription,
    JsonLongTermMemoryStore,
    JsonLoopRunStore,
    JsonWorkingMemoryStore,
    LongTermMemory,
    LongTermMemoryCategory,
    LoopDispatcher,
    LoopRunLedger,
    MemoryNamespace,
    MemoryScope,
    Plan,
    Reflection,
    ReflectionDecision,
    SkillContext,
    TaskWorkspaceManager,
    ToolRegistry,
    WorkingMemory,
)


FIXED_TIME = datetime.fromisoformat("2026-08-05T12:00:00+08:00")


class CompleteTaskTool:
    name = "task.complete"

    def run(self, arguments):
        return (
            f"已完成：{arguments['task']}；"
            f"项目记忆={arguments['project_memory_count']}；"
            f"组织记忆={arguments['organization_memory_count']}"
        )


class ScheduledContextAgent:
    name = "scheduled_context_agent"

    def create_plan(self, request):
        context = request.context["injected_context"]
        workspace = request.context["task_workspace"]
        return Plan(
            goal=request.task,
            steps=(
                f"读取 {len(context.skills)} 个 Skill",
                f"在隔离目录 {workspace} 执行",
                "调用受控工具并反思",
            ),
        )

    def choose_action(self, state):
        return Action(
            tool="task.complete",
            arguments={
                "task": state.request.task,
                "project_memory_count": len(state.context.project_memory),
                "organization_memory_count": len(
                    state.context.organization_memory
                ),
            },
            rationale="依据注入的 Skill、项目约定和长期记忆完成任务",
        )

    def reflect(self, state, observation):
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="受控工具返回有效结果",
            final_answer=str(observation.output),
        )


def run_demo(runtime_root: Path) -> int:
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_namespace = MemoryNamespace(MemoryScope.PROJECT, "agent-platform")
    organization_namespace = MemoryNamespace(
        MemoryScope.ORGANIZATION,
        "research-team",
    )
    long_term_memory = LongTermMemory(
        store=JsonLongTermMemoryStore(runtime_root / "long-term-memory.json"),
        clock=lambda: FIXED_TIME,
    )
    long_term_memory.upsert(
        project_namespace,
        key="decision.interface-language",
        category=LongTermMemoryCategory.DECISION,
        content="公共接口使用稳定英文命名",
        source="project_decision",
    )
    long_term_memory.upsert(
        organization_namespace,
        key="rule.source-attribution",
        category=LongTermMemoryCategory.CONVENTION,
        content="外部事实必须保留来源和时间",
        source="organization_policy",
    )
    context_injector = ContextInjector(
        memory=long_term_memory,
        project_namespace=project_namespace,
        organization_namespace=organization_namespace,
        project_memory_keys=("decision.interface-language",),
        organization_memory_keys=("rule.source-attribution",),
        skills=(
            SkillContext(
                name="controlled-task",
                content="只调用 ToolRegistry 中已注册的工具",
                source="demo",
            ),
        ),
        project_instructions=("真实交易保持关闭",),
    )

    def runner_factory(workspace):
        return CognitiveLoopRunner(
            agent=ScheduledContextAgent(),
            tools=ToolRegistry([CompleteTaskTool()]),
            context_injector=context_injector,
            workspace=workspace,
            working_memory=WorkingMemory(capacity=8),
            memory_store=JsonWorkingMemoryStore(
                workspace.resolve("working-memory.json")
            ),
            max_steps=2,
        )

    dispatcher = LoopDispatcher(
        handler=CognitiveLoopTaskHandler(runner_factory),
        workspace_manager=TaskWorkspaceManager(runtime_root / "tasks"),
        ledger=LoopRunLedger(JsonLoopRunStore(runtime_root / "run-ledger.json")),
        clock=lambda: FIXED_TIME,
    )

    heartbeat_record = HeartbeatLoop(
        dispatcher,
        interval_seconds=60,
        anchor=datetime.fromisoformat("2026-08-05T11:00:00+08:00"),
    ).tick(
        task_id="heartbeat-check",
        request=AgentRequest(task="执行 Heartbeat 健康检查"),
        now=FIXED_TIME,
    )
    cron_record = CronLoop(
        dispatcher,
        expression=CronExpression("0 12 * * *"),
    ).tick(
        task_id="cron-report",
        request=AgentRequest(task="生成 Cron 定时报告"),
        now=FIXED_TIME,
    )
    hook_records = HookLoop(
        dispatcher,
        subscriptions=(
            HookSubscription(
                hook_id="data-ready",
                event_name="data.ready",
                task_id="hook-analysis",
                task="处理 Hook 数据就绪事件",
            ),
        ),
    ).emit(
        HookEvent(
            event_id="demo-event-001",
            name="data.ready",
            payload={"dataset": "offline-demo"},
            occurred_at=FIXED_TIME,
        )
    )

    def decompose(goal, depth):
        if goal == "生成递归目标总结":
            return ("读取项目长期记忆", "读取组织长期记忆")
        return ()

    goal_result = GoalLoop(
        dispatcher,
        max_depth=2,
        max_goals=5,
    ).run(
        run_id="demo-goal-001",
        root_goal="生成递归目标总结",
        decompose=decompose,
    )

    project_count = len(long_term_memory.query(project_namespace))
    organization_count = len(long_term_memory.query(organization_namespace))
    records = dispatcher.ledger.records
    workspaces = {record.workspace for record in records}
    print("=== A3 Loop Engineering 离线演示 ===")
    print(f"长期记忆: project={project_count}, organization={organization_count}")
    print("Heartbeat:", heartbeat_record.status.value)
    print("Cron:", cron_record.status.value if cron_record else "not_due")
    print("Hook:", len(hook_records), "run")
    print("Goal:", len(goal_result.records), "runs")
    print("运行台账:", len(records))
    print("独立工作目录:", len(workspaces))
    print("运行目录:", runtime_root.resolve())
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 A3 Loop Engineering 演示。")
    parser.add_argument(
        "--runtime",
        type=Path,
        default=PROJECT_ROOT / ".runtime" / "a3-loop-engineering",
    )
    return parser.parse_args()


def main() -> int:
    return run_demo(parse_args().runtime)


if __name__ == "__main__":
    raise SystemExit(main())
