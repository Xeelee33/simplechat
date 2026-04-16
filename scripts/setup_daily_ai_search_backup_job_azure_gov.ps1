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
    [switch]$SetAzureCloud,

    [Parameter(Mandatory = $false)]
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $true
}

# Force UTF-8 so Azure CLI log streaming (colorama) doesn't crash on
# non-cp1252 characters when running on Windows.
$env:PYTHONIOENCODING = "utf-8"

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

    $installedCount = az extension list --query "[?name=='$Name'] | length(@)" -o tsv
    if ($installedCount -ge 1) {
        Write-Host "Azure CLI extension '$Name' already installed."
        return
    }

    $installedSuccessfully = $true
    try {
        az extension add --name $Name --allow-preview true | Out-Null
    }
    catch {
        $installedSuccessfully = $false
    }

    if (-not $installedSuccessfully) {
        Write-Warning "Failed to install Azure CLI extension '$Name'. Verifying whether 'az containerapp' is already available."
        $containerAppAvailable = $true
        try {
            az containerapp --help 1>$null 2>$null
        }
        catch {
            $containerAppAvailable = $false
        }

        if (-not $containerAppAvailable) {
            throw "Azure CLI extension '$Name' is required and could not be installed. Resolve Azure CLI extension installation issues and rerun."
        }
        Write-Host "Continuing because 'az containerapp' is available."
    }
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
        az acr build -r $RegistryName -g $GroupName -t "$ContainerImageName`:$ContainerImageTag" --file scripts/Dockerfile --no-logs . | Out-Null
    }
    else {
        Write-Host "Skipping image build. Use -BuildImage to build and push with az acr build."
    }
}

