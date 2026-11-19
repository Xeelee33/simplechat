# test_generated_document_contract.py
"""Regression coverage for validated structured Word and PDF generation.
Version: 0.261.053
Implemented in: 0.261.053
"""

import ast
import logging
import re
import sys
import json
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / 'application' / 'single_app'
sys.path.insert(0, str(APP))

from functions_generated_file_exports import (
    MODEL_GENERATED_FILE_GUIDANCE,
    MODEL_UNSTRUCTURED_FILE_GUIDANCE,
    build_generated_file_export,
    build_model_generated_file_response_format,
    decode_model_generated_file_response,
    generated_file_specs_match_request,
    get_requested_generated_file_formats,
    parse_model_generated_file_response,
    prepare_model_generated_file_response,
    resolve_pending_generated_file_formats,
    sanitize_unstructured_model_generated_file_response,
)


def _block(block_type, *, text='', level=0, items=None, columns=None, rows=None):
    return {
        'type': block_type,
        'text': text,
        'level': level,
        'items': items or [],
        'columns': columns or [],
        'rows': rows or [],
    }


def _document_spec(output_format='docx', blocks=None, **changes):
    return {
        'format': output_format,
        'title': 'Incident <Brief> & résumé',
        'columns': [],
        'rows': [],
        'row_source': 'model',
        'source_action': '',
        'formatting': {
            'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
            'body_font': '', 'body_size': 0,
        },
        'blocks': blocks or [
            _block('heading', text='Summary', level=1),
            _block('paragraph', text='Café & <critical> details.'),
            _block('list', items=['First finding', 'Second finding']),
            _block('table', columns=['Name', 'Finding'], rows=[['Ada', 'Unicode – ok']]),
        ],
        **changes,
    }


def _envelope(files):
    return json.dumps({'chat_response': 'The analysis is ready.', 'files': files})


def test_document_request_without_model_declaration_does_not_create_file():
    payload = build_generated_file_export(
        'Create a PDF report from the source document.',
        'The source discusses a PDF and contains a table.',
        function_results=[{
            'plugin_name': 'DirectoryPlugin',
            'function_name': 'list_people',
            'success': True,
            'function_result': {'rows': [{'Name': 'Ada'}]},
        }],
    )

    assert payload is None


def test_source_document_mentions_are_not_output_requests():
    assert get_requested_generated_file_formats('Analyze this Word document and discuss the PDF.') == []
    assert get_requested_generated_file_formats('Create a PDF from this Word document.') == ['pdf']
    assert get_requested_generated_file_formats('Convert this PDF to a Word document.') == ['docx']


def test_source_copy_word_honors_requested_title_heading_and_body_typography():
    from io import BytesIO

    from docx import Document
    from docx.oxml.ns import qn

    formatting = {
        'title_font': 'Times New Roman', 'title_size': 18,
        'heading_font': 'Arial', 'heading_size': 14,
        'body_font': 'Calibri', 'body_size': 12,
    }
    spec = _document_spec('docx', row_source='prior_assistant', blocks=[], formatting=formatting)
    spec['blocks'] = []
    response, specs = decode_model_generated_file_response(_envelope([spec]))
    assert len(specs) == 1
    result = build_generated_file_export(
        'Generate a word file from that draft using the formatting requirements.',
        response,
        structured_file_spec=specs[0],
        document_source_loader=lambda mode: {
            'id': 'original', 'content': '# DevSecOps Plan\n\n## Purpose\n\nBody text.\n\n- First item',
        },
    )
    document = Document(BytesIO(result['file_content']))
    title, heading, body, item = document.paragraphs
    assert [paragraph.text for paragraph in document.paragraphs] == [
        'DevSecOps Plan', 'Purpose', 'Body text.', 'First item',
    ]
    assert title.style.font.name == 'Times New Roman'
    assert title.style.font.size.pt == 18
    assert title.runs[0].font.name is None
    assert title.runs[0].font.size is None
    assert heading.style.font.name == 'Arial'
    assert heading.style.font.size.pt == 14
    assert body.style.font.name == 'Calibri'
    assert body.style.font.size.pt == 12
    assert item.style.font.name == 'Calibri'
    assert item.style.font.size.pt == 12
    for style in (title.style, heading.style, body.style):
        fonts = style._element.rPr.rFonts
        assert fonts.get(qn('w:asciiTheme')) is None
        assert fonts.get(qn('w:hAnsiTheme')) is None


@pytest.mark.parametrize('row_source', ['prior_assistant', 'model'])
def test_word_honors_requested_margins_and_line_spacing(row_source):
    from io import BytesIO
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING

    formatting = {
        'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
        'body_font': 'Papyrus', 'body_size': 14, 'margin_twips': 1440, 'line_spacing': 'single',
    }
    spec = _document_spec('docx', row_source=row_source, formatting=formatting)
    if row_source != 'model':
        spec['blocks'] = []
    result = build_generated_file_export(
        'Create a Word file with one-inch margins and single spacing.', 'Preparing.',
        structured_file_spec=spec,
        document_source_loader=lambda mode: {
            'id': 'original', 'content': '# Plan\n\nFirst paragraph.\n\n- First item',
        },
    )
    document = Document(BytesIO(result['file_content']))
    section = document.sections[0]
    assert [side.inches for side in (
        section.top_margin, section.bottom_margin, section.left_margin, section.right_margin,
    )] == [1, 1, 1, 1]
    assert all(paragraph.paragraph_format.line_spacing_rule == WD_LINE_SPACING.SINGLE for paragraph in document.paragraphs)
    assert document.styles['Normal'].font.name == 'Papyrus'


def test_model_authored_word_honors_typography_including_tables():
    from io import BytesIO

    from docx import Document

    formatting = {
        'title_font': 'Times New Roman', 'title_size': 18,
        'heading_font': 'Arial', 'heading_size': 14,
        'body_font': 'Calibri', 'body_size': 12,
    }
    blocks = [
        _block('heading', text='Purpose', level=1),
        _block('paragraph', text='Body text.'),
        _block('list', items=['First item']),
        _block('table', columns=['Name'], rows=[['Ada']]),
    ]
    result = build_generated_file_export(
        'Create a Word report.', 'Preparing the report.',
        structured_file_spec=_document_spec('docx', blocks=blocks, formatting=formatting),
    )
    document = Document(BytesIO(result['file_content']))
    assert document.paragraphs[0].style.font.name == 'Times New Roman'
    assert document.paragraphs[0].style.font.size.pt == 18
    assert document.paragraphs[0].runs[0].font.name is None
    assert document.paragraphs[0].runs[0].font.size is None
    assert document.paragraphs[1].style.font.name == 'Arial'
    assert document.paragraphs[1].style.font.size.pt == 14
    assert document.paragraphs[2].runs[0].font.name == 'Calibri'
    assert document.paragraphs[2].runs[0].font.size.pt == 12
    assert document.paragraphs[3].runs[0].font.name == 'Calibri'
    assert document.tables[0].cell(1, 0).paragraphs[0].runs[0].font.size.pt == 12


def test_source_copy_applies_body_typography_to_code_without_changing_content():
    from io import BytesIO

    from docx import Document

    formatting = {
        'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
        'body_font': 'Calibri', 'body_size': 12,
    }
    spec = _document_spec('docx', row_source='prior_assistant', formatting=formatting)
    spec['blocks'] = []
    result = build_generated_file_export(
        'Create a Word document.', 'Preparing.',
        structured_file_spec=spec,
        document_source_loader=lambda mode: {
            'id': 'answer', 'content': '# Plan\n\nUse `command`.\n\n```\nexample\n```',
        },
    )
    document = Document(BytesIO(result['file_content']))
    assert [paragraph.text for paragraph in document.paragraphs] == ['Plan', 'Use command.', 'example\n']
    inline_code = next(run for run in document.paragraphs[1].runs if run.text == 'command')
    block_code = document.paragraphs[2].runs[0]
    assert inline_code.font.name == 'Calibri'
    assert inline_code.font.size.pt == 12
    assert block_code.font.name == 'Calibri'
    assert block_code.font.size.pt == 12


@pytest.mark.parametrize('bad_formatting', [
    None,
    {},
    {'title_font': 'Arial'},
    {'title_font': '', 'title_size': True, 'heading_font': '', 'heading_size': 0, 'body_font': '', 'body_size': 0},
    {'title_font': '', 'title_size': 97, 'heading_font': '', 'heading_size': 0, 'body_font': '', 'body_size': 0},
    {'title_font': 'C:\\private', 'title_size': 18, 'heading_font': '', 'heading_size': 0, 'body_font': '', 'body_size': 0},
    {'title_font': 'A\nB', 'title_size': 18, 'heading_font': '', 'heading_size': 0, 'body_font': '', 'body_size': 0},
])
def test_invalid_document_typography_is_rejected(bad_formatting):
    assert parse_model_generated_file_response(_envelope([
        _document_spec('docx', formatting=bad_formatting),
    ])) is None


def test_typography_is_not_silently_ignored_for_pdf():
    formatting = {
        'title_font': 'Times New Roman', 'title_size': 18,
        'heading_font': '', 'heading_size': 0, 'body_font': '', 'body_size': 0,
    }
    assert parse_model_generated_file_response(_envelope([
        _document_spec('pdf', formatting=formatting),
    ])) is None


