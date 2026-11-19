# test_document_source_copy_resolution.py
"""Regression tests for authorized document source selection.

Version: 0.261.053
Implemented in: 0.261.053
"""

import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / 'application' / 'single_app'
sys.path.insert(0, str(APP))

from functions_document_source_resolution import (
    DocumentSourceUnavailable,
    append_document_copy_source_hint,
    ensure_document_copy_source_unchanged,
    load_authorized_document_copy_source,
    load_document_copy_source_from_container,
    resolve_document_copy_source,
)


def _message(message_id, timestamp, content='Original checklist', metadata=None):
    return {
        'id': message_id,
        'conversation_id': 'conversation',
        'role': 'assistant',
        'timestamp': timestamp,
        'content': content,
        'metadata': metadata or {},
    }


def _recent_users(records):
    def read(before):
        return sorted(
            (item for item in records.values() if item['role'] == 'user' and item['timestamp'] < before),
            key=lambda item: item['timestamp'], reverse=True,
        )[:2]
    return read


def _resolve(mode, nearest, by_id=None):
    return resolve_document_copy_source(
        mode,
        'conversation',
        '2026-01-03T00:00:00Z',
        nearest,
        lambda message_id: (by_id or {}).get(message_id),
    )


def test_prior_assistant_copies_only_nearest_eligible_message():
    nearest = _message('answer', '2026-01-01T00:00:00Z')
    assert _resolve('prior_assistant', nearest) == nearest


def test_authorized_loader_selects_unambiguous_named_earlier_answer():
    original = _message('plan', '2026-01-01T00:00:00Z', '# Brief Data Strategy Development Plan\n\nFirst step.')
    nearest = _message('governance', '2026-01-02T00:00:00Z', '# Governance Model\n\nOther content.')
    current = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
        'content': 'Generate a Word file of the Brief Data Strategy Development Plan.',
        'metadata': {},
    }
    records = {item['id']: item for item in (original, nearest, current)}
    result = load_authorized_document_copy_source(
        'named_assistant', 'conversation', 'request', lambda: None, records.get,
        lambda timestamp: [nearest, original],
        read_named_candidates=lambda timestamp: [nearest, original],
    )
    assert result == original


@pytest.mark.parametrize('ambiguous', [False, True])
def test_abbreviated_named_plan_is_never_used_as_an_exact_source(ambiguous):
    original = _message('plan', '2026-01-01T00:00:00Z', '# DevSecOps adoption plan\n\nFirst step.')
    nearest = _message('governance', '2026-01-02T00:00:00Z', '# Governance Model\n\nOther content.')
    current = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
        'content': 'Generate a word file with the devsecops plan.',
        'metadata': {},
    }
    candidates = [nearest, original]
    if ambiguous:
        candidates.append(_message('other-plan', '2026-01-01T12:00:00Z', '# DevSecOps rollout plan\n\nOther plan.'))
    records = {item['id']: item for item in (original, nearest, current)}

    def resolve(mode):
        return load_authorized_document_copy_source(
            mode, 'conversation', 'request', lambda: None, records.get,
            lambda timestamp: [nearest, original],
            read_named_candidates=lambda timestamp: candidates,
        )
    with pytest.raises(DocumentSourceUnavailable):
        resolve('named_assistant')
    with pytest.raises(DocumentSourceUnavailable):
        resolve('prior_assistant')


@pytest.mark.parametrize('case', ['duplicate', 'masked', 'wrong_conversation', 'oversized', 'not_named'])
def test_named_source_rejects_ambiguous_or_unauthorized_candidates(case):
    original = _message('plan', '2026-01-01T00:00:00Z', '# Brief Data Strategy Development Plan\n\nFirst step.')
    nearest = _message('other', '2026-01-02T00:00:00Z', '# Governance Model\n\nOther content.')
    current = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
        'content': 'Generate a Word file of the Brief Data Strategy Development Plan.',
        'metadata': {},
    }
    candidates = [nearest, original]
    if case == 'duplicate':
        candidates.append(_message('duplicate', '2026-01-01T12:00:00Z', original['content']))
    elif case == 'masked':
        original['metadata']['masked'] = True
    elif case == 'wrong_conversation':
        original['conversation_id'] = 'another'
    elif case == 'oversized':
        candidates.extend(_message(f'extra-{index}', '2025-01-01T00:00:00Z') for index in range(39))
    else:
        current['content'] = 'Create a Word document.'
    records = {item['id']: item for item in (original, nearest, current)}
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'named_assistant', 'conversation', 'request', lambda: None, records.get,
            lambda timestamp: [nearest, original],
            read_named_candidates=lambda timestamp: candidates,
        )


