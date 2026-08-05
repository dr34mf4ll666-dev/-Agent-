import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    Action,
    AgentRequest,
    CognitiveLoopRunner,
    InMemoryWorkingMemoryStore,
    JsonWorkingMemoryStore,
    MemoryKind,
    Plan,
    Reflection,
    ReflectionDecision,
    ToolRegistry,
    WorkingMemory,
    WorkingMemoryContractError,
    WorkingMemorySnapshotError,
)


class AddTool:
    name = "math.add"

    def run(self, arguments):
        return arguments["left"] + arguments["right"]


class MemoryAwareAgent:
    name = "memory_aware"

    def __init__(self):
        self.action_memory_kinds = ()
        self.reflection_memory_kinds = ()

    def create_plan(self, request):
        return Plan(goal=request.task, steps=("调用加法工具", "检查结果"))

    def choose_action(self, state):
        self.action_memory_kinds = tuple(
            entry.kind for entry in state.memory.entries
        )
        return Action(tool="math.add", arguments={"left": 4, "right": 5})

    def reflect(self, state, observation):
        self.reflection_memory_kinds = tuple(
            entry.kind for entry in state.memory.entries
        )
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="记忆中的工具结果已经满足目标",
            final_answer=f"结果是 {observation.output}",
        )


class WorkingMemoryTests(unittest.TestCase):
    def test_appends_entries_and_evicts_oldest_in_fifo_order(self):
        memory = WorkingMemory(capacity=3)

        memory.append(MemoryKind.PLAN, "plan", step=0)
        memory.append(MemoryKind.ACTION, "action", step=1)
        memory.append(MemoryKind.OBSERVATION, "observation", step=1)
        memory.append(MemoryKind.REFLECTION, "reflection", step=1)

        view = memory.view()
        self.assertEqual(view.capacity, 3)
        self.assertEqual(view.dropped_count, 1)
        self.assertEqual(
            tuple(entry.kind for entry in view.entries),
            (
                MemoryKind.ACTION,
                MemoryKind.OBSERVATION,
                MemoryKind.REFLECTION,
            ),
        )
        self.assertEqual(tuple(entry.sequence for entry in view.entries), (2, 3, 4))

    def test_rejects_invalid_entries_and_non_json_data(self):
        memory = WorkingMemory(capacity=2)

        with self.assertRaises(WorkingMemoryContractError):
            memory.append(MemoryKind.PLAN, "", step=0)
        with self.assertRaises(WorkingMemoryContractError):
            memory.append(
                MemoryKind.ACTION,
                "bad data",
                step=1,
                data={"value": object()},
            )
        with self.assertRaises(ValueError):
            WorkingMemory(capacity=0)

    def test_json_snapshot_round_trip_restores_sequence_and_capacity(self):
        memory = WorkingMemory(capacity=2)
        memory.append(MemoryKind.PLAN, "plan", step=0, data={"steps": ["a"]})
        memory.append(MemoryKind.ACTION, "action", step=1, data={"tool": "math.add"})
        memory.append(MemoryKind.OBSERVATION, "observation", step=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonWorkingMemoryStore(Path(temp_dir) / "memory.json")
            store.save(memory.snapshot())
            loaded = store.load()
            restored = WorkingMemory.restore(loaded)

        self.assertEqual(restored.capacity, 2)
        self.assertEqual(restored.view().dropped_count, 1)
        self.assertEqual(
            tuple(entry.sequence for entry in restored.view().entries),
            (2, 3),
        )
        next_entry = restored.append(MemoryKind.REFLECTION, "continue", step=1)
        self.assertEqual(next_entry.sequence, 4)

    def test_json_store_rejects_corrupted_or_incompatible_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.json"
            store = JsonWorkingMemoryStore(path)

            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(WorkingMemorySnapshotError):
                store.load()

            path.write_text(
                json.dumps(
                    {
                        "version": True,
                        "capacity": 2,
                        "next_sequence": 1,
                        "dropped_count": 0,
                        "entries": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(WorkingMemorySnapshotError):
                store.load()

            path.write_text(
                json.dumps(
                    {
                        "version": 99,
                        "capacity": 2,
                        "next_sequence": 1,
                        "dropped_count": 0,
                        "entries": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(WorkingMemorySnapshotError):
                store.load()

    def test_loop_writes_four_kinds_and_agents_read_current_memory_view(self):
        agent = MemoryAwareAgent()
        memory = WorkingMemory(capacity=10)
        store = InMemoryWorkingMemoryStore()
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([AddTool()]),
            working_memory=memory,
            memory_store=store,
            max_steps=2,
        )

        result = runner.run(AgentRequest(task="计算 4 + 5"))

        self.assertEqual(agent.action_memory_kinds, (MemoryKind.PLAN,))
        self.assertEqual(
            agent.reflection_memory_kinds,
            (MemoryKind.PLAN, MemoryKind.ACTION, MemoryKind.OBSERVATION),
        )
        self.assertEqual(
            tuple(entry.kind for entry in result.state.memory.entries),
            (
                MemoryKind.PLAN,
                MemoryKind.ACTION,
                MemoryKind.OBSERVATION,
                MemoryKind.REFLECTION,
            ),
        )
        self.assertEqual(store.save_count, 4)
        restored = WorkingMemory.restore(store.load())
        self.assertEqual(restored.view(), result.state.memory)

    def test_loop_memory_capacity_is_enforced_without_breaking_completion(self):
        agent = MemoryAwareAgent()
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([AddTool()]),
            working_memory=WorkingMemory(capacity=3),
            max_steps=1,
        )

        result = runner.run(AgentRequest(task="计算 4 + 5"))

        self.assertEqual(result.response.content, "结果是 9")
        self.assertEqual(result.state.memory.dropped_count, 1)
        self.assertEqual(
            tuple(entry.kind for entry in result.state.memory.entries),
            (
                MemoryKind.ACTION,
                MemoryKind.OBSERVATION,
                MemoryKind.REFLECTION,
            ),
        )


if __name__ == "__main__":
    unittest.main()