function Resolve-RegistryAuth {
    param([string]$GroupName, [string]$RegistryName, [string]$RegistryServer)

    $auth = @{
        UseAdminCredentials = $false
        Username = ""
        Password = ""
    }

    if ($RegistryServer.EndsWith(".azurecr.us")) {
        $adminEnabled = az acr show -g $GroupName -n $RegistryName --query "adminUserEnabled" -o tsv
        if ($adminEnabled -ne "true") {
            Write-Host "Enabling ACR admin user for Azure Government registry auth compatibility..."
            az acr update -g $GroupName -n $RegistryName --admin-enabled true | Out-Null
        }

        $username = $null
        $password = $null

        for ($attempt = 1; $attempt -le 6; $attempt++) {
            $username = az acr credential show -g $GroupName -n $RegistryName --query "username" -o tsv 2>$null
            $password = az acr credential show -g $GroupName -n $RegistryName --query "passwords[0].value" -o tsv 2>$null

            if (-not [string]::IsNullOrWhiteSpace($username) -and -not [string]::IsNullOrWhiteSpace($password)) {
                break
            }

            if ($attempt -lt 6) {
                Write-Host "Waiting for ACR admin credentials to propagate (attempt $attempt/6)..."
                Start-Sleep -Seconds 5
            }
        }

        if ([string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($password)) {
            throw "ACR credentials are empty for '$RegistryName' after enabling admin user and waiting for propagation. Verify registry state and rerun: az acr credential show -g $GroupName -n $RegistryName"
        }

        $auth.UseAdminCredentials = $true
        $auth.Username = $username
        $auth.Password = $password
    }

    return $auth
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
        [string]$ManagedIdentityClientId,
        [string]$RegistryServer,
        [bool]$UseRegistryAdminCredentials,
        [string]$RegistryUsername,
        [string]$RegistryPassword,
        [string]$SearchEndpoint,
        [string]$BlobContainerUrl,
        [string]$BlobPrefixValue
    )

    $jobExists = $null
    try {
        $jobExists = az containerapp job show -g $GroupName -n $JobName --query "name" -o tsv 2>$null
    } catch {}

    $commandArgs = "python scripts/backup_ai_search_indexes.py --endpoint $SearchEndpoint --write-direct-to-blob --blob-container-url $BlobContainerUrl --blob-prefix $BlobPrefixValue"

    if ([string]::IsNullOrWhiteSpace($jobExists)) {
        Write-Step "Creating Container Apps Job"
        if ($UseRegistryAdminCredentials) {
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
                --registry-username $RegistryUsername `
                --registry-password $RegistryPassword `
                --command "/bin/sh -c" `
                --set-env-vars "AZURE_CLIENT_ID=$ManagedIdentityClientId" `
                --args $commandArgs | Out-Null
        }
        else {
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
                --command "/bin/sh -c" `
                --set-env-vars "AZURE_CLIENT_ID=$ManagedIdentityClientId" `
                --args $commandArgs | Out-Null
        }
    }
    else {
        Write-Step "Updating existing Container Apps Job"
        if ($UseRegistryAdminCredentials) {
            az containerapp job update `
                -g $GroupName -n $JobName `
                --cron-expression "$CronExpression" `
                --replica-timeout $TimeoutSeconds `
                --replica-retry-limit $RetryLimit `
                --image $ImageRef `
                --cpu 1.0 --memory 2Gi `
                --mi-user-assigned $ManagedIdentityResourceId `
                --registry-server $RegistryServer `
                --registry-username $RegistryUsername `
                --registry-password $RegistryPassword `
                --command "/bin/sh -c" `
                --set-env-vars "AZURE_CLIENT_ID=$ManagedIdentityClientId" `
                --args $commandArgs | Out-Null
        }
        else {
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
                --command "/bin/sh -c" `
                --set-env-vars "AZURE_CLIENT_ID=$ManagedIdentityClientId" `
                --args $commandArgs | Out-Null
        }
    }
}

function Ensure-ContainerAppsJobShellCommandArray {
    param(
        [string]$GroupName,
        [string]$JobName,
        [string]$CommandArgs
    )

    $job = az containerapp job show -g $GroupName -n $JobName -o json | ConvertFrom-Json
    $jobId = $job.id
    $container = $job.properties.template.containers[0]

    if (
        $container.command.Count -eq 2 -and
        $container.command[0] -eq "/bin/sh" -and
        $container.command[1] -eq "-c" -and
        $container.args.Count -eq 1 -and
        $container.args[0] -eq $CommandArgs
    ) {
        return
    }

    $resourceManagerEndpoint = az cloud show --query "endpoints.resourceManager" -o tsv
    $armResource = az cloud show --query "endpoints.activeDirectoryResourceId" -o tsv
    $uri = "$($resourceManagerEndpoint.TrimEnd('/'))$jobId?api-version=2024-03-01"

    $patchBody = @{
        properties = @{
            template = @{
                containers = @(
                    @{
                        name = $container.name
                        image = $container.image
                        resources = @{
                            cpu = $container.resources.cpu
                            memory = $container.resources.memory
                        }
                        command = @("/bin/sh", "-c")
                        args = @($CommandArgs)
                    }
                )
            }
        }
    }

    $tmpFile = Join-Path $env:TEMP "job-command-patch-$JobName.json"
    $patchBody | ConvertTo-Json -Depth 30 | Set-Content -Path $tmpFile -Encoding utf8

    az rest `
        --method patch `
        --uri $uri `
        --resource $armResource `
        --headers "Content-Type=application/json" `
        --body "@$tmpFile" | Out-Null
}

function Invoke-PostSetupSmokeTest {
    param(
        [string]$GroupName,
        [string]$JobName,
        [string]$StorageAccount,
        [string]$StorageContainer,
        [string]$BlobPrefixValue,
        [int]$TimeoutSeconds = 900,
        [int]$PollIntervalSeconds = 10
    )

    Write-Step "Running post-setup smoke test"

    $executionName = az containerapp job start -g $GroupName -n $JobName --query name -o tsv
    if ([string]::IsNullOrWhiteSpace($executionName)) {
        throw "Smoke test failed: could not start job execution."
    }

    Write-Host "Started smoke-test execution: $executionName"

    $executionStartTime = [DateTime]::UtcNow
    $deadline = $executionStartTime.AddSeconds($TimeoutSeconds)
    $finalStatus = ""
    $terminalStatuses = @("Succeeded", "Failed", "Stopped", "Canceled")

    while ([DateTime]::UtcNow -lt $deadline) {
        $finalStatus = az containerapp job execution show `
            -g $GroupName `
            -n $JobName `
            --job-execution-name $executionName `
            --query "properties.status" `
            -o tsv

        if ($terminalStatuses -contains $finalStatus) {
            break
        }

        Start-Sleep -Seconds $PollIntervalSeconds
    }

    if ($finalStatus -ne "Succeeded") {
        throw "Smoke test failed: execution '$executionName' ended with status '$finalStatus'."
    }

    $manifestCandidates = az storage blob list `
        --account-name $StorageAccount `
        --container-name $StorageContainer `
        --auth-mode login `
        --num-results 5000 `
        --query "[?contains(name, '$BlobPrefixValue/') && ends_with(name, '/manifest.json')].{name:name,lastModified:properties.lastModified}" `
        -o json | ConvertFrom-Json

    if (-not $manifestCandidates -or $manifestCandidates.Count -eq 0) {
        throw "Smoke test failed: no manifest.json files found under blob prefix '$BlobPrefixValue'."
    }

    $latestManifest = $manifestCandidates |
        Sort-Object { [DateTime]::Parse($_.lastModified) } -Descending |
        Select-Object -First 1

    $latestManifestTime = [DateTime]::Parse($latestManifest.lastModified).ToUniversalTime()
    if ($latestManifestTime -lt $executionStartTime.AddMinutes(-1)) {
        throw "Smoke test failed: latest manifest '$($latestManifest.name)' is older than this smoke-test run."
    }

    Write-Host "Smoke test passed: execution '$executionName' succeeded and wrote '$($latestManifest.name)'." -ForegroundColor Green
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
$managedIdentityClientId = az identity show -g $ResourceGroupName -n $ManagedIdentityName --query clientId -o tsv

$registryAuth = Resolve-RegistryAuth -GroupName $ResourceGroupName -RegistryName $resolvedAcrName -RegistryServer $acrLoginServer

$searchResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Search/searchServices/$SearchServiceName"
$storageResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Storage/storageAccounts/$StorageAccountName"
$acrResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.ContainerRegistry/registries/$resolvedAcrName"
$searchEndpoint = "https://$SearchServiceName.search.azure.us"
$blobContainerUrl = "https://$StorageAccountName.blob.core.usgovcloudapi.net/$StorageContainerName"

Write-Step "Applying RBAC"
New-RoleAssignment -PrincipalObjectId $managedIdentityPrincipalId -RoleName "Search Index Data Reader" -Scope $searchResourceId
New-RoleAssignment -PrincipalObjectId $managedIdentityPrincipalId -RoleName "Search Service Contributor" -Scope $searchResourceId
New-RoleAssignment -PrincipalObjectId $managedIdentityPrincipalId -RoleName "Storage Blob Data Contributor" -Scope $storageResourceId
New-RoleAssignment -PrincipalObjectId $managedIdentityPrincipalId -RoleName "AcrPull" -Scope $acrResourceId

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
    -ManagedIdentityClientId $managedIdentityClientId `
    -RegistryServer $acrLoginServer `
    -UseRegistryAdminCredentials $registryAuth.UseAdminCredentials `
    -RegistryUsername $registryAuth.Username `
    -RegistryPassword $registryAuth.Password `
    -SearchEndpoint $searchEndpoint `
    -BlobContainerUrl $blobContainerUrl `
    -BlobPrefixValue $BlobPrefix

$commandArgs = "python scripts/backup_ai_search_indexes.py --endpoint $searchEndpoint --write-direct-to-blob --blob-container-url $blobContainerUrl --blob-prefix $BlobPrefix"
Ensure-ContainerAppsJobShellCommandArray `
    -GroupName $ResourceGroupName `
    -JobName $ContainerAppsJobName `
    -CommandArgs $commandArgs

if (-not $SkipSmokeTest.IsPresent) {
    Invoke-PostSetupSmokeTest `
        -GroupName $ResourceGroupName `
        -JobName $ContainerAppsJobName `
        -StorageAccount $StorageAccountName `
        -StorageContainer $StorageContainerName `
        -BlobPrefixValue $BlobPrefix
}
else {
    Write-Host "Skipping post-setup smoke test because -SkipSmokeTest was provided."
}

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