def test_named_request_cannot_be_silently_copied_from_the_nearest_answer():
    original = _message('plan', '2026-01-01T00:00:00Z', '# Brief Data Strategy Development Plan\n\nPlan.')
    nearest = _message('other', '2026-01-02T00:00:00Z', '# Governance Model\n\nOther.')
    current = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
        'content': 'Generate a Word file of the Brief Data Strategy Development Plan.',
        'metadata': {},
    }
    records = {item['id']: item for item in (original, nearest, current)}
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'prior_assistant', 'conversation', 'request', lambda: None, records.get,
            lambda timestamp: [nearest, original], read_named_candidates=lambda timestamp: [nearest, original],
        )


@pytest.mark.parametrize('metadata', [
    {'masked': True},
    {'masked_ranges': [[0, 5]]},
    {'thread_info': {'active_thread': False}},
    {'incomplete': True},
    {'error': 'failed'},
    {'status': 'cancelled'},
    {'is_generated_chat_artifact': True},
])
def test_unsafe_nearest_message_is_not_skipped(metadata):
    nearest = _message('answer', '2026-01-01T00:00:00Z', metadata=metadata)
    with pytest.raises(DocumentSourceUnavailable):
        _resolve('prior_assistant', nearest)


@pytest.mark.parametrize('changes', [
    {'conversation_id': 'another'},
    {'role': 'user'},
    {'timestamp': '2026-01-04T00:00:00Z'},
    {'timestamp': 'invalid'},
    {'content': ''},
])
def test_invalid_or_cross_conversation_source_is_rejected(changes):
    nearest = _message('answer', '2026-01-01T00:00:00Z')
    nearest.update(changes)
    with pytest.raises(DocumentSourceUnavailable):
        _resolve('prior_assistant', nearest)


def test_prior_document_follows_server_stored_lineage_and_rechecks_source():
    original = _message('original', '2026-01-01T00:00:00Z')
    acknowledgement = _message('ack', '2026-01-02T00:00:00Z', metadata={
        'generated_analysis_artifacts': [{
            'artifact_message_id': 'artifact',
            'output_format': 'docx',
            'source_copy_message_id': 'original',
        }],
    })
    assert _resolve('prior_document', acknowledgement, {'original': original}) == original
    original['metadata']['masked'] = True
    with pytest.raises(DocumentSourceUnavailable):
        _resolve('prior_document', acknowledgement, {'original': original})


def test_prior_document_rejects_missing_ambiguous_or_forged_lineage():
    original = _message('original', '2026-01-01T00:00:00Z')
    artifact = {
        'artifact_message_id': 'artifact',
        'output_format': 'docx',
        'source_copy_message_id': 'original',
    }
    for artifacts in [
        [],
        [dict(artifact, source_copy_message_id='missing')],
        [artifact, dict(artifact, source_copy_message_id='other')],
        [dict(artifact, artifact_message_id='')],
        [dict(artifact, output_format='csv')],
    ]:
        nearest = _message('ack', '2026-01-02T00:00:00Z', metadata={
            'generated_analysis_artifacts': artifacts,
        })
        with pytest.raises(DocumentSourceUnavailable):
            _resolve('prior_document', nearest, {'original': original})


def test_prior_document_rejects_source_newer_than_acknowledgement():
    source = _message('original', '2026-01-02T12:00:00Z')
    acknowledgement = _message('ack', '2026-01-02T00:00:00Z', metadata={
        'generated_analysis_artifacts': [{
            'artifact_message_id': 'artifact',
            'output_format': 'pdf',
            'source_copy_message_id': 'original',
        }],
    })
    with pytest.raises(DocumentSourceUnavailable):
        _resolve('prior_document', acknowledgement, {'original': source})


def test_prior_assistant_does_not_copy_document_handoff_as_the_source():
    nearest = _message('ack', '2026-01-02T00:00:00Z', metadata={
        'generated_analysis_artifacts': [{
            'artifact_message_id': 'artifact',
            'output_format': 'docx',
            'source_copy_message_id': 'original',
        }],
    })
    with pytest.raises(DocumentSourceUnavailable):
        _resolve('prior_assistant', nearest)