@pytest.mark.parametrize('margin,spacing', [(True, 'single'), (719, 'single'), (2881, 'single'), (1440, 'custom')])
def test_invalid_document_layout_is_rejected(margin, spacing):
    formatting = {
        'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
        'body_font': '', 'body_size': 0, 'margin_twips': margin, 'line_spacing': spacing,
    }
    assert parse_model_generated_file_response(_envelope([
        _document_spec('docx', formatting=formatting),
    ])) is None


def test_missing_typography_declaration_cannot_claim_a_formatted_file():
    spec = _document_spec('docx')
    del spec['formatting']
    response = json.dumps({
        'chat_response': 'I created the Word document with the requested formatting.',
        'files': [spec],
    })
    text, files = decode_model_generated_file_response(response)
    assert files == []
    assert text == 'The requested files or formatting could not be validated. No files were created.'
    schema = build_model_generated_file_response_format()['json_schema']['schema']
    file_schema = schema['properties']['files']['items']
    assert 'formatting' in file_schema['required']
    assert set(file_schema['properties']['formatting']['required']) == {
        'title_font', 'title_size', 'heading_font', 'heading_size', 'body_font', 'body_size',
        'margin_twips', 'line_spacing',
    }


def test_title_typography_without_an_unambiguous_source_title_fails_closed():
    from functions_document_source_copy import SourceCopyError

    formatting = {
        'title_font': 'Times New Roman', 'title_size': 18,
        'heading_font': '', 'heading_size': 0, 'body_font': '', 'body_size': 0,
    }
    declaration = _document_spec('docx', row_source='prior_assistant', formatting=formatting)
    declaration['blocks'] = []
    with pytest.raises(SourceCopyError):
        build_generated_file_export(
            'Create a Word document.', 'Preparing the document.',
            structured_file_spec=declaration,
            document_source_loader=lambda mode: {'id': 'answer', 'content': 'Text without a title.'},
        )


@pytest.mark.parametrize(('prompt', 'expected'), [
    ('Create a pdf of the instructions instead of a word document', ['pdf']),
    ('Create a Word document instead of a PDF.', ['docx']),
    ('Create a PDF rather than a Word document.', ['pdf']),
    ('Create both a Word document and PDF report.', ['docx', 'pdf']),
    ('Create a PDF and a Word document instead of a summary.', ['pdf', 'docx']),
])
def test_replaced_document_format_is_not_requested(prompt, expected):
    assert get_requested_generated_file_formats(prompt) == expected


def test_pdf_instead_of_word_accepts_a_pdf_only_declaration():
    prompt = 'Create a pdf of the instructions instead of a word document'
    assert generated_file_specs_match_request(prompt, [_document_spec('pdf')])
    assert not generated_file_specs_match_request(prompt, [_document_spec('pdf'), _document_spec('docx')])


def test_chat_document_hint_uses_the_authorized_current_turn_and_not_csv():
    from functions_document_source_resolution import (
        DocumentSourceUnavailable, append_document_copy_source_hint, document_recovery_choice,
    )

    route_path = APP / 'route_backend_chats.py'
    parsed = ast.parse(route_path.read_text(encoding='utf-8'), filename=str(route_path))
    helper = next(
        node for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == '_append_chat_document_copy_source_hint'
    )
    calls = []

    def load(user_id, conversation_id, message_id, source_mode, depth_out=None):
        if source_mode == 'named_assistant':
            raise DocumentSourceUnavailable('No named source.')
        calls.append((user_id, conversation_id, message_id, source_mode))
        return {'id': 'private-id', 'content': 'private source'}

    namespace = {
        'append_document_copy_source_hint': append_document_copy_source_hint,
        '_load_chat_document_copy_source': load,
        '_read_chat_pending_document_copy': lambda user_id, conversation_id: None,
        '_chat_has_unverified_document_reference': lambda *args: False,
        '_is_document_reference_continuation': lambda *args: True,
        'DocumentSourceUnavailable': DocumentSourceUnavailable,
        'document_recovery_choice': document_recovery_choice,
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(route_path), 'exec'), namespace)
    append_hint = namespace['_append_chat_document_copy_source_hint']
    messages = [{'role': 'user', 'content': 'Generate a PDF instead.'}]
    append_hint(messages, ['pdf'], 'user', 'conversation', 'current-turn')
    assert calls == [('user', 'conversation', 'current-turn', 'prior_document')]
    assert 'row_source="prior_document"' in messages[0]['content']
    csv_messages = [{'role': 'user', 'content': 'Generate a CSV.'}]
    append_hint(csv_messages, ['csv'], 'user', 'conversation', 'current-turn')
    assert len(calls) == 1
    assert len(csv_messages) == 1


def test_document_format_clarifications_retain_every_originally_requested_format():
    pending_formats = resolve_pending_generated_file_formats(
        'Use the standard report sections.',
        'What sections should the report include?',
        'Create both a Word document and PDF report.',
    )

    assert pending_formats == ['docx', 'pdf']


def test_formatting_question_keeps_document_format_available_for_structured_decision():
    pending_formats = resolve_pending_generated_file_formats(
        'Use your best judgment.',
        'Before I generate the Word file, what font and size would you like for the title and section headings?',
        'Generate a Word file of the Brief Data Strategy Development Plan.',
    )
    assert pending_formats == ['docx']


def test_numbered_formatting_and_content_questions_keep_word_request_available():
    pending_formats = resolve_pending_generated_file_formats(
        '1. Body only, use your best judgment.\n2. One inch margins and 1.5 spacing.\n3. Yes.',
        'I have enough content to generate the Word file. Before I create it, please confirm these formatting choices:\n'
        '1. Should I use Bookman Old Style 12 pt for the title and headings as well, or only the body text?\n'
        '2. Do you want standard 1-inch margins and single spacing?\n'
        '3. Should I include the recommendation that Engineering owns DevSecOps operationally?',
        'Generate a word file with the devsecops plan.',
    )
    assert pending_formats == ['docx']


@pytest.mark.parametrize('abbreviated', [False, True])
def test_unverified_named_source_blocks_model_document_after_clarification(abbreviated):
    from copy import deepcopy
    from functions_document_source_resolution import (
        DocumentSourceAmbiguousReference,
        DocumentSourceUnavailable,
        append_document_copy_source_hint,
        document_recovery_choice,
        load_document_copy_source_from_container,
    )

    records = {
        'plan': {
            'id': 'plan', 'conversation_id': 'conversation', 'role': 'assistant',
            'timestamp': '2026-01-01T00:00:00Z', 'content': '# DevSecOps adoption plan\n\nOriginal.',
            'metadata': {},
        },
        'other': {
            'id': 'other', 'conversation_id': 'conversation', 'role': 'assistant',
            'timestamp': '2026-01-01T00:00:01Z', 'content': '# Ownership\n\nRecommendation.',
            'metadata': {},
        },
        'request': {
            'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
            'timestamp': '2026-01-01T00:00:02Z',
            'content': 'Generate a Word file with the devsecops plan.' if abbreviated
            else 'Create a new Word document about deployment.',
            'metadata': {},
        },
        'clarification': {
            'id': 'clarification', 'conversation_id': 'conversation', 'role': 'assistant',
            'timestamp': '2026-01-01T00:00:03Z',
            'content': (
                'I have enough content to generate the Word file. Before I create it, please confirm:\n'
                '1. Should I use Bookman Old Style 12 pt for the title and headings or only the body text?\n'
                '2. Do you want standard 1-inch margins and single spacing?\n'
                '3. Should I include the recommendation that Engineering owns DevSecOps operationally?'
            ),
            'metadata': {},
        },
        'reply': {
            'id': 'reply', 'conversation_id': 'conversation', 'role': 'user',
            'timestamp': '2026-01-01T00:00:04Z',
            'content': '1. Body only, use your best judgment.\n2. One inch margins and 1.5 spacing.\n3. Yes.',
            'metadata': {},
        },
    }

    class Container:
        def read_item(self, item, partition_key):
            return deepcopy(records[item])

        def query_items(self, query, parameters, partition_key):
            values = {item['name']: item['value'] for item in parameters}
            before = values.get('@before_timestamp')
            results = [
                deepcopy(item) for item in records.values()
                if item['role'] == values['@role'] and (not before or item['timestamp'] < before)
            ]
            return sorted(results, key=lambda item: item['timestamp'], reverse=True)[:2]

    namespace = {
        'cosmos_messages_container': Container(),
        '_authorize_personal_conversation_access': lambda *args: None,
        'load_document_copy_source_from_container': load_document_copy_source_from_container,
        'DocumentSourceAmbiguousReference': DocumentSourceAmbiguousReference,
        'DocumentSourceUnavailable': DocumentSourceUnavailable,
        'append_document_copy_source_hint': append_document_copy_source_hint,
        'get_requested_generated_file_formats': get_requested_generated_file_formats,
        'resolve_pending_generated_file_formats': resolve_pending_generated_file_formats,
        'document_recovery_choice': document_recovery_choice,
        '_read_chat_pending_document_copy': lambda *args: None,
        're': re,
        'log_event': lambda *args, **kwargs: None,
        'logging': logging,
    }
    for name in (
        '_read_recent_assistant_messages', '_load_chat_document_copy_source',
        '_chat_has_unverified_document_reference', '_unverified_document_reference_notice',
        '_is_document_reference_continuation',
        '_document_recovery_publication_error', '_append_chat_document_copy_source_hint',
        '_build_chat_pending_document_copy',
    ):
        _load_function(APP / 'route_backend_chats.py', name, namespace)
    error = namespace['_document_recovery_publication_error'](
        records['reply']['content'], [_document_spec('docx')], 'user', 'conversation', 'reply',
    )
    if abbreviated:
        assert error and 'complete heading' in error
        confirmation = 'Yes, create the Word document with those choices.'
        original_reply = records['reply']['content']
        records['reply']['content'] = confirmation
        error = namespace['_document_recovery_publication_error'](
            confirmation, [_document_spec('docx')], 'user', 'conversation', 'reply',
        )
        assert error and 'complete heading' in error
        unrelated = 'No, create a Word document about a different subject.'
        records['reply']['content'] = unrelated
        error = namespace['_document_recovery_publication_error'](
            unrelated, [_document_spec('docx')], 'user', 'conversation', 'reply',
        )
        assert error is None
        records['reply']['content'] = original_reply
        messages = [{'role': 'user', 'content': records['reply']['content']}]
        namespace['_append_chat_document_copy_source_hint'](
            messages, ['docx'], 'user', 'conversation', 'reply',
        )
        assert 'identity has not been verified' in messages[0]['content']
        tracking = {}
        pending = namespace['_build_chat_pending_document_copy'](
            {'file_specs': [], 'pending_copy_source': 'none'}, ['docx'],
            'user', 'conversation', 'reply', tracking,
            'I cannot create a Word document.', records['reply']['content'],
        )
        assert pending is None
        assert 'complete heading' in tracking['notice']
        for unrelated_reply in ('No, thanks.', 'What is the weather?'):
            records['reply']['content'] = unrelated_reply
            messages = [{'role': 'user', 'content': unrelated_reply}]
            namespace['_append_chat_document_copy_source_hint'](
                messages, ['docx'], 'user', 'conversation', 'reply',
            )
            assert messages == [{'role': 'user', 'content': unrelated_reply}]
            tracking = {}
            pending = namespace['_build_chat_pending_document_copy'](
                {'file_specs': [], 'pending_copy_source': 'none'}, ['docx'],
                'user', 'conversation', 'reply', tracking, 'Understood.', unrelated_reply,
            )
            assert pending is None
            assert 'notice' not in tracking
        records['reply']['content'] = 'RECREATE DOCUMENT: Create a Word file based on the devsecops plan.'
        error = namespace['_document_recovery_publication_error'](
            records['reply']['content'], [_document_spec('docx')], 'user', 'conversation', 'reply',
        )
        assert error is None
        copy_error = namespace['_document_recovery_publication_error'](
            records['reply']['content'], [_document_spec('docx', row_source='prior_assistant')],
            'user', 'conversation', 'reply',
        )
        assert copy_error
        messages = [{'role': 'user', 'content': records['reply']['content']}]
        namespace['_append_chat_document_copy_source_hint'](
            messages, ['docx'], 'user', 'conversation', 'reply',
        )
        assert 'row_source="model"' in messages[0]['content']
        assert 'not a verbatim copy' in messages[0]['content']
        def deny(*args):
            raise PermissionError('Access revoked')

        namespace['_authorize_personal_conversation_access'] = deny
        with pytest.raises(PermissionError):
            namespace['_chat_has_unverified_document_reference'](
                'Yes.', 'user', 'conversation', 'reply',
            )
    else:
        assert error is None


