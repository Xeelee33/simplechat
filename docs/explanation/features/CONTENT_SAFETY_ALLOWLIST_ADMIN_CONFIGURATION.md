# Content Safety Allowlist Admin Configuration

## Overview

This feature exposes admin-configurable Content Safety false-positive allowlist controls in the Safety settings tab.

Version implemented: **0.240.068**

Dependencies:

- Admin Settings page (`admin_settings.html`)
- Admin settings save route (`route_frontend_admin_settings.py`)
- Chat content safety evaluation path (`route_backend_chats.py`)

## Technical Specifications

### Configuration Options

Two new admin-configurable settings are available:

- `content_safety_false_positive_allowlist`
- `content_safety_false_positive_allowlist_categories`

Behavior:

- Input supports comma-separated and newline-separated values.
- Values are trimmed and deduplicated case-insensitively on save.
- If categories are omitted, fallback defaults to `Hate`.

### Safety Constraints

Allowlist override logic remains constrained in chat runtime:

- Exact word-boundary term matching only.
- No override when blocklist matches are present.
- Severe categories (`severity >= 4`) must all be in allowed categories.

## Usage Instructions

1. Open **Admin Settings > Safety**.
2. Enable Content Safety (if not already enabled).
3. Under **False-Positive Allowlist (Optional)**:
   - Add terms in **Allowlisted Terms**.
   - Configure **Allowed Categories for Override**.
4. Save settings.

## Testing and Validation

Functional tests:

- `functional_tests/test_content_safety_allowlist_override.py`
- `functional_tests/test_admin_content_safety_allowlist_settings.py`