def test_authorized_loader_checks_current_user_and_ambiguous_timestamps():
    current = {
        'id': 'user-message',
        'conversation_id': 'conversation',
        'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
        'metadata': {},
    }
    original = _message('answer', '2026-01-01T00:00:00Z')
    authorized = []

    def load(current_message, recent):
        return load_authorized_document_copy_source(
            'prior_assistant', 'conversation', 'user-message',
            lambda: authorized.append(True),
            lambda message_id: current_message if message_id == 'user-message' else None,
            lambda timestamp: recent,
        )

    result = load(current, [original])
    assert result == original
    assert len(authorized) == 1
    with pytest.raises(DocumentSourceUnavailable):
        load(dict(current, role='assistant'), [original])
    with pytest.raises(DocumentSourceUnavailable):
        load(current, [original, _message('other', original['timestamp'])])
    with pytest.raises(DocumentSourceUnavailable):
        load(dict(current, metadata={'thread_info': {'active_thread': False}}), [original])


def test_container_loader_uses_authorized_partition_and_current_turn_boundary():
    current = {
        'id': 'user-message',
        'conversation_id': 'conversation',
        'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
        'metadata': {},
    }
    original = _message('answer', '2026-01-01T00:00:00Z')
    calls = []

    class Container:
        def read_item(self, item, partition_key):
            calls.append(('read', item, partition_key))
            return current

        def query_items(self, query, parameters, partition_key):
            calls.append(('query', parameters, partition_key))
            return [original]

    result = load_document_copy_source_from_container(
        'prior_assistant', 'conversation', 'user-message',
        lambda: calls.append(('authorize',)), Container(),
    )
    assert result == original
    assert calls[0] == ('authorize',)
    assert calls[1] == ('read', 'user-message', 'conversation')
    assert calls[2][2] == 'conversation'
    assert {'name': '@before_timestamp', 'value': current['timestamp']} in calls[2][1]


def test_source_must_still_match_at_publication():
    source = _message('answer', '2026-01-01T00:00:00Z')
    ensure_document_copy_source_unchanged(source, dict(source))
    changed = dict(source, content='Changed checklist')
    with pytest.raises(DocumentSourceUnavailable):
        ensure_document_copy_source_unchanged(source, changed)


def test_available_prior_document_hint_contains_no_source_data_or_identifiers():
    messages = [{'role': 'user', 'content': 'Generate a PDF instead.'}]
    calls = []

    def load(mode):
        calls.append(mode)
        return _message('private-source-id', '2026-01-01T00:00:00Z', 'Private source text')

    append_document_copy_source_hint(messages, load)
    assert calls == ['prior_document']
    assert messages[-1]['role'] == 'user'
    assert messages[-2]['role'] == 'system'
    assert 'prior_document' in messages[-2]['content']
    assert 'private-source-id' not in messages[-2]['content']
    assert 'Private source text' not in messages[-2]['content']


def test_verified_pending_copy_hint_does_not_expose_source():
    messages = [{'role': 'user', 'content': 'Use Bookman Old Style.'}]
    calls = []

    def load(mode):
        calls.append(mode)
        return _message('private-source-id', '2026-01-01T00:00:00Z', 'Private source text')

    append_document_copy_source_hint(messages, load, 'prior_assistant', ['docx', 'pdf'])
    assert calls == ['pending_copy']
    assert 'row_source="pending_copy"' in messages[0]['content']
    assert 'unrelated reply or cancellation' in messages[0]['content']
    assert 'Requested formats: docx, pdf.' in messages[0]['content']
    assert 'private-source-id' not in messages[0]['content']
    assert 'Private source text' not in messages[0]['content']


def test_unavailable_document_source_does_not_produce_hint():
    messages = [{'role': 'user', 'content': 'Create a PDF instead.'}]

    def load(mode):
        raise DocumentSourceUnavailable('not authorized')

    append_document_copy_source_hint(messages, load)
    assert messages == [{'role': 'user', 'content': 'Create a PDF instead.'}]


@pytest.mark.parametrize('metadata', [
    {'masked': True},
    {'masked_ranges': [[0, 3]]},
    {'thread_info': {'active_thread': False}},
    {'incomplete': True},
])
def test_unusable_document_handoff_produces_no_hint(metadata):
    nearest = _message('ack', '2026-01-02T00:00:00Z', metadata=metadata)
    messages = [{'role': 'user', 'content': 'Generate a PDF instead.'}]
    append_document_copy_source_hint(messages, lambda mode: _resolve(mode, nearest))
    assert len(messages) == 1