def test_explicit_reconstruction_can_include_requested_addition_and_formatting():
    from io import BytesIO
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING

    spec = _document_spec(
        blocks=[
            _block('heading', text='DevSecOps adoption plan', level=1),
            _block('paragraph', text='A reconstructed plan.'),
            _block('paragraph', text='Engineering owns operations.'),
        ],
        formatting={
            'title_font': '', 'title_size': 0, 'heading_font': 'Arial', 'heading_size': 0,
            'body_font': 'Bookman Old Style', 'body_size': 12,
            'margin_twips': 1440, 'line_spacing': 'one_and_half',
        },
    )
    payload = build_generated_file_export(
        'RECREATE DOCUMENT: Create a Word file based on the DevSecOps plan and include the ownership recommendation.',
        'Preparing a reconstruction.', structured_file_spec=spec,
    )
    document = Document(BytesIO(payload['file_content']))
    text = [paragraph.text for paragraph in document.paragraphs]
    assert 'A reconstructed plan.' in text
    assert 'Engineering owns operations.' in text
    assert document.styles['Normal'].font.name == 'Bookman Old Style'
    assert document.styles['Normal'].font.size.pt == 12
    assert document.sections[0].left_margin.inches == 1
    assert document.paragraphs[1].paragraph_format.line_spacing_rule == WD_LINE_SPACING.ONE_POINT_FIVE


def test_document_statement_clarification_keeps_pending_pdf_request():
    pending_formats = resolve_pending_generated_file_formats(
        'The security guide list in the earlier response.',
        "Please paste or resend the list you want included in the PDF, and I'll create it.",
        'I changed my mind, create a PDF instead.',
    )
    assert pending_formats == ['pdf']
    assert resolve_pending_generated_file_formats(
        'Here is the list.',
        'Please paste the list so I can review it.',
        'Summarize this document.',
    ) == []
    assert resolve_pending_generated_file_formats(
        'No, thanks.',
        'I can provide a list of alternative PDF tools.',
        'Create a PDF.',
    ) == []


def test_structured_document_clarification_declares_pending_copy_without_files():
    response = json.dumps({
        'chat_response': 'Please confirm the heading font.',
        'files': [],
        'pending_copy_source': 'prior_assistant',
    })
    parsed = parse_model_generated_file_response(response)
    assert parsed['pending_copy_source'] == 'prior_assistant'
    assert parsed['file_specs'] == []
    assert 'pending_copy_source' in build_model_generated_file_response_format()['json_schema']['schema']['required']

    invalid = json.loads(response)
    invalid['pending_copy_source'] = 'arbitrary-id'
    assert parse_model_generated_file_response(json.dumps(invalid)) is None
    invalid['chat_response'] = 'I created the file.'
    visible, files = decode_model_generated_file_response(json.dumps(invalid))
    assert visible == 'The requested files or formatting could not be validated. No files were created.'
    assert files == []
    invalid['pending_copy_source'] = 'prior_assistant'
    invalid['files'] = [_document_spec()]
    assert parse_model_generated_file_response(json.dumps(invalid)) is None


def test_incomplete_pending_copy_envelope_is_not_exposed():
    response = sanitize_unstructured_model_generated_file_response(
        '{"pending_copy_source":"prior_assistant"'
    )
    assert response == 'The model could not produce a valid response. Please try again.'


@pytest.mark.parametrize('pending_source', ['none', None, 'invalid'])
def test_chat_does_not_retain_a_source_without_a_valid_pending_declaration(pending_source):
    from functions_document_source_resolution import DocumentSourceUnavailable, create_pending_document_copy

    calls = []
    helper = _load_function(
        APP / 'route_backend_chats.py',
        '_build_chat_pending_document_copy',
        {
            '_load_chat_document_copy_source': lambda *args: calls.append(args),
            'create_pending_document_copy': create_pending_document_copy,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
        },
    )
    pending = helper(
        {'file_specs': [], 'pending_copy_source': pending_source},
        ['docx'], 'user', 'conversation', 'request',
    )
    assert pending is None
    assert calls == []


def test_pdf_followup_keeps_prior_answer_and_model_declaration():
    import fitz
    from test_chat_stream_history_context_fix import load_history_helpers

    messages = [
        {'id': 'u1', 'role': 'user', 'content': 'Give me a checklist.', 'metadata': {}},
        {'id': 'a1', 'role': 'assistant', 'content': 'Checklist: alpha, beta.', 'metadata': {}},
        {'id': 'retry', 'role': 'user', 'content': 'Old attempt', 'metadata': {'thread_info': {'active_thread': False}}},
        {'id': 'u2', 'role': 'user', 'content': 'Create a Word document.', 'metadata': {}},
        {'id': 'artifact', 'role': 'file', 'metadata': {'is_generated_chat_artifact': True}},
        {'id': 'a2', 'role': 'assistant', 'content': 'Document ready.', 'metadata': {}},
        {'id': 'u3', 'role': 'user', 'content': 'Create a PDF instead.', 'metadata': {}},
        {'id': 'a3', 'role': 'assistant', 'content': 'Please resend the list for the PDF.', 'metadata': {}},
        {'id': 'u4', 'role': 'user', 'content': 'The earlier checklist.', 'metadata': {}},
    ]
    history = load_history_helpers()[0]['build_conversation_history_segments'](
        messages, 6, user_message_id='u4', include_assistant_citation_context=False,
    )['history_messages']
    formats = resolve_pending_generated_file_formats(
        messages[-1]['content'], messages[-2]['content'], messages[-3]['content'],
    )
    response_format = prepare_model_generated_file_response(
        history, 'gpt-4.1', requested_formats=formats,
    )
    assert response_format == build_model_generated_file_response_format()
    assert any(message.get('content') == 'Checklist: alpha, beta.' for message in history)

    response, specs = decode_model_generated_file_response(_envelope([
        _document_spec('pdf', blocks=[_block('paragraph', text='Checklist: alpha, beta.')]),
    ]))
    payload = build_generated_file_export(
        messages[-1]['content'], response, pending_output_format=formats[0], structured_file_spec=specs[0],
    )
    assert payload is not None
    doc = fitz.open(stream=payload['file_content'], filetype='pdf')
    try:
        pdf_text = '\n'.join(page.get_text() for page in doc)
    finally:
        doc.close()
    assert 'Checklist: alpha, beta.' in pdf_text


