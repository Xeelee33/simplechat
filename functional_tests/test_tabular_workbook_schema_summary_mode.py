#!/usr/bin/env python3
# test_tabular_workbook_schema_summary_mode.py
"""
Functional test for workbook schema-summary routing fix.
Version: 0.261.057
Implemented in: 0.239.115; chart recommendation bounded routing in 0.261.055; deterministic chart recommendations in 0.261.056; workflow synthesis bypass in 0.261.057

This test ensures workbook-structure questions use schema-summary routing,
chart recommendation prompts use bounded schema routing, preserve
describe_tabular_file citations when appropriate, and avoid fallback prompts
that demand unavailable tool calls.
"""

import ast
import json
import os
import sys
from types import SimpleNamespace


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT_DIR)
sys.path.append(os.path.join(ROOT_DIR, 'application', 'single_app'))

ROUTE_FILE = os.path.join(ROOT_DIR, 'application', 'single_app', 'route_backend_chats.py')
WORKFLOW_RUNNER_FILE = os.path.join(ROOT_DIR, 'application', 'single_app', 'functions_workflow_runner.py')
TARGET_FUNCTIONS = {
    'get_tabular_discovery_function_names',
    'get_tabular_analysis_function_names',
    'is_tabular_chart_recommendation_question',
    'is_tabular_schema_summary_question',
    'is_tabular_entity_lookup_question',
    'get_tabular_execution_mode',
    'build_tabular_fallback_system_message',
    'build_tabular_chart_recommendation_fallback_from_invocations',
    'get_tabular_invocation_result_payload',
    'get_tabular_invocation_error_message',
    'split_tabular_analysis_invocations',
    'split_tabular_plugin_invocations',
    'filter_tabular_citation_invocations',
    '_append_unique_chart_recommendation',
    '_classify_tabular_chart_columns',
}


def load_tabular_route_helpers():
    """Load selected helpers from the chat route source."""
    with open(ROUTE_FILE, 'r', encoding='utf-8') as file_handle:
        route_content = file_handle.read()

    parsed = ast.parse(route_content, filename=ROUTE_FILE)
    selected_nodes = []

    for node in parsed.body:
        if isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    namespace = {
        'json': __import__('json'),
        're': __import__('re'),
        '_shared_question_requests_tabular_chart_recommendations': (
            lambda prompt: 'chart' in str(prompt or '').lower()
            and 'recommend' in str(prompt or '').lower()
            and 'spreadsheet' in str(prompt or '').lower()
        ),
        'question_requests_tabular_exhaustive_results': lambda prompt: False,
    }
    exec(compile(module, ROUTE_FILE, 'exec'), namespace)
    namespace['get_tabular_discovery_function_names'] = lambda: ['describe_tabular_file', 'list_tabular_files']
    namespace['get_tabular_analysis_function_names'] = lambda: [
        'lookup_value',
        'get_distinct_values',
        'count_rows',
        'aggregate_column',
        'filter_rows',
        'search_rows',
        'query_tabular_data',
        'filter_rows_by_related_values',
        'count_rows_by_related_values',
        'group_by_aggregate',
        'group_by_datetime_component',
    ]
    return namespace, route_content


def test_workbook_structure_questions_route_to_schema_summary_mode():
    """Verify workbook-summary prompts are routed to schema-summary mode."""
    print('🔍 Testing workbook schema-summary intent detection...')

    try:
        helpers, _ = load_tabular_route_helpers()
        is_schema_summary_question = helpers['is_tabular_schema_summary_question']
        get_execution_mode = helpers['get_tabular_execution_mode']

        workbook_question = (
            'Summarize this workbook for me. What worksheets does it contain, '
            'what does each worksheet represent, and how are they related?'
        )
        analytical_question = 'What was the total tax withheld in 2025?'

        assert is_schema_summary_question(workbook_question), workbook_question
        assert get_execution_mode(workbook_question) == 'schema_summary', workbook_question
        assert not is_schema_summary_question(analytical_question), analytical_question
        assert get_execution_mode(analytical_question) == 'analysis', analytical_question

        print('✅ Workbook schema-summary intent detection passed')
        return True

    except Exception as exc:
        print(f'❌ Test failed: {exc}')
        import traceback
        traceback.print_exc()
        return False


