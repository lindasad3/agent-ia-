# Met à jour l'agent depuis GitHub puis le redémarre.
#   powershell -ExecutionPolicy Bypass -File C:\AgentIAForex\deploy\windows\update.ps1
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
& "$PSScriptRoot\stop.ps1"
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
Start-ScheduledTask -TaskName "AgentIAForex"
Start-Sleep -Seconds 20
Write-Host "== Dernières lignes du journal ==" -ForegroundColor Cyan
Get-Content "$Root\logs\agent.log" -Encoding UTF8 -Tail 20
