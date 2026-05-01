# restore_ai_search_indexes.py
"""Restore Azure AI Search index definitions and documents from backup artifacts.

Expected backup structure:
- <backup_path>/manifest.json
- <backup_path>/indexes/<index_name>/index-schema.json
- <backup_path>/indexes/<index_name>/documents.jsonl

By default, restore targets are created with a suffix to avoid overwriting
active indexes.
"""

import argparse
import codecs
import copy
import json
import logging
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

DEFAULT_INDEXES = [
    "simplechat-user-index",
    "simplechat-group-index",
    "simplechat-public-index",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore Azure AI Search indexes from backup schema and JSONL documents."
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Azure AI Search endpoint, for example https://myservice.search.windows.net",
    )
    parser.add_argument(
        "--backup-path",
        default="",
        help="Path to a specific backup folder containing manifest.json and indexes/.",
    )
    parser.add_argument(
        "--blob-container-url",
        default="",
        help=(
            "Optional blob container URL to restore directly from backup artifacts in storage, "
            "for example https://myaccount.blob.core.windows.net/ai-search-backups"
        ),
    )
    parser.add_argument(
        "--blob-prefix",
        default="",
        help="Optional blob path prefix used during backup (for example simplechat/dev).",
    )
    parser.add_argument(
        "--backup-id",
        default="",
        help="Backup id folder name under blob prefix when restoring from blob storage.",
    )
    parser.add_argument(
        "--output-root",
        default="artifacts/ai_search_restores",
        help="Folder where restore manifests are written when restoring from blob storage.",
    )
    parser.add_argument(
        "--indexes",
        nargs="+",
        default=None,
        help="Optional subset of source indexes to restore.",
    )
    parser.add_argument(
        "--target-mode",
        choices=["suffix", "same"],
        default="suffix",
        help="Restore into source names (same) or source+suffix (suffix).",
    )
    parser.add_argument(
        "--target-suffix",
        default="-restore",
        help="Suffix used when target-mode is suffix.",
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Required when target-mode is same to acknowledge in-place overwrite risk.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Document upload batch size for restore operations.",
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
        help="Validate backup inputs and write restore manifest only.",
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
    return logging.getLogger("ai-search-restore")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def load_manifest(backup_path: Path) -> dict[str, Any]:
    manifest_path = backup_path / "manifest.json"
    if not manifest_path.exists():
        return {}
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        return json.load(manifest_file)


def build_blob_backup_root(blob_prefix: str, backup_id: str) -> str:
    normalized_prefix = blob_prefix.strip("/")
    return f"{normalized_prefix}/{backup_id}" if normalized_prefix else backup_id


def build_blob_url(blob_container_url: str, blob_name: str) -> str:
    return f"{blob_container_url.rstrip('/')}/{blob_name}"


def create_container_client(container_client_cls: Any, blob_container_url: str, credential: Any) -> Any:
    return container_client_cls.from_container_url(
        container_url=blob_container_url,
        credential=credential,
    )


def read_blob_text(container_client: Any, blob_name: str) -> str | None:
    blob_client = container_client.get_blob_client(blob_name)
    if not blob_client.exists():
        return None
    return blob_client.download_blob().readall().decode("utf-8")


def load_manifest_from_blob(container_client: Any, blob_backup_root: str) -> dict[str, Any]:
    manifest_blob_name = f"{blob_backup_root}/manifest.json"
    payload = read_blob_text(container_client, manifest_blob_name)
    if not payload:
        return {}
    return json.loads(payload)


def resolve_indexes(requested_indexes: list[str] | None, manifest: dict[str, Any]) -> list[str]:
    if requested_indexes:
        return requested_indexes
    manifest_indexes = manifest.get("indexes", [])
    return manifest_indexes if manifest_indexes else DEFAULT_INDEXES


def target_index_name(source_index_name: str, target_mode: str, target_suffix: str) -> str:
    if target_mode == "same":
        return source_index_name
    return f"{source_index_name}{target_suffix}"


def load_schema_file(schema_path: Path) -> dict[str, Any]:
    with schema_path.open("r", encoding="utf-8") as schema_file:
        return json.load(schema_file)


def count_jsonl_documents(documents_path: Path) -> int:
    count = 0
    with documents_path.open("r", encoding="utf-8") as documents_file:
        for line in documents_file:
            if line.strip():
                count += 1
    return count


def count_jsonl_documents_from_blob(container_client: Any, documents_blob_name: str) -> int:
    count = 0
    for line in iter_jsonl_lines_from_blob(container_client, documents_blob_name):
        if line.strip():
            count += 1
    return count


def load_schema_from_blob(container_client: Any, schema_blob_name: str) -> dict[str, Any] | None:
    payload = read_blob_text(container_client, schema_blob_name)
    if not payload:
        return None
    return json.loads(payload)


def build_index_definition(schema_payload: dict[str, Any], target_name: str) -> dict[str, Any]:
    payload = copy.deepcopy(schema_payload)
    payload["name"] = target_name
    payload.pop("@odata.etag", None)
    payload.pop("etag", None)
    return payload


def upload_documents_from_jsonl(
    search_client: Any,
    documents_path: Path,
    batch_size: int,
    retries: int,
    logger: logging.Logger,
    retry_exceptions: tuple[type[BaseException], ...],
) -> dict[str, int]:
    uploaded_count = 0
    failed_count = 0
    current_batch: list[dict[str, Any]] = []

    def upload_batch(batch: list[dict[str, Any]]) -> None:
        nonlocal uploaded_count, failed_count
        result = retry_call(
            lambda: search_client.upload_documents(documents=batch),
            retries=retries,
            logger=logger,
            operation_name=f"upload_documents({len(batch)} docs)",
            retry_exceptions=retry_exceptions,
        )
        for item in result:
            if getattr(item, "succeeded", False):
                uploaded_count += 1
            else:
                failed_count += 1

    with documents_path.open("r", encoding="utf-8") as documents_file:
        for raw_line in documents_file:
            line = raw_line.strip()
            if not line:
                continue
            current_batch.append(json.loads(line))
            if len(current_batch) >= batch_size:
                upload_batch(current_batch)
                current_batch = []

    if current_batch:
        upload_batch(current_batch)

    return {
        "uploaded_count": uploaded_count,
        "failed_count": failed_count,
    }


def iter_jsonl_lines_from_blob(container_client: Any, documents_blob_name: str):
    blob_client = container_client.get_blob_client(documents_blob_name)
    downloader = blob_client.download_blob()
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""

    for chunk in downloader.chunks():
        decoded = decoder.decode(chunk)
        if decoded:
            buffer += decoded
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line

    tail = decoder.decode(b"", final=True)
    if tail:
        buffer += tail

    if buffer:
        yield buffer


def upload_documents_from_jsonl_blob(
    search_client: Any,
    container_client: Any,
    documents_blob_name: str,
    batch_size: int,
    retries: int,
    logger: logging.Logger,
    retry_exceptions: tuple[type[BaseException], ...],
) -> dict[str, int]:
    uploaded_count = 0
    failed_count = 0
    current_batch: list[dict[str, Any]] = []

    def upload_batch(batch: list[dict[str, Any]]) -> None:
        nonlocal uploaded_count, failed_count
        result = retry_call(
            lambda: search_client.upload_documents(documents=batch),
            retries=retries,
            logger=logger,
            operation_name=f"upload_documents({len(batch)} docs)",
            retry_exceptions=retry_exceptions,
        )
        for item in result:
            if getattr(item, "succeeded", False):
                uploaded_count += 1
            else:
                failed_count += 1

    for raw_line in iter_jsonl_lines_from_blob(container_client, documents_blob_name):
        line = raw_line.strip()
        if not line:
            continue
        current_batch.append(json.loads(line))
        if len(current_batch) >= batch_size:
            upload_batch(current_batch)
            current_batch = []

    if current_batch:
        upload_batch(current_batch)

    return {
        "uploaded_count": uploaded_count,
        "failed_count": failed_count,
    }


def main() -> int:
    args = parse_args()
    logger = configure_logging(args.verbose)

    if args.target_mode == "same" and not args.allow_overwrite:
        logger.error("target-mode=same requires --allow-overwrite")
        return 1

    if args.batch_size <= 0:
        logger.error("batch-size must be greater than 0")
        return 1

    is_blob_source = bool(args.blob_container_url.strip())
    backup_id = args.backup_id.strip()
    blob_backup_root = ""

    if is_blob_source:
        if not backup_id:
            logger.error("Restoring from blob requires --backup-id.")
            return 1
        blob_backup_root = build_blob_backup_root(args.blob_prefix, backup_id)
        backup_path = None
    else:
        if not args.backup_path.strip():
            logger.error("Local restore requires --backup-path.")
            return 1
        backup_path = Path(args.backup_path).resolve()
        if not backup_path.exists() or not backup_path.is_dir():
            logger.error("backup-path does not exist or is not a directory: %s", backup_path)
            return 1

    manifest = {}
    source_indexes = resolve_indexes(args.indexes, manifest)

    restore_manifest = {
        "restore_id": utc_timestamp(),
        "restore_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": args.endpoint,
        "backup_path": str(backup_path) if backup_path else "",
        "source_backup_id": manifest.get("backup_id") or backup_id,
        "settings": {
            "target_mode": args.target_mode,
            "target_suffix": args.target_suffix,
            "allow_overwrite": args.allow_overwrite,
            "batch_size": args.batch_size,
            "retries": args.retries,
            "dry_run": args.dry_run,
            "blob_container_url": args.blob_container_url,
            "blob_prefix": args.blob_prefix,
            "backup_id": backup_id,
            "restore_from_blob": is_blob_source,
        },
        "results": [],
    }

    logger.info("Restore source: %s", blob_backup_root if is_blob_source else backup_path)
    credential: Any = None
    index_client: Any = None
    search_client_cls: Any = None
    search_index_cls: Any = None
    container_client: Any = None

    if not args.dry_run:
        try:
            from azure.core.exceptions import (
                AzureError,
                HttpResponseError,
                ServiceRequestError,
                ServiceResponseError,
            )
            from azure.identity import DefaultAzureCredential
            from azure.search.documents import SearchClient as AzureSearchClient
            from azure.search.documents.indexes import SearchIndexClient
            from azure.search.documents.indexes.models import SearchIndex as AzureSearchIndex
            from azure.storage.blob import ContainerClient
        except ModuleNotFoundError as error:
            logger.error(
                "Azure SDK packages are required. "
                "Install azure-identity, azure-search-documents, and azure-storage-blob. Missing module: %s",
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
        credential = DefaultAzureCredential()
        index_client = SearchIndexClient(endpoint=args.endpoint, credential=credential)
        search_client_cls = AzureSearchClient
        search_index_cls = AzureSearchIndex
        if is_blob_source:
            container_client = create_container_client(
                container_client_cls=ContainerClient,
                blob_container_url=args.blob_container_url.strip(),
                credential=credential,
            )
    else:
        retry_exceptions = ()

    if is_blob_source and args.dry_run:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import ContainerClient
        except ModuleNotFoundError as error:
            logger.error(
                "Azure SDK packages are required for blob-source dry-run restore. "
                "Install azure-identity and azure-storage-blob. Missing module: %s",
                error,
            )
            return 1
        credential = DefaultAzureCredential()
        container_client = create_container_client(
            container_client_cls=ContainerClient,
            blob_container_url=args.blob_container_url.strip(),
            credential=credential,
        )

    if is_blob_source:
        if container_client is None:
            logger.error("Blob-source restore requires an initialized blob container client.")
            return 1
        manifest = load_manifest_from_blob(container_client, blob_backup_root)
        if not manifest:
            logger.error(
                "Could not load backup manifest from blob source: %s",
                build_blob_url(args.blob_container_url.strip(), f"{blob_backup_root}/manifest.json"),
            )
            return 1
    else:
        manifest = load_manifest(backup_path)

    source_indexes = resolve_indexes(args.indexes, manifest)
    restore_manifest["source_backup_id"] = manifest.get("backup_id") or backup_id

    logger.info("Source indexes: %s", ", ".join(source_indexes))

    indexes_root = (backup_path / "indexes") if backup_path else None

    for source_index in source_indexes:
        target_index = target_index_name(source_index, args.target_mode, args.target_suffix)
        if is_blob_source:
            schema_blob_name = f"{blob_backup_root}/indexes/{source_index}/index-schema.json"
            documents_blob_name = f"{blob_backup_root}/indexes/{source_index}/documents.jsonl"

            schema_payload = load_schema_from_blob(container_client, schema_blob_name)
            documents_blob_exists = container_client.get_blob_client(documents_blob_name).exists()

            if schema_payload is None or not documents_blob_exists:
                logger.error("Missing backup blobs for index %s", source_index)
                restore_manifest["results"].append(
                    {
                        "source_index": source_index,
                        "target_index": target_index,
                        "success": False,
                        "error": "Missing index-schema.json or documents.jsonl in blob source",
                        "schema_blob": build_blob_url(args.blob_container_url.strip(), schema_blob_name),
                        "documents_blob": build_blob_url(args.blob_container_url.strip(), documents_blob_name),
                    }
                )
                continue

            source_doc_count = count_jsonl_documents_from_blob(container_client, documents_blob_name)
        else:
            if indexes_root is None:
                logger.error("Local restore mode expected indexes_root to be initialized.")
                return 1

            index_dir = indexes_root / source_index
            schema_path = index_dir / "index-schema.json"
            documents_path = index_dir / "documents.jsonl"

            if not schema_path.exists() or not documents_path.exists():
                logger.error("Missing backup files for index %s", source_index)
                restore_manifest["results"].append(
                    {
                        "source_index": source_index,
                        "target_index": target_index,
                        "success": False,
                        "error": "Missing index-schema.json or documents.jsonl",
                    }
                )
                continue

            schema_payload = load_schema_file(schema_path)
            source_doc_count = count_jsonl_documents(documents_path)

        if args.dry_run:
            restore_manifest["results"].append(
                {
                    "source_index": source_index,
                    "target_index": target_index,
                    "success": True,
                    "source_document_count": source_doc_count,
                    "uploaded_count": 0,
                    "failed_count": 0,
                    "dry_run": True,
                }
            )
            continue

        index_definition_payload = build_index_definition(schema_payload, target_index)
        if (
            search_client_cls is None
            or search_index_cls is None
            or index_client is None
            or credential is None
        ):
            logger.error("Restore runtime dependencies are not initialized.")
            return 1

        search_index_model = search_index_cls.deserialize(index_definition_payload)

        retry_call(
            partial(index_client.create_or_update_index, search_index_model),
            retries=args.retries,
            logger=logger,
            operation_name=f"create_or_update_index({target_index})",
            retry_exceptions=retry_exceptions,
        )

        search_client = search_client_cls(
            endpoint=args.endpoint,
            index_name=target_index,
            credential=credential,
        )

        if is_blob_source:
            upload_result = upload_documents_from_jsonl_blob(
                search_client=search_client,
                container_client=container_client,
                documents_blob_name=documents_blob_name,
                batch_size=args.batch_size,
                retries=args.retries,
                logger=logger,
                retry_exceptions=retry_exceptions,
            )
        else:
            upload_result = upload_documents_from_jsonl(
                search_client=search_client,
                documents_path=documents_path,
                batch_size=args.batch_size,
                retries=args.retries,
                logger=logger,
                retry_exceptions=retry_exceptions,
            )

        restore_manifest["results"].append(
            {
                "source_index": source_index,
                "target_index": target_index,
                "success": upload_result["failed_count"] == 0,
                "source_document_count": source_doc_count,
                "uploaded_count": upload_result["uploaded_count"],
                "failed_count": upload_result["failed_count"],
            }
        )

    restore_manifest["restore_finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    if is_blob_source:
        restore_output_root = Path(args.output_root).resolve()
        restore_output_root.mkdir(parents=True, exist_ok=True)
        restore_manifest_path = restore_output_root / f"restore_manifest_{restore_manifest['restore_id']}.json"
    else:
        if backup_path is None:
            logger.error("Local restore mode expected backup_path to be initialized.")
            return 1
        restore_manifest_path = backup_path / f"restore_manifest_{restore_manifest['restore_id']}.json"

    restore_manifest_path.write_text(
        json.dumps(restore_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    failed = [item for item in restore_manifest["results"] if not item.get("success")]
    if failed:
        logger.error("Restore completed with failures. Manifest: %s", restore_manifest_path)
        return 1

    logger.info("Restore completed successfully. Manifest: %s", restore_manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
