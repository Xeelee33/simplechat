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


def backup_single_index(
    index_name: str,
    endpoint: str,
    credential: Any,
    index_client: Any,
    search_client_cls: Any,
    retry_exceptions: tuple[type[BaseException], ...],
    index_output_dir: Path,
    max_documents: int,
    retries: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    logger.info("Backing up index: %s", index_name)
    index_output_dir.mkdir(parents=True, exist_ok=True)

    schema_path = index_output_dir / "index-schema.json"
    documents_path = index_output_dir / "documents.jsonl"

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

    search_client = search_client_cls(endpoint=endpoint, index_name=index_name, credential=credential)
    results = retry_call(
        lambda: search_client.search(search_text="*", include_total_count=True),
        retries=retries,
        logger=logger,
        operation_name=f"search({index_name})",
        retry_exceptions=retry_exceptions,
    )

    document_count = 0
    with documents_path.open("w", encoding="utf-8") as documents_file:
        for doc in results:
            documents_file.write(json.dumps(doc, ensure_ascii=False, default=json_default) + "\n")
            document_count += 1
            if max_documents > 0 and document_count >= max_documents:
                logger.info(
                    "Reached max-documents limit (%s) for %s",
                    max_documents,
                    index_name,
                )
                break

    logger.info("Finished %s (documents exported: %s)", index_name, document_count)
    return {
        "name": index_name,
        "success": True,
        "document_count": document_count,
        "schema_file": str(schema_path),
        "documents_file": str(documents_path),
    }


def main() -> int:
    args = parse_args()
    logger = configure_logging(args.verbose)

    backup_id = utc_timestamp()
    backup_root = Path(args.output_root) / backup_id
    indexes_root = backup_root / "indexes"
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
        },
        "results": [],
    }

    logger.info("Backup root: %s", backup_root)
    logger.info("Target indexes: %s", ", ".join(args.indexes))

    if args.dry_run:
        for index_name in args.indexes:
            index_dir = indexes_root / index_name
            index_dir.mkdir(parents=True, exist_ok=True)
            manifest["results"].append(
                {
                    "name": index_name,
                    "success": True,
                    "document_count": 0,
                    "schema_file": str(index_dir / "index-schema.json"),
                    "documents_file": str(index_dir / "documents.jsonl"),
                    "dry_run": True,
                }
            )
    else:
        try:
            from azure.core.exceptions import (
                AzureError,
                HttpResponseError,
                ServiceRequestError,
                ServiceResponseError,
            )
            from azure.identity import DefaultAzureCredential
            from azure.search.documents import SearchClient
            from azure.search.documents.indexes import SearchIndexClient
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

        for index_name in args.indexes:
            index_dir = indexes_root / index_name
            result = backup_single_index(
                index_name=index_name,
                endpoint=args.endpoint,
                credential=credential,
                index_client=index_client,
                search_client_cls=SearchClient,
                retry_exceptions=retry_exceptions,
                index_output_dir=index_dir,
                max_documents=args.max_documents,
                retries=args.retries,
                logger=logger,
            )
            manifest["results"].append(result)

    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=json_default),
        encoding="utf-8",
    )

    failed = [item for item in manifest["results"] if not item.get("success")]
    if failed:
        logger.error("Backup completed with failures. See manifest: %s", manifest_path)
        return 1

    logger.info("Backup completed successfully. Manifest: %s", manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
