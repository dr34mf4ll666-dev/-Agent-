"""Mermaid renderer for static and runtime graph state."""

from __future__ import annotations

import re

from .graph import GraphDefinition, GraphResult


class GraphVisualizer:
    """Render a GraphDefinition and optional GraphResult as Mermaid text."""

    def render_mermaid(
        self,
        graph: GraphDefinition,
        *,
        result: GraphResult | None = None,
    ) -> str:
        ids = self._node_ids(graph)
        lines = ["flowchart TD"]
        for name in graph.nodes:
            label = _escape(name)
            lines.append(f'    {ids[name]}["{label}"]')
        for edge in graph.edges:
            label = _escape(edge.condition_label)
            arrow = f" -->|{label}| " if label else " --> "
            lines.append(
                f"    {ids[edge.source]}{arrow}{ids[edge.target]}"
            )

        lines.extend(
            [
                "    classDef pending fill:#f3f4f6,stroke:#6b7280,color:#111827",
                "    classDef completed fill:#dcfce7,stroke:#16a34a,color:#14532d",
                "    classDef skipped fill:#fef3c7,stroke:#d97706,color:#78350f",
                "    classDef failed fill:#fee2e2,stroke:#dc2626,color:#7f1d1d",
            ]
        )
        if result is not None:
            for status in ("pending", "completed", "skipped", "failed"):
                members = [
                    ids[name]
                    for name in graph.nodes
                    if result.statuses.get(name, "pending") == status
                ]
                if members:
                    lines.append(f"    class {','.join(members)} {status}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _node_ids(graph: GraphDefinition) -> dict[str, str]:
        used: set[str] = set()
        ids: dict[str, str] = {}
        for index, name in enumerate(graph.nodes):
            candidate = re.sub(r"[^A-Za-z0-9_]", "_", name)
            if not candidate or candidate[0].isdigit():
                candidate = f"node_{index}_{candidate}"
            while candidate in used:
                candidate = f"{candidate}_{index}"
            ids[name] = candidate
            used.add(candidate)
        return ids


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "&quot;").replace("|", "&#124;")
