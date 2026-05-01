# Local Daily Azure AI Search Backup Scheduler

## Overview

This feature provides a local-machine scheduler pattern for daily Azure AI Search backups when running SimpleChat outside Azure-hosted schedulers.

Version implemented: **0.241.008**

Dependencies:

- `scripts/backup_ai_search_indexes.py`
- `scripts/run_daily_ai_search_backup_local.ps1`
- Azure CLI (`az`)
- Python runtime (`py` or `python`)

## Technical Specifications

### Architecture Overview

- Local Windows Task Scheduler triggers a PowerShell runner once per day.
- Runner invokes `scripts/backup_ai_search_indexes.py` with `--write-direct-to-blob`.
- Backup artifacts are written directly to Azure Blob Storage.

### Script Parameters

The runner script supports:

- `SearchEndpoint` (required)
- `BlobContainerUrl` (required)
- `BlobPrefix` (default: `simplechat/local`)
- `CloudName` (default: `AzureUSGovernment`)
- `PythonCommand` (default: `py`)
- `BackupScriptPath` (default: `scripts/backup_ai_search_indexes.py`)
- `EnsureLogin` (optional login/context check)
- `VerboseLogging` (optional verbose output)

### File Structure

- `scripts/run_daily_ai_search_backup_local.ps1`
- `scripts/backup_ai_search_indexes.py`

## Usage Instructions

### 1) Manual run validation

From repo root:

```powershell
./scripts/run_daily_ai_search_backup_local.ps1 `
  -SearchEndpoint "https://<search-name>.search.azure.us" `
  -BlobContainerUrl "https://<storage-account>.blob.core.usgovcloudapi.net/<container>" `
  -BlobPrefix "simplechat/prod" `
  -EnsureLogin
```

### 2) Create a daily Windows scheduled task

Use an elevated PowerShell prompt and adjust values:

```powershell
schtasks /Create /F /SC DAILY /ST 02:00 /TN "SimpleChat-AISearch-DailyBackup" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\cpalmer\chadstuff\simplechat\scripts\run_daily_ai_search_backup_local.ps1 -SearchEndpoint https://<search-name>.search.azure.us -BlobContainerUrl https://<storage-account>.blob.core.usgovcloudapi.net/<container> -BlobPrefix simplechat/prod -EnsureLogin" /RU "<service-account-or-user>"
```

### 3) Run on demand and inspect

```powershell
schtasks /Run /TN "SimpleChat-AISearch-DailyBackup"
schtasks /Query /TN "SimpleChat-AISearch-DailyBackup" /V /FO LIST
```

## Testing and Validation

### Test Coverage

- Functional scaffold test validates the presence of key runner markers and required backup arguments.

### Performance Considerations

- Use a stable host that remains online at schedule time.
- Prefer a dedicated service account and explicit Azure subscription context.

### Known Limitations

- Local scheduled tasks do not provide cloud-native resiliency by default.
- Authentication lifecycle for local credentials must be managed operationally.
