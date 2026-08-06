"""Non-financial reference integration built on the generic Agent platform."""

from .agents import (
    GatewayResearchPlanner,
    GatewayResearchReporter,
    build_offline_research_gateway,
)
from .contracts import ResearchContractError, ResearchDocument
from .tools import LocalDocumentSearchTool
from .workflow import (
    NonFinancialResearchRuntime,
    load_documents,
    organize_evidence,
    validate_report_citations,
)

__all__ = [
    "GatewayResearchPlanner",
    "GatewayResearchReporter",
    "LocalDocumentSearchTool",
    "NonFinancialResearchRuntime",
    "ResearchContractError",
    "ResearchDocument",
    "build_offline_research_gateway",
    "load_documents",
    "organize_evidence",
    "validate_report_citations",
]
