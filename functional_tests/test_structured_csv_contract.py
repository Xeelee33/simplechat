# test_structured_csv_contract.py
"""Cross-path structured CSV regressions.
Version: 0.261.053
Implemented in: 0.261.053

Validate model declarations, source copying, model requests and real agent streaming
without contacting cloud services.
"""

import ast
import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from pydantic import Field
import pytest
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.connectors.ai.prompt_execution_settings import PromptExecutionSettings
from semantic_kernel.contents import ChatMessageContent, StreamingChatMessageContent
from semantic_kernel.contents.chat_history import ChatHistory

APP = Path(__file__).resolve().parents[1] / 'application' / 'single_app'
sys.path.insert(0, str(APP))
from agent_logging_chat_completion import LoggingChatCompletionAgent
from functions_async_stream import SyncAsyncStream
from functions_generated_file_exports import (
    build_generated_file_export,
    create_model_csv_completion,
    decode_model_csv_response,
    get_generated_file_export_content,
    get_requested_artifact_formats,
    prepare_model_csv_response,
)
from functions_m365_execution import m365_execution_context
from functions_model_capabilities import CAPABILITY_STRUCTURED_OUTPUT, resolve_model_capability
from test_m365_agent_streaming import runtime, stream_loop
from test_assistant_table_csv_artifact import load_workflow_generated_file_export_helper


def csv_spec(**changes):
    return {
        'format': 'csv', 'filename': 'people.csv', 'title': 'People',
        'columns': ['Name'], 'rows': [['Alice']], 'row_source': 'model',
        'source_action': '', **changes,
    }


def envelope(files=None):
    return json.dumps({'chat_response': 'Here are the results.', 'files': files or []})


@pytest.mark.parametrize('prompt', ['Make a CSV file', 'Show a table', 'Yes, please', 'Export every row'])
def test_prompt_cannot_create_csv(prompt):
    result = build_generated_file_export(prompt, '| Name | Age |\n| --- | --- |\n| Alice | 42 |', pending_output_format='csv')
    assert result is None


def test_declared_action_copy_uses_all_rows_from_exact_action():
    source_rows = [{'Name': f'Person {index}'} for index in range(900)]
    results = [
        {'plugin_name': 'Directory', 'function_name': 'people', 'success': True, 'function_result': {'rows': source_rows}},
        {'plugin_name': 'Discovery', 'function_name': 'people', 'success': True, 'function_result': {'rows': [{'Name': 'Wrong'}]}},
    ]
    result = build_generated_file_export(
        'Yes, please', 'Here is a preview.', function_results=results,
        structured_csv_spec=csv_spec(row_source='current_action', source_action='Directory.people', rows=[]),
    )
    assert result['_structured_rows'] == source_rows
    assert result['row_count'] == 900
    assert result['passthrough_reason_code'] == 'declared_source_copy'


@pytest.mark.parametrize('changes', [
    {'columns': ['Name', 'Name'], 'rows': [['Alice', 'Alice']]},
    {'columns': ['api_key']},
    {'rows': [['Alice', 'Extra']]},
    {'rows': []},
    {'row_source': 'current_action', 'source_action': 'Directory.people'},
    {'row_source': 'untrusted'},
])
def test_invalid_declarations_fail_closed(changes):
    result = build_generated_file_export('Export CSV', 'Ready', structured_csv_spec=csv_spec(**changes))
    assert result is None


def test_source_schema_mismatch_and_missing_source_fail_closed():
    results = [{'plugin_name': 'Directory', 'function_name': 'people', 'function_result': {'rows': [{'Other': 'Alice'}]}}]
    for action in ('Directory.people', 'Missing.people'):
        result = build_generated_file_export(
            'Export CSV', 'Ready', function_results=results,
            structured_csv_spec=csv_spec(row_source='current_action', source_action=action, rows=[]),
        )
        assert result is None


