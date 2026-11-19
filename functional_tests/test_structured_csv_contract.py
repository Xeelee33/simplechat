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
from semantic_kernel.functions import KernelArguments
from semantic_kernel.contents import ChatMessageContent, StreamingChatMessageContent
from semantic_kernel.contents.chat_history import ChatHistory

APP = Path(__file__).resolve().parents[1] / 'application' / 'single_app'
sys.path.insert(0, str(APP))
from agent_logging_chat_completion import LoggingChatCompletionAgent
from functions_async_stream import SyncAsyncStream
from functions_document_source_resolution import append_document_copy_source_hint
from functions_generated_file_exports import (
    build_generated_file_export,
    create_model_csv_completion,
    decode_model_csv_response,
    get_generated_file_export_content,
    get_requested_artifact_formats,
    get_requested_generated_file_formats,
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
    received_messages: list = Field(default_factory=list)

    async def _inner_get_chat_message_contents(self, chat_history, settings):
        self.received_settings.append(settings)
        self.received_messages.append(list(chat_history.messages))
        return [ChatMessageContent(
            role='assistant',
            content=self.payload,
            ai_model_id=self.ai_model_id,
            metadata={'usage': {'total_tokens': 17}},
        )]

    async def _inner_get_streaming_chat_message_contents(self, chat_history, settings, function_invoke_attempt=0):
        self.received_settings.append(settings)
        self.received_messages.append(list(chat_history.messages))
        for content in (self.payload[:20], self.payload[20:]):
            yield [StreamingChatMessageContent(
                role='assistant',
                content=content,
                choice_index=0,
                ai_model_id=self.ai_model_id,
                metadata={'usage': {'total_tokens': 17}},
            )]


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


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('row_source', ['model', 'prior_assistant', 'named_assistant', 'prior_document', 'pending_copy'])
def test_real_agent_uses_structured_document_schema_and_preserves_settings(streaming, row_source, stream_loop, runtime):
    file_specs = [{
        'format': output_format,
        'title': f'{output_format.upper()} report',
        'columns': [],
        'rows': [],
        'row_source': row_source,
        'source_action': '',
        'formatting': {
            'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
            'body_font': '', 'body_size': 0, 'margin_twips': 0, 'line_spacing': '',
        },
        'blocks': [{
            'type': 'heading',
            'text': 'Findings',
            'level': 1,
            'items': [],
            'columns': [],
            'rows': [],
        }] if row_source == 'model' else [],
    } for output_format in ('docx', 'pdf')]
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({'chat_response': 'The report is ready.', 'files': file_specs}),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent',
        deployment_name='gpt-4.1',
        instructions='Test only',
        service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    invocation_options = {
        'messages': 'Create both a Word report and PDF.',
        'csv_response': True,
        'csv_response_only': True,
        'file_response_formats': ['docx', 'pdf'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**invocation_options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**invocation_options))

    assert response.content == 'The report is ready.'
    assert [spec['format'] for spec in response.metadata['generated_file_specs']] == ['docx', 'pdf']
    assert [spec['row_source'] for spec in response.metadata['generated_file_specs']] == [row_source, row_source]
    assert response.ai_model_id == 'gpt-4.1'
    assert response.metadata['usage']['total_tokens'] == 17
    response_settings = model.received_settings[0]
    response_format = getattr(response_settings, 'response_format', None) or response_settings.extension_data['response_format']
    assert response_format['json_schema']['strict'] is True
    assert response_settings.function_choice_behavior.type_.value == 'none'
    assert saved_settings.extension_data.get('response_format') is None
    assert saved_settings.function_choice_behavior is None


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_preserves_pending_document_copy_declaration(streaming, stream_loop, runtime):
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({
            'chat_response': 'Please confirm the heading font.',
            'files': [],
            'pending_copy_source': 'prior_assistant',
        }),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent', deployment_name='gpt-4.1', instructions='Test only', service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    options = {
        'messages': 'Convert the previous answer to Word.',
        'csv_response': True,
        'file_response_formats': ['docx'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**options))
    assert response.content == 'Please confirm the heading font.'
    assert response.metadata['pending_copy_source'] == 'prior_assistant'
    assert response.metadata['generated_file_specs'] == []
    assert response.metadata['usage']['total_tokens'] == 17
    assert saved_settings.extension_data.get('response_format') is None


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_preserves_named_source_clarification(streaming, stream_loop, runtime):
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({
            'chat_response': 'What font should the heading use?',
            'files': [],
            'pending_copy_source': 'named_assistant',
        }),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent', deployment_name='gpt-4.1', instructions='Test only', service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    options = {
        'messages': 'Create a Word copy of the Brief Data Strategy Development Plan.',
        'csv_response': True,
        'file_response_formats': ['docx'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**options))
    assert response.metadata['pending_copy_source'] == 'named_assistant'
    assert response.metadata['generated_file_specs'] == []
    assert response.metadata['usage']['total_tokens'] == 17
    assert saved_settings.extension_data.get('response_format') is None


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_receives_verified_clarification_budget(streaming, stream_loop, runtime):
    from functions_document_source_resolution import (
        MAX_PENDING_DOCUMENT_COPY_DEPTH,
        document_recovery_choice,
    )

    route_path = APP / 'route_backend_chats.py'
    tree = ast.parse(route_path.read_text(encoding='utf-8'))
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_append_chat_document_copy_source_hint'
    )

    def load_source(user_id, conversation_id, message_id, source_mode, depth_out=None):
        assert source_mode == 'pending_copy'
        if depth_out is not None:
            depth_out.append(3)
        return {'id': 'private-id', 'content': 'private source'}

    namespace = {
        'append_document_copy_source_hint': append_document_copy_source_hint,
        '_load_chat_document_copy_source': load_source,
        '_read_chat_pending_document_copy': lambda *args: {'source_mode': 'pending_copy'},
        'document_recovery_choice': document_recovery_choice,
        'MAX_PENDING_DOCUMENT_COPY_DEPTH': MAX_PENDING_DOCUMENT_COPY_DEPTH,
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(route_path), 'exec'), namespace)
    messages = [{'role': 'user', 'content': 'Use the default font.'}]
    namespace['_append_chat_document_copy_source_hint'](
        messages, ['docx'], 'user', 'conversation', 'current',
    )
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({
            'chat_response': 'One more detail?',
            'files': [],
            'pending_copy_source': 'pending_copy',
        }),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent', deployment_name='gpt-4.1', instructions='Test only', service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    options = {
        'messages': [ChatMessageContent(**message) for message in messages],
        'csv_response': True,
        'file_response_formats': ['docx'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**options))
    received = '\n'.join(str(message.content or '') for message in model.received_messages[0])
    assert '1 more clarification(s)' in received
    assert 'private source' not in received
    assert response.content == 'One more detail?'
    assert response.metadata['pending_copy_source'] == 'pending_copy'
    assert response.metadata['usage']['total_tokens'] == 17
    assert saved_settings.extension_data.get('response_format') is None


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_receives_explicit_reconstruction_hint(streaming, stream_loop, runtime):
    from functions_document_source_resolution import document_recovery_choice

    route_path = APP / 'route_backend_chats.py'
    tree = ast.parse(route_path.read_text(encoding='utf-8'))
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_append_chat_document_copy_source_hint'
    )
    namespace = {
        '_read_chat_pending_document_copy': lambda *args: None,
        '_chat_has_unverified_document_reference': lambda *args: False,
        '_is_document_reference_continuation': lambda *args: True,
        'document_recovery_choice': document_recovery_choice,
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(route_path), 'exec'), namespace)
    messages = [{
        'role': 'user',
        'content': 'RECREATE DOCUMENT: Create a Word file based on the plan and include the recommendation.',
    }]
    namespace['_append_chat_document_copy_source_hint'](
        messages, ['docx'], 'user', 'conversation', 'current',
    )
    spec = {
        'format': 'docx', 'title': 'Plan', 'columns': [], 'rows': [],
        'row_source': 'model', 'source_action': '',
        'blocks': [{'type': 'paragraph', 'text': 'Reconstructed plan and recommendation.',
                    'level': 0, 'items': [], 'columns': [], 'rows': []}],
        'formatting': {
            'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
            'body_font': '', 'body_size': 0, 'margin_twips': 0, 'line_spacing': '',
        },
    }
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({'chat_response': 'Preparing the file.', 'files': [spec]}),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent', deployment_name='gpt-4.1', instructions='Test only', service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    options = {
        'messages': [ChatMessageContent(**message) for message in messages],
        'csv_response': True, 'csv_response_only': True, 'file_response_formats': ['docx'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**options))
    sent = '\n'.join(str(message.content or '') for message in model.received_messages[0])
    assert 'not a verbatim copy' in sent
    assert response.content == 'Preparing the file.'
    assert response.metadata['generated_file_specs'][0]['row_source'] == 'model'
    assert response.metadata['usage']['total_tokens'] == 17
    assert model.received_settings[0].function_choice_behavior.type_.value == 'none'
    assert saved_settings.extension_data.get('response_format') is None
    assert saved_settings.function_choice_behavior is None


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_receives_verified_prior_document_hint(streaming, stream_loop, runtime):
    spec = {
        'format': 'pdf', 'title': 'Checklist', 'columns': [], 'rows': [],
        'row_source': 'prior_document', 'source_action': '', 'blocks': [],
        'formatting': {
            'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
            'body_font': '', 'body_size': 0,
        },
    }
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({'chat_response': 'Preparing the PDF.', 'files': [spec]}),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent', deployment_name='gpt-4.1', instructions='Test only', service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    messages = [{'role': 'user', 'content': 'Generate a PDF instead.'}]
    append_document_copy_source_hint(messages, lambda mode: {'content': 'private', 'id': 'private-id'})
    options = {
        'messages': [ChatMessageContent(**message) for message in messages],
        'csv_response': True,
        'file_response_formats': ['pdf'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**options))

    sent = '\n'.join(str(message.content or '') for message in model.received_messages[0])
    assert 'row_source="prior_document"' in sent
    assert 'private-id' not in sent
    assert response.content == 'Preparing the PDF.'
    assert response.metadata['generated_file_specs'][0]['row_source'] == 'prior_document'
    assert response.metadata['usage']['total_tokens'] == 17
    assert saved_settings.extension_data.get('response_format') is None
    assert saved_settings.function_choice_behavior is None


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_preserves_followup_word_typography(streaming, stream_loop, runtime):
    from io import BytesIO

    from docx import Document

    formatting = {
        'title_font': 'Times New Roman', 'title_size': 18,
        'heading_font': 'Arial', 'heading_size': 14,
        'body_font': 'Calibri', 'body_size': 12,
    }
    spec = {
        'format': 'docx', 'title': 'Plan', 'columns': [], 'rows': [],
        'row_source': 'prior_assistant', 'source_action': '', 'blocks': [],
        'formatting': formatting,
    }
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload=json.dumps({'chat_response': 'Preparing the document.', 'files': [spec]}),
    )
    saved_settings = PromptExecutionSettings(service_id=model.service_id)
    agent = LoggingChatCompletionAgent(
        name='document-agent', deployment_name='gpt-4.1', instructions='Test only', service=model,
        arguments=KernelArguments(settings={model.service_id: saved_settings}),
    )
    messages = [
        ChatMessageContent(role='user', content='Draft a plan: title 18pt Times New Roman, headings 14pt Arial, body 12pt Calibri.'),
        ChatMessageContent(role='assistant', content='# Plan\n\n## Purpose\n\nOriginal body.'),
        ChatMessageContent(role='user', content='Generate a word file from that draft using the formatting requirements.'),
    ]
    options = {'messages': messages, 'csv_response': True, 'file_response_formats': ['docx']}
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**options))

    sent = '\n'.join(str(message.content or '') for message in model.received_messages[0])
    assert 'title 18pt Times New Roman' in sent
    assert 'title_font' in sent
    assert response.content == 'Preparing the document.'
    assert response.metadata['usage']['total_tokens'] == 17
    assert response.metadata['generated_file_specs'][0]['formatting'] == formatting
    assert saved_settings.extension_data.get('response_format') is None
    assert saved_settings.function_choice_behavior is None
    export = build_generated_file_export(
        'Generate a word file from that draft using the formatting requirements.',
        response.content, structured_file_spec=response.metadata['generated_file_specs'][0],
        document_source_loader=lambda mode: {'id': 'answer', 'content': '# Plan\n\n## Purpose\n\nOriginal body.'},
    )
    document = Document(BytesIO(export['file_content']))
    assert [paragraph.text for paragraph in document.paragraphs] == ['Plan', 'Purpose', 'Original body.']
    assert document.paragraphs[0].style.font.name == 'Times New Roman'
    assert document.paragraphs[1].style.font.name == 'Arial'
    assert document.paragraphs[2].runs[0].font.name == 'Calibri'


