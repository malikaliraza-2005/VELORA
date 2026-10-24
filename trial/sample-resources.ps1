# Record the trial containers' CPU and memory every minute to evidence\resources.csv (T5.1).
# Leave it running in its own window during a session; stop it with Ctrl+C.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$out = Join-Path $PSScriptRoot "evidence\resources.csv"
if (-not (Test-Path $out)) {
    [System.IO.File]::WriteAllText($out, "utc,container,cpu_percent,memory_usage,memory_percent,pids`n")
}
Write-Host "Sampling every 60 s into $out (Ctrl+C to stop)"
while ($true) {
    $now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $rows = docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.PIDs}}" |
        Where-Object { $_ -like "velora-trial-*" }
    foreach ($row in $rows) {
        Add-Content -Path $out -Value "$now,$row" -Encoding utf8
    }
    Start-Sleep -Seconds 60
}
