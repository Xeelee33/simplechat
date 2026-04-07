# Azure AI Search Backup Strategy

## Overview

This feature defines a practical backup and recovery strategy for Azure AI Search indexes used by SimpleChat.

Implemented in version: **0.239.013**
Enhanced in version: **0.239.014**
Enhanced in version: **0.239.015**
Enhanced in version: **0.239.016**
Enhanced in version: **0.240.004**
Enhanced in version: **0.240.005**
Enhanced in version: **0.240.006**
Enhanced in version: **0.240.007**
Enhanced in version: **0.240.021**

Related configuration update: `application/single_app/config.py` version updated to **0.240.021**.

## Purpose

Azure AI Search does not provide native self-service backup and restore for index content. This strategy establishes repeatable backup jobs and restore procedures so index loss or corruption can be recovered quickly with a documented operational process.

## Dependencies

- Azure AI Search (user, group, public indexes)
- Managed identity or equivalent identity allowed to read indexes/documents
- Local or mounted storage target for backup artifacts
- New script: `scripts/backup_ai_search_indexes.py`
- New script: `scripts/restore_ai_search_indexes.py`

## Technical Specifications

### Architecture Overview

SimpleChat treats Azure AI Search as a derived search layer, with the primary data in upstream stores and application workflows. This strategy adds point-in-time index exports (schema + documents) so operators can restore to a known backup set when required.

### Backup Jobs

1. Daily backup job (recommended)
   - Run `backup_ai_search_indexes.py` for all three indexes.
   - Export index schema + document payloads.
2. Weekly restore drill (recommended)
   - Restore into a non-production search service.
   - Validate document counts and sample query behavior.

### Dry-Run Remote Validation (New in 0.239.015)

The backup script now supports an optional validation mode for dry runs:

- `--dry-run` alone: creates folder scaffolding and a manifest only; no Azure reads.
- `--dry-run --dry-run-validate-remote`: validates each index remotely and captures Azure-reported total document counts in the manifest.

This mode does not export `index-schema.json` or `documents.jsonl`; it is intended for connectivity and count verification before a full backup run.

Requirements:

- `azure-identity` and `azure-search-documents` packages installed in the runtime.
- Identity with read access to target Azure AI Search indexes.

### Sovereign Cloud Audience Support (New in 0.239.016)

The backup script now resolves Azure AI Search token audience based on endpoint domain:

- `.search.windows.net` -> `https://search.azure.com`
- `.search.azure.us` -> `https://search.azure.us`
- `.search.azure.cn` -> `https://search.azure.cn`
- `.search.microsoftazure.de` -> `https://search.microsoftazure.de`

You can also override audience explicitly with:

- `--audience https://search.azure.us`

The generated manifest includes `resolved_search_audience` in `settings` for troubleshooting and verification.

### Optional Azure Blob Upload (New in 0.240.004)

The backup script now supports optional upload of completed local backup artifacts to Azure Blob Storage.

New CLI options:

- `--upload-to-blob`
- `--blob-container-url`
- `--blob-prefix`

Behavior:

- Backup files are still created locally first under `artifacts/ai_search_backups/<backup_id>/`.
- When upload is enabled, all files in that backup folder are uploaded to:
   - `<blob_prefix>/<backup_id>/...` (or `<backup_id>/...` when prefix is empty).
- Upload uses `DefaultAzureCredential` (managed identity, Azure CLI, or environment credentials).

Requirements:

- `azure-storage-blob` package in the runtime.
- Identity with write permission to the target container (for example, `Storage Blob Data Contributor`).

### Direct-to-Blob Backup Mode (New in 0.240.005)

The backup script now supports writing backup artifacts directly to Azure Blob Storage without writing per-index files to local disk.

New CLI option:

- `--write-direct-to-blob`

Behavior:

- Index schema and `documents.jsonl` are uploaded directly to blob paths under `<blob_prefix>/<backup_id>/indexes/<index_name>/`.
- `manifest.json` is uploaded directly to blob at `<blob_prefix>/<backup_id>/manifest.json`.
- Local artifact files are not created in this mode (except normal process temp/memory usage).

