# Content Safety Block Threshold

Implemented in version: **0.261.058**

## Overview

The Content Safety block threshold lets administrators choose the Azure AI Content Safety prompt severity that blocks a chat request and records a safety violation. The setting is useful when deployments need different safety postures: a youth-focused chatbot can block lower-severity prompts, while investigative or operational teams can allow higher-severity prompts that are expected in their work.

## Dependencies

- Azure AI Content Safety must be enabled in Admin Settings.
- The Content Safety endpoint must be configured directly or through Azure API Management.
- Safety violation logging uses the existing Cosmos DB settings and safety containers.

## Technical specifications

### Architecture

- The admin setting is stored in the Cosmos DB settings document as `content_safety_block_threshold`.
- The default threshold is `4`, preserving the previous block behavior.
- Accepted values are integers from `2` through `7`.
- Chat requests are blocked and recorded when any returned Content Safety category severity is greater than or equal to the configured threshold.
- Blocklist matches continue to block independently of the severity threshold.

### Files

- `application/single_app/functions_content_safety.py`
- `application/single_app/functions_settings.py`
- `application/single_app/route_frontend_admin_settings.py`
- `application/single_app/route_backend_chats.py`
- `application/single_app/templates/admin/_panes/content-safety.html`
- `application/single_app/static/js/admin/admin_settings.js`

## Usage instructions

1. Open Admin Settings and select the Content Safety tab.
2. Enable Content Safety and configure the endpoint or APIM route.
3. Move the **Prompt Block Severity Threshold** slider to the desired severity from `2` to `7`.
4. Save settings.

A lower value blocks more prompts. A higher value blocks only more severe prompts.

## Testing and validation

- `functional_tests/test_content_safety_violation_message.py` validates threshold normalization and admin-template coverage.
- `ui_tests/test_admin_content_safety_threshold_ui.py` validates the slider and live badge wiring.

## Known limitations

The threshold applies to prompt checks in the existing chat Content Safety flow. It does not change blocklist behavior, which always blocks when Azure Content Safety reports a configured blocklist match.