def test_structured_file_envelope_validates_mixed_formats_and_inline_answer():
    files = [_document_spec('docx'), _document_spec('pdf')]
    parsed = parse_model_generated_file_response(_envelope(files))

    assert parsed is not None
    assert [spec['format'] for spec in parsed['file_specs']] == ['docx', 'pdf']
    assert parse_model_generated_file_response(_envelope([])) == {
        'chat_response': 'The analysis is ready.',
        'file_specs': [],
    }


def test_document_source_copy_declaration_is_distinct_from_generated_content():
    for mode in ('prior_assistant', 'prior_document'):
        declaration = _document_spec('docx', row_source=mode, blocks=[])
        declaration['blocks'] = []
        parsed = parse_model_generated_file_response(_envelope([declaration]))
        assert parsed is not None
        assert parsed['file_specs'][0]['row_source'] == mode
        assert parsed['file_specs'][0]['blocks'] == []
        assert parse_model_generated_file_response(_envelope([
            dict(declaration, source_action='arbitrary-message-id'),
        ])) is None
        assert parse_model_generated_file_response(_envelope([
            dict(declaration, blocks=[_block('paragraph', text='replacement')]),
        ])) is None


def test_source_copy_can_coexist_with_csv_without_extending_csv_sources():
    document = _document_spec('pdf', row_source='prior_assistant')
    document['blocks'] = []
    csv = {
        'format': 'csv',
        'title': 'People',
        'columns': ['Name'],
        'rows': [['Ada']],
        'row_source': 'model',
        'source_action': '',
        'blocks': [],
        'formatting': {
            'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
            'body_font': '', 'body_size': 0,
        },
    }
    parsed = parse_model_generated_file_response(_envelope([csv, document]))
    assert parsed is not None
    assert [item['format'] for item in parsed['file_specs']] == ['csv', 'pdf']
    assert parse_model_generated_file_response(_envelope([
        dict(csv, row_source='prior_document', rows=[]),
    ])) is None


def test_mixed_csv_and_word_declarations_are_both_renderable_after_validation():
    csv = {
        'format': 'csv', 'title': 'People', 'columns': ['Name'], 'rows': [['Ada']],
        'row_source': 'model', 'source_action': '', 'blocks': [],
        'formatting': {
            'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
            'body_font': '', 'body_size': 0,
        },
    }
    response, specs = decode_model_generated_file_response(_envelope([csv, _document_spec('docx')]))
    payloads = [
        build_generated_file_export(
            'Create a CSV and Word document.', response, structured_file_spec=spec,
        )
        for spec in specs
    ]
    assert len(payloads) == 2
    assert all(payloads)
    assert 'Ada' in payloads[0]['file_content']
    assert payloads[1]['file_content'].startswith(b'PK')


def test_document_source_copy_without_authorized_loader_fails_closed():
    from functions_document_source_resolution import DocumentSourceUnavailable

    declaration = _document_spec('docx', row_source='prior_assistant')
    declaration['blocks'] = []
    with pytest.raises(DocumentSourceUnavailable):
        build_generated_file_export(
            'Create a Word document from the previous answer.',
            'Preparing the document.',
            structured_file_spec=declaration,
        )


@pytest.mark.parametrize('output_format', ['docx', 'pdf'])
@pytest.mark.parametrize('citation_field', ['hybrid_citations', 'cited_web_search_citations'])
def test_document_source_copy_builder_uses_authorized_source_without_model_rewriting(output_format, citation_field):
    source = {
        'id': 'earlier-answer',
        'content': '# Checklist\n\n- Exact first item\n- Exact second item',
        citation_field: [{'title': 'Referenced document'}],
    }
    declaration = _document_spec(output_format, row_source='prior_assistant')
    declaration['blocks'] = []
    seen = []

    def load_source(mode):
        seen.append(mode)
        return source

    payload = build_generated_file_export(
        'Create a Word document and PDF from the previous answer.',
        'Preparing the files.',
        structured_file_spec=declaration,
        document_source_loader=load_source,
    )
    assert seen == ['prior_assistant']
    assert payload['source_copy_message_id'] == 'earlier-answer'
    assert payload['_source_copy_snapshot'] == source
    assert 'References displayed separately' in payload['summary']
    assert payload['file_content']


@pytest.mark.parametrize('content', [
    'What sections should the report include?',
    'Please paste or resend the list you want included in the PDF.',
])
def test_document_source_copy_does_not_export_a_clarification(content):
    from functions_document_source_resolution import DocumentSourceUnavailable

    declaration = _document_spec('docx', row_source='prior_assistant')
    declaration['blocks'] = []
    with pytest.raises(DocumentSourceUnavailable):
        build_generated_file_export(
            'Create a Word document from the previous answer.',
            'Preparing the document.',
            structured_file_spec=declaration,
            document_source_loader=lambda mode: {'id': 'clarification', 'content': content},
        )


def test_malformed_and_oversized_document_declarations_fail_closed():
    invalid_specs = [
        _document_spec('docx', blocks=[_block('paragraph', text='x')], source_action='arbitrary'),
        _document_spec('docx', blocks=[_block('table', columns=['A'], rows=[['A', 'extra']])]),
        _document_spec('docx', blocks=[_block('paragraph', text='x' * 20001)]),
        _document_spec('docx', blocks=[_block('paragraph', text='x')], filesystem_path='C:\\secret'),
    ]
    for spec in invalid_specs:
        assert parse_model_generated_file_response(_envelope([spec])) is None
    assert parse_model_generated_file_response(_envelope([_document_spec(), _document_spec()])) is None
    assert decode_model_generated_file_response('{"chat_response":"safe","files":[{"format":"pdf"}]}') == (
        'The requested files or formatting could not be validated. No files were created.',
        [],
    )


def test_unstructured_fallback_never_exposes_file_envelopes():
    assert sanitize_unstructured_model_generated_file_response(
        _envelope([_document_spec('pdf')])
    ) == 'The analysis is ready.'
    assert sanitize_unstructured_model_generated_file_response(
        '{"chat_response":"safe","files":[{"format":"pdf"'
    ) == 'The model could not produce a valid response. Please try again.'
    assert sanitize_unstructured_model_generated_file_response(
        '```json\n{"chat_response":"safe","files":'
    ) == 'The model could not produce a valid response. Please try again.'
    assert sanitize_unstructured_model_generated_file_response(
        'Fallback: {"chat_response":"safe","files":'
    ) == 'The model could not produce a valid response. Please try again.'
    assert sanitize_unstructured_model_generated_file_response(
        'A PDF may contain structured text.'
    ) == 'A PDF may contain structured text.'


def test_document_models_receive_structured_schema_for_explicit_outputs_only():
    messages = [{'role': 'user', 'content': 'Create a PDF report.'}]
    response_format = prepare_model_generated_file_response(messages, 'gpt-4.1')
    assert response_format == build_model_generated_file_response_format()
    assert messages[0]['content'] == MODEL_GENERATED_FILE_GUIDANCE

    inline_messages = [{'role': 'user', 'content': 'Summarize this PDF.'}]
    assert prepare_model_generated_file_response(inline_messages, 'gpt-4.1') is None
    assert inline_messages == [{'role': 'user', 'content': 'Summarize this PDF.'}]

    unsupported_messages = [{'role': 'user', 'content': 'Create a Word report.'}]
    assert prepare_model_generated_file_response(unsupported_messages, 'unknown-model') is None
    assert unsupported_messages[0]['content'] == MODEL_UNSTRUCTURED_FILE_GUIDANCE


def test_docx_and_pdf_render_semantic_blocks_with_safe_text_and_pagination():
    import fitz
    from docx import Document

    blocks = [
        _block('heading', text='Findings', level=1),
        _block('paragraph', text='Unicode résumé — <script> & "quotes".'),
        _block('list', items=['Alpha', 'βeta']),
        _block('table', columns=['Name', 'Value'], rows=[['Ada', 'one & <two>']]),
        _block('paragraph', text='Long detail. ' * 1400),
    ]
    docx_payload = build_generated_file_export(
        'Create a Word report.',
        'The analysis is ready.',
        structured_file_spec=_document_spec('docx', blocks=blocks),
    )
    pdf_payload = build_generated_file_export(
        'Create a PDF report.',
        'The analysis is ready.',
        structured_file_spec=_document_spec('pdf', blocks=blocks),
    )

    assert docx_payload is not None and docx_payload['file_content'].startswith(b'PK')
    assert pdf_payload is not None and pdf_payload['file_content'].startswith(b'%PDF')
    docx = Document(__import__('io').BytesIO(docx_payload['file_content']))
    docx_text = '\n'.join(paragraph.text for paragraph in docx.paragraphs)
    assert 'Findings' in docx_text
    assert 'βeta' in docx_text
    assert docx.tables[0].cell(1, 1).text == 'one & <two>'

    pdf = fitz.open(stream=pdf_payload['file_content'], filetype='pdf')
    pdf_text = '\n'.join(page.get_text() for page in pdf)
    assert 'Findings' in pdf_text
    assert 'résumé' in pdf_text
    assert '<script>' in pdf_text
    assert 'one & <two>' in pdf_text
    assert len(pdf) > 1


