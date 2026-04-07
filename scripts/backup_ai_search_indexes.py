# backup_ai_search_indexes.py
"""Back up Azure AI Search index definitions and documents.

This script exports each target index into a timestamped folder containing:
- index-schema.json
- documents.jsonl
- manifest.json (at the backup root)

Authentication uses managed identity via DefaultAzureCredential.
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_INDEXES = [
    "simplechat-user-index",
    "simplechat-group-index",
    "simplechat-public-index",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up Azure AI Search index schemas and documents to local JSON files."
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Azure AI Search endpoint, for example https://myservice.search.windows.net",
    )
    parser.add_argument(
        "--indexes",
        nargs="+",
        default=DEFAULT_INDEXES,
        help="Index names to export. Defaults to the three SimpleChat indexes.",
    )
    parser.add_argument(
        "--output-root",
        default="artifacts/ai_search_backups",
        help="Root folder where backups are written.",
    )
    parser.add_argument(
        "--max-documents",
        type=int,
        default=0,
        help="Optional cap per index for document export (0 means no cap).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="Retry attempts for transient Azure calls.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create folder structure and manifest only; skip Azure reads.",
    )
    parser.add_argument(
        "--dry-run-validate-remote",
        action="store_true",
        help=(
            "When used with --dry-run, query Azure AI Search to validate index existence "
            "and capture remote total document counts in the manifest."
        ),
    )
    parser.add_argument(
        "--audience",
        default="",
        help=(
            "Optional token audience override. If omitted, audience is inferred from endpoint "
            "(for example https://search.azure.us for Azure Government)."
        ),
    )
    parser.add_argument(
        "--upload-to-blob",
        action="store_true",
        help="Upload the completed backup folder to Azure Blob Storage.",
    )
    parser.add_argument(
        "--blob-container-url",
        default="",
        help=(
            "Blob container URL for uploads, for example "
            "https://myaccount.blob.core.windows.net/ai-search-backups"
        ),
    )
    parser.add_argument(
        "--blob-prefix",
        default="",
        help="Optional blob path prefix (for example simplechat/prod).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a previously interrupted local backup using checkpoint state files.",
    )
    parser.add_argument(
        "--backup-id",
        default="",
        help=(
            "Optional backup id override. Required with --resume so the script can target "
            "the existing backup folder."
        ),
    )
    parser.add_argument(
        "--write-direct-to-blob",
        action="store_true",
        help=(
            "Write backup artifacts directly to Azure Blob Storage without saving "
            "index files locally. Requires --blob-container-url."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    return logging.getLogger("ai-search-backup")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "serialize"):
        return value.serialize()
    raise TypeError(f"Type {type(value)} is not JSON serializable")


def retry_call(
    func,
    retries: int,
    logger: logging.Logger,
    operation_name: str,
    retry_exceptions: tuple[type[BaseException], ...],
):
    delay_seconds = 1.0
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            return func()
        except retry_exceptions as error:
            last_error = error
            if attempt >= retries:
                break
            logger.warning(
                "%s failed (attempt %s/%s): %s. Retrying in %.1fs",
                operation_name,
                attempt,
                retries,
                error,
                delay_seconds,
            )
            time.sleep(delay_seconds)
            delay_seconds *= 2

    raise RuntimeError(f"{operation_name} failed after {retries} attempts: {last_error}")


def serialize_index(index_obj: Any) -> dict[str, Any]:
    if hasattr(index_obj, "serialize"):
        return index_obj.serialize(keep_readonly=True)
    if hasattr(index_obj, "as_dict"):
        return index_obj.as_dict()
    return json.loads(json.dumps(index_obj, default=json_default))


def resolve_search_audience(endpoint: str, audience_override: str) -> str:
    if audience_override:
        return audience_override.strip()

    host = (urlparse(endpoint).hostname or "").lower()
    if host.endswith(".search.azure.us"):
        return "https://search.azure.us"
    if host.endswith(".search.azure.cn"):
        return "https://search.azure.cn"
    if host.endswith(".search.microsoftazure.de"):
        return "https://search.microsoftazure.de"
    return "https://search.azure.com"


def create_search_index_client(index_client_cls: Any, endpoint: str, credential: Any, audience: str, logger: logging.Logger):
    try:
        return index_client_cls(endpoint=endpoint, credential=credential, audience=audience)
    except TypeError:
        logger.warning(
            "SearchIndexClient in this azure-search-documents version does not support 'audience'. "
            "Falling back to default audience."
        )
        return index_client_cls(endpoint=endpoint, credential=credential)


def create_search_client(search_client_cls: Any, endpoint: str, index_name: str, credential: Any, audience: str, logger: logging.Logger):
    try:
        return search_client_cls(
            endpoint=endpoint,
            index_name=index_name,
            credential=credential,
            audience=audience,
        )
    except TypeError:
        logger.warning(
            "SearchClient in this azure-search-documents version does not support 'audience'. "
            "Falling back to default audience."
        )
        return search_client_cls(endpoint=endpoint, index_name=index_name, credential=credential)


def backup_single_index(
    index_name: str,
    endpoint: str,
    credential: Any,
    search_audience: str,
    index_client: Any,
    search_client_cls: Any,
    retry_exceptions: tuple[type[BaseException], ...],
    index_output_dir: Path,
    max_documents: int,
    retries: int,
    resume: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    logger.info("Backing up index: %s", index_name)
    index_output_dir.mkdir(parents=True, exist_ok=True)

    schema_path = index_output_dir / "index-schema.json"
    documents_path = index_output_dir / "documents.jsonl"
    state_path = index_output_dir / "backup-state.json"

    resume_state = load_local_resume_state(state_path) if resume else None
    if resume and resume_state and resume_state.get("completed"):
        logger.info("Index %s already completed in resume state. Skipping.", index_name)
        return {
            "name": index_name,
            "success": True,
            "document_count": int(resume_state.get("document_count", 0)),
            "schema_file": str(schema_path),
            "documents_file": str(documents_path),
            "resumed": True,
            "skipped_completed": True,
        }

    continuation_token = resume_state.get("continuation_token") if resume_state else None
    document_count = int(resume_state.get("document_count", 0)) if resume_state else 0

    search_index = retry_call(
        lambda: index_client.get_index(index_name),
        retries=retries,
        logger=logger,
        operation_name=f"get_index({index_name})",
        retry_exceptions=retry_exceptions,
    )
    schema_payload = serialize_index(search_index)
    schema_path.write_text(
        json.dumps(schema_payload, indent=2, ensure_ascii=False, default=json_default),
        encoding="utf-8",
    )

    search_client = create_search_client(
        search_client_cls=search_client_cls,
        endpoint=endpoint,
        index_name=index_name,
        credential=credential,
        audience=search_audience,
        logger=logger,
    )
    results = retry_call(
        lambda: search_client.search(search_text="*", include_total_count=True),
        retries=retries,
        logger=logger,
        operation_name=f"search({index_name})",
        retry_exceptions=retry_exceptions,
    )

    write_mode = "a" if resume and documents_path.exists() else "w"
    with documents_path.open(write_mode, encoding="utf-8") as documents_file:
        page_iterator = results.by_page(continuation_token=continuation_token)
        for page in page_iterator:
            for doc in page:
                documents_file.write(json.dumps(doc, ensure_ascii=False, default=json_default) + "\n")
                document_count += 1
                if max_documents > 0 and document_count >= max_documents:
                    logger.info(
                        "Reached max-documents limit (%s) for %s",
                        max_documents,
                        index_name,
                    )
                    continuation_token = None
                    break

            if max_documents > 0 and document_count >= max_documents:
                save_local_resume_state(
                    state_path,
                    {
                        "index_name": index_name,
                        "document_count": document_count,
                        "continuation_token": None,
                        "completed": True,
                    },
                )
                break

            continuation_token = resolve_page_continuation_token(page_iterator, page)
            save_local_resume_state(
                state_path,
                {
                    "index_name": index_name,
                    "document_count": document_count,
                    "continuation_token": continuation_token,
                    "completed": continuation_token is None,
                },
            )

            if continuation_token is None:
                break

    save_local_resume_state(
        state_path,
        {
            "index_name": index_name,
            "document_count": document_count,
            "continuation_token": None,
            "completed": True,
        },
    )

    logger.info("Finished %s (documents exported: %s)", index_name, document_count)
    return {
        "name": index_name,
        "success": True,
        "document_count": document_count,
        "schema_file": str(schema_path),
        "documents_file": str(documents_path),
        "resumed": bool(resume and resume_state),
        "resume_state_file": str(state_path),
    }


def validate_remote_index_for_dry_run(
    index_name: str,
    endpoint: str,
    credential: Any,
    search_audience: str,
    index_client: Any,
    search_client_cls: Any,
    retry_exceptions: tuple[type[BaseException], ...],
    retries: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    try:
        retry_call(
            lambda: index_client.get_index(index_name),
            retries=retries,
            logger=logger,
            operation_name=f"dry_run_get_index({index_name})",
            retry_exceptions=retry_exceptions,
        )

        search_client = create_search_client(
            search_client_cls=search_client_cls,
            endpoint=endpoint,
            index_name=index_name,
            credential=credential,
            audience=search_audience,
            logger=logger,
        )
        results = retry_call(
            lambda: search_client.search(search_text="*", include_total_count=True, top=0),
            retries=retries,
            logger=logger,
            operation_name=f"dry_run_search_count({index_name})",
            retry_exceptions=retry_exceptions,
        )

        remote_document_count = None
        if hasattr(results, "get_count"):
            remote_document_count = results.get_count()

        return {
            "remote_validation_success": True,
            "remote_document_count": remote_document_count,
        }
    except Exception as error:
        logger.error("Dry-run remote validation failed for %s: %s", index_name, error)
        return {
            "remote_validation_success": False,
            "remote_document_count": None,
            "remote_validation_error": str(error),
        }


def upload_backup_to_blob(
    backup_root: Path,
    backup_id: str,
    blob_container_url: str,
    blob_prefix: str,
    credential: Any,
    container_client_cls: Any,
    logger: logging.Logger,
) -> dict[str, Any]:
    container_client = container_client_cls.from_container_url(
        container_url=blob_container_url,
        credential=credential,
    )

    normalized_prefix = blob_prefix.strip("/")
    upload_root = f"{normalized_prefix}/{backup_id}" if normalized_prefix else backup_id

    uploaded_files = 0
    uploaded_bytes = 0

    for file_path in sorted(backup_root.rglob("*")):
        if not file_path.is_file():
            continue

        relative_path = file_path.relative_to(backup_root).as_posix()
        blob_name = f"{upload_root}/{relative_path}"
        with file_path.open("rb") as file_stream:
            container_client.upload_blob(name=blob_name, data=file_stream, overwrite=True)

        uploaded_files += 1
        uploaded_bytes += file_path.stat().st_size

    logger.info(
        "Uploaded %s backup files (%s bytes) to blob container path: %s",
        uploaded_files,
        uploaded_bytes,
        upload_root,
    )

    return {
        "container_url": blob_container_url,
        "upload_root": upload_root,
        "uploaded_files": uploaded_files,
        "uploaded_bytes": uploaded_bytes,
    }


def build_blob_upload_root(blob_prefix: str, backup_id: str) -> str:
    normalized_prefix = blob_prefix.strip("/")
    return f"{normalized_prefix}/{backup_id}" if normalized_prefix else backup_id


def build_blob_url(blob_container_url: str, blob_name: str) -> str:
    return f"{blob_container_url.rstrip('/')}/{blob_name}"


def create_container_client(container_client_cls: Any, blob_container_url: str, credential: Any) -> Any:
    return container_client_cls.from_container_url(
        container_url=blob_container_url,
        credential=credential,
    )


def upload_manifest_to_blob(
    manifest: dict[str, Any],
    blob_container_url: str,
    upload_root: str,
    container_client: Any,
) -> str:
    manifest_blob_name = f"{upload_root}/manifest.json"
    manifest_payload = json.dumps(manifest, indent=2, ensure_ascii=False, default=json_default)
    container_client.upload_blob(
        name=manifest_blob_name,
        data=manifest_payload.encode("utf-8"),
        overwrite=True,
    )
    return build_blob_url(blob_container_url, manifest_blob_name)


def load_local_resume_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_local_resume_state(state_path: Path, state_payload: dict[str, Any]) -> None:
    state_path.write_text(
        json.dumps(state_payload, indent=2, ensure_ascii=False, default=json_default),
        encoding="utf-8",
    )


def load_blob_resume_state(container_client: Any, state_blob_name: str) -> dict[str, Any] | None:
    blob_client = container_client.get_blob_client(state_blob_name)
    try:
        if not blob_client.exists():
            return None
        payload = blob_client.download_blob().readall().decode("utf-8")
        return json.loads(payload)
    except Exception:
        return None


def save_blob_resume_state(container_client: Any, state_blob_name: str, state_payload: dict[str, Any]) -> None:
    blob_client = container_client.get_blob_client(state_blob_name)
    blob_client.upload_blob(
        data=json.dumps(state_payload, indent=2, ensure_ascii=False, default=json_default).encode("utf-8"),
        overwrite=True,
    )


def resolve_page_continuation_token(page_iterator: Any, page_object: Any) -> Any:
    page_token = getattr(page_object, "continuation_token", None)
    if page_token:
        return page_token
    return getattr(page_iterator, "continuation_token", None)


def backup_single_index_direct_to_blob(
    index_name: str,
    endpoint: str,
    credential: Any,
    search_audience: str,
    index_client: Any,
    search_client_cls: Any,
    retry_exceptions: tuple[type[BaseException], ...],
    container_client: Any,
    blob_container_url: str,
    upload_root: str,
    max_documents: int,
    retries: int,
    resume: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    logger.info("Backing up index directly to blob: %s", index_name)

    schema_blob_name = f"{upload_root}/indexes/{index_name}/index-schema.json"
    documents_blob_name = f"{upload_root}/indexes/{index_name}/documents.jsonl"
    state_blob_name = f"{upload_root}/indexes/{index_name}/backup-state.json"

    resume_state = load_blob_resume_state(container_client, state_blob_name) if resume else None
    if resume and resume_state and resume_state.get("completed"):
        logger.info("Index %s already completed in blob resume state. Skipping.", index_name)
        return {
            "name": index_name,
            "success": True,
            "document_count": int(resume_state.get("document_count", 0)),
            "schema_file": build_blob_url(blob_container_url, schema_blob_name),
            "documents_file": build_blob_url(blob_container_url, documents_blob_name),
            "direct_to_blob": True,
            "resumed": True,
            "skipped_completed": True,
            "resume_state_file": build_blob_url(blob_container_url, state_blob_name),
        }

    continuation_token = resume_state.get("continuation_token") if resume_state else None
    document_count = int(resume_state.get("document_count", 0)) if resume_state else 0

    documents_blob_client = container_client.get_blob_client(documents_blob_name)
    if resume and resume_state:
        if not documents_blob_client.exists():
            logger.warning(
                "Resume state exists for %s but documents blob is missing. Restarting index export.",
                index_name,
            )
            continuation_token = None
            document_count = 0
            documents_blob_client.create_append_blob()
    else:
        if documents_blob_client.exists():
            documents_blob_client.delete_blob()
        documents_blob_client.create_append_blob()

    search_index = retry_call(
        lambda: index_client.get_index(index_name),
        retries=retries,
        logger=logger,
        operation_name=f"get_index({index_name})",
        retry_exceptions=retry_exceptions,
    )
    schema_payload = serialize_index(search_index)
    schema_blob_bytes = json.dumps(
        schema_payload,
        indent=2,
        ensure_ascii=False,
        default=json_default,
    ).encode("utf-8")
    container_client.upload_blob(name=schema_blob_name, data=schema_blob_bytes, overwrite=True)

    search_client = create_search_client(
        search_client_cls=search_client_cls,
        endpoint=endpoint,
        index_name=index_name,
        credential=credential,
        audience=search_audience,
        logger=logger,
    )
    results = retry_call(
        lambda: search_client.search(search_text="*", include_total_count=True),
        retries=retries,
        logger=logger,
        operation_name=f"search({index_name})",
        retry_exceptions=retry_exceptions,
    )

    page_iterator = results.by_page(continuation_token=continuation_token)
    for page in page_iterator:
        buffer_lines: list[str] = []
        for doc in page:
            buffer_lines.append(json.dumps(doc, ensure_ascii=False, default=json_default) + "\n")
            document_count += 1
            if max_documents > 0 and document_count >= max_documents:
                logger.info(
                    "Reached max-documents limit (%s) for %s",
                    max_documents,
                    index_name,
                )
                continuation_token = None
                break

        if buffer_lines:
            documents_blob_client.append_block("".join(buffer_lines).encode("utf-8"))

        if max_documents > 0 and document_count >= max_documents:
            save_blob_resume_state(
                container_client,
                state_blob_name,
                {
                    "index_name": index_name,
                    "document_count": document_count,
                    "continuation_token": None,
                    "completed": True,
                },
            )
            break

        continuation_token = resolve_page_continuation_token(page_iterator, page)
        save_blob_resume_state(
            container_client,
            state_blob_name,
            {
                "index_name": index_name,
                "document_count": document_count,
                "continuation_token": continuation_token,
                "completed": continuation_token is None,
            },
        )

        if continuation_token is None:
            break

    save_blob_resume_state(
        container_client,
        state_blob_name,
        {
            "index_name": index_name,
            "document_count": document_count,
            "continuation_token": None,
            "completed": True,
        },
    )

    logger.info("Finished %s (documents exported directly to blob: %s)", index_name, document_count)
    return {
        "name": index_name,
        "success": True,
        "document_count": document_count,
        "schema_file": build_blob_url(blob_container_url, schema_blob_name),
        "documents_file": build_blob_url(blob_container_url, documents_blob_name),
        "direct_to_blob": True,
        "resumed": bool(resume and resume_state),
        "resume_state_file": build_blob_url(blob_container_url, state_blob_name),
    }


def main() -> int:
    args = parse_args()
    logger = configure_logging(args.verbose)

    backup_id = args.backup_id.strip() or utc_timestamp()
    backup_root = Path(args.output_root) / backup_id
    indexes_root = backup_root / "indexes"

    if not args.write_direct_to_blob:
        indexes_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "backup_id": backup_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": args.endpoint,
        "indexes": args.indexes,
        "settings": {
            "max_documents": args.max_documents,
            "retries": args.retries,
            "dry_run": args.dry_run,
            "dry_run_validate_remote": args.dry_run_validate_remote,
            "audience": args.audience,
            "upload_to_blob": args.upload_to_blob,
            "blob_container_url": args.blob_container_url,
            "blob_prefix": args.blob_prefix,
            "resume": args.resume,
            "backup_id": backup_id,
            "write_direct_to_blob": args.write_direct_to_blob,
        },
        "results": [],
    }

    logger.info("Backup root: %s", backup_root)
    logger.info("Target indexes: %s", ", ".join(args.indexes))

    credential = None
    index_client = None
    search_client_cls = None
    container_client_cls = None
    container_client = None
    retry_exceptions = ()
    search_audience = resolve_search_audience(args.endpoint, args.audience)

    manifest["settings"]["resolved_search_audience"] = search_audience

    should_initialize_azure_clients = (not args.dry_run) or args.dry_run_validate_remote
    should_initialize_identity = should_initialize_azure_clients or args.upload_to_blob or args.write_direct_to_blob

    if (args.upload_to_blob or args.write_direct_to_blob) and not args.blob_container_url.strip():
        logger.error("Blob upload modes require --blob-container-url.")
        return 1

    if args.resume and not args.backup_id.strip():
        logger.error("--resume requires --backup-id.")
        return 1

    if args.write_direct_to_blob and args.dry_run:
        logger.error("--write-direct-to-blob is not supported with --dry-run.")
        return 1

    if should_initialize_identity:
        try:
            from azure.identity import DefaultAzureCredential
        except ModuleNotFoundError as error:
            logger.error(
                "Azure identity package is required for this mode. "
                "Install azure-identity. Missing module: %s",
                error,
            )
            return 1

        credential = DefaultAzureCredential()

    if args.upload_to_blob or args.write_direct_to_blob:
        try:
            from azure.storage.blob import ContainerClient
        except ModuleNotFoundError as error:
            logger.error(
                "Azure Blob package is required for upload mode. "
                "Install azure-storage-blob. Missing module: %s",
                error,
            )
            return 1

        container_client_cls = ContainerClient
        container_client = create_container_client(
            container_client_cls=container_client_cls,
            blob_container_url=args.blob_container_url.strip(),
            credential=credential,
        )

    upload_root = build_blob_upload_root(args.blob_prefix, backup_id)

    if should_initialize_azure_clients:
        try:
            from azure.core.exceptions import (
                AzureError,
                HttpResponseError,
                ServiceRequestError,
                ServiceResponseError,
            )
            from azure.search.documents import SearchClient
            from azure.search.documents.indexes import SearchIndexClient
        except ModuleNotFoundError as error:
            logger.error(
                "Azure SDK packages are required for this mode. "
                "Install azure-identity and azure-search-documents. Missing module: %s",
                error,
            )
            return 1

        retry_exceptions = (
            HttpResponseError,
            ServiceRequestError,
            ServiceResponseError,
            AzureError,
            OSError,
            TimeoutError,
        )
        index_client = create_search_index_client(
            index_client_cls=SearchIndexClient,
            endpoint=args.endpoint,
            credential=credential,
            audience=search_audience,
            logger=logger,
        )
        search_client_cls = SearchClient

    if args.dry_run:
        if args.dry_run_validate_remote and (
            credential is None
            or index_client is None
            or search_client_cls is None
        ):
            logger.error("dry-run remote validation requested but Azure clients are not initialized.")
            return 1

        for index_name in args.indexes:
            index_dir = indexes_root / index_name
            index_dir.mkdir(parents=True, exist_ok=True)
            index_result = {
                "name": index_name,
                "success": True,
                "document_count": 0,
                "schema_file": str(index_dir / "index-schema.json"),
                "documents_file": str(index_dir / "documents.jsonl"),
                "dry_run": True,
            }

            if args.dry_run_validate_remote:
                index_result["remote_validation_enabled"] = True
                remote_validation = validate_remote_index_for_dry_run(
                    index_name=index_name,
                    endpoint=args.endpoint,
                    credential=credential,
                    search_audience=search_audience,
                    index_client=index_client,
                    search_client_cls=search_client_cls,
                    retry_exceptions=retry_exceptions,
                    retries=args.retries,
                    logger=logger,
                )
                index_result.update(remote_validation)
                if not remote_validation.get("remote_validation_success", False):
                    index_result["success"] = False

            manifest["results"].append(index_result)
    else:
        if credential is None or index_client is None or search_client_cls is None:
            logger.error("Non-dry-run mode requires initialized Azure clients.")
            return 1

        for index_name in args.indexes:
            if args.write_direct_to_blob:
                if container_client is None:
                    logger.error("Direct-to-blob mode requires an initialized blob container client.")
                    return 1
                result = backup_single_index_direct_to_blob(
                    index_name=index_name,
                    endpoint=args.endpoint,
                    credential=credential,
                    search_audience=search_audience,
                    index_client=index_client,
                    search_client_cls=search_client_cls,
                    retry_exceptions=retry_exceptions,
                    container_client=container_client,
                    blob_container_url=args.blob_container_url.strip(),
                    upload_root=upload_root,
                    max_documents=args.max_documents,
                    retries=args.retries,
                    resume=args.resume,
                    logger=logger,
                )
            else:
                index_dir = indexes_root / index_name
                result = backup_single_index(
                    index_name=index_name,
                    endpoint=args.endpoint,
                    credential=credential,
                    search_audience=search_audience,
                    index_client=index_client,
                    search_client_cls=search_client_cls,
                    retry_exceptions=retry_exceptions,
                    index_output_dir=index_dir,
                    max_documents=args.max_documents,
                    retries=args.retries,
                    resume=args.resume,
                    logger=logger,
                )
            manifest["results"].append(result)

    manifest_path = backup_root / "manifest.json"
    manifest_path_output = str(manifest_path)
    if args.write_direct_to_blob:
        if container_client is None:
            logger.error("Direct-to-blob mode requires an initialized blob container client.")
            return 1
        try:
            manifest_path_output = upload_manifest_to_blob(
                manifest=manifest,
                blob_container_url=args.blob_container_url.strip(),
                upload_root=upload_root,
                container_client=container_client,
            )
        except Exception as error:
            logger.error("Direct-to-blob manifest upload failed: %s", error)
            return 1
    else:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=json_default),
            encoding="utf-8",
        )

    failed = [item for item in manifest["results"] if not item.get("success")]
    if failed:
        logger.error("Backup completed with failures. See manifest: %s", manifest_path_output)
        return 1

    if args.upload_to_blob and not args.write_direct_to_blob:
        if credential is None or container_client_cls is None:
            logger.error("Blob upload requested but Azure Blob upload dependencies are unavailable.")
            return 1
        try:
            upload_result = upload_backup_to_blob(
                backup_root=backup_root,
                backup_id=backup_id,
                blob_container_url=args.blob_container_url.strip(),
                blob_prefix=args.blob_prefix,
                credential=credential,
                container_client_cls=container_client_cls,
                logger=logger,
            )
            logger.info(
                "Blob upload completed: %s",
                json.dumps(upload_result, ensure_ascii=False),
            )
        except Exception as error:
            logger.error("Backup upload to blob failed: %s", error)
            return 1

    logger.info("Backup completed successfully. Manifest: %s", manifest_path_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