def test_clarification_does_not_resolve_an_older_document_implicitly():
    nearest = _message('clarification', '2026-01-02T00:00:00Z', 'Please provide the content.')
    messages = [{'role': 'user', 'content': 'Create a PDF of those instructions.'}]
    append_document_copy_source_hint(messages, lambda mode: _resolve(mode, nearest))
    assert len(messages) == 1


def test_explicit_pending_copy_survives_a_formatting_clarification():
    from functions_document_source_resolution import create_pending_document_copy

    original = _message('original', '2026-01-01T00:00:00Z', '# Plan\n\nOriginal content.')
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
    }
    followup = {
        'id': 'followup', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'metadata': {},
    }
    pending = create_pending_document_copy(
        'prior_assistant', ['docx'], request['id'], original,
    )
    clarification = _message(
        'clarification', '2026-01-03T00:00:00Z',
        'Please confirm the heading font.', {'pending_document_copy': pending},
    )
    records = {item['id']: item for item in (original, request, followup, clarification)}

    def recent(timestamp):
        return [original] if timestamp == request['timestamp'] else [clarification, original]

    resolved = load_authorized_document_copy_source(
        'pending_copy', 'conversation', 'followup', lambda: None, records.get, recent, _recent_users(records),
    )
    assert resolved == original
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'prior_assistant', 'conversation', 'followup', lambda: None, records.get, recent,
        )


@pytest.mark.parametrize('change', ['masked', 'content', 'conversation', 'missing', 'digest', 'formats'])
def test_pending_copy_fails_closed_when_its_lineage_changes(change):
    from copy import deepcopy
    from functions_document_source_resolution import create_pending_document_copy

    original = _message('original', '2026-01-01T00:00:00Z', '# Plan\n\nOriginal content.')
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
    }
    followup = {
        'id': 'followup', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'metadata': {},
    }
    pending = create_pending_document_copy('prior_assistant', ['docx'], request['id'], original)
    clarification = _message(
        'clarification', '2026-01-03T00:00:00Z',
        'Please confirm the heading font.', {'pending_document_copy': pending},
    )
    records = {item['id']: deepcopy(item) for item in (original, request, followup, clarification)}
    if change == 'masked':
        records['original']['metadata']['masked'] = True
    elif change == 'content':
        records['original']['content'] = 'Changed.'
    elif change == 'conversation':
        records['original']['conversation_id'] = 'another'
    elif change == 'missing':
        del records['request']
    elif change == 'digest':
        records['clarification']['metadata']['pending_document_copy']['source_digest'] = 'forged'
    else:
        records['clarification']['metadata']['pending_document_copy']['formats'] = [{}]

    def recent(timestamp):
        return [records['original']] if timestamp == request['timestamp'] else [records['clarification'], records['original']]

    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'followup', lambda: None, records.get, recent, _recent_users(records),
        )


def test_pending_copy_reauthorizes_the_original_request_and_current_turn():
    from functions_document_source_resolution import create_pending_document_copy

    original = _message('original', '2026-01-01T00:00:00Z')
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
    }
    followup = {
        'id': 'followup', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'metadata': {},
    }
    pending = create_pending_document_copy('prior_assistant', ['pdf'], request['id'], original)
    clarification = _message(
        'clarification', '2026-01-03T00:00:00Z', 'Please confirm.',
        {'pending_document_copy': pending},
    )
    records = {item['id']: item for item in (original, request, followup, clarification)}
    checks = []

    def authorize():
        checks.append(True)
        if len(checks) == 2:
            raise PermissionError('revoked')

    with pytest.raises(PermissionError):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'followup', authorize, records.get,
            lambda timestamp: [original] if timestamp == request['timestamp'] else [clarification, original],
            _recent_users(records),
        )
    assert len(checks) == 2


