# Accès depuis le téléphone : adresse HTTPS gratuite (DuckDNS) + certificat automatique (Caddy).
#   powershell -ExecutionPolicy Bypass -File C:\AgentIAForex\deploy\windows\enable_https.ps1
# Prérequis : compte gratuit sur https://www.duckdns.org (connexion Google/GitHub), un sous-domaine créé, le token.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$CaddyDir = Join-Path $Root "tools\caddy"

# 0. Sécurité : jamais d'exposition publique avec un mot de passe par défaut
$envFile = Get-Content (Join-Path $Root ".env") -Encoding UTF8 -ErrorAction SilentlyContinue
$pw = ($envFile | Where-Object { $_ -match "^FXA_API_PASSWORD=" }) -replace "^FXA_API_PASSWORD=", ""
if (-not $pw -or $pw -match "change-me" -or $pw.Trim("'", '"').Length -lt 10) {
    Write-Host "Mot de passe de l'application absent ou faible : lancez d'abord  .venv\Scripts\python.exe scripts\setup.py secrets" -ForegroundColor Red
    exit 1
}

# 1. DuckDNS : le sous-domaine pointe vers l'IP de ce VPS
$sub = (Read-Host "Sous-domaine DuckDNS (ex. monagentfx pour monagentfx.duckdns.org)").Trim().ToLower() -replace "\.duckdns\.org$", ""
$tok = (Read-Host "Token DuckDNS (en haut de la page duckdns.org)").Trim()
$r = Invoke-RestMethod "https://www.duckdns.org/update?domains=$sub&token=$tok&ip=" -UseBasicParsing
if ("$r".Trim() -ne "OK") { Write-Host "DuckDNS a refusé la mise à jour (sous-domaine ou token incorrect)." -ForegroundColor Red; exit 1 }
$Domain = "$sub.duckdns.org"
Write-Host "OK : $Domain pointe vers ce VPS." -ForegroundColor Green

# 2. Caddy (serveur HTTPS, certificat Let's Encrypt automatique)
New-Item -ItemType Directory -Force -Path $CaddyDir | Out-Null
if (-not (Test-Path "$CaddyDir\caddy.exe")) {
    Invoke-WebRequest "https://github.com/caddyserver/caddy/releases/download/v2.8.4/caddy_2.8.4_windows_amd64.zip" -OutFile "$env:TEMP\caddy.zip" -UseBasicParsing
    Expand-Archive "$env:TEMP\caddy.zip" -DestinationPath $CaddyDir -Force
}
$caddyfile = @"
$Domain {
	encode gzip
	reverse_proxy 127.0.0.1:8000
	header Strict-Transport-Security "max-age=31536000"
	log {
		output file "$($CaddyDir -replace '\\','/')/access.log"
	}
}
"@
Set-Content -Path "$CaddyDir\Caddyfile" -Value $caddyfile -Encoding ASCII

# 3. Pare-feu Windows : ports 80 (validation du certificat) et 443 (HTTPS)
if (-not (Get-NetFirewallRule -DisplayName "AgentIAForex HTTPS" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "AgentIAForex HTTPS" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow | Out-Null
}

# 4. Démarrage automatique de Caddy (compte SYSTEM, au démarrage du VPS, relance en cas d'arrêt)
$action = New-ScheduledTaskAction -Execute "$CaddyDir\caddy.exe" -Argument "run --config `"$CaddyDir\Caddyfile`" --adapter caddyfile" -WorkingDirectory $CaddyDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "AgentIAForexHTTPS" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Stop-ScheduledTask -TaskName "AgentIAForexHTTPS" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "AgentIAForexHTTPS"

# 5. Vérification (obtention du certificat : jusqu'à 1-2 minutes)
Write-Host "Obtention du certificat HTTPS..."
$ok = $false
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 10
    try {
        $h = Invoke-RestMethod "https://$Domain/api/health" -UseBasicParsing -TimeoutSec 10
        if ($h.ok) { $ok = $true; break }
    } catch { }
}
if ($ok) {
    Write-Host ""
    Write-Host "== Application accessible : https://$Domain ==" -ForegroundColor Green
    Write-Host "Sur le téléphone : ouvrez cette adresse, connectez-vous, puis"
    Write-Host "  iPhone (Safari) : bouton Partager > « Sur l'écran d'accueil »"
    Write-Host "  Android (Chrome) : menu ⋮ > « Installer l'application »"
} else {
    Write-Host "Pas encore de réponse HTTPS. Vérifiez : agent démarré (http://127.0.0.1:8000), ports 80/443 ouverts" -ForegroundColor Yellow
    Write-Host "(pare-feu Contabo s'il est activé dans le panneau), puis : Get-Content $CaddyDir\access.log" -ForegroundColor Yellow
}