@pytest.mark.parametrize('streaming', [False, True])
def test_real_agent_hides_malformed_document_envelopes(streaming, stream_loop, runtime):
    model = StructuredOfflineModel(
        ai_model_id='gpt-4.1',
        payload='{"chat_response":"safe text","files":[{"format":"pdf"}]}',
    )
    agent = LoggingChatCompletionAgent(name='document-agent', deployment_name='gpt-4.1', service=model)
    invocation_options = {
        'messages': 'Create a PDF report.',
        'csv_response': True,
        'csv_response_only': True,
        'file_response_formats': ['pdf'],
    }
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**invocation_options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**invocation_options))

    assert response.content == 'The requested files or formatting could not be validated. No files were created.'
    assert response.metadata['generated_file_specs'] == []
    assert '{' not in response.content


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('response_wrapper_enabled', [False, True])
def test_unsupported_agent_hides_unstructured_document_envelopes(
    streaming,
    response_wrapper_enabled,
    stream_loop,
    runtime,
):
    model = StructuredOfflineModel(
        ai_model_id='unknown-model',
        payload=envelope([{
            'format': 'pdf',
            'title': 'Report',
            'columns': [],
            'rows': [],
            'row_source': 'model',
            'source_action': '',
            'blocks': [],
        }]),
    )
    agent = LoggingChatCompletionAgent(
        name='unsupported-document-agent',
        deployment_name='unknown-model',
        service=model,
    )
    if response_wrapper_enabled:
        invocation_options = {
            'messages': 'Create a PDF report.',
            'csv_response': True,
            'csv_response_only': True,
            'file_response_formats': ['pdf'],
        }
    else:
        agent.supports_csv_response = False
        invocation_options = {'messages': 'Create a PDF report.'}
    with Flask(__name__).test_request_context(), m365_execution_context(runtime.context):
        if streaming:
            with SyncAsyncStream(agent.invoke_stream(**invocation_options), stream_loop) as stream:
                response = list(stream)[-1]
        else:
            response = stream_loop.run_until_complete(agent.invoke(**invocation_options))

    assert response.content == 'Here are the results.'
    assert '{' not in response.content
    assert 'generated_file_specs' not in response.metadata


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
        'get_requested_generated_file_formats': get_requested_generated_file_formats,
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

    finalize(result, 'Create a CSV from this table', invoke)
    assert 'Complete original analysis' in calls[0][0]
    assert calls[0][1] == 'csv_final_response'
    assert result['csv_file_spec'] == file_spec
    assert result['analysis_reply'] == result['reply'] == 'Final answer'


