# Démonstration en conditions réelles (compte démo MT5) — checklist

Pré-requis : agent démarré, message Telegram « Agent démarré (broker MT5 connecté ✅…) », mode **manuel**.
Les signaux n'arrivent qu'aux clôtures de bougies H1, marché ouvert (dimanche 23h → vendredi 23h, heure de Paris),
et seulement quand une stratégie trouve une configuration validée par le Risk Engine (souvent quelques heures).

| # | Étape du cahier des charges | Où le constater |
|---|---|---|
| 1-4 | Analyse temps réel, opportunité, conditions de marché, stratégie | App → *Market Analysis* (régime, SMC, order flow, raisonnement) |
| 5-7 | Explication, entrée / SL / TP / volume, contrôle du risque | Message Telegram du signal (tous les paramètres) + App → *Live Trading* |
| 8 | Notification Telegram complète | Message reçu avec boutons ✅ / ❌ |
| 9 | Mode manuel : validation humaine | Cliquer **✅ Valider** (Telegram) ou « Valider et exécuter » (App) |
| 10-11 | Ordre placé sur MT5, exécution vérifiée | Message « ✅ Ordre exécuté » avec ticket MT5 et prix réel |
| 12 | Position visible dans MT5 et dans l'application | Onglet *Trade* de MT5 + App → *Live Trading* |
| 13 | Confirmation Telegram avec informations réelles | Même message (ticket, prix, slippage, SL/TP confirmés) |
| 14-15 | Surveillance, modifications, clôture notifiées | Messages « Break-even activé » / « Trailing stop » / « Position clôturée » |
| 16 | Résultat enregistré | App → *Trade History* |
| 17 | Performances actualisées | App → *Performance Analytics* et *Dashboard* |

Après la clôture, produire la preuve (contrôle croisé avec l'historique du terminal) :

```powershell
cd C:\AgentIAForex
.venv\Scripts\python.exe scripts\demo_report.py --verify-mt5
```

Le rapport `reports\demo\DEMO_<ticket>.md` reprend les 17 étapes avec les données réellement enregistrées.
Pour démontrer le **mode automatique** : App → Dashboard → Mode « Automatique » + Exécution auto ON
(ou `/mode auto` puis `/auto on` sur Telegram) ; le prochain signal est exécuté sans validation.
