# Superviseur : relance l'agent s'il s'arrête (crash, mise à jour MT5...). Journal dans logs\agent-YYYYMMDD.log
$Root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $Root
while ($true) {
    $log = "logs\agent-$(Get-Date -Format yyyyMMdd).log"
    "$(Get-Date -Format o) démarrage de l'agent" | Out-File -Append $log
    & .\.venv\Scripts\python.exe scripts\run_agent.py *>> $log
    "$(Get-Date -Format o) arrêt (code $LASTEXITCODE) — redémarrage dans 15 s" | Out-File -Append $log
    Start-Sleep -Seconds 15
}
