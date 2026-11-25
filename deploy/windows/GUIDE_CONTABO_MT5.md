# Mise en route MT5 sur un VPS Windows Contabo

Durée : 20 à 30 minutes. Tout se fait dans le Bureau à distance du VPS.

## 1. Se connecter au VPS

- Windows : « Connexion Bureau à distance » (`mstsc`) ; Mac : « Windows App » (Microsoft Remote Desktop).
- Ordinateur : **l'adresse IP du VPS** (e-mail Contabo ou panneau `my.contabo.com`), utilisateur `Administrator`,
  mot de passe défini à la commande (réinitialisable dans le panneau Contabo).

## 2. Lancer l'installation automatique

Menu Démarrer → clic droit sur **Windows PowerShell** → **Exécuter en tant qu'administrateur**, puis coller :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"
Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile "$env:TEMP\git.exe" -UseBasicParsing
Start-Process "$env:TEMP\git.exe" -ArgumentList "/VERYSILENT /NORESTART /SP-" -Wait
$env:Path += ";C:\Program Files\Git\cmd"
git clone -b claude/kind-lamport-cfi5vi https://github.com/lindasad3/agent-ia-.git C:\AgentIAForex
C:\AgentIAForex\deploy\windows\bootstrap.ps1
```

Si le dépôt est privé, une fenêtre « Connect to GitHub » s'ouvre : choisissez **Sign in with your browser** et
connectez-vous à votre compte GitHub.

Le script installe Python 3.12 et MetaTrader 5, les dépendances, puis ouvre MetaTrader 5.

## 3. Compte démo dans MetaTrader 5 (quand le script le demande)

- **Vous n'avez pas de compte** : `Fichier` → `Ouvrir un compte` → tapez *MetaQuotes* → **MetaQuotes Ltd** →
  « Ouvrir un compte démo » → remplissez → **notez le login, le mot de passe et le serveur** (ex. `MetaQuotes-Demo`).
  Vous pouvez aussi ouvrir un compte démo chez le broker de votre choix (même procédure, cherchez son nom).
- **Vous avez un compte démo** : `Fichier` → `Se connecter à un compte de trading`.
- Cliquez sur le bouton **Algo Trading** de la barre d'outils (il doit devenir vert).
- Optionnel mais recommandé : ouvrez un graphique **H1** de chaque paire (EURUSD, GBPUSD, USDJPY, AUDUSD,
  USDCAD, USDCHF, EURJPY, GBPJPY, XAUUSD) pour que MT5 télécharge l'historique.

Revenez dans PowerShell et appuyez sur Entrée.

## 4. Assistant de configuration

Il demande : login, mot de passe (masqué), serveur → se connecte, vérifie que le compte est **démo**, que
l'Algo Trading est actif, détecte les noms de symboles du broker (`EURUSDm`, `GOLD`…) et le décalage horaire,
puis enregistre tout dans `C:\AgentIAForex\.env`. Il génère aussi le mot de passe de l'application (affiché).

Il propose d'autoriser la version de modèle **non validée** sur ce compte démo : répondez **oui** pour la
démonstration (refusé automatiquement sur un compte réel).

## 5. Diagnostic + ordre de test

`mt5_check.py --test-order` affiche chaque contrôle (✔/✖) et passe un ordre réel de 0,01 lot sur EURUSD, le
vérifie dans MT5, le referme et lit son historique. Vous verrez le trade dans l'onglet *Historique* de MT5.

## 6. Démarrage permanent

- Une tâche planifiée « AgentIAForex » lance l'agent à chaque ouverture de session et le relance s'il s'arrête.
- **Autologon** (proposé) : ouvre la session automatiquement après un redémarrage du VPS (mises à jour Windows).
  Saisissez le mot de passe Administrator dans la fenêtre, cliquez *Enable*.
- Pour partir : **fermez la fenêtre Bureau à distance** (croix). Ne cliquez jamais « Se déconnecter / Sign out » :
  cela fermerait MT5 et l'agent.

Application : `http://127.0.0.1:8000` dans le navigateur du VPS (accès depuis le téléphone : étape HTTPS plus tard).

## Problèmes fréquents

| Message | Solution |
|---|---|
| `initialize() a échoué` | MT5 installé ailleurs : corrigez `FXA_MT5_PATH` ; ouvrez MT5 une fois à la main |
| `account_info() indisponible` | Login / mot de passe / serveur inexacts (le serveur doit être écrit exactement comme dans MT5) |
| `Compte RÉEL détecté` | Normal : l'agent refuse un compte réel. Utilisez un compte démo |
| `AutoTrading désactivé dans le terminal` (10027) | Cliquez sur **Algo Trading** dans MT5 |
| `Symbole introuvable` | Le broker nomme autrement le symbole : `FXA_MT5_SYMBOL_MAP={"XAUUSD":"GOLD"}` |
| `Marché fermé` (10018) | Le Forex est fermé du vendredi 22h au dimanche 22h (UTC) : refaites le test en semaine |
| Décalage horaire « non détecté » | Marché fermé au moment du test : relancez en semaine ou fixez `FXA_MT5_SERVER_UTC_OFFSET=2` (heure d'hiver) / `3` (été) pour la plupart des brokers |
| Journaux | `C:\AgentIAForex\logs\agent-AAAAMMJJ.log` |

Relancer seulement une étape : `cd C:\AgentIAForex` puis `.venv\Scripts\python.exe scripts\setup.py mt5`
ou `.venv\Scripts\python.exe scripts\mt5_check.py --test-order`.
