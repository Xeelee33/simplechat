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
            "dry_run_validate_remote": args.dry_run_validate_remote,
            "audience": args.audience,
        },
        "results": [],
    }

    logger.info("Backup root: %s", backup_root)
    logger.info("Target indexes: %s", ", ".join(args.indexes))

    credential = None
    index_client = None
    search_client_cls = None
    retry_exceptions = ()
    search_audience = resolve_search_audience(args.endpoint, args.audience)

    manifest["settings"]["resolved_search_audience"] = search_audience

    should_initialize_azure_clients = (not args.dry_run) or args.dry_run_validate_remote

    if should_initialize_azure_clients:
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

        credential = DefaultAzureCredential()
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
