import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core.long_term_memory import (
    InMemoryLongTermMemoryStore,
    JsonLongTermMemoryStore,
    LongTermMemory,
    LongTermMemoryCategory,
    LongTermMemoryContractError,
    LongTermMemorySnapshotError,
    MemoryNamespace,
    MemoryScope,
)


class SequenceClock:
    def __init__(self, *values):
        self._values = iter(values)

    def __call__(self):
        return datetime.fromisoformat(next(self._values))


class LongTermMemoryTests(unittest.TestCase):
    def test_project_namespaces_are_strictly_isolated(self):
        memory = LongTermMemory(
            store=InMemoryLongTermMemoryStore(),
            clock=SequenceClock(
                "2026-08-05T10:00:00+08:00",
                "2026-08-05T10:01:00+08:00",
            ),
        )
        project_a = MemoryNamespace(MemoryScope.PROJECT, "project-a")
        project_b = MemoryNamespace(MemoryScope.PROJECT, "project-b")

        memory.upsert(
            project_a,
            key="language",
            category=LongTermMemoryCategory.FACT,
            content="项目使用 Python",
            source="user_confirmed",
        )
        memory.upsert(
            project_b,
            key="language",
            category=LongTermMemoryCategory.FACT,
            content="项目使用 Rust",
            source="user_confirmed",
        )

        self.assertEqual(
            tuple(entry.content for entry in memory.query(project_a)),
            ("项目使用 Python",),
        )
        self.assertEqual(
            tuple(entry.content for entry in memory.query(project_b)),
            ("项目使用 Rust",),
        )

    def test_upsert_preserves_creation_time_and_increments_revision(self):
        store = InMemoryLongTermMemoryStore()
        memory = LongTermMemory(
            store=store,
            clock=SequenceClock(
                "2026-08-05T10:00:00+08:00",
                "2026-08-05T11:00:00+08:00",
            ),
        )
        namespace = MemoryNamespace(MemoryScope.PROJECT, "agent-platform")

        first = memory.upsert(
            namespace,
            key="decision.memory",
            category=LongTermMemoryCategory.DECISION,
            content="工作记忆容量为 20",
            source="project_decision",
            data={"capacity": 20},
        )
        updated = memory.upsert(
            namespace,
            key="decision.memory",
            category=LongTermMemoryCategory.DECISION,
            content="工作记忆容量可配置，默认 20",
            source="project_decision",
            data={"capacity": 20, "configurable": True},
        )

        self.assertEqual(updated.created_at, first.created_at)
        self.assertGreater(updated.updated_at, first.updated_at)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(store.save_count, 2)
        self.assertEqual(len(memory.query(namespace)), 1)

    def test_query_filters_and_controlled_delete(self):
        memory = LongTermMemory(
            store=InMemoryLongTermMemoryStore(),
            clock=SequenceClock(
                "2026-08-05T10:00:00+08:00",
                "2026-08-05T10:01:00+08:00",
                "2026-08-05T10:02:00+08:00",
            ),
        )
        namespace = MemoryNamespace(MemoryScope.ORGANIZATION, "research-team")
        memory.upsert(
            namespace,
            key="rule.sources",
            category=LongTermMemoryCategory.CONVENTION,
            content="外部事实必须保留来源",
            source="organization_policy",
        )
        memory.upsert(
            namespace,
            key="fact.language",
            category=LongTermMemoryCategory.FACT,
            content="默认使用简体中文",
            source="organization_policy",
        )

        conventions = memory.query(
            namespace,
            category=LongTermMemoryCategory.CONVENTION,
            text="来源",
        )
        selected = memory.query(namespace, keys=("fact.language",))

        self.assertEqual(tuple(item.key for item in conventions), ("rule.sources",))
        self.assertEqual(tuple(item.key for item in selected), ("fact.language",))
        self.assertTrue(memory.delete(namespace, "fact.language"))
        self.assertFalse(memory.delete(namespace, "fact.language"))
        self.assertEqual(tuple(item.key for item in memory.query(namespace)), ("rule.sources",))

    def test_json_store_restores_project_and_organization_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "long-term-memory.json"
            store = JsonLongTermMemoryStore(path)
            memory = LongTermMemory(
                store=store,
                clock=SequenceClock(
                    "2026-08-05T10:00:00+08:00",
                    "2026-08-05T10:01:00+08:00",
                ),
            )
            project = MemoryNamespace(MemoryScope.PROJECT, "agent-platform")
            organization = MemoryNamespace(
                MemoryScope.ORGANIZATION,
                "research-team",
            )
            memory.upsert(
                project,
                key="artifact.readme",
                category=LongTermMemoryCategory.ARTIFACT,
                content="README.md",
                source="repository",
            )
            memory.upsert(
                organization,
                key="rule.language",
                category=LongTermMemoryCategory.CONVENTION,
                content="默认使用简体中文",
                source="organization_policy",
            )

            restored = LongTermMemory(
                store=JsonLongTermMemoryStore(path),
                clock=lambda: datetime.fromisoformat(
                    "2026-08-05T12:00:00+08:00"
                ),
            )

        self.assertEqual(restored.query(project)[0].content, "README.md")
        self.assertEqual(
            restored.query(organization)[0].content,
            "默认使用简体中文",
        )

    def test_rejects_invalid_contracts_and_corrupted_snapshots(self):
        with self.assertRaises(LongTermMemoryContractError):
            MemoryNamespace(MemoryScope.PROJECT, "../other-project")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(LongTermMemorySnapshotError):
                LongTermMemory(
                    store=JsonLongTermMemoryStore(path),
                    clock=lambda: datetime.fromisoformat(
                        "2026-08-05T12:00:00+08:00"
                    ),
                )

            path.write_text(
                json.dumps({"version": 99, "entries": []}),
                encoding="utf-8",
            )
            with self.assertRaises(LongTermMemorySnapshotError):
                LongTermMemory(
                    store=JsonLongTermMemoryStore(path),
                    clock=lambda: datetime.fromisoformat(
                        "2026-08-05T12:00:00+08:00"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