def test_chart_recommendation_questions_route_to_bounded_schema_mode():
    """Verify chart recommendation prompts avoid full analytical tool loops."""
    print('🔍 Testing chart recommendation bounded routing...')

    try:
        helpers, route_content = load_tabular_route_helpers()
        is_chart_recommendation_question = helpers['is_tabular_chart_recommendation_question']
        get_execution_mode = helpers['get_tabular_execution_mode']
        build_fallback_prompt = helpers['build_tabular_fallback_system_message']

        chart_question = 'What meaningful charts and graphs do you recommend creating from the data in this spreadsheet?'

        assert is_chart_recommendation_question(chart_question), chart_question
        assert get_execution_mode(chart_question) == 'chart_recommendation', chart_question
        fallback_prompt = build_fallback_prompt(
            'Project Phase and Milestone Date Example.xlsx',
            execution_mode='chart_recommendation',
        )
        assert 'Recommend chart types from the visible workbook shape only' in fallback_prompt, fallback_prompt
        assert "'chart_recommendation'" in route_content, 'Expected chart_recommendation execution mode.'
        assert 'max_tabular_attempts = 1 if chart_recommendation_mode else 3' in route_content
        assert 'max_auto_tool_invocations = 4 if chart_recommendation_mode else 20' in route_content
        assert 'Do not call aggregate, filter, query, lookup, grouped-analysis, or document-search tools.' in route_content
        assert 'if chart_recommendation_mode:' in route_content
        assert 'return build_tabular_chart_recommendation_fallback_from_invocations(' in route_content

        print('✅ Chart recommendation bounded routing passed')
        return True

    except Exception as exc:
        print(f'❌ Test failed: {exc}')
        import traceback
        traceback.print_exc()
        return False


def test_chart_recommendation_builder_uses_schema_without_model_synthesis():
    """Verify schema-only chart recommendations are deterministic and concise."""
    print('🔍 Testing deterministic chart recommendation builder...')

    try:
        helpers, _ = load_tabular_route_helpers()
        build_recommendations = helpers['build_tabular_chart_recommendation_fallback_from_invocations']

        schema_payload = {
            'filename': 'test-data.xlsx',
            'is_workbook': True,
            'sheet_names': ['OIE BB', 'BB', 'GAGAS'],
            'sheet_count': 3,
            'per_sheet_schemas': {
                'GAGAS': {
                    'row_count': 15,
                    'columns': [
                        'Phase',
                        'Phase Timeframe',
                        'Phase Begin Date Workflow',
                        'Phase End Date Workflow',
                    ],
                    'dtypes': {
                        'Phase': 'object',
                        'Phase Timeframe': 'int64',
                        'Phase Begin Date Workflow': 'object',
                        'Phase End Date Workflow': 'object',
                    },
                },
            },
        }
        invocations = [SimpleNamespace(
            function_name='describe_tabular_file',
            error_message=None,
            result=json.dumps(schema_payload),
            success=True,
        )]

        recommendations = build_recommendations(
            'What meaningful charts and graphs do you recommend creating from the data in this spreadsheet?',
            invocations,
        )

        assert recommendations, recommendations
        assert 'Recommended charts from workbook structure' in recommendations, recommendations
        assert 'bar chart' in recommendations, recommendations
        assert 'timeline or line chart' in recommendations, recommendations
        assert 'Phase Timeframe' in recommendations, recommendations
        assert 'No aggregate totals were invented' in recommendations, recommendations
        assert 'TOOL RESULT SUMMARIES' not in recommendations, recommendations
        assert len(recommendations) < 10000, len(recommendations)

        print('✅ Deterministic chart recommendation builder passed')
        return True

    except Exception as exc:
        print(f'❌ Test failed: {exc}')
        import traceback
        traceback.print_exc()
        return False


