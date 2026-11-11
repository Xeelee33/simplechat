# test_agent_modal_model_endpoint_filtering.py
"""
Functional test for agent modal model endpoint filtering.
Version: 0.261.065
Implemented in: 0.236.056; updated in 0.261.065

This test ensures the agent modal dropdown includes non-AOAI providers for local
agents, defaults new local agents to the chat default model, orders options
alphanumerically, normalizes model IDs/display names when building the model
list, and does not fall back to legacy global model settings when governed
multi-endpoint settings return no available endpoints.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def test_agent_modal_model_endpoint_filtering():
    """Validate agent modal dropdown logic for model endpoints."""
    print("🔍 Validating agent modal model endpoint filtering...")

    try:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        js_path = os.path.join(
            repo_root,
            "application",
            "single_app",
            "static",
            "js",
            "agents_common.js",
        )

        if not os.path.exists(js_path):
            raise FileNotFoundError(f"agents_common.js not found at {js_path}")

        with open(js_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        agent_route_path = os.path.join(
            repo_root,
            "application",
            "single_app",
            "route_backend_agents.py",
        )
        with open(agent_route_path, "r", encoding="utf-8") as handle:
            agent_route_content = handle.read()

        required_snippets = [
            "agentType === 'aifoundry' && provider !== 'aifoundry'",
            "const modelId = model.id",
            "const displayName = model.displayName",
            "display_name: displayName",
            "if (multiEndpointEnabled) {",
            "const defaultModelSelection = settings?.default_model_selection;",
            "endpoint_id: defaultModelSelection.endpoint_id",
            "model_id: defaultModelSelection.model_id",
            "const sortedModels = [...models].sort",
            "localeCompare(rightLabel, undefined, { numeric: true, sensitivity: 'base' })",
            "return { models, selectedModel };\n\t}\n\tif (apimEnabled)",
        ]

        missing = [snippet for snippet in required_snippets if snippet not in content]
        if missing:
            raise AssertionError(
                f"Missing expected filtering/default/sorting snippets: {', '.join(missing)}"
            )

        assert '"default_model_selection": settings.get("default_model_selection", {})' in agent_route_content, (
            "Expected agent settings API to provide the chat default model selection."
        )

        print("✅ Agent modal model filtering, default selection, and sorting logic present.")
        return True

    except Exception as exc:
        print(f"❌ Test failed: {exc}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_agent_modal_model_endpoint_filtering()
    sys.exit(0 if success else 1)
