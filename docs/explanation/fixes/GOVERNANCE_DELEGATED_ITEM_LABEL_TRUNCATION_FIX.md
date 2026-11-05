# Governance Delegated Item Label Truncation Fix

Fixed in version: **0.261.063**

Updated in version: **0.261.064**

## Issue Description

The **Select an item** menu in Admin Settings > Governance > Policies displayed complete delegated action-type descriptions. Long action descriptions made native browser dropdown menus exceed the available width, leaving much of the text inaccessible.

## Root Cause Analysis

The governance item lookup rendered each option from the action-type label and description without a display-length limit. Native HTML select menus do not provide a reliable cross-browser way to wrap option rows.

## Technical Details

Files modified:

- `application/single_app/static/js/admin/admin_governance.js`
- `application/single_app/config.py`
- `ui_tests/test_admin_governance_tab.py`

Code changes summary:

- Limits delegated-item option labels to 50 characters, including a trailing ellipsis when truncation is needed.
- Preserves the complete label in the option's native hover tooltip.
- Retains the existing full resource label for the policy editor and saved policy data.
- Sorts dropdown options alphabetically by their complete label before rendering their truncated text.
- Updates the application version to `0.261.064`.

## Validation

The Governance UI test now loads a deliberately long personal action-type description and verifies that its option is shortened while its hover title contains the complete text.

Before the fix, long descriptions could expand the native dropdown beyond the available width. After the fix, each option has a predictable width and the full description remains available on rollover.