Requirements:

- `--blob-container-url` must be provided.
- `--dry-run` cannot be combined with `--write-direct-to-blob`.

### Resume Checkpoints (Enhanced in 0.240.007)

The backup script now supports resume checkpoints for both local-first and direct-to-blob export modes.

New CLI options:

- `--resume`
- `--backup-id`

Behavior:

- During local export, each index writes a `backup-state.json` checkpoint file that tracks document count and page continuation token.
- If a run is interrupted, rerunning with the same `--backup-id` and `--resume` continues from the last saved checkpoint for each index.
- Completed indexes are skipped automatically when checkpoint state indicates completion.
- During direct-to-blob export, each index writes `backup-state.json` to blob and appends document pages to an append blob so interrupted runs can continue from the saved continuation token.

Requirements / limits:

- `--resume` requires `--backup-id`.
- `--write-direct-to-blob` still cannot be combined with `--dry-run`.

### Restore Automation

The companion restore scaffold can recreate index definitions and rehydrate
documents from backup artifacts.

- Default behavior restores into suffixed target indexes (for example `simplechat-user-index-restore`) to reduce accidental in-place overwrite risk.
- Dry-run mode validates backup inputs and writes a restore manifest without calling Azure.

### Restore from Azure Blob Storage Container (New in 0.240.021)

The restore script now supports reading backup artifacts directly from the same blob container layout produced by the backup script.

New restore CLI options:

- `--blob-container-url`
- `--blob-prefix`
- `--backup-id`
- `--output-root`

Behavior:

- Restores `manifest.json`, `index-schema.json`, and `documents.jsonl` directly from blob paths under `<blob_prefix>/<backup_id>/...`.
- Does not require local backup folders when blob source mode is used.
- Writes restore manifests locally under `--output-root` (default `artifacts/ai_search_restores`).

Requirements:

- `azure-storage-blob` package in the runtime.
- Identity with read access to the target blob container.
- `--backup-id` is required when restoring from blob source.

### Storage Layout

The script writes backups under:

`artifacts/ai_search_backups/<backup_id>/`

Where each backup includes:

- `manifest.json`
- `indexes/<index_name>/index-schema.json`
- `indexes/<index_name>/documents.jsonl`

When uploaded to blob (`--upload-to-blob` or `--write-direct-to-blob`), the same relative structure is used under:

- `<blob_prefix>/<backup_id>/manifest.json`
- `<blob_prefix>/<backup_id>/indexes/<index_name>/index-schema.json`
- `<blob_prefix>/<backup_id>/indexes/<index_name>/documents.jsonl`

When `--dry-run-validate-remote` is enabled, per-index manifest results also include:

- `remote_validation_enabled`
- `remote_validation_success`
- `remote_document_count` (from Azure Search total count)
- `remote_validation_error` (when validation fails)

When audience resolution is enabled, manifest settings also include:

- `resolved_search_audience`

### Restore Steps

1. Choose backup folder and confirm `manifest.json` integrity.
2. Recreate target index schema from `index-schema.json`.
3. Re-ingest `documents.jsonl` into the target index.
4. Validate index health and document counts.
5. Switch query traffic (or alias) to restored index.

### RPO and RTO Targets

Initial operational targets:

- RPO: **24 hours** (daily backup cadence)
- RTO: **2–4 hours** for full index restoration and validation

Adjust targets based on index size, ingestion rate, and restore drill outcomes.

## Usage Instructions

### Run Backup Script

Example dry run:

`py -3 scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --dry-run`

Example dry run with remote validation/count check:

`py -3 scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --dry-run --dry-run-validate-remote`

Example dry run with explicit audience override:

`py -3 scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --dry-run --dry-run-validate-remote --audience https://search.azure.us`

Example backup run:

`py -3 scripts/backup_ai_search_indexes.py --endpoint https://<service>.search.azure.us --output-root artifacts/ai_search_backups`

Example backup + blob upload:

