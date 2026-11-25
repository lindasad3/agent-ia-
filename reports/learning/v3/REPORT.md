# Rapport d'apprentissage offline — v3

Timeframe d'entrée **H1** · 9 instruments · capital 10,000 USD · IS 2023-01-18 → 2024-10-27 · OOS 2024-10-27 → 2025-07-31

**Statut : REJECTED** — profit factor OOS < 1.1 (1.0263)

## Pré-validation

- Données : 9/9 instruments valides (≥ 90 jours, sans NaN, OHLC cohérents)
- Agrégation MTF : OK
- Absence de look-ahead : OK (3 sondages)
- Stratégies < 30 trades IS : orderflow_absorption

## Agent complet — hors échantillon (jamais optimisé)

| Trades | Profit net | Rendement | Win rate | Profit factor | Espérance (R) | Drawdown max |
|---|---|---|---|---|---|---|
| 184 | 225.12 $ | 2.25 % | 45.6 % | 1.03 | 0.02 | 11.61 % |

### Par stratégie (OOS)

| Stratégie | Trades | Net $ | Win rate | PF | Esp. R |
|---|---|---|---|---|---|
| breakout | 34 | -84.83 | 50.0 % | 0.94 | 0.00 |
| london_breakout | 48 | 686.16 | 52.1 % | 1.32 | 0.15 |
| momentum | 8 | -100.16 | 37.5 % | 0.80 | -0.12 |
| smc_sweep | 33 | -482.29 | 39.4 % | 0.74 | -0.13 |
| trend_pullback | 16 | -752.93 | 31.2 % | 0.16 | -0.51 |
| trend_trail | 45 | 959.17 | 46.7 % | 1.55 | 0.23 |

### Par paire (OOS)

| Paire | Trades | Net $ | Win rate | PF |
|---|---|---|---|---|
| AUDUSD | 11 | -293.82 | 36.4 % | 0.59 |
| EURJPY | 57 | -178.62 | 40.4 % | 0.94 |
| EURUSD | 22 | 232.10 | 54.5 % | 1.25 |
| GBPJPY | 23 | -33.24 | 47.8 % | 0.97 |
| GBPUSD | 21 | 18.74 | 52.4 % | 1.02 |
| USDCAD | 12 | -440.71 | 33.3 % | 0.42 |
| USDJPY | 8 | 172.77 | 50.0 % | 1.59 |
| XAUUSD | 30 | 747.91 | 50.0 % | 1.61 |

## Walk-forward (fenêtres de test de 3 mois)

Global : 576 trades, net -681.48 $, PF 0.98, win rate 44.4 %, espérance -0.01 R

| Fenêtre | Trades train | Trades test | Net $ | PF | Win rate | DD max |
|---|---|---|---|---|---|---|
| 2024-01-18 → 2024-04-18 | 3123 | 25 | -1,055.28 | 0.28 | 28.0 % | 10.55 % |
| 2024-04-18 → 2024-07-18 | 3279 | 83 | 73.28 | 1.02 | 37.4 % | 10.55 % |
| 2024-07-18 → 2024-10-18 | 3441 | 88 | -734.13 | 0.82 | 46.6 % | 10.65 % |
| 2024-10-18 → 2025-01-18 | 3430 | 164 | 553.65 | 1.07 | 48.2 % | 9.35 % |
| 2025-01-18 → 2025-04-18 | 3502 | 112 | 94.68 | 1.02 | 46.4 % | 10.38 % |
| 2025-04-18 → 2025-07-18 | 3464 | 82 | -479.89 | 0.89 | 39.0 % | 10.11 % |
| 2025-07-18 → 2025-10-18 | 3374 | 22 | 866.22 | 2.41 | 63.6 % | 2.96 % |

## Stratégies isolées (avant arbitrage du cerveau)

| Stratégie | IS trades | IS esp. R | OOS trades | OOS esp. R |
|---|---|---|---|---|
| breakout | 1100 | -0.051 | 411 | +0.034 |
| london_breakout | 2294 | -0.038 | 987 | -0.038 |
| mean_reversion | 91 | -0.058 | 34 | -0.184 |
| momentum | 202 | +0.034 | 92 | +0.047 |
| orderflow_absorption | 3 | +1.062 | 0 | +0.000 |
| smc_sweep | 1173 | -0.053 | 523 | -0.088 |
| trend_pullback | 252 | -0.024 | 206 | -0.099 |
| trend_trail | 740 | +0.030 | 508 | +0.018 |

## Optimisation Optuna imbriquée (IS-A recherche / IS-B validation)

| Stratégie | Réf. IS-A | Meilleur IS-A | Réf. IS-B | Meilleur IS-B | Adopté | Essais (élagués) |
|---|---|---|---|---|---|---|
| trend_pullback | 0.042 | 0.398 | -0.709 | -0.93 | non | 15 (0) |
| breakout | -2.314 | -1.619 | 0.589 | 0.269 | non | 15 (0) |
| mean_reversion | -1.179 | -0.506 | -2.601 | -0.235 | oui | 15 (7) |
| smc_sweep | -2.922 | -1.099 | 1.142 | -0.866 | non | 15 (1) |
| orderflow_absorption | -9.933 | -9.933 | -9.967 | -9.967 | non | 15 (0) |
| momentum | -0.875 | -0.39 | 2.006 | 1.893 | non | 15 (0) |
| trend_trail | -0.183 | 0.275 | 1.407 | -1.052 | non | 15 (4) |
| london_breakout | -4.465 | -2.658 | -0.091 | 0.805 | oui | 15 (0) |

## Méta-modèle ML

- Statut : **rejeté (pas d'avantage hors échantillon)** (n=5855)
- AUC OOS : 0.5129 · Brier 0.2576 vs base 0.2494
- Filtre optimal : {'threshold': 0.64, 'n': 156, 'exp_r': 0.3188} · espérance sans filtre -0.0006

## Méthodologie et limites

- Exécution à l'ouverture suivant le signal, chemin du prix rejoué sur M1 réelles, SL prioritaire si SL et TP dans la même minute, spread typique + commission 7 $/lot.
- Les performances passées ne garantissent pas les performances futures ; le slippage réel et les spreads variables (news) peuvent dégrader les résultats.
- Le modèle de production est réappris sur tout l'historique après validation ; le chiffre de référence reste le test OOS ci-dessus.