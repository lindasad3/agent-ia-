# Installation / mise à jour de l'Agent IA Forex sur Windows Server (PowerShell administrateur).
#   Set-ExecutionPolicy -Scope Process Bypass -Force ; .\deploy\windows\install.ps1
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$env:PYTHONUTF8 = "1"
Write-Host "== Agent IA Forex : installation dans $Root" -ForegroundColor Cyan

# 1. Python 3.12 (sans winget, absent de Windows Server 2019/2022)
$Py = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "Téléchargement de Python 3.12..."
    Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile "$env:TEMP\python312.exe" -UseBasicParsing
    Start-Process "$env:TEMP\python312.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
}

# 2. MetaTrader 5
$Mt5 = "C:\Program Files\MetaTrader 5\terminal64.exe"
if (-not (Test-Path $Mt5)) {
    Write-Host "Téléchargement de MetaTrader 5..."
    Invoke-WebRequest "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile "$env:TEMP\mt5setup.exe" -UseBasicParsing
    Start-Process "$env:TEMP\mt5setup.exe" -ArgumentList "/auto" -Wait
}

# 3. Environnement virtuel + dépendances (MetaTrader5 = paquet officiel, Windows uniquement)
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $Py -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\python.exe -m pip install -r requirements.txt MetaTrader5 --quiet
New-Item -ItemType Directory -Force -Path data, logs, backups | Out-Null

# 4. Ouverture du terminal MT5 : connexion au compte démo + bouton « Algo Trading »
Write-Host ""
Write-Host "== Étape manuelle dans MetaTrader 5 ==" -ForegroundColor Yellow
Write-Host " - Pas encore de compte démo : Fichier > Ouvrir un compte > MetaQuotes Ltd > compte démo (notez login, mot de passe, serveur)"
Write-Host " - Sinon : Fichier > Se connecter à un compte de trading"
Write-Host " - Activez le bouton « Algo Trading » (barre d'outils, il doit être vert)"
Start-Process $Mt5
Read-Host "Appuyez sur Entrée quand MT5 est connecté au compte démo"

# 5. Configuration interactive (.env) : secrets, MT5, Telegram
.\.venv\Scripts\python.exe scripts\setup.py secrets
.\.venv\Scripts\python.exe scripts\setup.py mt5
$tg = Read-Host "Configurer Telegram maintenant ? (o/N)"
if ($tg -match "^[oOyY]") { .\.venv\Scripts\python.exe scripts\setup.py telegram }

# 6. Diagnostic complet + ordre de test (compte démo)
.\.venv\Scripts\python.exe scripts\mt5_check.py --test-order

# 7. Démarrage automatique à l'ouverture de session (MT5 a besoin d'une session Windows interactive)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$Root\deploy\windows\run_forever.ps1`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "AgentIAForex" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Tâche planifiée 'AgentIAForex' enregistrée (lancement à chaque ouverture de session)." -ForegroundColor Green

# 8. Ouverture de session automatique après un redémarrage du VPS (mot de passe chiffré par Windows, outil Microsoft)
$al = Read-Host "Installer l'ouverture de session automatique (Sysinternals Autologon) pour survivre aux redémarrages ? (O/n)"
if ($al -notmatch "^[nN]") {
    Invoke-WebRequest "https://download.sysinternals.com/files/AutoLogon.zip" -OutFile "$env:TEMP\AutoLogon.zip" -UseBasicParsing
    Expand-Archive "$env:TEMP\AutoLogon.zip" -DestinationPath "$env:TEMP\AutoLogon" -Force
    Start-Process "$env:TEMP\AutoLogon\Autologon64.exe" -ArgumentList "-accepteula" -Wait
}

Start-ScheduledTask -TaskName "AgentIAForex"
Write-Host ""
Write-Host "== Terminé ==" -ForegroundColor Green
Write-Host "Application : http://127.0.0.1:8000 (navigateur du VPS). Journaux : $Root\logs"
Write-Host "IMPORTANT : pour quitter le VPS, fermez la fenêtre Bureau à distance (ne cliquez pas « Se déconnecter / Sign out »)."
