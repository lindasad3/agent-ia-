# Rapport de sélection technologique — AI Research Lab

**Périmètre :** 504 dépôts GitHub trouvés sur 8 thèmes (36 requêtes), **109 analysés** en détail (arborescence,
README, dépendances), classés et comparés (`REPORT.md`, `catalog.csv`, `KNOWLEDGE_BASE.md` — une fiche par dépôt).
Revue manuelle des dépôts clés ci-dessous, puis **tests réels** des composants retenus (`component_tests.json`).
Aucun script tiers n'a été exécuté ; seuls des paquets PyPI publiés ont été installés pour les tests.

## Constats de l'écosystème

| Constat | Mesure | Conséquence pour notre agent |
|---|---|---|
| La gestion du risque est le maillon faible | 55 % des dépôts n'en documentent aucune | Risk Engine **indépendant** du cerveau, 17 contrôles, droit de veto |
| Peu de projets sont testés | 38 % sans aucun test | Suite pytest (35 tests) dont broker MT5 simulé, API, flux complet |
| Les agents LLM dominent les tendances 2025-2026 (Vibe-Trading, QuantDinger, LangAlpha) | 20 dépôts « AI trading agents » | Pas de LLM dans la boucle d'exécution : latence, non-déterminisme, coût. Le raisonnement est explicable et reproductible |
| Séparation offline / online (asavinov/intelligent-trading-bot) | — | Apprentissage strictement offline, registre de versions, activation sous condition |
| RL : beaucoup d'environnements, peu de preuves hors échantillon (TensorTrade, TradeMaster, FinRL) | — | RL écarté pour la V1 : pas de gain démontré face au coût de validation |
| Order flow : bons outils de visualisation (OrderflowChart, py-market-profile) mais pas de signal validé | — | Order flow = filtre de confirmation (delta, DOM, absorption) via Quantower |

## Sélection par besoin

| Besoin | Retenu | Décision | Justification |
|---|---|---|---|
| Indicateurs techniques | **Implémentation maison** validée contre `ta` (MIT) | Garder la nôtre | Corrélation 1.000 sur RSI, ATR, ADX, Bollinger, MACD (écart max ≈ 1e-8) ; zéro dépendance |
| Smart Money Concepts | `joshyattridge/smart-money-concepts` (2.3k ★, MIT) comme **référence de test** | Module maison | Concordance FVG 97 % (Jaccard), 3x plus rapide, horodatage à la confirmation (pas de look-ahead) |
| Exécution MT5 | `Ichinga-Samuel/aiomql` (MIT) — **idées** | Connecteur maison | Reconnexion automatique, gestion de session : repris dans `MT5Broker` (vérification réelle de chaque ordre, requotes, rejets, compte réel bloqué par défaut) |
| Optimisation | Optuna (TPE + MedianPruner) | Adopté | Recommandé par le skill strategy-workflow ; déterministe avec graine (testé) ; optimisation **imbriquée** IS-A/IS-B |
| ML / méta-labeling | scikit-learn HistGradientBoosting + méthodologie López de Prado (`stefan-jansen/machine-learning-for-trading`, 22k ★) | Adopté | Purged walk-forward CV + embargo ; activation seulement si AUC/Brier OOS battent la base (rejeté honnêtement : AUC 0.51) |
| Backtest | Moteur événementiel maison sur M1 réelles | Adopté | `kernc/backtesting.py` est AGPL (réutilisation contrainte) ; notre moteur partage le Risk Engine du live |
| Mémoire des erreurs | `mnemox-ai/tradememory-protocol` (MIT) — **idée** | Intégrée | « Retrouver où l'historique perd de l'argent » → `journal_review.py` (pertes par stratégie, régime, heure, slippage) |
| Gestion de portefeuille | `skfolio` (BSD-3) | Plus tard (V2) | Utile pour l'allocation multi-paires une fois un avantage validé |
| Order flow | Quantower (plugin C# maison) | Adopté | Seule source de delta réel / DOM ; pour le FX, utiliser les futures CME (6E, 6B, 6J, GC…) |
| Interface | FastAPI + PWA vanilla | Adopté | 12 % des dépôts utilisent FastAPI ; pas de build front, installable sur téléphone |

## Résultats des tests de composants (données EURUSD H1 réelles, 17 009 bougies)

| Composant | Test | Résultat |
|---|---|---|
| `ta` 0.11 | Indicateurs maison vs référence | Corrélation 1.000 sur les 5 indicateurs |
| `smartmoneyconcepts` 0.0.27 | Détection FVG | Jaccard 0.967 (haussiers) / 0.973 (baissiers) ; nous : 0.065 s vs 0.217 s |
| `optuna` 5.1 | Déterminisme TPE | Deux exécutions identiques (x = 1.7519 pour une cible de 1.7) |

## Bibliothèque de composants réutilisables (dans ce dépôt)

`forex_agent/analysis` (indicateurs, régimes, price action, SMC, order flow, features MTF anti-fuite) ·
`forex_agent/risk/engine.py` · `forex_agent/execution/mt5_broker.py` · `forex_agent/learning` (backtest M1,
walk-forward, Optuna imbriqué, registre) · `forex_agent/brain/meta_model.py` · `quantower_bridge/`.

## Veille continue

Relancer `python research_lab/lab.py all` (mensuel) : les nouveaux dépôts sont classés selon la même grille ; tout
composant candidat est testé offline (`test_components.py`) avant intégration.
