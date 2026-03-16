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
        required=True,
        help="Path to a specific backup folder containing manifest.json and indexes/.",
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


def main() -> int:
    args = parse_args()
    logger = configure_logging(args.verbose)

    if args.target_mode == "same" and not args.allow_overwrite:
        logger.error("target-mode=same requires --allow-overwrite")
        return 1

    if args.batch_size <= 0:
        logger.error("batch-size must be greater than 0")
        return 1

    backup_path = Path(args.backup_path).resolve()
    if not backup_path.exists() or not backup_path.is_dir():
        logger.error("backup-path does not exist or is not a directory: %s", backup_path)
        return 1

    manifest = load_manifest(backup_path)
    source_indexes = resolve_indexes(args.indexes, manifest)

    restore_manifest = {
        "restore_id": utc_timestamp(),
        "restore_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": args.endpoint,
        "backup_path": str(backup_path),
        "source_backup_id": manifest.get("backup_id"),
        "settings": {
            "target_mode": args.target_mode,
            "target_suffix": args.target_suffix,
            "allow_overwrite": args.allow_overwrite,
            "batch_size": args.batch_size,
            "retries": args.retries,
            "dry_run": args.dry_run,
        },
        "results": [],
    }

    logger.info("Restore source: %s", backup_path)
    logger.info("Source indexes: %s", ", ".join(source_indexes))

    indexes_root = backup_path / "indexes"
    credential: Any = None
    index_client: Any = None
    search_client_cls: Any = None
    search_index_cls: Any = None

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
        except ModuleNotFoundError as error:
            logger.error(
                "Azure SDK packages are required for non-dry-run mode. "
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
        credential = DefaultAzureCredential()
        index_client = SearchIndexClient(endpoint=args.endpoint, credential=credential)
        search_client_cls = AzureSearchClient
        search_index_cls = AzureSearchIndex
    else:
        retry_exceptions = ()

    for source_index in source_indexes:
        target_index = target_index_name(source_index, args.target_mode, args.target_suffix)
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
