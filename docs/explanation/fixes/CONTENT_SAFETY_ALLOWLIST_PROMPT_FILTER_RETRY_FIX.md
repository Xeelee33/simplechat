# Content Safety Allowlist Prompt-Filter Retry Fix

## Issue Description

After a prompt was exempted by the app-level Content Safety false-positive allowlist, Azure OpenAI could still block the same prompt with a `content_filter` prompt policy violation.

## Root Cause Analysis

The app-level allowlist override only affected the Azure Content Safety gate. The downstream Azure OpenAI call still used the original prompt context and could independently apply prompt filtering.

## Version Implemented

Fixed/Implemented in version: **0.240.070**

Related config version update:

- `application/single_app/config.py` → `VERSION = "0.240.070"`

## Technical Details

### Files Modified

- `application/single_app/route_backend_chats.py`
- `functional_tests/test_content_safety_allowlist_prompt_filter_retry.py`

### Code Changes Summary

- Added prompt content-filter error classifier helper.
- Added helper to prepend a disambiguation system message for matched allowlist terms.
- Added one retry path for non-streaming GPT requests when:
  - allowlist override matched terms exist, and
  - Azure OpenAI returns prompt `content_filter` error.
- Added the same one-retry behavior for streaming GPT request creation.
- Added a second fallback retry for both paths when disambiguation retry still fails:
  - masks matched allowlist terms in the final user message with neutral placeholders,
  - prepends a moderation fallback system note,
  - retries once more while still honoring Azure OpenAI safety policies.

### Safety Constraints

- Retry is only attempted when allowlist terms were matched by the prior Content Safety override logic.
- Retry does not override blocklist matches.
- Retry includes disambiguation context and still relies on Azure OpenAI policy enforcement.

## Validation

### Test Results

- `py -m py_compile application/single_app/route_backend_chats.py application/single_app/functions_settings.py application/single_app/config.py`
- `py functional_tests/test_content_safety_allowlist_override.py`
- `py functional_tests/test_admin_content_safety_allowlist_settings.py`
- `py functional_tests/test_content_safety_allowlist_prompt_filter_retry.py`

### Before/After Comparison

- **Before**: allowlisted false positives could still fail with Azure OpenAI prompt content filter 400 after Content Safety bypass.
- **After**: app attempts a disambiguation-context retry, then a benign-placeholder retry for matched allowlist terms before failing.

## Follow-Up Hardening Note

- Additional hardening for this same issue lineage is documented in release notes version **0.240.070**.
- See: `docs/explanation/release_notes.md` under **Content Safety Allowlist Prompt-Filter Fallback Hardening**.
