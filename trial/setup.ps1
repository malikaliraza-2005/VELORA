# Prepare the OpenWA trial on Windows (P0-E1): writes .env with fresh random secrets and
# creates the evidence folder. Run once, in this folder:  powershell -ExecutionPolicy Bypass -File setup.ps1
# Refuses to overwrite an existing .env: OpenWA's admin key and pepper are fixed at first boot.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (Test-Path ".env") {
    Write-Host ".env already exists; keeping it. Delete it only together with the volumes (docker compose down -v)."
    exit 1
}

function New-Secret([int]$bytes) {
    $buffer = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ([System.BitConverter]::ToString($buffer) -replace "-", "").ToLower()
}

$lines = @(
    "# Written by setup.ps1. Secret: never share, screenshot or commit this file.",
    "ENGINE_TYPE=baileys",
    "OPENWA_DB_PASSWORD=$(New-Secret 24)",
    "API_MASTER_KEY=$(New-Secret 32)",
    "API_KEY_PEPPER=$(New-Secret 32)",
    "WEBHOOK_SECRET=$(New-Secret 32)",
    "TRIAL_UID=0",
    "TRIAL_GID=0"
)
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))

New-Item -ItemType Directory -Force -Path "evidence" | Out-Null
if (-not (Test-Path "evidence\evidence-log.csv")) {
    Copy-Item "templates\evidence-log.csv" "evidence\evidence-log.csv"
}
Write-Host "Ready. Next: docker compose up -d   (then see README.md, step 3)"