def test_pending_copy_survives_multiple_clarifications_but_not_an_unrelated_answer():
    from functions_document_source_resolution import create_pending_document_copy

    original = _message('original', '2026-01-01T00:00:00Z', '# Plan\n\nFirst item.')
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
    }
    first = _message('first', '2026-01-03T00:00:00Z', 'Which font?', {
        'pending_document_copy': create_pending_document_copy('prior_assistant', ['docx'], 'request', original),
    })
    reply = {
        'id': 'reply', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'metadata': {},
    }
    second = _message('second', '2026-01-05T00:00:00Z', 'Which size?', {
        'pending_document_copy': create_pending_document_copy('pending_copy', ['docx'], 'reply', original),
    })
    current = {
        'id': 'current', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-06T00:00:00Z', 'metadata': {},
    }
    records = {item['id']: item for item in (original, request, first, reply, second, current)}

    def recent(timestamp):
        return sorted(
            (item for item in records.values() if item['role'] == 'assistant' and item['timestamp'] < timestamp),
            key=lambda item: item['timestamp'], reverse=True,
        )[:2]

    depth_out = []
    result = load_authorized_document_copy_source(
        'pending_copy', 'conversation', 'current', lambda: None, records.get, recent, _recent_users(records),
        depth_out=depth_out,
    )
    assert result == original
    assert depth_out == [2]
    records['second']['metadata'] = {}
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'current', lambda: None, records.get, recent, _recent_users(records),
        )


def test_pending_copy_can_reuse_a_generated_document_source():
    from functions_document_source_resolution import create_pending_document_copy

    original = _message('original', '2026-01-01T00:00:00Z', '# Plan\n\nFirst item.')
    handoff = _message('handoff', '2026-01-02T00:00:00Z', 'Word document ready.', {
        'generated_analysis_artifacts': [{
            'artifact_message_id': 'artifact', 'output_format': 'docx',
            'source_copy_message_id': original['id'],
        }],
    })
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z', 'metadata': {},
    }
    clarification = _message('clarification', '2026-01-04T00:00:00Z', 'Which layout?', {
        'pending_document_copy': create_pending_document_copy('prior_document', ['pdf'], 'request', original),
    })
    current = {
        'id': 'current', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-05T00:00:00Z', 'metadata': {},
    }
    records = {item['id']: item for item in (original, handoff, request, clarification, current)}

    def recent(timestamp):
        return sorted(
            (item for item in records.values() if item['role'] == 'assistant' and item['timestamp'] < timestamp),
            key=lambda item: item['timestamp'], reverse=True,
        )[:2]

    result = load_authorized_document_copy_source(
        'pending_copy', 'conversation', 'current', lambda: None, records.get, recent, _recent_users(records),
    )
    assert result == original
    records['handoff']['metadata']['generated_analysis_artifacts'][0]['source_copy_message_id'] = 'another'
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'current', lambda: None, records.get, recent, _recent_users(records),
        )


def test_pending_copy_rejects_mismatched_or_ambiguous_lineage():
    from functions_document_source_resolution import create_pending_document_copy

    source = _message('source', '2026-01-01T00:00:00Z')
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
    }
    pending = create_pending_document_copy('prior_assistant', ['docx'], request['id'], source)
    pending['source_message_id'] = 'another-answer'
    clarification = _message('clarification', '2026-01-03T00:00:00Z', 'Which font?', {
        'pending_document_copy': pending,
    })
    current = {
        'id': 'current', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'metadata': {},
    }
    records = {item['id']: item for item in (source, request, clarification, current)}
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'current', lambda: None, records.get,
            lambda timestamp: [source] if timestamp == request['timestamp'] else [clarification, source],
            _recent_users(records),
        )
    pending['source_message_id'] = source['id']
    duplicate = _message('duplicate', clarification['timestamp'])
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'current', lambda: None, records.get,
            lambda timestamp: [clarification, duplicate],
            _recent_users(records),
        )


@pytest.mark.parametrize('case', ['intervening', 'tied', 'unavailable'])
def test_pending_clarification_must_belong_to_its_unique_request(case):
    from functions_document_source_resolution import create_pending_document_copy

    source = _message('source', '2026-01-01T00:00:00Z')
    request = {
        'id': 'request', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z', 'metadata': {},
    }
    clarification = _message('clarification', '2026-01-03T00:00:00Z', 'Which font?', {
        'pending_document_copy': create_pending_document_copy('prior_assistant', ['docx'], 'request', source),
    })
    current = {
        'id': 'current', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-04T00:00:00Z', 'metadata': {},
    }
    records = {item['id']: item for item in (source, request, clarification, current)}
    if case != 'unavailable':
        records['other'] = {
            'id': 'other', 'conversation_id': 'conversation', 'role': 'user',
            'timestamp': '2026-01-02T12:00:00Z' if case == 'intervening' else request['timestamp'],
            'metadata': {},
        }
    with pytest.raises(DocumentSourceUnavailable):
        load_authorized_document_copy_source(
            'pending_copy', 'conversation', 'current', lambda: None, records.get,
            lambda timestamp: [source] if timestamp == request['timestamp'] else [clarification, source],
            None if case == 'unavailable' else _recent_users(records),
        )


