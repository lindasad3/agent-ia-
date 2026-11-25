# Agent IA Forex — amorçage sur un VPS Windows Server neuf (Contabo, etc.).
# À coller dans PowerShell lancé « en tant qu'administrateur ». Installe Git + Python 3.12 + MetaTrader 5,
# clone le projet dans C:\AgentIAForex puis lance l'installation.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"   # accélère fortement Invoke-WebRequest
$Dest = "C:\AgentIAForex"
$Repo = "https://github.com/lindasad3/agent-ia-.git"
$Branch = "claude/kind-lamport-cfi5vi"
$Tmp = "$env:TEMP\fxa-setup"; New-Item -ItemType Directory -Force -Path $Tmp | Out-Null

function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Installation de Git..." -ForegroundColor Cyan
    Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile "$Tmp\git.exe" -UseBasicParsing
    Start-Process "$Tmp\git.exe" -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP-" -Wait
    Refresh-Path
}
if (-not (Test-Path "C:\Program Files\Python312\python.exe")) {
    Write-Host "Installation de Python 3.12..." -ForegroundColor Cyan
    Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile "$Tmp\python.exe" -UseBasicParsing
    Start-Process "$Tmp\python.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_launcher=1" -Wait
    Refresh-Path
}
if (-not (Test-Path "C:\Program Files\MetaTrader 5\terminal64.exe")) {
    Write-Host "Installation de MetaTrader 5..." -ForegroundColor Cyan
    Invoke-WebRequest "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" -OutFile "$Tmp\mt5setup.exe" -UseBasicParsing
    Start-Process "$Tmp\mt5setup.exe" -ArgumentList "/auto" -Wait
}
if (-not (Test-Path "$Dest\.git")) {
    Write-Host "Clonage du projet (une fenêtre de connexion GitHub peut s'ouvrir si le dépôt est privé)..." -ForegroundColor Cyan
    git clone -b $Branch $Repo $Dest
} else {
    git -C $Dest pull
}
Set-Location $Dest
& "$Dest\deploy\windows\install.ps1"