`python scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --output-root artifacts/ai_search_backups --upload-to-blob --blob-container-url https://stasbxaisearchbackup.blob.core.usgovcloudapi.net/ai-search-backups --blob-prefix simplechat/dev`

Example direct-to-blob backup (no local index files):

`python scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --write-direct-to-blob --blob-container-url https://stasbxaisearchbackup.blob.core.usgovcloudapi.net/ai-search-backups --blob-prefix simplechat/dev`

Example resume of interrupted local backup:

`python scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --output-root artifacts/ai_search_backups --backup-id 20260325T182513Z --resume`

Example resume of interrupted direct-to-blob backup:

`python scripts/backup_ai_search_indexes.py --endpoint https://ai-search-oigchat-sbx.search.azure.us --write-direct-to-blob --blob-container-url https://stasbxaisearchbackup.blob.core.usgovcloudapi.net/ai-search-backups --blob-prefix simplechat/dev --backup-id 20260325T182513Z --resume`

### Run Restore Script

Example dry run:

`py -3 scripts/restore_ai_search_indexes.py --endpoint https://<service>.search.windows.net --backup-path artifacts/ai_search_backups/<backup_id> --dry-run`

Example restore run:

`py -3 scripts/restore_ai_search_indexes.py --endpoint https://<service>.search.windows.net --backup-path artifacts/ai_search_backups/<backup_id>`

Example restore directly from blob backup artifacts:

`python scripts/restore_ai_search_indexes.py --endpoint https://<service>.search.azure.us --blob-container-url https://stasbxaisearchbackup.blob.core.usgovcloudapi.net/ai-search-backups --blob-prefix simplechat/dev --backup-id 20260325T182513Z`

## Integration Points

- Search writes and metadata propagation occur in `application/single_app/functions_documents.py`
- Upload entry points are in:
  - `application/single_app/route_backend_documents.py`
  - `application/single_app/route_backend_group_documents.py`
  - `application/single_app/route_backend_public_documents.py`
  - `application/single_app/route_external_public_documents.py`

## Testing and Validation

- Functional test coverage: `functional_tests/test_ai_search_backup_script_scaffold.py`
- Functional test coverage: `functional_tests/test_ai_search_restore_script_scaffold.py`
- Test verifies dry-run scaffolding and manifest generation without requiring Azure connectivity.
- 0.239.015 enhancement validated by script compile check and runtime functional test suite execution.
- 0.239.016 enhancement validated by script compile check and runtime functional test suite execution.
- 0.240.004 enhancement validated by script compile check and updated backup scaffold functional tests.
- 0.240.005 enhancement validated by script compile check and updated backup scaffold functional tests.
- 0.240.006 enhancement validated by script compile check and updated backup scaffold functional tests.
- 0.240.007 enhancement validated by script compile check and updated backup scaffold functional tests.
- 0.240.021 enhancement validated by script compile check and updated restore scaffold functional tests.

## Troubleshooting

### Azure Government Authentication Error (`invalid_resource` / `AADSTS500011`)

If manifest entries show `remote_validation_success: false` with an error referencing:

- `The resource principal named https://search.azure.com was not found...`

then the login token scope is targeting Public cloud audience while your endpoint is Azure Government.

Recommended fix:

1. Set Azure CLI cloud:
   - `az cloud set --name AzureUSGovernment`
2. Re-authenticate with Gov Search scope:
   - `az login --tenant <tenant-id> --scope https://search.azure.us/.default`
3. Re-run backup dry-run remote validation:
   - `python scripts/backup_ai_search_indexes.py --endpoint https://<service>.search.azure.us --dry-run --dry-run-validate-remote`

If needed, force audience explicitly:

- `python scripts/backup_ai_search_indexes.py --endpoint https://<service>.search.azure.us --dry-run --dry-run-validate-remote --audience https://search.azure.us`

## Known Limitations

- Restore script is currently scaffold-focused and intended for controlled operational use with validation drills.
- For very large indexes, backup duration and storage footprint will increase proportionally.