def test_document_preview_reflects_file_blocks_not_assistant_confirmation():
    import io
    import fitz
    from docx import Document

    blocks = [
        _block('heading', text='Security checklist', level=1),
        _block('list', items=['First instruction', 'Second instruction']),
        _block('paragraph', text='Additional guidance.'),
    ]
    for output_format, request in [('docx', 'Create a Word document.'), ('pdf', 'Create a PDF.')]:
        payload = build_generated_file_export(
            request,
            'I created the requested document.',
            structured_file_spec=_document_spec(output_format, blocks=blocks),
        )

        assert payload is not None
        assert payload['preview_lines'] == [
            'Security checklist',
            'First instruction',
            'Second instruction',
        ]
        if output_format == 'docx':
            doc = Document(io.BytesIO(payload['file_content']))
            actual_text = '\n'.join(paragraph.text for paragraph in doc.paragraphs)
        else:
            doc = fitz.open(stream=payload['file_content'], filetype='pdf')
            actual_text = '\n'.join(page.get_text() for page in doc)
            doc.close()
        assert all(line in actual_text for line in payload['preview_lines'])
        assert 'I created the requested document.' not in actual_text


def test_document_preview_includes_table_data_and_bounds_long_lines():
    blocks = [
        _block('table', columns=['Item', 'Details'], rows=[['One', 'x' * 300]]),
        _block('paragraph', text='After table'),
    ]
    payload = build_generated_file_export(
        'Create a Word document.',
        'The document is ready.',
        structured_file_spec=_document_spec('docx', blocks=blocks),
    )

    assert payload is not None
    assert payload['preview_lines'] == ['Item | Details', 'One | ' + 'x' * 234, 'After table']


