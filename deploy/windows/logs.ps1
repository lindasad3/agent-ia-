# Affiche le journal de l'agent (UTF-8) et le suit en direct. Ctrl+C pour quitter.
#   powershell -ExecutionPolicy Bypass -File C:\AgentIAForex\deploy\windows\logs.ps1
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Write-Host "== logs\console.log (démarrages / plantages) ==" -ForegroundColor Cyan
if (Test-Path "$Root\logs\console.log") { Get-Content "$Root\logs\console.log" -Encoding UTF8 -Tail 8 }
Write-Host "== logs\agent.log (suivi en direct) ==" -ForegroundColor Cyan
Get-Content "$Root\logs\agent.log" -Encoding UTF8 -Tail 40 -Wait
