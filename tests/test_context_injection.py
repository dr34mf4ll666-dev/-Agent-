import sys
import unittest
from datetime import datetime
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    Action,
    AgentRequest,
    CognitiveContractError,
    CognitiveLoopRunner,
    Plan,
    Reflection,
    ReflectionDecision,
    ToolRegistry,
)
from agent_platform.core.context_injection import (
    ContextInjectionError,
    ContextInjector,
    SkillContext,
)
from agent_platform.core.long_term_memory import (
    InMemoryLongTermMemoryStore,
    LongTermMemory,
    LongTermMemoryCategory,
    MemoryNamespace,
    MemoryScope,
)


FIXED_TIME = datetime.fromisoformat("2026-08-05T12:00:00+08:00")


class AddTool:
    name = "math.add"

    def run(self, arguments):
        return arguments["left"] + arguments["right"]


class ContextAwareAgent:
    name = "context-aware"

    def __init__(self):
        self.planning_context = None
        self.action_context = None
        self.reflection_context = None

    def create_plan(self, request):
        self.planning_context = request.context["injected_context"]
        return Plan(goal=request.task, steps=("读取注入上下文", "调用工具"))

    def choose_action(self, state):
        self.action_context = state.context
        return Action(tool="math.add", arguments={"left": 1, "right": 2})

    def reflect(self, state, observation):
        self.reflection_context = state.context
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="已读取上下文并完成",
            final_answer=f"结果是 {observation.output}",
        )


class ContextInjectionTests(unittest.TestCase):
    def setUp(self):
        self.memory = LongTermMemory(
            store=InMemoryLongTermMemoryStore(),
            clock=lambda: FIXED_TIME,
        )
        self.project = MemoryNamespace(MemoryScope.PROJECT, "agent-platform")
        self.organization = MemoryNamespace(
            MemoryScope.ORGANIZATION,
            "research-team",
        )
        self.memory.upsert(
            self.project,
            key="decision.language",
            category=LongTermMemoryCategory.DECISION,
            content="公共接口使用英文命名",
            source="project_decision",
        )
        self.memory.upsert(
            self.project,
            key="secret.unselected",
            category=LongTermMemoryCategory.FACT,
            content="不应自动注入的内容",
            source="test",
        )
        self.memory.upsert(
            self.organization,
            key="rule.sources",
            category=LongTermMemoryCategory.CONVENTION,
            content="外部事实必须标明来源",
            source="organization_policy",
        )

    def test_builds_read_only_context_from_explicit_sources(self):
        injector = ContextInjector(
            memory=self.memory,
            project_namespace=self.project,
            organization_namespace=self.organization,
            project_memory_keys=("decision.language",),
            organization_memory_keys=("rule.sources",),
            skills=(
                SkillContext(
                    name="calculator",
                    content="只使用已注册的确定性计算工具",
                    source="Skill/calculator.md",
                ),
            ),
            project_instructions=("真实交易保持关闭",),
            task_context={"request_id": "base"},
        )

        context = injector.build(
            AgentRequest(
                task="计算 1 + 2",
                context={
                    "request_id": "task-1",
                    "priority": "normal",
                    "nested": {"value": 1},
                },
            )
        )

        self.assertEqual(context.skills[0].name, "calculator")
        self.assertEqual(context.project_instructions, ("真实交易保持关闭",))
        self.assertEqual(context.task_context["request_id"], "task-1")
        self.assertEqual(context.task_context["priority"], "normal")
        self.assertEqual(
            tuple(item.key for item in context.project_memory),
            ("decision.language",),
        )
        self.assertEqual(
            tuple(item.key for item in context.organization_memory),
            ("rule.sources",),
        )
        with self.assertRaises(TypeError):
            context.task_context["new"] = "blocked"
        with self.assertRaises(TypeError):
            context.task_context["nested"]["value"] = 2

    def test_rejects_scope_mismatch(self):
        with self.assertRaises(ContextInjectionError):
            ContextInjector(
                memory=self.memory,
                project_namespace=self.organization,
            )

    def test_cognitive_loop_rejects_reserved_context_keys(self):
        agent = ContextAwareAgent()
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([AddTool()]),
            max_steps=1,
        )

        with self.assertRaises(CognitiveContractError) as raised:
            runner.run(
                AgentRequest(
                    task="冲突请求",
                    context={"injected_context": "forged"},
                )
            )
        self.assertIn("reserved keys", str(raised.exception))

    def test_cognitive_loop_exposes_same_context_to_action_and_reflection(self):
        injector = ContextInjector(
            memory=self.memory,
            project_namespace=self.project,
            organization_namespace=self.organization,
            project_memory_keys=("decision.language",),
            organization_memory_keys=("rule.sources",),
            skills=(
                SkillContext(
                    name="calculator",
                    content="使用 math.add",
                    source="test",
                ),
            ),
            project_instructions=("输出必须可验证",),
        )
        agent = ContextAwareAgent()
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([AddTool()]),
            context_injector=injector,
            max_steps=1,
        )

        result = runner.run(
            AgentRequest(task="计算 1 + 2", context={"task_id": "ctx-1"})
        )

        self.assertEqual(result.response.content, "结果是 3")
        self.assertIs(agent.planning_context, agent.action_context)
        self.assertIs(agent.action_context, agent.reflection_context)
        self.assertEqual(agent.action_context.task_context["task_id"], "ctx-1")
        self.assertEqual(len(agent.action_context.project_memory), 1)
        self.assertEqual(len(agent.action_context.organization_memory), 1)


if __name__ == "__main__":
    unittest.main()