def test_workflow_chart_recommendation_bypasses_final_model_synthesis():
    """Verify workflow document actions return chart recommendations directly."""
    print('🔍 Testing workflow chart recommendation synthesis bypass...')

    try:
        with open(WORKFLOW_RUNNER_FILE, 'r', encoding='utf-8') as file_handle:
            workflow_content = file_handle.read()

        assert 'from functions_tabular_orchestration import question_requests_tabular_chart_recommendations' in workflow_content
        assert "tabular_execution_mode = 'chart_recommendation' if question_requests_tabular_chart_recommendations(task_prompt) else 'analysis'" in workflow_content
        assert "execution_mode=tabular_execution_mode" in workflow_content
        assert "if tabular_execution_mode == 'chart_recommendation':" in workflow_content
        bypass_index = workflow_content.index("if tabular_execution_mode == 'chart_recommendation':")
        invoke_index = workflow_content.index("'analysis_reply': str(invoke_prompt(", bypass_index)
        assert bypass_index < invoke_index
        assert "direct_reply = '\\n\\n'.join(" in workflow_content[bypass_index:invoke_index]
        assert "'analysis_reply': direct_reply" in workflow_content[bypass_index:invoke_index]

        print('✅ Workflow chart recommendation synthesis bypass passed')
        return True

    except Exception as exc:
        print(f'❌ Test failed: {exc}')
        import traceback
        traceback.print_exc()
        return False


def test_schema_summary_fallback_prompt_does_not_require_unavailable_tools():
    """Verify the outer fallback prompt no longer requires tool calls after SK failure."""
    print('🔍 Testing workbook fallback prompt behavior...')

    try:
        helpers, route_content = load_tabular_route_helpers()
        build_fallback_prompt = helpers['build_tabular_fallback_system_message']

        schema_prompt = build_fallback_prompt(
            'irs_treasury_multi_tab_workbook.xlsx',
            execution_mode='schema_summary',
        )
        analysis_prompt = build_fallback_prompt(
            'irs_treasury_multi_tab_workbook.xlsx',
            execution_mode='analysis',
        )

        assert 'answer from the schema summary only' in schema_prompt, schema_prompt
        assert 'MUST use the tabular_processing plugin functions' not in schema_prompt, schema_prompt
        assert 'could not compute tool-backed results' in analysis_prompt, analysis_prompt
        assert 'MUST use the tabular_processing plugin functions' not in analysis_prompt, analysis_prompt
        assert 'AVAILABLE FUNCTIONS: describe_tabular_file only.' in route_content, 'Schema-summary prompt should constrain tool choice.'
        assert 'build_tabular_fallback_system_message(' in route_content, 'Workspace fallback should use the shared fallback helper.'

        print('✅ Workbook fallback prompt behavior passed')
        return True

    except Exception as exc:
        print(f'❌ Test failed: {exc}')
        import traceback
        traceback.print_exc()
        return False


def test_schema_summary_citations_keep_describe_calls_when_they_are_the_only_successes():
    """Verify describe_tabular_file citations are preserved when they are the only successful tool calls."""
    print('🔍 Testing schema-summary citation filtering...')

    try:
        helpers, _ = load_tabular_route_helpers()
        filter_invocations = helpers['filter_tabular_citation_invocations']

        describe_only_invocations = [
            SimpleNamespace(
                function_name='describe_tabular_file',
                error_message=None,
                result='{"sheet_count": 6}',
                success=True,
            )
        ]
        filtered_describe_only = filter_invocations(describe_only_invocations)
        assert [invocation.function_name for invocation in filtered_describe_only] == ['describe_tabular_file'], filtered_describe_only

        mixed_invocations = [
            SimpleNamespace(
                function_name='describe_tabular_file',
                error_message=None,
                result='{"sheet_count": 6}',
                success=True,
            ),
            SimpleNamespace(
                function_name='lookup_value',
                error_message=None,
                result='{"value": 42}',
                success=True,
            ),
        ]
        filtered_mixed = filter_invocations(mixed_invocations)
        assert [invocation.function_name for invocation in filtered_mixed] == ['lookup_value'], filtered_mixed

        print('✅ Schema-summary citation filtering passed')
        return True

    except Exception as exc:
        print(f'❌ Test failed: {exc}')
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    tests = [
        test_workbook_structure_questions_route_to_schema_summary_mode,
        test_chart_recommendation_questions_route_to_bounded_schema_mode,
        test_chart_recommendation_builder_uses_schema_without_model_synthesis,
        test_workflow_chart_recommendation_bypasses_final_model_synthesis,
        test_schema_summary_fallback_prompt_does_not_require_unavailable_tools,
        test_schema_summary_citations_keep_describe_calls_when_they_are_the_only_successes,
    ]

    results = []
    for test in tests:
        print(f'\n🧪 Running {test.__name__}...')
        results.append(test())

    success = all(results)
    print(f'\n📊 Results: {sum(results)}/{len(results)} tests passed')
    sys.exit(0 if success else 1)