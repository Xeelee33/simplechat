# run_daily_ai_search_backup_local.ps1
param(
    [Parameter(Mandatory = $true)]
    [string]$SearchEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$BlobContainerUrl,

    [Parameter(Mandatory = $false)]
    [string]$BlobPrefix = "simplechat/local",

    [Parameter(Mandatory = $false)]
    [string]$CloudName = "AzureUSGovernment",

    [Parameter(Mandatory = $false)]
    [string]$PythonCommand = "py",

    [Parameter(Mandatory = $false)]
    [string]$BackupScriptPath = "scripts/backup_ai_search_indexes.py",

    [Parameter(Mandatory = $false)]
    [switch]$EnsureLogin,

    [Parameter(Mandatory = $false)]
    [switch]$VerboseLogging
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Ensure-CommandExists {
    param(
        [string]$CommandName,
        [string]$Description
    )

    $resolvedCommand = Get-Command $CommandName -ErrorAction SilentlyContinue
    if (-not $resolvedCommand) {
        throw "$Description ($CommandName) is required but was not found in PATH."
    }
}

function Resolve-RepoRoot {
    $scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
    return (Resolve-Path (Join-Path $scriptDirectory "..")).Path
}

function Ensure-AzureLogin {
    param([string]$SelectedCloud)

    az cloud set --name $SelectedCloud | Out-Null

    $account = az account show --query "id" -o tsv 2>$null
    if ([string]::IsNullOrWhiteSpace($account)) {
        throw "No Azure login context found. Run 'az login' (and 'az account set --subscription <id>' if needed)."
    }
}

Write-Step "Validating prerequisites"
Ensure-CommandExists -CommandName "az" -Description "Azure CLI"
Ensure-CommandExists -CommandName $PythonCommand -Description "Python command"

$repoRoot = Resolve-RepoRoot
$resolvedBackupScriptPath = Join-Path $repoRoot $BackupScriptPath
if (-not (Test-Path $resolvedBackupScriptPath)) {
    throw "Backup script not found at path: $resolvedBackupScriptPath"
}

if ($EnsureLogin) {
    Write-Step "Ensuring Azure CLI cloud and login context"
    Ensure-AzureLogin -SelectedCloud $CloudName
}

Write-Step "Running Azure AI Search backup"
$arguments = @(
    $resolvedBackupScriptPath,
    "--endpoint", $SearchEndpoint,
    "--write-direct-to-blob",
    "--blob-container-url", $BlobContainerUrl,
    "--blob-prefix", $BlobPrefix
)

if ($VerboseLogging) {
    $arguments += "--verbose"
}

& $PythonCommand @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Backup script failed with exit code $LASTEXITCODE"
}

Write-Step "Backup completed successfully"
Write-Host "Search endpoint: $SearchEndpoint"
Write-Host "Blob container URL: $BlobContainerUrl"
Write-Host "Blob prefix: $BlobPrefix"