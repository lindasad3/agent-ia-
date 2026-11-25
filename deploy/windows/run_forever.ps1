# Superviseur : relance l'agent s'il s'arrête (crash, mise à jour MT5...). Journal : logs\agent-YYYYMMDD.log
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
while ($true) {
    $log = "logs\agent-$(Get-Date -Format yyyyMMdd).log"
    "$(Get-Date -Format o) démarrage de l'agent" | Out-File -Append -Encoding utf8 $log
    & .\.venv\Scripts\python.exe scripts\run_agent.py *>&1 | Out-File -Append -Encoding utf8 $log
    "$(Get-Date -Format o) arrêt (code $LASTEXITCODE) — redémarrage dans 15 s" | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds 15
}