def test_prior_copy_is_explicit_and_lazy():
    calls = []

    def previous_source():
        calls.append(True)
        return {'content': '| Name | Age |\n| --- | --- |\n| Alice | 42 |'}

    model_result = build_generated_file_export('Yes', 'Ready', prior_assistant_source_loader=previous_source, structured_csv_spec=csv_spec())
    assert model_result is not None
    assert calls == []
    copied = build_generated_file_export(
        'Yes', 'Ready', prior_assistant_source_loader=previous_source,
        structured_csv_spec=csv_spec(columns=['Name', 'Age'], rows=[], row_source='prior_assistant'),
    )
    assert copied['_structured_rows'] == [{'Name': 'Alice', 'Age': '42'}]
    assert calls == [True]


def test_decode_invalid_file_keeps_only_chat_text():
    reply, spec = decode_model_csv_response(envelope([csv_spec(rows=[])]))
    assert reply == 'Here are the results.'
    assert spec is None
    reply, spec = decode_model_csv_response('{"chat_response":')
    assert '{' not in reply
    assert spec is None


@pytest.mark.parametrize('prompt', ['Create a PDF report', 'Create a Word document', 'Return JSON'])
def test_other_formats_do_not_get_csv_envelope(prompt):
    messages = [{'role': 'user', 'content': prompt}]
    response_format = prepare_model_csv_response(messages, 'gpt-4.1')
    assert response_format is None
    assert len(messages) == 1


def test_raw_model_schema_rejection_retries_as_text_only():
    calls = []

    def create(**parameters):
        calls.append({**parameters, 'messages': list(parameters['messages'])})
        if len(calls) == 1:
            raise ValueError('response_format json_schema unsupported')
        return SimpleNamespace(choices=[])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    completion, structured = create_model_csv_completion(client, 'gpt-4.1', [{'role': 'user', 'content': 'Export a CSV'}])
    assert completion.choices == []
    assert structured is False
    assert calls[0]['response_format']['json_schema']['strict'] is True
    assert 'response_format' not in calls[1]
    assert not any('row_source=' in message['content'] for message in calls[1]['messages'])


class StructuredOfflineModel(ChatCompletionClientBase):
    payload: str = ''
    received_settings: list = Field(default_factory=list)

    async def _inner_get_chat_message_contents(self, chat_history, settings):
        self.received_settings.append(settings)
        return [ChatMessageContent(role='assistant', content=self.payload, metadata={'usage': {'total_tokens': 17}})]

    async def _inner_get_streaming_chat_message_contents(self, chat_history, settings, function_invoke_attempt=0):
        self.received_settings.append(settings)
        for content in (self.payload[:20], self.payload[20:]):
            yield [StreamingChatMessageContent(role='assistant', content=content, choice_index=0, metadata={'usage': {'total_tokens': 17}})]


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('response_only', [False, True])
def test_real_agent_uses_schema_and_hides_envelope(streaming, response_only, stream_loop, runtime):
    model = StructuredOfflineModel(ai_model_id='gpt-4.1', payload=envelope([csv_spec()]))
    agent = LoggingChatCompletionAgent(name='csv-agent', deployment_name='gpt-4.1', instructions='Test only', service=model)
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(messages='Export a CSV', csv_response=True, csv_response_only=response_only), stream_loop) as stream:
                responses = list(stream)
            assert len(responses) == 1
            response = responses[0]
        else:
            response = stream_loop.run_until_complete(agent.invoke(messages='Export a CSV', csv_response=True, csv_response_only=response_only))
    assert response.content == 'Here are the results.'
    assert response.metadata['csv_file_spec']['rows'] == [['Alice']]
    assert response.metadata['usage']['total_tokens'] == 17
    settings = model.received_settings[0]
    response_format = getattr(settings, 'response_format', None) or settings.extension_data['response_format']
    assert response_format['json_schema']['strict'] is True
    if response_only:
        assert settings.function_choice_behavior.type_.value == 'none'
        assert settings.function_choice_behavior.maximum_auto_invoke_attempts == 0
    assert not agent.arguments or not agent.arguments.execution_settings


