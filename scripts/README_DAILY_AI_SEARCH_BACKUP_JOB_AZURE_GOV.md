# Daily Azure AI Search Backup Job (Azure Government)

Version implemented: **0.240.057**

This script provisions an Azure Container Apps Job that runs `scripts/backup_ai_search_indexes.py` once a day using managed identity.

- Script: `scripts/setup_daily_ai_search_backup_job_azure_gov.ps1`
- Scheduler target: Azure Container Apps Job (cron)
- Backup mode: `--write-direct-to-blob`

## Prerequisites

- Azure CLI installed and authenticated
- Container source image published in ACR (or run with `-BuildImage`)
- Access to create/modify resources in subscription

## Required parameters

- `-SubscriptionId`
- `-ResourceGroupName`
- `-SearchServiceName`
- `-StorageAccountName`
- `-StorageContainerName`

## Example

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

## Defaults

- Schedule: `0 2 * * *` (daily at 02:00 UTC)
- Job name: `job-search-backup-daily`
- Managed identity: `id-search-backup-job`
- Environment: `cae-search-backup-gov`

## Validation

After provisioning:

```powershell
az containerapp job start -g <resource-group> -n job-search-backup-daily
az containerapp job execution list -g <resource-group> -n job-search-backup-daily -o table
```

## Permissions applied by the script

- `Search Index Data Reader` on the Azure AI Search service
- `Storage Blob Data Contributor` on the storage account
