# Agent IA Forex — guide pour Claude Code

## Commandes
- Tests : `.venv/bin/python -m pytest -q`
- Données réelles : `.venv/bin/python scripts/download_data.py` (data/fx/*.parquet, non versionné)
- Apprentissage offline : `.venv/bin/python scripts/train.py [--trials 15] [--version vX]`
- Agent + API + PWA : `.venv/bin/python scripts/run_agent.py` (config via `.env`, voir `.env.example`)
- Simulation : `FXA_BROKER=paper FXA_ALLOW_UNVALIDATED_MODEL=true ...`
- Research Lab : `.venv/bin/python research_lab/lab.py all` puis `research_lab/test_components.py`
- Preuve de démo : `.venv/bin/python scripts/demo_report.py [--verify-mt5]`

## Règles du projet (ne pas déroger)
- Aucune information future dans les features : pivots confirmés, HTF alignés sur leur clôture
  (`tests/test_analysis.py::test_no_lookahead` doit rester vert).
- Le Risk Engine (`forex_agent/risk/engine.py`) reste indépendant du cerveau et peut refuser tout ordre.
- Jamais d'optimisation ni de choix de seuils sur la période OOS ; une version n'est activée que via
  `learning/registry.gate` (PF OOS ≥ 1.1, espérance > 0, ≥ 30 trades, DD ≤ 15 %).
- Un modèle non validé ne doit jamais trader un compte réel (`main.py` le refuse).
- Toute exécution est vérifiée auprès du broker avant d'être enregistrée ; la simulation est toujours étiquetée.
- Les statistiques affichées proviennent uniquement des trades enregistrés.

## Skills du projet
`.claude/skills/` : strategy-workflow (validation, walk-forward, Optuna), ml-pipeline (anti-leakage,
méta-labeling), research-documentation (documentation, recherche).