@pytest.mark.parametrize('result,prompt', [
    ({'generated_tabular_outputs': [{'status': 'queued'}]}, 'Export CSV'),
    ({'deferred_composition': {'status': 'pending'}}, 'Export CSV'),
])
def test_document_action_pending_work_and_other_formats_skip_csv_pass(result, prompt):
    finalize = load_document_csv_finalizer()
    calls = []
    finalize(result, prompt, lambda *args, **kwargs: calls.append(True))
    assert calls == []
    assert 'csv_file_spec' not in result


def test_document_action_pdf_request_runs_the_structured_file_pass():
    finalize = load_document_csv_finalizer()
    result = {'analysis_reply': 'Complete original analysis'}
    calls = []

    def invoke(prompt, stage):
        calls.append(stage)
        return {
            'reply': 'The report is ready.',
            'generated_file_specs': [{
                'format': 'pdf',
                'title': 'Report',
                'columns': [],
                'rows': [],
                'row_source': 'model',
                'source_action': '',
                'blocks': [{
                    'type': 'paragraph',
                    'text': 'Validated report content.',
                    'level': 0,
                    'items': [],
                    'columns': [],
                    'rows': [],
                }],
            }],
        }

    finalize(result, 'Create a PDF report', invoke)

    assert calls == ['csv_final_response']
    assert result['generated_file_specs'][0]['format'] == 'pdf'
    assert result['reply'] == 'The report is ready.'


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