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
    Plan,
    Reflection,
    ReflectionDecision,
    ToolRegistry,
)
from agent_platform.core.task_workspace import (
    TaskWorkspaceError,
    TaskWorkspaceManager,
)


class EchoTool:
    name = "text.echo"

    def run(self, arguments):
        return arguments["text"]


class WorkspaceAwareAgent:
    name = "workspace-aware"

    def __init__(self):
        self.planning_workspace = None
        self.action_workspace = None

    def create_plan(self, request):
        self.planning_workspace = request.context["task_workspace"]
        return Plan(goal=request.task, steps=("读取工作目录", "完成"))

    def choose_action(self, state):
        self.action_workspace = state.workspace
        return Action(tool="text.echo", arguments={"text": "done"})

    def reflect(self, state, observation):
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="工作目录已隔离",
            final_answer=observation.output,
        )


class TaskWorkspaceTests(unittest.TestCase):
    def test_tasks_use_separate_persistent_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TaskWorkspaceManager(Path(temp_dir) / "tasks")
            first = manager.open("task-a")
            second = manager.open("task-b")
            first.resolve("result.txt").write_text("A", encoding="utf-8")
            second.resolve("result.txt").write_text("B", encoding="utf-8")

            reopened = manager.open("task-a")

            self.assertNotEqual(first.path, second.path)
            self.assertEqual(reopened.path, first.path)
            self.assertEqual(
                reopened.resolve("result.txt").read_text(encoding="utf-8"),
                "A",
            )
            self.assertEqual(
                second.resolve("result.txt").read_text(encoding="utf-8"),
                "B",
            )

    def test_rejects_task_and_relative_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TaskWorkspaceManager(Path(temp_dir) / "tasks")
            with self.assertRaises(TaskWorkspaceError):
                manager.open("../escape")
            workspace = manager.open("safe-task")
            with self.assertRaises(TaskWorkspaceError):
                workspace.resolve("../escape.txt")
            with self.assertRaises(TaskWorkspaceError):
                workspace.resolve(Path(temp_dir) / "absolute.txt")

    def test_cognitive_loop_exposes_workspace_to_plan_and_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = TaskWorkspaceManager(Path(temp_dir) / "tasks").open(
                "loop-task"
            )
            agent = WorkspaceAwareAgent()
            result = CognitiveLoopRunner(
                agent=agent,
                tools=ToolRegistry([EchoTool()]),
                workspace=workspace,
                max_steps=1,
            ).run(AgentRequest(task="完成隔离任务"))

        self.assertEqual(result.response.content, "done")
        self.assertEqual(agent.planning_workspace, str(workspace.path))
        self.assertIs(agent.action_workspace, workspace)
        self.assertIs(result.state.workspace, workspace)


if __name__ == "__main__":
    unittest.main()
