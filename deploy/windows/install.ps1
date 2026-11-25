# Installation de l'Agent IA Forex sur un VPS Windows (Windows Server 2019/2022, PowerShell administrateur).
# Prérequis : terminal MetaTrader 5 installé et connecté au compte (démo), Quantower installé (optionnel).
#   Set-ExecutionPolicy -Scope Process Bypass ; .\deploy\windows\install.ps1
$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $Root
Write-Host "== Agent IA Forex : installation dans $Root" -ForegroundColor Cyan

# 1. Python 3.12
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Installation de Python 3.12 (winget)..."
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# 2. Environnement virtuel + dépendances (MetaTrader5 = paquet officiel, Windows uniquement)
if (-not (Test-Path ".venv")) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt MetaTrader5

# 3. Configuration
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Fichier .env créé : renseignez vos identifiants MT5, Telegram et mots de passe AVANT de démarrer." -ForegroundColor Yellow
    notepad .env
}
New-Item -ItemType Directory -Force -Path data, logs, backups | Out-Null

# 4. Vérification MT5
Write-Host "== Test de connexion MT5"
.\.venv\Scripts\python.exe scripts\mt5_check.py

# 5. Démarrage automatique (tâche planifiée au démarrage, redémarrage en boucle si crash)
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Root\deploy\windows\run_forever.ps1`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName "AgentIAForex" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Tâche planifiée 'AgentIAForex' enregistrée (démarrage automatique)."

# 6. HTTPS (indispensable pour installer la PWA sur téléphone) : Caddy + certificat Let's Encrypt
Write-Host "Pour l'accès HTTPS : installez Caddy (winget install CaddyServer.Caddy), éditez deploy\windows\Caddyfile"
Write-Host "avec votre nom de domaine puis lancez : caddy run --config deploy\windows\Caddyfile"
Write-Host "Ouvrez les ports 80/443 : New-NetFirewallRule -DisplayName 'HTTPS' -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow"
Start-ScheduledTask -TaskName "AgentIAForex"
Write-Host "== Terminé. Application : http://127.0.0.1:8000 (puis https://votre-domaine)" -ForegroundColor Green