def test_workflow_finalizer_requires_declaration_and_preserves_queue():
    uploads = []
    queued = []
    namespace = {
        'get_requested_generated_file_format': lambda question: 'csv',
        'has_generated_tabular_csv_output': lambda outputs: bool(outputs),
        'has_generated_file_output': lambda outputs, output_format: bool(outputs),
        'build_generated_file_export': build_generated_file_export,
        'get_settings': lambda: {},
        'build_tabular_generated_output_row_batches': lambda rows, settings: [rows],
        'should_queue_tabular_generated_output_background': lambda *args: True,
        'queue_tabular_generated_output_run': lambda **kwargs: queued.append(kwargs) or {'id': 'queued'},
        'build_background_tabular_generated_output_metadata': lambda run: run,
        'log_event': lambda *args, **kwargs: None,
        'upload_generated_analysis_artifact_for_user': lambda **kwargs: uploads.append(kwargs),
    }
    finalize = load_workflow_generated_file_export_helper(namespace)
    absent = finalize({'user_id': 'owner'}, 'conversation', 'Export CSV', 'Table')
    assert absent is None
    result = finalize({'user_id': 'owner'}, 'conversation', 'Yes', 'Ready', structured_csv_spec=csv_spec())
    assert result == {'id': 'queued'}
    assert queued[0]['user_id'] == 'owner'
    assert queued[0]['row_batches'] == [[{'Name': 'Alice'}]]
    duplicate = finalize({'user_id': 'owner'}, 'conversation', 'Yes', 'Ready', existing_outputs=[result], structured_csv_spec=csv_spec())
    assert duplicate is None
    assert len(queued) == 1
    assert uploads == []


