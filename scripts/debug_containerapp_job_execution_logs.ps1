# debug_containerapp_job_execution_logs.ps1
param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory = $true)]
    [string]$ContainerAppsEnvironmentName,

    [Parameter(Mandatory = $true)]
    [string]$ContainerAppsJobName,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionName,

    [Parameter(Mandatory = $false)]
    [int]$LookbackDays = 2
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Ensure-AzCli {
    $azCommand = Get-Command az -ErrorAction SilentlyContinue
    if (-not $azCommand) {
        throw "Azure CLI (az) is required but not found in PATH."
    }
}

function Get-AzAccessToken {
    param([string]$Resource)

    $token = az account get-access-token --resource $Resource --query accessToken -o tsv
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "Failed to acquire access token for resource '$Resource'."
    }

    return $token
}

function Invoke-WorkspaceQuery {
    param(
        [string]$WorkspaceId,
        [string]$CustomerId,
        [string]$QueryText
    )

    $payloadJson = @{ query = $QueryText } | ConvertTo-Json -Compress

    $logAnalyticsResource = az cloud show --query "endpoints.logAnalyticsResourceId" -o tsv
    if ([string]::IsNullOrWhiteSpace($logAnalyticsResource)) {
        throw "Could not resolve Log Analytics resource endpoint from Azure cloud configuration."
    }

    $logAnalyticsToken = Get-AzAccessToken -Resource $logAnalyticsResource
    $logAnalyticsUri = "$($logAnalyticsResource.TrimEnd('/'))/v1/workspaces/$CustomerId/query"

    try {
        $dataPlaneResponse = Invoke-RestMethod `
            -Method Post `
            -Uri $logAnalyticsUri `
            -Headers @{ Authorization = "Bearer $logAnalyticsToken" } `
            -ContentType "application/json" `
            -Body $payloadJson

        if ($null -ne $dataPlaneResponse) {
            $queryResult = $dataPlaneResponse
        }
    }
    catch {
        $queryResult = $null
    }

    if ($null -eq $queryResult) {
        $armEndpoint = az cloud show --query "endpoints.resourceManager" -o tsv
        if ([string]::IsNullOrWhiteSpace($armEndpoint)) {
            throw "Could not resolve ARM endpoint from Azure cloud configuration."
        }

        $armResource = az cloud show --query "endpoints.activeDirectoryResourceId" -o tsv
        if ([string]::IsNullOrWhiteSpace($armResource)) {
            throw "Could not resolve Azure AD ARM token resource from Azure cloud configuration."
        }

        $armToken = Get-AzAccessToken -Resource $armResource
        $apiVersions = @("2021-12-01-preview", "2017-10-01")
        $armQueryError = $null

        foreach ($apiVersion in $apiVersions) {
            $workspaceQueryUri = "$armEndpoint$WorkspaceId/query?api-version=$apiVersion"
            try {
                $armResponse = Invoke-RestMethod `
                    -Method Post `
                    -Uri $workspaceQueryUri `
                    -Headers @{ Authorization = "Bearer $armToken" } `
                    -ContentType "application/json" `
                    -Body $payloadJson

                if ($null -ne $armResponse) {
                    $queryResult = $armResponse
                    break
                }
            }
            catch {
                $armQueryError = $_.Exception.Message
            }
        }

        if ($null -eq $queryResult) {
            if ([string]::IsNullOrWhiteSpace($armQueryError)) {
                throw "Workspace query failed using both Log Analytics data-plane and ARM endpoints."
            }
            throw "Workspace query failed using both Log Analytics data-plane and ARM endpoints. Last error: $armQueryError"
        }
    }

    if ($null -eq $queryResult) {
        Write-Host "No rows returned."
        return
    }

    if ($null -eq $queryResult.tables -or $queryResult.tables.Count -eq 0) {
        Write-Host "No rows returned."
        return
    }

    $table = $queryResult.tables[0]
    if ($null -eq $table.rows -or $table.rows.Count -eq 0) {
        Write-Host "No rows returned."
        return
    }

    $outputRows = foreach ($row in $table.rows) {
        $item = [ordered]@{}
        for ($i = 0; $i -lt $table.columns.Count; $i++) {
            $item[$table.columns[$i].name] = $row[$i]
        }
        [pscustomobject]$item
    }

    $outputRows | Format-Table -AutoSize
}

Write-Step "Validating prerequisites"
Ensure-AzCli

Write-Step "Execution details"
az containerapp job execution show `
    -g $ResourceGroupName `
    -n $ContainerAppsJobName `
    --job-execution-name $ExecutionName `
    -o yaml

Write-Step "Resolving Log Analytics workspace"
$customerId = az containerapp env show `
    -g $ResourceGroupName `
    -n $ContainerAppsEnvironmentName `
    --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId `
    -o tsv

if ([string]::IsNullOrWhiteSpace($customerId)) {
    throw "Could not resolve Log Analytics customerId from Container Apps environment '$ContainerAppsEnvironmentName'."
}

$workspaceId = az monitor log-analytics workspace list `
    -g $ResourceGroupName `
    --query "[?customerId=='$customerId'].id | [0]" `
    -o tsv

if ([string]::IsNullOrWhiteSpace($workspaceId)) {
    throw "Could not resolve Log Analytics workspace ID in resource group '$ResourceGroupName' for customerId '$customerId'."
}

Write-Host "Workspace ID: $workspaceId"

$consoleQuery = "ContainerAppConsoleLogs_CL | where TimeGenerated > ago($LookbackDays`d) | where ContainerJobName_s == '$ContainerAppsJobName' and (Log_s has '$ExecutionName' or ContainerGroupName_s has '$ExecutionName') | project TimeGenerated, ContainerGroupName_s, ContainerName_s, Log_s | order by TimeGenerated asc"
$systemQuery = "ContainerAppSystemLogs_CL | where TimeGenerated > ago($LookbackDays`d) | where JobName_s == '$ContainerAppsJobName' and ExecutionName_s == '$ExecutionName' | project TimeGenerated, Type_s, Reason_s, EventSource_s, Log_s | order by TimeGenerated asc"

Write-Step "Console logs"
Invoke-WorkspaceQuery -WorkspaceId $workspaceId -CustomerId $customerId -QueryText $consoleQuery

Write-Step "System logs"
Invoke-WorkspaceQuery -WorkspaceId $workspaceId -CustomerId $customerId -QueryText $systemQuery

Write-Host ""
Write-Host "If no rows were returned, rerun with a larger lookback window (for example -LookbackDays 7)." -ForegroundColor Yellow