def _load_function(path, function_name, namespace):
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(path))
    function_nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    ]
    assert len(function_nodes) == 1
    module = ast.Module(body=function_nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[function_name]


@pytest.mark.parametrize('output_format', ['docx', 'pdf'])
@pytest.mark.parametrize('source_mode', ['prior_assistant', 'named_assistant', 'named_candidate'])
def test_chat_pending_copy_flow_retains_original_content_and_formatting(output_format, source_mode):
    from copy import deepcopy
    from io import BytesIO

    import fitz
    from docx import Document
    from functions_document_source_resolution import (
        DocumentSourceAmbiguousReference,
        DocumentSourceUnavailable,
        MAX_PENDING_DOCUMENT_COPY_DEPTH,
        append_document_copy_source_hint,
        create_pending_document_copy,
        document_clarification_notice,
        document_recovery_choice,
        load_document_copy_source_from_container,
    )

    conversation_id = 'conversation'
    original = {
        'id': 'original', 'conversation_id': conversation_id, 'role': 'assistant',
        'timestamp': '2026-01-01T00:00:00Z',
        'content': '# Plan\n\n## Preparation\n\nOriginal content.\n\n- First item\n- Second item',
        'metadata': {},
    }
    request = {
        'id': 'request', 'conversation_id': conversation_id, 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
        'content': (
            f'Convert the Plan to a {output_format} document.' if source_mode != 'prior_assistant'
            else f'Convert that to a {output_format} document.'
        ),
    }
    records = {item['id']: deepcopy(item) for item in [original, request]}
    if source_mode != 'prior_assistant':
        original['content'] = original['content'].replace('# Plan', '# Detailed Plan')
        records['original'] = deepcopy(original)
        request['content'] = f'Convert the Detailed Plan to a {output_format} document.'
        records['request'] = deepcopy(request)
        records['other'] = {
            'id': 'other', 'conversation_id': conversation_id, 'role': 'assistant',
            'timestamp': '2026-01-01T12:00:00Z', 'content': '# Unrelated answer\n\nOther content.',
            'metadata': {},
        }
    authorization_checks = []

    class Container:
        def read_item(self, item, partition_key):
            assert partition_key == conversation_id
            return deepcopy(records[item])

        def query_items(self, query, parameters, partition_key):
            assert partition_key == conversation_id
            values = {item['name']: item['value'] for item in parameters}
            before = values.get('@before_timestamp')
            results = [
                deepcopy(item) for item in records.values()
                if item['role'] == values['@role'] and (not before or item['timestamp'] < before)
            ]
            return sorted(results, key=lambda item: item['timestamp'], reverse=True)[:2]

    def authorize(user_id, target_conversation):
        authorization_checks.append((user_id, target_conversation))
        assert (user_id, target_conversation) == ('user', conversation_id)

    namespace = {
        'cosmos_messages_container': Container(),
        '_authorize_personal_conversation_access': authorize,
        'load_document_copy_source_from_container': load_document_copy_source_from_container,
        'create_pending_document_copy': create_pending_document_copy,
        'append_document_copy_source_hint': append_document_copy_source_hint,
        'DocumentSourceAmbiguousReference': DocumentSourceAmbiguousReference,
        'DocumentSourceUnavailable': DocumentSourceUnavailable,
        'MAX_PENDING_DOCUMENT_COPY_DEPTH': MAX_PENDING_DOCUMENT_COPY_DEPTH,
        'document_clarification_notice': document_clarification_notice,
        'document_recovery_choice': document_recovery_choice,
        'log_event': lambda *args, **kwargs: None,
        'logging': logging,
        'get_requested_generated_file_formats': get_requested_generated_file_formats,
        'get_tabular_generated_output_format': lambda text: None,
        'resolve_pending_generated_file_formats': resolve_pending_generated_file_formats,
        're': re,
    }
    route_path = APP / 'route_backend_chats.py'
    for name in (
        '_read_recent_assistant_messages', '_load_chat_document_copy_source',
        '_chat_has_unverified_document_reference', '_is_document_reference_continuation',
        '_read_chat_pending_document_copy', '_append_chat_document_copy_source_hint',
        '_build_chat_pending_document_copy', '_resolve_pending_generated_file_formats',
        '_resolve_generated_file_guidance_formats',
    ):
        _load_function(route_path, name, namespace)

    if source_mode != 'prior_assistant':
        initial_messages = [{'role': 'user', 'content': request['content']}]
        namespace['_append_chat_document_copy_source_hint'](
            initial_messages, [output_format], 'user', conversation_id, request['id'],
        )
        assert 'pending_copy_source="named_assistant"' in initial_messages[0]['content']
    response = parse_model_generated_file_response(json.dumps({
        'chat_response': 'What font should the heading use?' if source_mode == 'named_candidate'
        else 'Please confirm the heading font, or provide an alternative.',
        'files': [], 'pending_copy_source': 'none' if source_mode == 'named_candidate' else source_mode,
    }))
    state = {'file_specs': response['file_specs'], 'pending_copy_source': response['pending_copy_source']}
    tracking = {}
    pending = namespace['_build_chat_pending_document_copy'](
        state, [output_format], 'user', conversation_id, request['id'], tracking,
        response['chat_response'], request['content'],
    )
    if source_mode == 'named_candidate':
        assert pending is None
        pending = tracking['candidate']
    assert pending['source_message_id'] == original['id']
    clarification = {
        'id': 'clarification', 'conversation_id': conversation_id, 'role': 'assistant',
        'timestamp': '2026-01-03T00:00:00Z', 'content': response['chat_response'],
        'metadata': {
            'pending_document_candidate' if source_mode == 'named_candidate' else 'pending_document_copy': pending,
        },
    }
    followup = {
        'id': 'followup', 'conversation_id': conversation_id, 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'content': 'Use Bookman Old Style instead.',
        'metadata': {},
    }
    records.update({item['id']: deepcopy(item) for item in [clarification, followup]})
    formats = namespace['_resolve_generated_file_guidance_formats'](
        followup['content'], conversation_id, 'user',
    )
    assert formats == [output_format]
    messages = [{'role': 'user', 'content': followup['content']}]
    namespace['_append_chat_document_copy_source_hint'](
        messages, formats, 'user', conversation_id, followup['id'],
    )
    assert 'row_source="pending_copy"' in messages[0]['content']
    assert original['content'] not in messages[0]['content']

    formatting = {
        'title_font': '', 'title_size': 0, 'heading_font': 'Bookman Old Style', 'heading_size': 14,
        'body_font': '', 'body_size': 0,
    } if output_format == 'docx' else {
        'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
        'body_font': '', 'body_size': 0,
    }
    if source_mode == 'named_candidate' and output_format == 'docx':
        formatting.update({
            'body_font': 'Papyrus', 'body_size': 14,
            'margin_twips': 1440, 'line_spacing': 'single',
        })
    spec = _document_spec(output_format, row_source='pending_copy', formatting=formatting)
    spec['blocks'] = []
    declaration = parse_model_generated_file_response(json.dumps({
        'chat_response': 'Preparing the file.', 'files': [spec], 'pending_copy_source': 'none',
    }))
    payload = build_generated_file_export(
        followup['content'], declaration['chat_response'],
        structured_file_spec=declaration['file_specs'][0], pending_output_format=output_format,
        document_source_loader=lambda mode: namespace['_load_chat_document_copy_source'](
            'user', conversation_id, followup['id'], mode,
        ),
    )
    assert payload['source_copy_message_id'] == original['id']
    if output_format == 'docx':
        document = Document(BytesIO(payload['file_content']))
        assert [paragraph.text for paragraph in document.paragraphs] == [
            'Detailed Plan' if source_mode != 'prior_assistant' else 'Plan',
            'Preparation', 'Original content.', 'First item', 'Second item',
        ]
        if source_mode == 'named_candidate':
            from docx.enum.text import WD_LINE_SPACING
            assert document.styles['Normal'].font.name == 'Papyrus'
            assert document.styles['Normal'].font.size.pt == 14
            assert document.sections[0].left_margin.inches == 1
            assert document.paragraphs[2].paragraph_format.line_spacing_rule == WD_LINE_SPACING.SINGLE
        assert document.paragraphs[1].style.font.name == 'Bookman Old Style'
        assert document.paragraphs[1].style.font.size.pt == 14
    else:
        with fitz.open(stream=payload['file_content'], filetype='pdf') as document:
            text = ''.join(page.get_text() for page in document)
        assert all(value in text for value in ('Plan', 'Preparation', 'Original content.', 'First item', 'Second item'))
    assert len(authorization_checks) >= 4


def test_clarification_budget_and_recovery_require_verified_lineage():
    from copy import deepcopy
    from functions_document_source_resolution import (
        DocumentSourceUnavailable,
        MAX_PENDING_DOCUMENT_COPY_DEPTH,
        append_document_copy_source_hint,
        create_pending_document_copy,
        document_clarification_notice,
        document_recovery_choice,
        load_document_copy_source_from_container,
    )

    conversation_id = 'conversation'
    records = {
        'original': {
            'id': 'original', 'conversation_id': conversation_id, 'role': 'assistant',
            'timestamp': '2026-01-01T00:00:00Z', 'content': 'The complete original.', 'metadata': {},
        },
        'user-1': {
            'id': 'user-1', 'conversation_id': conversation_id, 'role': 'user',
            'timestamp': '2026-01-01T00:00:01Z', 'content': 'Create Word and PDF copies.', 'metadata': {},
        },
    }

    class Container:
        def read_item(self, item, partition_key):
            return deepcopy(records[item])

        def query_items(self, query, parameters, partition_key):
            values = {item['name']: item['value'] for item in parameters}
            before = values.get('@before_timestamp')
            results = [
                deepcopy(item) for item in records.values()
                if item['role'] == values['@role'] and (not before or item['timestamp'] < before)
            ]
            return sorted(results, key=lambda item: item['timestamp'], reverse=True)[:2]

    namespace = {
        'cosmos_messages_container': Container(),
        '_authorize_personal_conversation_access': lambda *args: None,
        'load_document_copy_source_from_container': load_document_copy_source_from_container,
        'create_pending_document_copy': create_pending_document_copy,
        'append_document_copy_source_hint': append_document_copy_source_hint,
        'DocumentSourceUnavailable': DocumentSourceUnavailable,
        'MAX_PENDING_DOCUMENT_COPY_DEPTH': MAX_PENDING_DOCUMENT_COPY_DEPTH,
        'document_clarification_notice': document_clarification_notice,
        'document_recovery_choice': document_recovery_choice,
        'log_event': lambda *args, **kwargs: None,
        'logging': logging,
        'get_requested_generated_file_formats': get_requested_generated_file_formats,
        'get_tabular_generated_output_format': lambda text: None,
        'resolve_pending_generated_file_formats': resolve_pending_generated_file_formats,
    }
    for name in (
        '_read_recent_assistant_messages', '_load_chat_document_copy_source',
        '_read_chat_pending_document_copy', '_append_chat_document_copy_source_hint',
        '_build_chat_pending_document_copy', '_resolve_pending_generated_file_formats',
        '_resolve_generated_file_guidance_formats', '_document_recovery_publication_error',
    ):
        _load_function(APP / 'route_backend_chats.py', name, namespace)

    formats = ['docx', 'pdf']
    for turn in range(1, 6):
        state = {'file_specs': [], 'pending_copy_source': 'prior_assistant' if turn == 1 else 'pending_copy'}
        tracking = {}
        pending = namespace['_build_chat_pending_document_copy'](
            state, formats, 'user', conversation_id, f'user-{turn}', tracking,
        )
        if turn <= 4:
            assert pending is not None
            metadata = {'pending_document_copy': pending}
        else:
            assert pending is None
            metadata = {'pending_document_recovery': tracking['recovery']}
        if turn < 3:
            assert not tracking['notice']
        else:
            assert 'RECREATE DOCUMENT' in tracking['notice']
        assistant = {
            'id': f'assistant-{turn}', 'conversation_id': conversation_id, 'role': 'assistant',
            'timestamp': f'2026-01-01T00:00:{turn * 2:02}Z', 'content': 'Which formatting?',
            'metadata': metadata,
        }
        next_user = {
            'id': f'user-{turn + 1}', 'conversation_id': conversation_id, 'role': 'user',
            'timestamp': f'2026-01-01T00:00:{turn * 2 + 1:02}Z',
            'content': 'Use the default.', 'metadata': {},
        }
        records.update({assistant['id']: assistant, next_user['id']: next_user})
        assert namespace['_document_recovery_publication_error'](
            'Use the default.', [_document_spec('docx')], 'user', conversation_id, next_user['id'],
        )
        if turn <= 4:
            messages = [{'role': 'user', 'content': next_user['content']}]
            namespace['_append_chat_document_copy_source_hint'](
                messages, formats, 'user', conversation_id, next_user['id'],
            )
            assert f'{4 - turn} more clarification(s)' in messages[-2]['content']
        if turn == 4:
            depth = []
            source = namespace['_load_chat_document_copy_source'](
                'user', conversation_id, next_user['id'], 'pending_copy', depth,
            )
            assert source['id'] == 'original'
            assert depth == [4]

    source = namespace['_load_chat_document_copy_source'](
        'user', conversation_id, 'user-6', 'recovery_copy',
    )
    assert source['id'] == 'original'
    doc = _document_spec('docx')
    guard = namespace['_document_recovery_publication_error']
    assert guard('Use the default.', [doc], 'user', conversation_id, 'user-6')
    assert guard('RECREATE DOCUMENT', [doc], 'user', conversation_id, 'user-6') is None
    assert guard('SOURCE: My source text', [doc], 'user', conversation_id, 'user-6') is None
    assert guard('do not recreate document', [doc], 'user', conversation_id, 'user-6')
    assert namespace['_resolve_generated_file_guidance_formats'](
        'SOURCE: text mentioning CSV', conversation_id, 'user',
    ) == formats
    assert namespace['_resolve_generated_file_guidance_formats'](
        'RECREATE DOCUMENT: Create a PDF instead.', conversation_id, 'user',
    ) == ['pdf']
    assert namespace['_resolve_pending_generated_file_formats'](
        'unrelated message', conversation_id, 'user',
    ) == []
    messages = [{'role': 'user', 'content': 'RECREATE DOCUMENT'}]
    namespace['_append_chat_document_copy_source_hint'](
        messages, formats, 'user', conversation_id, 'user-6',
    )
    assert 'not a verbatim copy' in messages[0]['content']
    records['original']['metadata']['masked'] = True
    depth = []
    with pytest.raises(DocumentSourceUnavailable):
        namespace['_load_chat_document_copy_source']('user', conversation_id, 'user-6', 'recovery_copy', depth)
    assert depth == []
    records['original']['metadata']['masked'] = False
    records['original']['content'] = 'Changed after authorization.'
    with pytest.raises(DocumentSourceUnavailable):
        namespace['_load_chat_document_copy_source']('user', conversation_id, 'user-6', 'recovery_copy', depth)
    assert depth == []


@pytest.mark.parametrize('source_changes', [False, True])
def test_recovery_rechecks_source_and_rolls_back_if_it_changes(source_changes):
    from copy import deepcopy
    from functions_document_source_copy import SourceCopyError
    from functions_document_source_resolution import (
        DocumentSourceCleanupError,
        DocumentSourceUnavailable,
        document_recovery_choice,
        ensure_document_copy_source_unchanged,
    )

    reads = []
    rolled_back = []
    failures = []

    def load_source(*args):
        reads.append(args[-1])
        return {'id': 'original', 'content': 'changed' if source_changes and len(reads) >= 3 else 'original'}

    helper = _load_function(
        APP / 'route_backend_chats.py',
        'maybe_create_generated_file_outputs',
        {
            '_document_recovery_publication_error': lambda *args: None,
            '_resolve_generated_file_guidance_formats': lambda *args: ['docx', 'pdf'],
            'generated_file_specs_match_request': lambda *args: True,
            'get_current_user_id': lambda: 'user',
            '_read_chat_pending_document_copy': lambda *args: {'source_mode': 'recovery'},
            'document_recovery_choice': document_recovery_choice,
            '_load_chat_document_copy_source': load_source,
            'ensure_document_copy_source_unchanged': ensure_document_copy_source_unchanged,
            'deepcopy': deepcopy,
            'has_generated_file_output': lambda outputs, output_format: any(
                output.get('output_format') == output_format for output in outputs
            ),
            'maybe_create_generated_file_output': lambda **kwargs: {
                'output_format': kwargs['structured_file_spec']['format'], 'artifact_message_id': 'artifact',
            },
            '_rollback_mixed_source_chat_publication': lambda *args: (
                rolled_back.append(args[2]) or {'rollback_failure_count': 0}
            ),
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
            'SourceCopyError': SourceCopyError,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'DocumentSourceCleanupError': DocumentSourceCleanupError,
        },
    )
    outputs = helper(
        'RECREATE DOCUMENT', 'Preparing.', 'conversation',
        [_document_spec('docx'), _document_spec('pdf')],
        current_user_message_id='user-message', failure_messages=failures,
        document_recovery_enabled=True,
    )
    if source_changes:
        assert outputs == []
        assert reads == ['recovery_copy', 'recovery_copy', 'recovery_copy']
        assert rolled_back == [[{'output_format': 'docx', 'artifact_message_id': 'artifact'}]]
        assert 'source changed' in failures[0]
    else:
        assert [output['output_format'] for output in outputs] == ['docx', 'pdf']
        assert len(reads) == 6
        assert not rolled_back
        assert not failures


def test_streamed_clarification_notice_is_a_delta_and_is_persisted():
    source = (APP / 'route_backend_chats.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "document_copy_tracking.get('notice')"
        and any(isinstance(child, ast.Yield) for child in ast.walk(node))
    ]
    assert len(candidates) == 1
    test_function = ast.FunctionDef(
        name='run_notice', args=ast.arguments(
            posonlyargs=[], args=[ast.arg(arg='accumulated_content')], kwonlyargs=[], kw_defaults=[], defaults=[],
        ),
        body=[candidates[0], ast.Return(value=ast.Name(id='accumulated_content', ctx=ast.Load()))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[test_function], type_ignores=[]))
    namespace = {
        'json': json,
        'document_copy_tracking': {'notice': 'One clarification remains.'},
    }
    exec(compile(module, str(APP / 'route_backend_chats.py'), 'exec'), namespace)
    stream = namespace['run_notice']('Which font?')
    event = next(stream)
    assert json.loads(event.removeprefix('data: ').strip()) == {
        'content': '\n\nOne clarification remains.',
    }
    with pytest.raises(StopIteration) as finished:
        next(stream)
    assert finished.value.value == 'Which font?\n\nOne clarification remains.'


def test_streaming_defers_recovery_responses_even_when_model_declares_no_files():
    from functions_document_source_resolution import document_recovery_choice

    tree = ast.parse((APP / 'route_backend_chats.py').read_text(encoding='utf-8'))
    expressions = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'deferred_file_chat_response' for target in node.targets)
        and 'document_recovery_choice' in ast.unparse(node.value)
    ]
    assert len(expressions) >= 2
    for expression in expressions:
        code = compile(ast.Expression(body=expression), '<stream-defer>', 'eval')
        context = {
            'file_specs': [],
            'user_message': 'RECREATE DOCUMENT',
            'document_recovery_choice': document_recovery_choice,
            'structured_document_response_requested': True,
        }
        assert eval(code, context) is True


