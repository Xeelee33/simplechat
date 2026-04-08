# Content Safety False Positive Allowlist Fix

## Issue Description

Certain benign prompts (for example, "Jaws") could be blocked by Azure Content Safety with high severity due to lexical confusion.

## Root Cause Analysis

SimpleChat previously blocked prompts when `max_severity >= 4` or when blocklist matches were present, without an app-level false-positive override mechanism.

## Version Implemented

Fixed/Implemented in version: **0.240.067**

Related config version update:

- `application/single_app/config.py` → `VERSION = "0.240.067"`

## Technical Details

### Files Modified

- `application/single_app/route_backend_chats.py`
- `application/single_app/functions_settings.py`
- `functional_tests/test_content_safety_allowlist_override.py`

### Code Changes Summary

- Added strict allowlist override helper for chat content safety checks.
- Added default settings:
  - `content_safety_false_positive_allowlist`
  - `content_safety_false_positive_allowlist_categories`
- Applied override logic in both non-streaming and streaming chat paths.

### Safety Constraints

Override is only applied when all of the following are true:

- No Content Safety blocklist matches exist.
- Prompt includes exact word-boundary match for one or more allowlist terms.
- All severe categories (`severity >= 4`) are within configured allowlist categories (default `Hate`).

## Validation

### Test Results

- `py -m py_compile application/single_app/route_backend_chats.py application/single_app/functions_settings.py application/single_app/config.py`
- `py functional_tests/test_content_safety_allowlist_override.py`

### Before/After Comparison

- **Before**: High-severity lexical false positives were always blocked.
- **After**: Qualified false positives can pass through with explicit, constrained allowlist override logic and audit logging.
