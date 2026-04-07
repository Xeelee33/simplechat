# setup_daily_ai_search_backup_job_azure_gov.ps1
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory = $false)]
    [string]$Location = "usgovvirginia",

    [Parameter(Mandatory = $true)]
    [string]$SearchServiceName,

    [Parameter(Mandatory = $true)]
    [string]$StorageAccountName,

    [Parameter(Mandatory = $true)]
    [string]$StorageContainerName,

    [Parameter(Mandatory = $false)]
    [string]$BlobPrefix = "simplechat/prod",

    [Parameter(Mandatory = $false)]
    [string]$ContainerAppsEnvironmentName = "cae-search-backup-gov",

    [Parameter(Mandatory = $false)]
    [string]$ContainerAppsJobName = "job-search-backup-daily",

    [Parameter(Mandatory = $false)]
    [string]$ManagedIdentityName = "id-search-backup-job",

    [Parameter(Mandatory = $false)]
    [string]$AcrName = "",

    [Parameter(Mandatory = $false)]
    [string]$ImageName = "search-backup",

    [Parameter(Mandatory = $false)]
    [string]$ImageTag = "v1",

    [Parameter(Mandatory = $false)]
    [string]$ScheduleCron = "0 2 * * *",

    [Parameter(Mandatory = $false)]
    [int]$ReplicaTimeoutSeconds = 3600,

    [Parameter(Mandatory = $false)]
    [int]$ReplicaRetryLimit = 2,

    [Parameter(Mandatory = $false)]
    [switch]$BuildImage,

    [Parameter(Mandatory = $false)]
    [switch]$SetAzureCloud
)

$ErrorActionPreference = "Stop"

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

function Ensure-Extension {
    param([string]$Name)
    az extension add --name $Name --upgrade | Out-Null
}

function Ensure-Group {
    param([string]$GroupName, [string]$GroupLocation)
    az group create -n $GroupName -l $GroupLocation | Out-Null
}

function Resolve-AcrName {
    param([string]$RequestedAcrName, [string]$GroupName)

    if (-not [string]::IsNullOrWhiteSpace($RequestedAcrName)) {
        return $RequestedAcrName
    }

    $seed = ($GroupName -replace '[^a-zA-Z0-9]', '').ToLower()
    if ($seed.Length -gt 35) {
        $seed = $seed.Substring(0, 35)
    }

    $randomSuffix = -join ((48..57) + (97..122) | Get-Random -Count 8 | ForEach-Object {[char]$_})
    return "acr$seed$randomSuffix"
}

function Ensure-Acr {
    param([string]$GroupName, [string]$RegistryName)
    az acr create -g $GroupName -n $RegistryName --sku Basic | Out-Null
}

function Ensure-ContainerAppEnvironment {
    param([string]$GroupName, [string]$EnvironmentName, [string]$EnvironmentLocation)
    az containerapp env create -g $GroupName -n $EnvironmentName -l $EnvironmentLocation | Out-Null
}

function Ensure-ManagedIdentity {
    param([string]$GroupName, [string]$IdentityName)
    az identity create -g $GroupName -n $IdentityName | Out-Null
}

function New-RoleAssignment {
    param(
        [string]$PrincipalObjectId,
        [string]$RoleName,
        [string]$Scope
    )

    $existing = az role assignment list --assignee-object-id $PrincipalObjectId --scope $Scope --query "[?roleDefinitionName=='$RoleName'] | length(@)" -o tsv
    if ($existing -eq "0") {
        az role assignment create --assignee-object-id $PrincipalObjectId --assignee-principal-type ServicePrincipal --role $RoleName --scope $Scope | Out-Null
        Write-Host "Created role assignment: $RoleName on $Scope"
    }
    else {
        Write-Host "Role assignment already exists: $RoleName on $Scope"
    }
}

function Build-ImageIfRequested {
    param(
        [bool]$ShouldBuild,
        [string]$RegistryName,
        [string]$GroupName,
        [string]$ContainerImageName,
        [string]$ContainerImageTag
    )

    if ($ShouldBuild) {
        Write-Step "Building image in ACR"
        az acr build -r $RegistryName -g $GroupName -t "$ContainerImageName`:$ContainerImageTag" . | Out-Null
    }
    else {
        Write-Host "Skipping image build. Use -BuildImage to build and push with az acr build."
    }
}