def load_document_csv_finalizer():
    source = ast.parse((APP / 'functions_workflow_runner.py').read_text(encoding='utf-8'))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_finalize_document_action_csv_result')
    namespace = {
        'get_generated_file_export_content': get_generated_file_export_content,
        'get_requested_artifact_formats': get_requested_artifact_formats,
        '_get_pending_tabular_generated_output': lambda outputs: bool(outputs),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<document_csv_finalizer>', 'exec'), namespace)
    return namespace[function.name]


@pytest.mark.parametrize('reply_field', ['analysis_reply', 'reply'])
@pytest.mark.parametrize('file_spec', [None, csv_spec()])
def test_document_actions_keep_evidence_and_forward_only_declared_files(reply_field, file_spec):
    finalize = load_document_csv_finalizer()
    result = {reply_field: 'Complete original analysis'}
    calls = []

    def invoke(prompt, stage):
        calls.append((prompt, stage))
        return {'reply': 'Final answer', 'csv_file_spec': file_spec}

    finalize(result, 'Show a table', invoke)
    assert 'Complete original analysis' in calls[0][0]
    assert calls[0][1] == 'csv_final_response'
    assert result['csv_file_spec'] == file_spec
    assert result['analysis_reply'] == result['reply'] == 'Final answer'


@pytest.mark.parametrize('result,prompt', [
    ({'generated_tabular_outputs': [{'status': 'queued'}]}, 'Export CSV'),
    ({'deferred_composition': {'status': 'pending'}}, 'Export CSV'),
    ({}, 'Create a PDF report'),
])
def test_document_action_pending_work_and_other_formats_skip_csv_pass(result, prompt):
    finalize = load_document_csv_finalizer()
    calls = []
    finalize(result, prompt, lambda *args, **kwargs: calls.append(True))
    assert calls == []
    assert 'csv_file_spec' not in result


def test_document_analysis_has_no_separate_csv_sidecar_publisher():
    source = ast.parse((APP / 'functions_workflow_runner.py').read_text(encoding='utf-8'))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_maybe_create_document_analysis_generated_artifacts')
    upload_formats = [
        node.args[3].value for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == '_upload_document_analysis_generated_artifact'
        and isinstance(node.args[3], ast.Constant)
    ]
    assert upload_formats
    assert 'csv' not in upload_formats


def load_tabular_csv_helpers(extra_globals):
    source = ast.parse((APP / 'route_backend_chats.py').read_text(encoding='utf-8'))
    names = {'_confirm_tabular_csv_output', '_build_direct_tabular_generated_output_source', 'maybe_queue_direct_tabular_generated_output', 'maybe_create_tabular_generated_output'}
    functions = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    namespace = {
        'asyncio': asyncio, 'json': json, 'logging': logging,
        'ChatHistory': ChatHistory, 'PromptExecutionSettings': PromptExecutionSettings,
        'CAPABILITY_STRUCTURED_OUTPUT': CAPABILITY_STRUCTURED_OUTPUT,
        'resolve_model_capability': resolve_model_capability,
        'MODEL_ENDPOINT_PROTOCOL_AZURE_OPENAI': 'azure_openai',
        'MODEL_ENDPOINT_PROTOCOL_OPENAI_STYLE': 'openai',
        'log_event': lambda *args, **kwargs: None,
        'get_tabular_generated_output_format': lambda prompt: 'csv',
        **extra_globals,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<tabular_csv_helpers>', 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('payload,expected', [
    ('{"files":[{"format":"csv"}]}', True),
    ('{"files":[]}', False),
    ('{"files":[{"format":"pdf"}]}', False),
    ('{"files":[{"format":"csv"}],"extra":true}', False),
    ('{"files":[{"format":"csv"},{"format":"csv"}]}', False),
    ('not JSON', False),
])
def test_tabular_gate_requires_exact_model_declaration(payload, expected):
    model = StructuredOfflineModel(ai_model_id='gpt-4.1', payload=payload)
    helpers = load_tabular_csv_helpers({'build_semantic_kernel_chat_service_for_model': lambda *args, **kwargs: (model, 'azure_openai')})
    declared = asyncio.run(helpers['_confirm_tabular_csv_output']('Show a table from source.csv', 'gpt-4.1', {}))
    assert declared is expected
    assert model.received_settings[0].extension_data['response_format']['json_schema']['strict'] is True
    assert model.received_settings[0].function_choice_behavior is None


@pytest.mark.parametrize('model_name,protocol', [('unknown-model', 'azure_openai'), ('gpt-4.1', 'anthropic')])
def test_tabular_gate_does_not_invoke_unsupported_models(model_name, protocol):
    model = StructuredOfflineModel(ai_model_id=model_name, payload='{"files":[{"format":"csv"}]}')
    helpers = load_tabular_csv_helpers({'build_semantic_kernel_chat_service_for_model': lambda *args, **kwargs: (model, protocol)})
    declared = asyncio.run(helpers['_confirm_tabular_csv_output']('Export CSV', model_name, {}))
    assert declared is False
    assert model.received_settings == []


def test_tabular_planner_metadata_cannot_authorize_csv_and_decline_stops_queue():
    helpers = load_tabular_csv_helpers({})
    result = helpers['_build_direct_tabular_generated_output_source']('Export CSV', [], 'owner', 'conversation', {}, planner_metadata={'output_format': 'csv'})
    assert result is None

    async def decline(*args, **kwargs):
        return False

    helpers['_confirm_tabular_csv_output'] = decline
    result = helpers['maybe_queue_direct_tabular_generated_output']('Export CSV', [], 'owner', 'conversation', 'gpt-4.1', {}, planner_metadata={'output_format': 'csv'})
    assert result is None
    result = asyncio.run(helpers['maybe_create_tabular_generated_output']('Export CSV', [], 'gpt-4.1', {}, 'conversation', user_id='owner'))
    assert result is None