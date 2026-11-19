# test_generated_structured_artifact_parity.py
#!/usr/bin/env python3
"""
Functional test for generated artifact parity across CSV, DOCX, PDF, JSON, and XML.
Version: 0.260.010
Implemented in: 0.260.010

The CSV work in 0.260.007 through 0.260.009 left three gaps in the sibling
formats and one loose end:

  1. JSON and XML could not use authorized action rows or reach back to earlier
     turns.
  2. DOCX and PDF were generated from prose or action rows without a validated
     model declaration.

This test ensures JSON/XML row behavior remains intact and DOCX/PDF publication
requires a validated structured declaration.
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / 'application' / 'single_app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_support.versioning import assert_app_version_at_least  # noqa: E402

from functions_generated_file_exports import (  # noqa: E402
    build_generated_file_export,
    build_generated_file_output_guidance,
    build_structured_artifact_rows_payload,
)

IMPLEMENTED_VERSION = '0.260.010'

TELEMETRY_ROWS = [
    {'id': f'R-{index}', 'voltage': 27 + index % 5, 'state': 'CRITICAL'}
    for index in range(30)
]
TELEMETRY_FUNCTION_RESULTS = [{
    'plugin_name': 'YamcsPlugin',
    'function_name': 'list_parameter_history',
    'success': True,
    'function_result': {'row_count': len(TELEMETRY_ROWS), 'rows': TELEMETRY_ROWS},
}]
CLARIFICATION_REPLY = (
    'Should each row represent one telemetry sample, and which columns should it include?'
)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_json_and_xml_use_authorized_action_rows():
    """A JSON or XML request must serialize action rows when the reply carries no payload."""
    print('Testing JSON/XML row fallback...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    json_payload = build_structured_artifact_rows_payload(
        'create a json of that data',
        'json',
        function_results=TELEMETRY_FUNCTION_RESULTS,
    )
    xml_payload = build_structured_artifact_rows_payload(
        'create an xml of that data',
        'xml',
        function_results=TELEMETRY_FUNCTION_RESULTS,
    )

    assert_true(json_payload is not None, 'Expected a JSON payload from authorized action rows.')
    assert_true(
        json_payload['row_count'] == len(TELEMETRY_ROWS),
        'Expected every authorized row in the JSON artifact.',
    )
    assert_true(json_payload['file_content'].lstrip().startswith('['), 'Expected a JSON array payload.')
    assert_true(xml_payload is not None, 'Expected an XML payload from authorized action rows.')
    assert_true(
        '<GeneratedRows>' in xml_payload['file_content'],
        'Expected the shared generated-rows XML root.',
    )


def test_json_and_xml_reach_back_to_earlier_turns():
    """JSON and XML must reuse rows an earlier turn gathered, the same way CSV does."""
    print('Testing JSON/XML earlier-turn reach-back...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    json_payload = build_structured_artifact_rows_payload(
        'create a json',
        'json',
        function_results=[],
        prior_function_results_loader=lambda: TELEMETRY_FUNCTION_RESULTS,
    )

    assert_true(json_payload is not None, 'Expected the follow-up JSON request to reuse earlier rows.')
    assert_true(
        json_payload['row_source'] == 'earlier action result',
        'Expected reused rows to declare their earlier-turn provenance.',
    )
    assert_true(
        json_payload['row_count'] == len(TELEMETRY_ROWS),
        'Expected the full earlier result set in the JSON artifact.',
    )


def test_row_fallback_is_limited_to_structured_formats():
    """CSV, DOCX, and PDF keep their own renderers; the fallback must not claim them."""
    print('Testing structured row fallback scope...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    for output_format in ('csv', 'docx', 'pdf', '', None):
        assert_true(
            build_structured_artifact_rows_payload(
                'create a file',
                output_format,
                function_results=TELEMETRY_FUNCTION_RESULTS,
            ) is None,
            f'Expected {output_format!r} to be handled by its own renderer.',
        )


def test_row_fallback_requires_a_passthrough_contract():
    """Rows must still satisfy the passthrough contract before they become an artifact."""
    print('Testing structured row fallback eligibility...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    assert_true(
        build_structured_artifact_rows_payload(
            'analyze the telemetry and summarize each sample',
            'json',
            function_results=TELEMETRY_FUNCTION_RESULTS,
        ) is None,
        'Expected a derived-output request to refuse raw row passthrough.',
    )
    assert_true(
        build_structured_artifact_rows_payload('create a json', 'json', function_results=[]) is None,
        'Expected no artifact when no rows are available.',
    )


def test_clarification_turn_publishes_no_artifact_in_any_format():
    """A clarifying question must not publish a CSV, DOCX, or PDF."""
    print('Testing clarification gate across formats...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    for question in ('create a csv', 'create a word document', 'create a pdf'):
        assert_true(
            build_generated_file_export(
                question,
                CLARIFICATION_REPLY,
                function_results=TELEMETRY_FUNCTION_RESULTS,
            ) is None,
            f'Expected no artifact for a clarification turn on: {question}',
        )


def test_docx_and_pdf_require_a_validated_declaration():
    """Prose or action rows alone must not authorize DOCX or PDF publication."""
    print('Testing DOCX/PDF declaration requirement...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    for question in ('create a word document', 'create a pdf'):
        export_payload = build_generated_file_export(
            question,
            'Here is the summary of the retrieved samples.',
            function_results=TELEMETRY_FUNCTION_RESULTS,
        )
        assert_true(export_payload is None, f'Expected no undeclared artifact for: {question}')


def test_docx_and_pdf_do_not_copy_earlier_action_rows_without_declaration():
    """An earlier action result must not replace the required document declaration."""
    print('Testing DOCX/PDF earlier-row exclusion...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    prior_calls = []
    export_payload = build_generated_file_export(
        'create a word document',
        '',
        function_results=[],
        prior_function_results_loader=lambda: prior_calls.append(True) or TELEMETRY_FUNCTION_RESULTS,
    )

    assert_true(export_payload is None, 'Expected earlier action rows not to create an undeclared DOCX.')
    assert_true(not prior_calls, 'Expected undeclared DOCX requests not to load earlier action rows.')


def test_docx_and_pdf_guidance_states_the_declaration_contract():
    """DOCX and PDF guidance must constrain declarations and publication claims."""
    print('Testing DOCX/PDF declaration guidance...')
    assert_app_version_at_least(IMPLEMENTED_VERSION)

    for question in ('create a word document', 'create a pdf'):
        guidance = build_generated_file_output_guidance(question)
        assert_true(
            'Use files=[] for an inline answer' in guidance,
            f'Expected inline answers and clarifications to remain attachment-free for: {question}',
        )
        assert_true(
            'Never say a file is attached' in guidance,
            f'Expected attachment claims to wait for publication for: {question}',
        )
        assert_true(
            'include every requested format' in guidance
            and 'ordered blocks of heading, paragraph, list, and table content' in guidance,
            f'Expected complete structured document declarations for: {question}',
        )


def run_tests() -> bool:
    tests = [
        test_json_and_xml_use_authorized_action_rows,
        test_json_and_xml_reach_back_to_earlier_turns,
        test_row_fallback_is_limited_to_structured_formats,
        test_row_fallback_requires_a_passthrough_contract,
        test_clarification_turn_publishes_no_artifact_in_any_format,
        test_docx_and_pdf_require_a_validated_declaration,
        test_docx_and_pdf_do_not_copy_earlier_action_rows_without_declaration,
        test_docx_and_pdf_guidance_states_the_declaration_contract,
    ]

    results = []
    for test in tests:
        print(f'\nRunning {test.__name__}...')
        try:
            test()
            print(f'{test.__name__} passed')
            results.append(True)
        except Exception as exc:
            print(f'{test.__name__} failed: {exc}')
            traceback.print_exc()
            results.append(False)

    passed = sum(1 for result in results if result)
    print(f'\nResults: {passed}/{len(tests)} tests passed')
    return all(results)


if __name__ == '__main__':
    sys.exit(0 if run_tests() else 1)
