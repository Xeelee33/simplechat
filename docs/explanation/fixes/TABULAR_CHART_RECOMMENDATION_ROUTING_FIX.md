# TABULAR CHART RECOMMENDATION ROUTING FIX

Fixed in version: **0.261.057**

## Issue Description

Small uploaded Excel workbooks could take far too long when a user selected the Analyze document action and asked which charts or graphs would be meaningful for the spreadsheet. A prompt such as `What meaningful charts and graphs do you recommend creating from the data in this spreadsheet?` is a bounded recommendation request, but it could either be classified like a generated spreadsheet output request, enter the open-ended foreground tabular tool loop, or fall back to a very large final model synthesis prompt.

## Root Cause Analysis

- The tabular planner used shared generated-output detection before distinguishing recommendation-only chart prompts.
- Source wording that mentioned `spreadsheet` plus creation language could be interpreted as a structured output request.
- In Analyze mode, prompts that stayed foreground still used the general Semantic Kernel tabular analysis loop, which allowed repeated `filter_rows` and `group_by_aggregate` calls across sheets for a tiny workbook.
- When the inner tabular synthesis step did not complete, the fallback path could send a large computed-results prompt plus proactive chart guidance to GPT 5.6 Luna. For `test-data.xlsx`, that final synthesis request could remain waiting on response headers for a long time.

## Version Implemented

- **0.261.057**

## Files Modified

- `application/single_app/functions_tabular_orchestration.py`
- `application/single_app/route_backend_chats.py`
- `application/single_app/functions_workflow_runner.py`
- `application/single_app/config.py`
- `functional_tests/test_tabular_workbook_schema_summary_mode.py`
- `functional_tests/test_tabular_shared_request_planner.py`
- `docs/explanation/fixes/TABULAR_CHART_RECOMMENDATION_ROUTING_FIX.md`

## Code Changes Summary

- Added a planner-level classifier for chart and graph recommendation prompts.
- Suppressed generated-output format inference for recommendation-only chart prompts, while leaving explicit export, download, file, CSV, JSON, XML, DOCX, and PDF requests on their existing paths.
- Kept recommendation prompts on the `foreground_aggregate` contract so the model can answer from bounded tabular evidence without queuing durable output work.
- Added a `chart_recommendation` tabular execution mode for chat/document actions.
- Limited chart recommendation analysis to workbook schema discovery via `describe_tabular_file`, one SK attempt, and at most four automatic tool invocations.
- Accepted successful workbook-schema invocations as sufficient native evidence for chart recommendation prompts.
- Added a deterministic chart recommendation builder that returns schema-based recommendations before invoking Semantic Kernel chat completion, avoiding large GPT synthesis prompts for this intent.
- Short-circuited workflow document-action final synthesis for chart recommendations so the deterministic tabular response is returned directly instead of passing through `invoke_prompt`.

## Testing Approach

- Extended the shared tabular request planner functional test with the exact reported chart recommendation wording.
- Verified the plan stays foreground, has no durable task type, requests no generated output, and records no requested output formats.
- Extended workbook schema-summary routing coverage to verify chart recommendation prompts route to bounded schema mode and do not permit aggregate/filter/query tools.
- Added deterministic builder coverage using a schema shaped like `test-data.xlsx`.
- Added source-level workflow coverage for the final model synthesis bypass.

## Impact Analysis

- Small spreadsheets used with the Analyze action should no longer be diverted into long-running generated-output orchestration when the user only asks for chart recommendations.
- Foreground chart recommendation analysis should no longer spend extended time probing every sheet with aggregate/filter tools.
- If the workbook has schema evidence, chart recommendations are generated without a final GPT synthesis call.
- Workflow Analyze actions for chart recommendations now return deterministic tabular recommendations directly.
- Explicit requests to create, export, download, or attach structured files remain eligible for generated-output routing.

## Validation

- Before: chart recommendation wording that included `creating` and `spreadsheet` could be mistaken for a generated spreadsheet output request.
- Before: foreground chart recommendations could still run through the general SK tool loop for many minutes.
- Before: fallback synthesis could still send a large computed-results prompt to GPT 5.6 Luna and wait for a slow response.
- After: the same prompt is classified as bounded foreground tabular analysis with `reason_code` set to `bounded_foreground`, then answered through deterministic schema-only chart recommendation mode.