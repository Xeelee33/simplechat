# functions_document_source_resolution.py
"""Validate server-selected conversation sources for faithful document copies."""

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Dict, Optional


MAX_PENDING_DOCUMENT_COPY_DEPTH = 4
MAX_NAMED_DOCUMENT_SOURCE_CANDIDATES = 40


def document_recovery_choice(user_message: str) -> Optional[str]:
    """Recognize only the recovery choices explicitly offered to the user."""
    text = str(user_message or '').strip()
    if text.casefold() == 'recreate document' or (
        len(text) <= 2_000_000 and re.match(r'^recreate document\s*:\s*\S', text, re.IGNORECASE)
    ):
        return 'recreate'
    if re.match(r'^source\s*:\s*\S', text, re.IGNORECASE) and len(text) <= 2_000_000:
        return 'source'
    return None


def document_clarification_notice(depth: int) -> str:
    """Explain the remaining exact-copy budget and explicit recovery options."""
    if depth < MAX_PENDING_DOCUMENT_COPY_DEPTH - 1:
        return ''
    if depth < MAX_PENDING_DOCUMENT_COPY_DEPTH:
        status = 'One more clarification can be retained for an exact copy.'
    elif depth == MAX_PENDING_DOCUMENT_COPY_DEPTH:
        status = 'This is the last clarification that can be retained for an exact copy.'
    else:
        status = (
            'The exact-copy clarification limit has been reached. '
            'Answering the preceding question will not create an exact copy.'
        )
    continuation = (
        'You can answer the current question to continue. '
        if depth <= MAX_PENDING_DOCUMENT_COPY_DEPTH else ''
    )
    return (
        f'{status} {continuation}'
        'Alternatively, reply `RECREATE DOCUMENT` to authorize a non-verbatim reconstruction, '
        'or reply `SOURCE:` followed by the text to provide the source again. '
        'Both options may reword the source.'
    )


class DocumentSourceUnavailable(ValueError):
    """The requested source cannot be copied safely without clarification."""


class DocumentSourceAmbiguousReference(DocumentSourceUnavailable):
    """A possible source reference needs explicit identification."""


class DocumentSourceCleanupError(DocumentSourceUnavailable):
    """A source-copy publication could not be fully rolled back."""


DOCUMENT_COPY_SOURCE_HINT = (
    'Server-verified document source availability: the immediately preceding generated document has an '
    'authorized, complete source that the server can copy. When structured document declarations are available, '
    'for a requested download of the same complete content in another format, including "PDF instead", '
    'declare row_source="prior_document" with blocks=[]. Do not claim the source is unavailable merely '
    'because the document contents are not in the chat history. '
    'This availability is not a file request: use files=[] for an inline answer or clarification, and use '
    'model-authored blocks only when the user requests changes rather than an unchanged copy. '
    'The server will reauthorize the source when publishing.'
)


def append_document_copy_source_hint(
    messages: list,
    source_loader: Callable[[str], Dict[str, Any]],
    pending_source_mode: Optional[str] = None,
    pending_formats: Optional[list] = None,
) -> None:
    """Tell the model about a verified source without disclosing its contents or identity."""
    if not messages or messages[-1].get('role') != 'user':
        return
    has_pending = pending_source_mode in ('prior_assistant', 'named_assistant', 'prior_document', 'pending_copy')
    source_mode = 'pending_copy' if has_pending else 'prior_document'
    try:
        source_loader(source_mode)
    except DocumentSourceUnavailable:
        return
    if has_pending:
        hint = (
            'Server-verified pending document source availability: an earlier document-copy request is '
            'awaiting clarification, and its original authorized source is still available. If this user '
            'reply completes that same request for an exact Word or PDF copy, use '
            'row_source="pending_copy" and blocks=[]. For CSV use the CSV source rules instead. '
            'Preserve the previously requested formats and formatting, incorporating the user clarification. '
            'For another clarification about that same copy, use pending_copy_source="pending_copy". '
            'This is not authorization to create a file for an unrelated reply or cancellation; use files=[] '
            'and pending_copy_source="none" in that case. The server reauthorizes the source at publication.'
        )
        if (
            isinstance(pending_formats, list)
            and pending_formats
            and all(item in ('csv', 'docx', 'pdf') for item in pending_formats)
        ):
            hint += f" Requested formats: {', '.join(pending_formats)}."
    else:
        hint = DOCUMENT_COPY_SOURCE_HINT
    messages.insert(len(messages) - 1, {'role': 'system', 'content': hint})