def test_chat_rolls_back_partial_multi_format_publication():
    route_path = APP / 'route_backend_chats.py'
    created = []
    rolled_back = []

    def create_output(**kwargs):
        output_format = kwargs['structured_file_spec']['format']
        created.append(output_format)
        if output_format == 'docx':
            return {
                'output_format': output_format,
                'artifact_message_id': 'docx-artifact',
            }
        return None

    helper = _load_function(
        route_path,
        'maybe_create_generated_file_outputs',
        {
            '_resolve_generated_file_guidance_formats': lambda *args: ['docx', 'pdf'],
            '_document_recovery_publication_error': lambda *args: None,
            'document_recovery_choice': lambda text: None,
            'get_current_user_id': lambda: 'user-1',
            'generated_file_specs_match_request': lambda *args: True,
            'has_generated_file_output': lambda outputs, output_format: any(
                output.get('output_format') == output_format for output in outputs
            ),
            'maybe_create_generated_file_output': create_output,
            '_rollback_mixed_source_chat_publication': lambda *args: (
                rolled_back.append(args[2]) or {'rollback_failure_count': 0}
            ),
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
        },
    )

    result = helper(
        user_question='Create a Word report and PDF report.',
        assistant_content='The report is ready.',
        conversation_id='conversation-1',
        structured_file_specs=[_document_spec('docx'), _document_spec('pdf')],
    )

    assert result == []
    assert created == ['docx', 'pdf']
    assert rolled_back == [[{
        'output_format': 'docx',
        'artifact_message_id': 'docx-artifact',
    }]]


def test_chat_copy_failure_rolls_back_and_reports_reason():
    from functions_document_source_copy import SourceCopyError
    from functions_document_source_resolution import DocumentSourceCleanupError, DocumentSourceUnavailable

    rolled_back = []
    errors = []

    def create_output(**kwargs):
        if kwargs['structured_file_spec']['format'] == 'docx':
            return {'output_format': 'docx', 'artifact_message_id': 'docx-artifact'}
        raise SourceCopyError('Images are not supported.')

    helper = _load_function(
        APP / 'route_backend_chats.py',
        'maybe_create_generated_file_outputs',
        {
            '_resolve_generated_file_guidance_formats': lambda *args: ['docx', 'pdf'],
            '_document_recovery_publication_error': lambda *args: None,
            'document_recovery_choice': lambda text: None,
            'get_current_user_id': lambda: 'user-1',
            'generated_file_specs_match_request': lambda *args: True,
            'has_generated_file_output': lambda outputs, output_format: any(
                output.get('output_format') == output_format for output in outputs
            ),
            'maybe_create_generated_file_output': create_output,
            '_rollback_mixed_source_chat_publication': lambda *args: (
                rolled_back.append(args[2]) or {'rollback_failure_count': 0}
            ),
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
            'SourceCopyError': SourceCopyError,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'DocumentSourceCleanupError': DocumentSourceCleanupError,
        },
    )
    docx = _document_spec('docx', row_source='prior_assistant')
    pdf = _document_spec('pdf', row_source='prior_assistant')
    docx['blocks'] = []
    pdf['blocks'] = []
    result = helper(
        user_question='Create a Word report and PDF report.',
        assistant_content='Preparing the files.',
        conversation_id='conversation-1',
        structured_file_specs=[docx, pdf],
        failure_messages=errors,
    )
    assert result == []
    assert rolled_back == [[{'output_format': 'docx', 'artifact_message_id': 'docx-artifact'}]]
    assert len(errors) == 1
    assert 'Images are not supported' in errors[0]
    assert 'No new files remain published' in errors[0]


def test_chat_rollback_reports_unconfirmed_deletion():
    helper = _load_function(
        APP / 'route_backend_chats.py',
        '_rollback_mixed_source_chat_publication',
        {
            'delete_generated_chat_artifact_for_current_user': lambda *args: False,
            '_rollback_agent_citation_artifacts': lambda *args: {'rollback_failure_count': 0},
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
        },
    )
    result = helper('user-1', 'conversation-1', [{'artifact_message_id': 'artifact'}])
    assert result['rollback_failure_count'] == 1


def test_chat_mixed_formats_reject_source_changes_between_files():
    from functions_document_source_copy import SourceCopyError
    from functions_document_source_resolution import (
        DocumentSourceCleanupError,
        DocumentSourceUnavailable,
        ensure_document_copy_source_unchanged,
    )

    reads = []
    rolled_back = []
    errors = []

    def load_source(*args):
        reads.append(True)
        return {'id': 'source', 'content': 'first' if len(reads) == 1 else 'changed'}

    def create_output(**kwargs):
        kwargs['document_source_loader']('prior_assistant')
        return {'output_format': kwargs['structured_file_spec']['format'], 'artifact_message_id': 'artifact'}

    helper = _load_function(
        APP / 'route_backend_chats.py',
        'maybe_create_generated_file_outputs',
        {
            '_resolve_generated_file_guidance_formats': lambda *args: ['docx', 'pdf'],
            '_document_recovery_publication_error': lambda *args: None,
            'document_recovery_choice': lambda text: None,
            'get_current_user_id': lambda: 'user-1',
            'generated_file_specs_match_request': lambda *args: True,
            'has_generated_file_output': lambda outputs, output_format: any(
                output.get('output_format') == output_format for output in outputs
            ),
            '_load_chat_document_copy_source': load_source,
            'ensure_document_copy_source_unchanged': ensure_document_copy_source_unchanged,
            'deepcopy': __import__('copy').deepcopy,
            'maybe_create_generated_file_output': create_output,
            '_rollback_mixed_source_chat_publication': lambda *args: (
                rolled_back.append(args[2]) or {'rollback_failure_count': 0}
            ),
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
            'SourceCopyError': SourceCopyError,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'DocumentSourceCleanupError': DocumentSourceCleanupError,
        },
    )
    specs = []
    for output_format in ['docx', 'pdf']:
        spec = _document_spec(output_format, row_source='prior_assistant')
        spec['blocks'] = []
        specs.append(spec)
    result = helper('Create a Word report and PDF report.', 'Preparing.', 'conversation-1',
                    specs, current_user_message_id='user-message', failure_messages=errors)
    assert result == []
    assert len(rolled_back) == 1
    assert 'source changed' in errors[0].lower()


