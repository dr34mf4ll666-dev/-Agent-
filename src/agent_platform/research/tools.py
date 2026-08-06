"""Controlled tools used by the non-financial research workflow."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import ResearchContractError, ResearchDocument, require_text


_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")


class LocalDocumentSearchTool:
    """Deterministically rank a fixed, local, provenance-bearing corpus."""

    name = "local_document_search"

    def __init__(
        self,
        documents: Iterable[ResearchDocument],
        *,
        default_limit: int = 3,
    ) -> None:
        self._documents = tuple(documents)
        if not self._documents:
            raise ResearchContractError("documents must not be empty")
        if isinstance(default_limit, bool) or not isinstance(default_limit, int):
            raise ResearchContractError("default_limit must be an integer")
        if default_limit < 1:
            raise ResearchContractError("default_limit must be at least 1")
        ids = [document.document_id for document in self._documents]
        if len(ids) != len(set(ids)):
            raise ResearchContractError("document_id values must be unique")
        self._default_limit = default_limit

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise ResearchContractError("search arguments must be an object")
        query = require_text(arguments.get("query"), "query")
        limit = arguments.get("limit", self._default_limit)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 10
        ):
            raise ResearchContractError("limit must be an integer from 1 to 10")

        terms = tuple(dict.fromkeys(item.casefold() for item in _TERM_PATTERN.findall(query)))
        ranked: list[tuple[int, ResearchDocument]] = []
        for document in self._documents:
            title = document.title.casefold()
            content = document.content.casefold()
            score = sum(3 for term in terms if term in title)
            score += sum(1 for term in terms if term in content)
            if score:
                ranked.append((score, document))
        ranked.sort(key=lambda item: (-item[0], item[1].document_id))

        results = [
            {
                "document_id": document.document_id,
                "title": document.title,
                "excerpt": document.content,
                "score": score,
                "source": document.source,
                "timestamp": document.timestamp,
                "as_of": document.as_of,
            }
            for score, document in ranked[:limit]
        ]
        return {
            "query": query,
            "result_count": len(results),
            "results": results,
        }