def ensure_document_copy_source_unchanged(expected: Dict[str, Any], actual: Dict[str, Any]) -> None:
    """Fail closed if any source data changed after rendering."""
    if expected != actual:
        raise DocumentSourceUnavailable('The source changed while the document was being prepared.')


def _document_copy_digest(source: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def create_pending_document_copy(
    source_mode: str, formats: list, request_message_id: str, source: Dict[str, Any],
) -> Dict[str, Any]:
    """Record a server-selected source without trusting a model-provided identifier."""
    if (
        source_mode not in ('prior_assistant', 'named_assistant', 'prior_document', 'pending_copy')
        or not isinstance(formats, list)
        or not formats
        or len(formats) > 3
        or any(not isinstance(item, str) or item not in {'csv', 'docx', 'pdf'} for item in formats)
        or not any(item in {'docx', 'pdf'} for item in formats)
        or len(set(formats)) != len(formats)
        or not isinstance(request_message_id, str)
        or not request_message_id.strip()
        or not isinstance(source, dict)
        or not isinstance(source.get('id'), str)
        or not source['id'].strip()
    ):
        raise DocumentSourceUnavailable('The pending document request is invalid.')
    return {
        'source_mode': source_mode,
        'request_message_id': request_message_id,
        'source_message_id': source['id'],
        'source_digest': _document_copy_digest(source),
        'formats': list(formats),
    }


def _source_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DocumentSourceUnavailable('The source message has no valid timestamp.')
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise DocumentSourceUnavailable('The source message has no valid timestamp.') from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _validate_source(message: Any, conversation_id: str, before: datetime) -> Dict[str, Any]:
    if not isinstance(message, dict):
        raise DocumentSourceUnavailable('The requested source message is unavailable.')
    metadata = message.get('metadata')
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise DocumentSourceUnavailable('The source message metadata is invalid.')
    thread_info = metadata.get('thread_info')
    if thread_info is None:
        thread_info = {}
    if not isinstance(thread_info, dict):
        raise DocumentSourceUnavailable('The source message thread is invalid.')
    status = str(metadata.get('status') or message.get('status') or '').strip().lower()
    if (
        not isinstance(message.get('id'), str)
        or not message['id'].strip()
        or message.get('conversation_id') != conversation_id
        or message.get('role') != 'assistant'
        or not isinstance(message.get('content'), str)
        or not message['content'].strip()
        or _source_timestamp(message.get('timestamp')) >= before
        or metadata.get('masked')
        or metadata.get('masked_ranges')
        or metadata.get('is_generated_chat_artifact')
        or thread_info.get('active_thread') is False
        or metadata.get('incomplete')
        or metadata.get('error')
        or status in {'failed', 'error', 'canceled', 'cancelled', 'interrupted'}
    ):
        raise DocumentSourceUnavailable('The requested source message cannot be copied safely.')
    return message


def _candidate_heading(candidate: Any) -> str:
    if not isinstance(candidate, dict) or not isinstance(candidate.get('content'), str):
        raise DocumentSourceUnavailable('The named document source cannot be verified.')
    heading = re.match(r'\s*#{1,6}\s+([^\r\n]+)', candidate['content'])
    if not heading:
        heading = re.match(r'\s*\*\*([^\r\n*]+)\*\*\s*(?:\r?\n|$)', candidate['content'])
    return heading.group(1).strip().rstrip('#').strip() if heading else ''


def _named_source_matches(request_text: str, candidates: list) -> list:
    matches = []
    for candidate in candidates:
        title = _candidate_heading(candidate)
        if 8 <= len(title) <= 200 and re.search(
            r'(?<!\w)' + re.escape(title.casefold()) + r'(?!\w)', request_text,
        ):
            matches.append(candidate)
    return matches


def _has_abbreviated_source_reference(request_text: str, candidates: list) -> bool:
    """Detect possible shortened titles for refusal, never for selecting a source."""
    for candidate in candidates:
        title = _candidate_heading(candidate)
        if not 8 <= len(title) <= 200:
            continue
        title = title.rstrip('.').strip().casefold()
        words = re.fullmatch(
            r'([\w-]{5,})\s+(?:[\w-]+\s+)+(plan|report|strategy|guide|checklist|summary|proposal|analysis)',
            title,
        )
        if words and re.search(
            r'(?<!\w)' + re.escape(words.group(1)) + r'\s+' + re.escape(words.group(2)) + r'(?!\w)',
            request_text,
        ):
            return True
    return False


def resolve_document_copy_source(
    source_mode: str,
    conversation_id: str,
    current_user_timestamp: str,
    nearest_assistant: Optional[Dict[str, Any]],
    load_message: Callable[[str], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Resolve a copy source after the caller authorizes the conversation.

    The only permitted lineage is the server-recorded source of every document
    artifact on the immediately preceding assistant message.
    """
    if source_mode not in {'prior_assistant', 'prior_document'} or not conversation_id:
        raise DocumentSourceUnavailable('The requested document source is invalid.')
    nearest = _validate_source(
        nearest_assistant, conversation_id, _source_timestamp(current_user_timestamp),
    )
    if source_mode == 'prior_assistant':
        artifacts = (nearest.get('metadata') or {}).get('generated_analysis_artifacts') or []
        if not isinstance(artifacts, list) or any(
            isinstance(artifact, dict) and artifact.get('output_format') in {'docx', 'pdf'}
            for artifact in artifacts
        ):
            raise DocumentSourceUnavailable('The previous answer is a document handoff, not its source.')
        return nearest

    metadata = nearest.get('metadata') or {}
    artifacts = metadata.get('generated_analysis_artifacts')
    if not isinstance(artifacts, list):
        raise DocumentSourceUnavailable('The previous document has no copyable source.')
    document_artifacts = [
        artifact for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get('output_format') in {'docx', 'pdf'}
    ]
    if not document_artifacts:
        raise DocumentSourceUnavailable('The previous document has no copyable source.')
    source_ids = set()
    for artifact in document_artifacts:
        source_id = artifact.get('source_copy_message_id')
        artifact_id = artifact.get('artifact_message_id')
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(artifact_id, str) or not artifact_id.strip():
            raise DocumentSourceUnavailable('The previous document has no unambiguous source.')
        source_ids.add(source_id)
    if len(source_ids) != 1:
        raise DocumentSourceUnavailable('The previous documents have different sources.')
    source_id = next(iter(source_ids))
    source = _validate_source(
        load_message(source_id), conversation_id, _source_timestamp(nearest.get('timestamp')),
    )
    if source.get('id') != source_id:
        raise DocumentSourceUnavailable('The previous document source does not match.')
    return source


def load_authorized_document_copy_source(
    source_mode: str,
    conversation_id: str,
    current_user_message_id: str,
    authorize: Callable[[], None],
    read_message: Callable[[str], Optional[Dict[str, Any]]],
    read_recent: Callable[[str], list],
    read_recent_user: Optional[Callable[[str], list]] = None,
    depth_out: Optional[list] = None,
    _pending_depth: int = 0,
    read_named_candidates: Optional[Callable[[str], list]] = None,
) -> Dict[str, Any]:
    """Reauthorize and load a unique previous source for this particular user turn."""
    if not conversation_id or not current_user_message_id:
        raise DocumentSourceUnavailable('The current conversation turn is unavailable.')
    authorize()
    current = read_message(current_user_message_id)
    if not isinstance(current, dict):
        raise DocumentSourceUnavailable('The current conversation turn is unavailable.')
    metadata = current.get('metadata') or {}
    if not isinstance(metadata, dict):
        raise DocumentSourceUnavailable('The current conversation turn is invalid.')
    thread_info = metadata.get('thread_info') or {}
    if not isinstance(thread_info, dict):
        raise DocumentSourceUnavailable('The current conversation thread is invalid.')
    if (
        current.get('id') != current_user_message_id
        or current.get('conversation_id') != conversation_id
        or current.get('role') != 'user'
        or thread_info.get('active_thread') is False
        or metadata.get('masked')
        or metadata.get('masked_ranges')
    ):
        raise DocumentSourceUnavailable('The current conversation turn cannot use this source.')
    current_timestamp = _source_timestamp(current.get('timestamp'))
    recent = read_recent(current['timestamp'])
    if not isinstance(recent, list) or not recent:
        raise DocumentSourceUnavailable('There is no previous assistant answer to copy.')
    if len(recent) > 1 and _source_timestamp(recent[0].get('timestamp')) == _source_timestamp(recent[1].get('timestamp')):
        raise DocumentSourceUnavailable('The previous assistant source is ambiguous.')
    nearest = _validate_source(recent[0], conversation_id, current_timestamp)
    named_matches = None
    if source_mode in ('prior_assistant', 'named_assistant') and read_named_candidates is not None:
        candidates = read_named_candidates(current['timestamp'])
        if not isinstance(candidates, list):
            raise DocumentSourceUnavailable('The named document source cannot be verified.')
        named_matches = _named_source_matches(str(current.get('content') or '').casefold(), candidates)
        if len(candidates) > MAX_NAMED_DOCUMENT_SOURCE_CANDIDATES and (source_mode == 'named_assistant' or named_matches):
            raise DocumentSourceUnavailable('The named document source is ambiguous or outside the search limit.')
        if source_mode == 'prior_assistant' and not named_matches and _has_abbreviated_source_reference(
            str(current.get('content') or '').casefold(), candidates,
        ):
            raise DocumentSourceAmbiguousReference('Please identify the earlier answer by its complete heading.')
        if source_mode == 'prior_assistant' and named_matches and (
            len(named_matches) != 1 or named_matches[0].get('id') != nearest.get('id')
        ):
            if len(named_matches) != 1:
                raise DocumentSourceAmbiguousReference('The named document source is ambiguous.')
            raise DocumentSourceUnavailable('The named answer is not the immediately preceding source.')
    if source_mode == 'prior_assistant' and any(
        (nearest.get('metadata') or {}).get(key) is not None
        for key in ('pending_document_copy', 'pending_document_candidate', 'pending_document_recovery')
    ):
        raise DocumentSourceUnavailable('The previous answer is a document clarification, not its source.')
    if source_mode in ('pending_copy', 'recovery_copy'):
        nearest_metadata = nearest.get('metadata') or {}
        if source_mode == 'recovery_copy':
            recovery = nearest_metadata.get('pending_document_recovery')
            pending = recovery.get('copy') if isinstance(recovery, dict) else None
        else:
            pending = nearest_metadata.get('pending_document_copy')
            if pending is None:
                pending = nearest_metadata.get('pending_document_candidate')
        if not isinstance(pending, dict) or _pending_depth >= MAX_PENDING_DOCUMENT_COPY_DEPTH:
            raise DocumentSourceUnavailable('The pending document source is invalid.')
        mode = pending.get('source_mode')
        request_id = pending.get('request_message_id')
        if mode not in ('prior_assistant', 'named_assistant', 'prior_document', 'pending_copy') or not isinstance(request_id, str) or not request_id.strip():
            raise DocumentSourceUnavailable('The pending document source does not match.')
        request = read_message(request_id)
        if not isinstance(request, dict) or _source_timestamp(request.get('timestamp')) >= _source_timestamp(nearest.get('timestamp')):
            raise DocumentSourceUnavailable('The pending document request is invalid.')
        if read_recent_user is None:
            raise DocumentSourceUnavailable('The pending document request cannot be verified.')
        recent_users = read_recent_user(nearest['timestamp'])
        if (
            not isinstance(recent_users, list)
            or not recent_users
            or not isinstance(recent_users[0], dict)
            or recent_users[0].get('id') != request_id
            or (
                len(recent_users) > 1
                and _source_timestamp(recent_users[0].get('timestamp')) == _source_timestamp(recent_users[1].get('timestamp'))
            )
        ):
            raise DocumentSourceUnavailable('The pending document clarification has no unique request.')
        verified_depth = []
        source = load_authorized_document_copy_source(
            mode, conversation_id, request_id, authorize, read_message, read_recent,
            read_recent_user, verified_depth, _pending_depth + (source_mode == 'pending_copy'),
            read_named_candidates,
        )
        expected = create_pending_document_copy(mode, pending.get('formats'), request_id, source)
        if pending != expected:
            raise DocumentSourceUnavailable('The pending document source changed or is invalid.')
        if depth_out is not None:
            depth_out.extend(verified_depth)
        return source
    if source_mode == 'named_assistant':
        if named_matches is None:
            raise DocumentSourceUnavailable('The named document source cannot be verified.')
        if len(named_matches) != 1:
            raise DocumentSourceUnavailable('The named document source is missing or ambiguous.')
        nearest = _validate_source(named_matches[0], conversation_id, current_timestamp)
        if any((nearest.get('metadata') or {}).get(key) is not None for key in (
            'pending_document_copy', 'pending_document_candidate', 'pending_document_recovery',
        )):
            raise DocumentSourceUnavailable('The named answer is a document clarification.')
        source_mode = 'prior_assistant'
    source = resolve_document_copy_source(
        source_mode, conversation_id, current_timestamp.isoformat(), nearest, read_message,
    )
    if depth_out is not None:
        depth_out.append(_pending_depth)
    return source


def load_document_copy_source_from_container(
    source_mode: str,
    conversation_id: str,
    current_user_message_id: str,
    authorize: Callable[[], None],
    messages_container: Any,
    depth_out: Optional[list] = None,
) -> Dict[str, Any]:
    """Resolve the source using only records in the authorized conversation partition."""
    def read_message(message_id: str) -> Optional[Dict[str, Any]]:
        return messages_container.read_item(item=message_id, partition_key=conversation_id)

    def read_recent_role(before_timestamp: str, role: str) -> list:
        return list(messages_container.query_items(
            query=(
                'SELECT TOP 2 * FROM c WHERE c.conversation_id = @conversation_id '
                'AND c.role = @role AND c.timestamp < @before_timestamp ORDER BY c.timestamp DESC'
            ),
            parameters=[
                {'name': '@conversation_id', 'value': conversation_id},
                {'name': '@role', 'value': role},
                {'name': '@before_timestamp', 'value': before_timestamp},
            ],
            partition_key=conversation_id,
        ))

    def read_named_candidates(before_timestamp: str) -> list:
        return list(messages_container.query_items(
            query=(
                f'SELECT TOP {MAX_NAMED_DOCUMENT_SOURCE_CANDIDATES + 1} * FROM c '
                'WHERE c.conversation_id = @conversation_id AND c.role = @role '
                'AND c.timestamp < @before_timestamp ORDER BY c.timestamp DESC'
            ),
            parameters=[
                {'name': '@conversation_id', 'value': conversation_id},
                {'name': '@role', 'value': 'assistant'},
                {'name': '@before_timestamp', 'value': before_timestamp},
            ],
            partition_key=conversation_id,
        ))

    return load_authorized_document_copy_source(
        source_mode, conversation_id, current_user_message_id, authorize, read_message,
        lambda timestamp: read_recent_role(timestamp, 'assistant'),
        lambda timestamp: read_recent_role(timestamp, 'user'),
        depth_out,
        read_named_candidates=read_named_candidates,
    )