@pytest.mark.parametrize('cleanup_succeeds', [False, True])
def test_chat_rechecks_copy_source_after_upload_and_deletes_on_change(cleanup_succeeds):
    from functions_document_source_resolution import (
        DocumentSourceCleanupError,
        DocumentSourceUnavailable,
        ensure_document_copy_source_unchanged,
    )

    source = {'id': 'source', 'content': 'original'}
    reads = []
    deleted = []

    def load_source(*args):
        reads.append(True)
        return source if len(reads) == 1 else dict(source, content='changed')

    helper = _load_function(
        APP / 'route_backend_chats.py',
        'maybe_create_generated_file_output',
        {
            'raise_if_mixed_source_cancelled': lambda *args, **kwargs: None,
            'get_requested_generated_file_format': lambda value: 'docx',
            'get_current_user_id': lambda: 'user-1',
            'has_generated_file_output': lambda *args: False,
            '_build_prior_turn_source_loaders': lambda *args, **kwargs: (None, None),
            '_load_chat_document_copy_source': load_source,
            'get_settings': lambda: {},
            'build_generated_file_export': lambda *args, **kwargs: {
                'file_name': 'output.docx', 'file_content': b'docx',
                'output_format': 'docx', '_source_copy_snapshot': source,
            },
            '_safe_int': lambda value: 0,
            'ensure_document_copy_source_unchanged': ensure_document_copy_source_unchanged,
            'upload_generated_analysis_artifact_for_current_user': lambda **kwargs: {
                'message': {'id': 'artifact'},
            },
            'delete_generated_chat_artifact_for_current_user': lambda *args: deleted.append(args) or cleanup_succeeds,
            'MixedSourceCancellationError': type('MixedSourceCancellationError', (Exception,), {}),
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'DocumentSourceCleanupError': DocumentSourceCleanupError,
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
        },
    )
    spec = _document_spec('docx', row_source='prior_assistant')
    spec['blocks'] = []
    expected_error = DocumentSourceUnavailable if cleanup_succeeds else DocumentSourceCleanupError
    with pytest.raises(expected_error):
        helper('Create a Word document', 'Preparing.', 'conversation-1',
               current_user_message_id='user-message', structured_file_spec=spec)
    assert len(reads) == 2
    assert deleted == [('conversation-1', 'artifact')]


@pytest.mark.parametrize('cancel_after_upload', [False, True])
def test_workflow_rechecks_source_and_rolls_back_after_upload(cancel_after_upload):
    from functions_document_source_resolution import (
        DocumentSourceCleanupError,
        DocumentSourceUnavailable,
        ensure_document_copy_source_unchanged,
    )

    source = {'id': 'source', 'content': 'original'}
    reads = []
    deleted = []
    cancellation_checks = []

    class WorkflowRunCancelledError(BaseException):
        pass

    def check_cancellation(*args):
        cancellation_checks.append(True)
        if cancel_after_upload and len(cancellation_checks) == 2:
            raise WorkflowRunCancelledError()

    def load_source(*args):
        reads.append(True)
        return source if len(reads) == 1 else dict(source, content='changed')

    helper = _load_function(
        APP / 'functions_workflow_runner.py',
        '_maybe_create_workflow_generated_file_output',
        {
            'get_requested_generated_file_format': lambda value: 'docx',
            'has_generated_file_output': lambda *args: False,
            '_load_workflow_document_copy_source': load_source,
            'get_settings': lambda: {},
            'build_generated_file_export': lambda *args, **kwargs: {
                'file_name': 'output.docx', 'file_content': b'docx',
                'output_format': 'docx', '_source_copy_snapshot': source,
            },
            '_raise_if_workflow_run_cancelled': check_cancellation,
            'ensure_document_copy_source_unchanged': ensure_document_copy_source_unchanged,
            'upload_generated_analysis_artifact_for_user': lambda **kwargs: {
                'message': {'id': 'artifact'},
            },
            'delete_generated_chat_artifact_for_user': lambda *args: deleted.append(args) or True,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'DocumentSourceCleanupError': DocumentSourceCleanupError,
            'WorkflowRunCancelledError': WorkflowRunCancelledError,
            'log_event': lambda *args, **kwargs: None,
        },
    )
    spec = _document_spec('docx', row_source='prior_assistant')
    spec['blocks'] = []
    expected_error = WorkflowRunCancelledError if cancel_after_upload else DocumentSourceUnavailable
    with pytest.raises(expected_error):
        helper({'user_id': 'user-1'}, 'conversation-1', 'Create Word document',
               'Preparing.', structured_file_spec=spec, current_user_message_id='user-message',
               run_id='run')
    assert len(reads) == (1 if cancel_after_upload else 2)
    assert deleted == [('user-1', 'conversation-1', 'artifact')]


def test_workflow_copy_reauthorizes_group_membership_and_conversation():
    from functions_document_source_resolution import DocumentSourceUnavailable

    checks = []

    class Container:
        def read_item(self, item, partition_key):
            return {'id': item, 'user_id': 'user-1', 'group_id': 'group-1'}

    def load_source(mode, conversation_id, message_id, authorize, container):
        authorize()
        return {'id': 'answer', 'content': 'source'}

    helper = _load_function(
        APP / 'functions_workflow_runner.py',
        '_load_workflow_document_copy_source',
        {
            'cosmos_conversations_container': Container(),
            'cosmos_messages_container': object(),
            '_is_authorized_workflow_conversation': lambda conversation, workflow: True,
            '_get_workflow_group_id': lambda workflow: 'group-1',
            'assert_group_role': lambda *args, **kwargs: checks.append(('group', args)),
            'build_conversation_participation_context': lambda *args: checks.append(('conversation', args)),
            'load_document_copy_source_from_container': load_source,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'WORKFLOW_CONVERSATION_ACCESS_ERROR': 'Access denied',
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
        },
    )
    result = helper({'user_id': 'user-1'}, 'conversation-1', 'user-message', 'prior_assistant')
    assert result['id'] == 'answer'
    assert [check[0] for check in checks] == ['group', 'conversation']


@pytest.mark.parametrize('copy_failure', [False, True])
def test_workflow_rolls_back_partial_multi_format_publication(copy_failure):
    from functions_document_source_copy import SourceCopyError
    from functions_document_source_resolution import DocumentSourceCleanupError, DocumentSourceUnavailable

    workflow_path = APP / 'functions_workflow_runner.py'
    created = []
    rolled_back = []

    class FakeContainer:
        def __init__(self):
            self.items = []

        def upsert_item(self, item):
            self.items.append(item)

    def create_output(**kwargs):
        output_format = kwargs['structured_file_spec']['format']
        created.append(output_format)
        if output_format == 'docx':
            return {
                'output_format': output_format,
                'artifact_message_id': 'docx-artifact',
            }
        if copy_failure:
            raise SourceCopyError('Images are not supported.')
        return None

    helper = _load_function(
        workflow_path,
        '_create_assistant_message',
        {
            '_utc_now_iso': lambda: '2025-01-01T00:00:00+00:00',
            '_get_document_action_config': lambda workflow: {},
            '_get_workflow_scope': lambda workflow: 'personal',
            '_get_workflow_group_id': lambda workflow: '',
            'generated_file_specs_match_request': lambda *args: True,
            'has_generated_file_output': lambda outputs, output_format: any(
                output.get('output_format') == output_format for output in outputs
            ),
            '_maybe_create_workflow_generated_file_output': create_output,
            '_rollback_mixed_source_generated_outputs': lambda *args, **kwargs: (
                rolled_back.append(args[2]) or {'rollback_failure_count': 0}
            ),
            'get_generated_file_export_content': lambda result: result.get('reply', ''),
            'apply_agent_document_citations': lambda *args, **kwargs: None,
            'build_cited_source_subsets': lambda *args, **kwargs: {
                'cited_hybrid_citations': [],
            },
            '_persist_agent_citation_artifacts': lambda **kwargs: [],
            'attach_m365_message_provenance': lambda item: item,
            'cosmos_messages_container': FakeContainer(),
            'cosmos_conversations_container': FakeContainer(),
            'log_token_usage': lambda **kwargs: None,
            'debug_print': lambda *args, **kwargs: None,
            'initialize_conversation_used_document_tracking': lambda conversation: None,
            'merge_cited_documents_into_conversation': lambda *args, **kwargs: None,
            'log_event': lambda *args, **kwargs: None,
            'logging': logging,
            'uuid': __import__('uuid'),
            'SourceCopyError': SourceCopyError,
            'DocumentSourceUnavailable': DocumentSourceUnavailable,
            'DocumentSourceCleanupError': DocumentSourceCleanupError,
            'WorkflowRunCancelledError': type('WorkflowRunCancelledError', (BaseException,), {}),
        },
    )

    assistant_doc = helper(
        conversation={'id': 'conversation-1', 'title': 'Workflow conversation'},
        workflow={
            'id': 'workflow-1',
            'user_id': 'user-1',
            'task_prompt': 'Create a Word report and PDF report.',
            'name': 'Document workflow',
            'runner_type': 'model',
            'selected_agent': {},
            'model_binding_summary': {},
        },
        result={
            'reply': 'The report is ready.',
            'agent_citations': [],
            'generated_file_specs': [
                _document_spec('docx', row_source='prior_assistant' if copy_failure else 'model'),
                _document_spec('pdf', row_source='prior_assistant' if copy_failure else 'model'),
            ],
        },
        trigger_source='manual',
        run_id='run-1',
        user_message_doc={'metadata': {}},
        assistant_message_id='assistant-1',
    )

    assert created == ['docx', 'pdf']
    if copy_failure:
        assert 'Images are not supported' in assistant_doc['content']
    assert rolled_back == [[{
        'output_format': 'docx',
        'artifact_message_id': 'docx-artifact',
    }]]
    assert assistant_doc['metadata']['generated_analysis_artifacts'] == []
