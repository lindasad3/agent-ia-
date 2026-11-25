# Mise en route Telegram

Durée : 5 minutes. À faire sur le VPS (Telegram n'est pas joignable depuis l'environnement de développement).

## 1. Créer le bot (sur votre téléphone)

1. Dans Telegram, ouvrez **@BotFather** → envoyez `/newbot`.
2. Donnez un nom (ex. *Mon Agent FX*) puis un identifiant se terminant par `bot` (ex. `mon_agent_fx_bot`).
3. BotFather répond avec un **token** du type `7123456789:AAH...` : gardez-le secret.

## 2. Configurer l'agent (sur le VPS)

```powershell
cd C:\AgentIAForex
Stop-ScheduledTask -TaskName AgentIAForex        # l'agent et l'assistant ne doivent pas lire Telegram en même temps
.venv\Scripts\python.exe scripts\setup.py telegram
Start-ScheduledTask -TaskName AgentIAForex
```

L'assistant :
- vérifie le token auprès de Telegram et affiche le nom du bot ;
- vous demande d'envoyer `/start` au bot depuis votre téléphone, **détecte automatiquement votre chat_id** ;
- vous demande quels comptes autoriser (seuls eux pourront voir les signaux et piloter l'agent) ;
- envoie un message de test, puis enregistre tout dans `.env` ;
- propose un **PIN** pour les commandes sensibles (ex. `/stop 4821`).

## 3. Vérifier

Dans le bot : `/status` → état de l'agent, compte MT5, mode. Dans l'application : *Settings → Telegram →
Envoyer un message de test*.

| Commande | Effet |
|---|---|
| `/status` | état de l'agent, connexions, solde, P&L du jour |
| `/positions` | positions ouvertes |
| `/performance` | statistiques des trades enregistrés |
| `/signals` | derniers signaux |
| `/pause` · `/resume` | suspendre / reprendre les nouvelles entrées (PIN si configuré) |
| `/mode` · `/mode auto` · `/mode manual` | consulter / changer le mode |
| `/auto on` · `/auto off` | exécution automatique ON/OFF |
| `/stop` | arrêt d'urgence : exécution auto OFF, entrées bloquées, positions de l'agent fermées |
| `/close 123456` | fermer une position |

En mode manuel, chaque signal arrive avec les boutons **✅ Valider** / **❌ Refuser**.

## Problèmes fréquents

| Symptôme | Solution |
|---|---|
| `Token refusé` | Token mal copié ou révoqué : `/token` dans @BotFather |
| `Aucun chat détecté` | Envoyez bien `/start` au bot (pas à BotFather), puis relancez l'assistant |
| `Conflict: terminated by other getUpdates request` | L'agent tourne pendant l'assistant : `Stop-ScheduledTask -TaskName AgentIAForex` |
| Pas de notification | Vérifiez `FXA_TELEGRAM_CHAT_IDS` dans `.env` et la pastille Telegram dans l'application |
