# Agent default-model menu fix

## Issue

New local agents did not preselect the model configured as the chat default, and model choices could appear in endpoint discovery order.

Fixed in version: **0.261.065**

## Root cause

The agent-settings API did not expose `default_model_selection`, and the shared dropdown builder did not use that setting or order its model list.

## Technical details

- [route_backend_agents.py](../../../application/single_app/route_backend_agents.py) now returns the configured chat default with agent model settings.
- [agents_common.js](../../../application/single_app/static/js/agents_common.js) selects that endpoint-and-model pair for new local agents while preserving an existing agent's explicit binding.
- The same dropdown builder sorts labels with case-insensitive numeric-aware ordering before rendering options.

## Validation

[functional_tests/test_agent_modal_model_endpoint_filtering.py](../../../functional_tests/test_agent_modal_model_endpoint_filtering.py) verifies the API wiring, chat-default selection, and alphanumeric sort behavior.
