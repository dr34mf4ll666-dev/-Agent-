"""P7 LLM governance demo: versions, cache, budget and safe release boundary."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.core import MockModelAdapter, ModelGateway, ModelRequest, ModelUsage  # noqa: E402
from agent_platform.llm_governance import (  # noqa: E402
    GovernanceBudgetExceeded,
    GovernancePolicy,
    ModelGovernanceRuntime,
)


def main() -> int:
    adapter = MockModelAdapter(
        content="safe governance answer",
        usage=ModelUsage(input_tokens=8, output_tokens=4, total_tokens=12),
    )
    governance = ModelGovernanceRuntime(
        ModelGateway(adapter),
        policy=GovernancePolicy(
            policy_version="p7-demo-policy-v1",
            prompt_version="p7-demo-prompt-v1",
            schema_version="p7-demo-schema-v1",
            route="scripted_mock",
            max_calls=1,
            max_total_tokens=100,
            max_output_tokens=30,
            cache_ttl_seconds=60,
        ),
    )
    request = ModelRequest(prompt="验证 P7 治理", max_output_tokens=20)
    first = governance.generate(request, operation="p7_demo")
    cached = governance.generate(request, operation="p7_demo")
    try:
        governance.generate(ModelRequest(prompt="第二个不同请求", max_output_tokens=20))
        budget = "unexpected_pass"
    except GovernanceBudgetExceeded as error:
        budget = str(error)

    print("=== P7 LLM 治理演示 ===")
    print(f"policy_version: {first.governance['policy_version']}")
    print(f"prompt_version: {first.governance['prompt_version']}")
    print(f"schema_version: {first.governance['schema_version']}")
    print(f"first_call_cache_hit: {first.governance['cache_hit']}")
    print(f"second_call_cache_hit: {cached.governance['cache_hit']}")
    print(f"provider_calls: {adapter.calls}")
    print(f"budget_rejection: {budget}")
    print(f"snapshot: {governance.snapshot()}")
    print("safety: model_only_explains=true; deterministic_finance_controls_unchanged=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
