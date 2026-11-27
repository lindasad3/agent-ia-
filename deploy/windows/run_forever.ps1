# Supervisor: restarts the agent if it stops. The agent writes its own UTF-8 log (logs\agent.log).
# Start/stop events and crash tracebacks go to logs\console.log. ASCII-only on purpose (Windows PowerShell 5.1).
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$Logs = Join-Path $Root "logs"
$Console = Join-Path $Logs "console.log"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
while ($true) {
    Add-Content -Path $Console -Value "$(Get-Date -Format o) agent start" -Encoding UTF8
    $p = Start-Process -FilePath (Join-Path $Root ".venv\Scripts\python.exe") -ArgumentList "scripts\run_agent.py" `
        -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "stdout.log") -RedirectStandardError (Join-Path $Logs "stderr.log")
    $code = $p.ExitCode
    Add-Content -Path $Console -Value "$(Get-Date -Format o) agent stopped (exit code $code), restart in 15 s" -Encoding UTF8
    if ($code -ne 0 -and (Test-Path (Join-Path $Logs "stderr.log"))) {
        Get-Content (Join-Path $Logs "stderr.log") -Encoding UTF8 -Tail 40 | Add-Content -Path $Console -Encoding UTF8
    }
    Start-Sleep -Seconds 15
}