def test_hint_does_not_authorize_publication_after_source_becomes_masked():
    from functions_generated_file_exports import build_generated_file_export

    original = _message('original', '2026-01-01T00:00:00Z')
    acknowledgement = _message('ack', '2026-01-02T00:00:00Z', metadata={
        'generated_analysis_artifacts': [{
            'artifact_message_id': 'artifact',
            'output_format': 'docx',
            'source_copy_message_id': 'original',
        }],
    })
    load = lambda mode: _resolve(mode, acknowledgement, {'original': original})
    messages = [{'role': 'user', 'content': 'Generate a PDF instead.'}]
    append_document_copy_source_hint(messages, load)
    original['metadata']['masked'] = True
    with pytest.raises(DocumentSourceUnavailable):
        build_generated_file_export(
            messages[-1]['content'], 'Preparing the PDF.',
            structured_file_spec={
                'format': 'pdf', 'title': 'Checklist', 'columns': [], 'rows': [],
                'row_source': 'prior_document', 'source_action': '', 'blocks': [],
                'formatting': {
                    'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
                    'body_font': '', 'body_size': 0,
                },
            },
            document_source_loader=load,
        )


def test_word_then_pdf_followup_reuses_original_server_recorded_source():
    from io import BytesIO

    import fitz
    from docx import Document
    from functions_generated_file_exports import (
        build_generated_file_artifact_metadata,
        build_generated_file_export,
    )

    original = _message(
        'original', '2026-01-01T00:00:00Z',
        '# Security checklist\n\n1. Keep the first item.\n2. Keep the second item.',
    )
    messages = {'original': original}
    recent = [original]

    class Container:
        def read_item(self, item, partition_key):
            return messages[item]

        def query_items(self, query, parameters, partition_key):
            return list(recent)

    def create_file(output_format, mode, user_message_id):
        declaration = {
            'format': output_format,
            'title': 'Checklist',
            'columns': [],
            'rows': [],
            'row_source': mode,
            'source_action': '',
            'blocks': [],
            'formatting': {
                'title_font': '', 'title_size': 0, 'heading_font': '', 'heading_size': 0,
                'body_font': '', 'body_size': 0,
            },
        }
        return build_generated_file_export(
            'Create a Word document and PDF.',
            'Preparing the files.',
            structured_file_spec=declaration,
            document_source_loader=lambda source_mode: load_document_copy_source_from_container(
                source_mode, 'conversation', user_message_id, lambda: None, Container(),
            ),
        )

    messages['user1'] = {
        'id': 'user1', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-02T00:00:00Z',
    }
    docx = create_file('docx', 'prior_assistant', 'user1')
    paragraphs = [paragraph.text for paragraph in Document(BytesIO(docx['file_content'])).paragraphs]
    assert 'Security checklist' in paragraphs
    assert 'Keep the first item.' in paragraphs
    assert 'Keep the second item.' in paragraphs

    artifact = build_generated_file_artifact_metadata(
        docx, {'message': {'id': 'word-artifact'}}, 'conversation',
    )
    assert artifact['source_copy_message_id'] == 'original'
    acknowledgement = _message(
        'ack', '2026-01-02T01:00:00Z', 'Preparing the Word file.',
        {'generated_analysis_artifacts': [artifact]},
    )
    recent[:] = [acknowledgement, original]
    messages['user2'] = {
        'id': 'user2', 'conversation_id': 'conversation', 'role': 'user',
        'timestamp': '2026-01-03T00:00:00Z',
    }
    model_messages = [{'role': 'user', 'content': 'Generate a PDF instead.'}]
    append_document_copy_source_hint(
        model_messages,
        lambda mode: load_document_copy_source_from_container(
            mode, 'conversation', 'user2', lambda: None, Container(),
        ),
    )
    assert 'row_source="prior_document"' in model_messages[0]['content']
    pdf = create_file('pdf', 'prior_document', 'user2')
    document = fitz.open(stream=pdf['file_content'], filetype='pdf')
    try:
        text = '\n'.join(page.get_text() for page in document)
    finally:
        document.close()
    assert 'Security checklist' in text
    assert 'Keep the first item.' in text
    assert 'Keep the second item.' in text
    assert 'Preparing the Word file.' not in text