function Upsert-ContainerAppsJob {
    param(
        [string]$GroupName,
        [string]$JobName,
        [string]$EnvironmentName,
        [string]$CronExpression,
        [int]$TimeoutSeconds,
        [int]$RetryLimit,
        [string]$ImageRef,
        [string]$ManagedIdentityResourceId,
        [string]$RegistryServer,
        [string]$SearchEndpoint,
        [string]$BlobContainerUrl,
        [string]$BlobPrefixValue
    )

    $jobExists = az containerapp job show -g $GroupName -n $JobName --query "name" -o tsv 2>$null

    $commandArgs = "scripts/backup_ai_search_indexes.py --endpoint $SearchEndpoint --write-direct-to-blob --blob-container-url $BlobContainerUrl --blob-prefix $BlobPrefixValue"

    if ([string]::IsNullOrWhiteSpace($jobExists)) {
        Write-Step "Creating Container Apps Job"
        az containerapp job create `
            -g $GroupName -n $JobName `
            --environment $EnvironmentName `
            --trigger-type Schedule `
            --cron-expression "$CronExpression" `
            --replica-timeout $TimeoutSeconds `
            --replica-retry-limit $RetryLimit `
            --parallelism 1 `
            --replica-completion-count 1 `
            --image $ImageRef `
            --cpu 1.0 --memory 2Gi `
            --mi-user-assigned $ManagedIdentityResourceId `
            --registry-server $RegistryServer `
            --registry-identity $ManagedIdentityResourceId `
            --command "python" `
            --args $commandArgs | Out-Null
    }
    else {
        Write-Step "Updating existing Container Apps Job"
        az containerapp job update `
            -g $GroupName -n $JobName `
            --cron-expression "$CronExpression" `
            --replica-timeout $TimeoutSeconds `
            --replica-retry-limit $RetryLimit `
            --image $ImageRef `
            --cpu 1.0 --memory 2Gi `
            --mi-user-assigned $ManagedIdentityResourceId `
            --registry-server $RegistryServer `
            --registry-identity $ManagedIdentityResourceId `
            --command "python" `
            --args $commandArgs | Out-Null
    }
}

Write-Step "Validating prerequisites"
Ensure-AzCli
Ensure-Extension -Name "containerapp"

if ($SetAzureCloud) {
    Write-Step "Setting Azure cloud to AzureUSGovernment"
    az cloud set --name AzureUSGovernment | Out-Null
}

Write-Step "Selecting subscription"
az account set --subscription $SubscriptionId

Write-Step "Creating baseline resources"
Ensure-Group -GroupName $ResourceGroupName -GroupLocation $Location

$resolvedAcrName = Resolve-AcrName -RequestedAcrName $AcrName -GroupName $ResourceGroupName
Ensure-Acr -GroupName $ResourceGroupName -RegistryName $resolvedAcrName
Ensure-ContainerAppEnvironment -GroupName $ResourceGroupName -EnvironmentName $ContainerAppsEnvironmentName -EnvironmentLocation $Location
Ensure-ManagedIdentity -GroupName $ResourceGroupName -IdentityName $ManagedIdentityName

$acrLoginServer = az acr show -g $ResourceGroupName -n $resolvedAcrName --query loginServer -o tsv
$managedIdentityId = az identity show -g $ResourceGroupName -n $ManagedIdentityName --query id -o tsv
$managedIdentityPrincipalId = az identity show -g $ResourceGroupName -n $ManagedIdentityName --query principalId -o tsv

$searchResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Search/searchServices/$SearchServiceName"
$storageResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"
$searchEndpoint = "https://$SearchServiceName.search.azure.us"
$blobContainerUrl = "https://$StorageAccountName.blob.core.usgovcloudapi.net/$StorageContainerName"

Write-Step "Applying RBAC"
New-RoleAssignment -PrincipalObjectId $managedIdentityPrincipalId -RoleName "Search Index Data Reader" -Scope $searchResourceId
New-RoleAssignment -PrincipalObjectId $managedIdentityPrincipalId -RoleName "Storage Blob Data Contributor" -Scope $storageResourceId

Build-ImageIfRequested -ShouldBuild $BuildImage.IsPresent -RegistryName $resolvedAcrName -GroupName $ResourceGroupName -ContainerImageName $ImageName -ContainerImageTag $ImageTag

$imageRef = "$acrLoginServer/$ImageName`:$ImageTag"

Upsert-ContainerAppsJob `
    -GroupName $ResourceGroupName `
    -JobName $ContainerAppsJobName `
    -EnvironmentName $ContainerAppsEnvironmentName `
    -CronExpression $ScheduleCron `
    -TimeoutSeconds $ReplicaTimeoutSeconds `
    -RetryLimit $ReplicaRetryLimit `
    -ImageRef $imageRef `
    -ManagedIdentityResourceId $managedIdentityId `
    -RegistryServer $acrLoginServer `
    -SearchEndpoint $searchEndpoint `
    -BlobContainerUrl $blobContainerUrl `
    -BlobPrefixValue $BlobPrefix

Write-Step "Setup complete"
Write-Host "Container Apps Job: $ContainerAppsJobName"
Write-Host "Image: $imageRef"
Write-Host "Search endpoint: $searchEndpoint"
Write-Host "Blob container URL: $blobContainerUrl"
Write-Host "Cron schedule: $ScheduleCron"
Write-Host ""
Write-Host "Manual run command:" -ForegroundColor Yellow
Write-Host "az containerapp job start -g $ResourceGroupName -n $ContainerAppsJobName"
Write-Host ""
Write-Host "Execution history command:" -ForegroundColor Yellow
Write-Host "az containerapp job execution list -g $ResourceGroupName -n $ContainerAppsJobName -o table"
