import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import AgentRequest
from agent_platform.core.loop_scheduling import (
    CronExpression,
    CronLoop,
    GoalLoop,
    GoalLoopLimitError,
    HeartbeatLoop,
    HookEvent,
    HookLoop,
    HookSubscription,
    InMemoryLoopRunStore,
    JsonLoopRunStore,
    LoopDispatcher,
    LoopRunLedger,
    LoopRunSnapshotError,
    LoopRunStatus,
    LoopTaskResult,
)
from agent_platform.core.task_workspace import TaskWorkspaceManager


FIXED_TIME = datetime.fromisoformat("2026-08-05T12:00:00+08:00")


class RecordingHandler:
    def __init__(self):
        self.invocations = []

    def __call__(self, invocation):
        self.invocations.append(invocation)
        return LoopTaskResult(
            content=f"completed: {invocation.request.task}",
            metadata={"trigger": invocation.trigger},
        )


class FailingHandler:
    def __call__(self, invocation):
        raise RuntimeError("controlled failure")


class LoopSchedulingTests(unittest.TestCase):
    def _dispatcher(self, root, handler, store=None):
        return LoopDispatcher(
            handler=handler,
            workspace_manager=TaskWorkspaceManager(Path(root) / "tasks"),
            ledger=LoopRunLedger(store or InMemoryLoopRunStore()),
            clock=lambda: FIXED_TIME,
        )

    def test_heartbeat_runs_once_per_slot_and_resumes_from_json_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "runs.json"
            handler = RecordingHandler()
            dispatcher = self._dispatcher(
                temp_dir,
                handler,
                JsonLoopRunStore(store_path),
            )
            heartbeat = HeartbeatLoop(
                dispatcher,
                interval_seconds=60,
                anchor=datetime.fromisoformat("2026-08-05T11:00:00+08:00"),
            )

            first = heartbeat.tick(
                task_id="health-check",
                request=AgentRequest(task="检查服务状态"),
                now=datetime.fromisoformat("2026-08-05T12:00:30+08:00"),
            )
            repeated = heartbeat.tick(
                task_id="health-check",
                request=AgentRequest(task="检查服务状态"),
                now=datetime.fromisoformat("2026-08-05T12:00:50+08:00"),
            )

            resumed_handler = RecordingHandler()
            resumed = HeartbeatLoop(
                self._dispatcher(
                    temp_dir,
                    resumed_handler,
                    JsonLoopRunStore(store_path),
                ),
                interval_seconds=60,
                anchor=datetime.fromisoformat("2026-08-05T11:00:00+08:00"),
            ).tick(
                task_id="health-check",
                request=AgentRequest(task="检查服务状态"),
                now=datetime.fromisoformat("2026-08-05T12:00:40+08:00"),
            )

            next_slot = heartbeat.tick(
                task_id="health-check",
                request=AgentRequest(task="检查服务状态"),
                now=datetime.fromisoformat("2026-08-05T12:01:00+08:00"),
            )

        self.assertEqual(first.status, LoopRunStatus.COMPLETED)
        self.assertEqual(repeated.run_id, first.run_id)
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(len(handler.invocations), 2)
        self.assertEqual(len(resumed_handler.invocations), 0)
        self.assertNotEqual(next_slot.run_id, first.run_id)

    def test_cron_matches_five_fields_and_deduplicates_the_same_minute(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = RecordingHandler()
            cron = CronLoop(
                self._dispatcher(temp_dir, handler),
                expression=CronExpression("*/15 9 * * 1-5"),
            )
            monday_0930 = datetime.fromisoformat("2026-08-03T09:30:10+08:00")

            first = cron.tick(
                task_id="market-scan",
                request=AgentRequest(task="扫描市场"),
                now=monday_0930,
            )
            repeated = cron.tick(
                task_id="market-scan",
                request=AgentRequest(task="扫描市场"),
                now=datetime.fromisoformat("2026-08-03T09:30:50+08:00"),
            )
            not_due = cron.tick(
                task_id="market-scan",
                request=AgentRequest(task="扫描市场"),
                now=datetime.fromisoformat("2026-08-03T09:31:00+08:00"),
            )

        self.assertIsNotNone(first)
        self.assertEqual(first.run_id, repeated.run_id)
        self.assertIsNone(not_due)
        self.assertEqual(len(handler.invocations), 1)
        with self.assertRaises(ValueError):
            CronExpression("61 * * * *")

    def test_hook_filters_events_and_is_idempotent_by_event_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = RecordingHandler()
            hook = HookLoop(
                self._dispatcher(temp_dir, handler),
                subscriptions=(
                    HookSubscription(
                        hook_id="price-analysis",
                        event_name="price.updated",
                        task_id="analyze-price",
                        task="分析新的价格事件",
                    ),
                ),
            )
            event = HookEvent(
                event_id="event-001",
                name="price.updated",
                payload={"symbol": "DEMO", "price": "10.50"},
                occurred_at=FIXED_TIME,
            )

            first = hook.emit(event)
            repeated = hook.emit(event)
            ignored = hook.emit(
                HookEvent(
                    event_id="event-002",
                    name="news.updated",
                    payload={},
                    occurred_at=FIXED_TIME,
                )
            )

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].run_id, repeated[0].run_id)
        self.assertEqual(ignored, ())
        self.assertEqual(len(handler.invocations), 1)
        injected_event = handler.invocations[0].request.context["hook_event"]
        self.assertEqual(injected_event["payload"]["symbol"], "DEMO")

    def test_recursive_goal_loop_runs_postorder_and_skips_completed_goals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = RecordingHandler()
            goal_loop = GoalLoop(
                self._dispatcher(temp_dir, handler),
                max_depth=2,
                max_goals=5,
            )

            def decompose(goal, depth):
                if goal == "完成报告":
                    return ("收集资料", "分析资料")
                return ()

            first = goal_loop.run(
                run_id="report-001",
                root_goal="完成报告",
                decompose=decompose,
            )
            repeated = goal_loop.run(
                run_id="report-001",
                root_goal="完成报告",
                decompose=decompose,
            )

        self.assertEqual(
            tuple(invocation.request.task for invocation in handler.invocations),
            ("收集资料", "分析资料", "完成报告"),
        )
        self.assertEqual(tuple(record.depth for record in first.records), (1, 1, 0))
        root_context = handler.invocations[-1].request.context["goal_context"]
        self.assertEqual(len(root_context["child_results"]), 2)
        self.assertEqual(
            tuple(record.run_id for record in repeated.records),
            tuple(record.run_id for record in first.records),
        )
        self.assertEqual(len(handler.invocations), 3)

    def test_goal_limits_and_handler_failures_stop_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            goal_loop = GoalLoop(
                self._dispatcher(temp_dir, RecordingHandler()),
                max_depth=1,
                max_goals=3,
            )

            with self.assertRaises(GoalLoopLimitError):
                goal_loop.run(
                    run_id="too-deep",
                    root_goal="root",
                    decompose=lambda goal, depth: ("child",),
                )

            failed = self._dispatcher(temp_dir, FailingHandler()).dispatch(
                task_id="expected-failure",
                request=AgentRequest(task="失败任务"),
                trigger="hook",
                dedupe_key="failure:1",
            )

        self.assertEqual(failed.status, LoopRunStatus.FAILED)
        self.assertIn("RuntimeError: controlled failure", failed.error)

    def test_json_ledger_rejects_corrupted_or_incompatible_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runs.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(LoopRunSnapshotError):
                LoopRunLedger(JsonLoopRunStore(path))

            path.write_text(
                json.dumps({"version": 99, "records": []}),
                encoding="utf-8",
            )
            with self.assertRaises(LoopRunSnapshotError):
                LoopRunLedger(JsonLoopRunStore(path))


if __name__ == "__main__":
    unittest.main()
