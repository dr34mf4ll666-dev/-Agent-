"""Official MCP Python SDK entrypoint for the B1 financial data tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .data_hub import (
    SUPPORTED_FINANCIAL_DATASETS,
    FinancialDataTool,
    build_default_financial_data_tool,
)


def create_financial_mcp_server(
    tool: FinancialDataTool | None = None,
    *,
    project_root: str | Path | None = None,
):
    """Create a stdio/HTTP-capable MCP server without starting it."""

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError(
            "the optional mcp dependency is required for the financial MCP server"
        ) from error

    financial_tool = tool or build_default_financial_data_tool(
        project_root=project_root
    )
    server = FastMCP(
        "agent-platform-financial-data",
        instructions=(
            "Read-only financial data tools. Live trading is unavailable. "
            "Use offline mode for deterministic replay and live mode explicitly."
        ),
        json_response=True,
    )

    @server.tool(structured_output=True)
    def list_financial_datasets() -> dict[str, Any]:
        """List the read-only datasets exposed by this B1 server."""

        return {
            "datasets": list(SUPPORTED_FINANCIAL_DATASETS),
            "default_mode": "offline",
            "live_trading": False,
        }

    @server.tool(structured_output=True)
    def get_financial_data(
        dataset: str,
        params: dict[str, Any] | None = None,
        mode: Literal["offline", "live"] = "offline",
    ) -> dict[str, Any]:
        """Fetch one provenance-bearing dataset through cache and guardrails."""

        return financial_tool.run(
            {
                "dataset": dataset,
                "params": params or {},
                "mode": mode,
            }
        )

    return server


def main() -> None:
    create_financial_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
