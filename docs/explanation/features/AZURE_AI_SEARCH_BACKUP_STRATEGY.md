# Azure AI Search Backup Strategy

## Overview

This feature defines a practical backup and recovery strategy for Azure AI Search indexes used by SimpleChat.

Implemented in version: **0.239.013**
Enhanced in version: **0.239.014**
Enhanced in version: **0.239.015**
Enhanced in version: **0.239.016**

Related configuration update: `application/single_app/config.py` version updated to **0.239.016**.

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

### Restore Automation

The companion restore scaffold can recreate index definitions and rehydrate
documents from backup artifacts.

- Default behavior restores into suffixed target indexes (for example `simplechat-user-index-restore`) to reduce accidental in-place overwrite risk.
- Dry-run mode validates backup inputs and writes a restore manifest without calling Azure.

### Storage Layout

The script writes backups under:

`artifacts/ai_search_backups/<backup_id>/`

Where each backup includes:

- `manifest.json`
- `indexes/<index_name>/index-schema.json`
- `indexes/<index_name>/documents.jsonl`

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

### Run Restore Script

Example dry run:

`py -3 scripts/restore_ai_search_indexes.py --endpoint https://<service>.search.windows.net --backup-path artifacts/ai_search_backups/<backup_id> --dry-run`

Example restore run:

`py -3 scripts/restore_ai_search_indexes.py --endpoint https://<service>.search.windows.net --backup-path artifacts/ai_search_backups/<backup_id>`

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
