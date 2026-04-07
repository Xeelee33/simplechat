# Azure Daily Search Backup Job Automation

## Overview

This feature adds an operations script to provision a scheduled Azure Government job that runs the Azure AI Search backup script once per day.

Version implemented: **0.240.057**

Dependencies:

- Azure Container Apps Jobs
- Azure Container Registry (ACR)
- Azure Managed Identity
- Azure Blob Storage
- Azure AI Search

## Technical Specifications

### Architecture Overview

The provisioning script creates a scheduled Azure Container Apps Job that runs:

- `scripts/backup_ai_search_indexes.py`

The job uses managed identity and writes backup artifacts directly to blob storage using:

- `--write-direct-to-blob`

### Configuration Options

Script: `scripts/setup_daily_ai_search_backup_job_azure_gov.ps1`

Key parameters:

- `SubscriptionId`
- `ResourceGroupName`
- `SearchServiceName`
- `StorageAccountName`
- `StorageContainerName`
- `BlobPrefix`
- `ScheduleCron` (default `0 2 * * *`)

### Security and Permissions

The script assigns:

- `Search Index Data Reader` on Azure AI Search
- `Storage Blob Data Contributor` on Storage Account

## Usage Instructions

### How to run

```powershell
./scripts/setup_daily_ai_search_backup_job_azure_gov.ps1 `
  -SubscriptionId "<sub-id>" `
  -ResourceGroupName "rg-simplechat-gov" `
  -SearchServiceName "ai-search-oigchat-sbx" `
  -StorageAccountName "stasbxaisearchbackup" `
  -StorageContainerName "ai-search-backups" `
  -BlobPrefix "simplechat/prod" `
  -AcrName "acrsimplechatgov001" `
  -BuildImage `
  -SetAzureCloud
```

### Validation steps

```powershell
az containerapp job start -g <resource-group> -n job-search-backup-daily
az containerapp job execution list -g <resource-group> -n job-search-backup-daily -o table
```

## Testing and Validation

Functional test coverage:

- `functional_tests/test_daily_ai_search_backup_job_script_scaffold.py`

Checks include:

- Scheduled Container Apps Job command markers
- Backup command markers (`--write-direct-to-blob`)
- README usage marker verification
