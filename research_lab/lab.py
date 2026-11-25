"""AI Research Lab — collecte et analyse de 100+ dépôts GitHub publics.

Usage :
    python research_lab/lab.py collect   # recherche GitHub -> output/candidates.json
    python research_lab/lab.py analyze   # clone léger (sans blobs) + analyse -> output/catalog.json/.csv
    python research_lab/lab.py report    # classement + rapport -> output/REPORT.md, KNOWLEDGE_BASE.md

Aucun script des dépôts analysés n'est exécuté : seuls l'arborescence, le README
et les fichiers de dépendances sont lus.
"""
from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
CACHE = HERE / "cache"

CATEGORIES: dict[str, list[str]] = {
    "ai_trading_agents": ["llm trading agent", "ai trading agent", "multi-agent trading llm", "gpt trading bot"],
    "autonomous_agents": ["autonomous trading agent", "autonomous agent stock trading", "agentic trading"],
    "forex_algo": ["forex trading bot", "forex algorithmic trading", "metatrader5 python", "mt5 trading bot python",
                   "oanda trading bot", "forex expert advisor"],
    "reinforcement_learning": ["reinforcement learning trading", "deep reinforcement learning stock trading",
                               "reinforcement learning forex"],
    "order_flow": ["order flow trading", "limit order book deep learning", "footprint chart", "volume profile trading",
                   "orderbook imbalance"],
    "risk_management": ["trading risk management position sizing", "portfolio risk management python",
                        "drawdown risk management trading"],
    "backtesting_optimization": ["backtesting framework python", "backtest strategy optimization",
                                 "walk forward optimization trading"],
    "ml_finance": ["machine learning trading", "deep learning stock prediction", "financial machine learning",
                   "smart money concepts", "market regime detection"],
}

TECH = {
    # IA / ML
    "torch": "PyTorch", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "keras": "Keras", "jax": "JAX",
    "scikit-learn": "scikit-learn", "sklearn": "scikit-learn", "xgboost": "XGBoost", "lightgbm": "LightGBM",
    "catboost": "CatBoost", "stable-baselines3": "Stable-Baselines3", "stable_baselines3": "Stable-Baselines3",
    "gym": "Gym", "gymnasium": "Gymnasium", "finrl": "FinRL", "elegantrl": "ElegantRL", "ray": "Ray/RLlib",
    "transformers": "HF Transformers", "langchain": "LangChain", "langgraph": "LangGraph", "openai": "OpenAI API",
    "anthropic": "Anthropic API", "optuna": "Optuna", "hyperopt": "Hyperopt", "prophet": "Prophet",
    "statsmodels": "statsmodels", "hmmlearn": "hmmlearn", "arch": "arch (GARCH)",
    # Trading / données
    "backtrader": "Backtrader", "vectorbt": "VectorBT", "backtesting": "backtesting.py", "zipline": "Zipline",
    "nautilus_trader": "NautilusTrader", "freqtrade": "Freqtrade", "ccxt": "CCXT", "metatrader5": "MetaTrader5",
    "oandapyv20": "OANDA v20", "ib_insync": "IB (ib_insync)", "alpaca-trade-api": "Alpaca", "alpaca-py": "Alpaca",
    "yfinance": "yfinance", "ta": "ta", "ta-lib": "TA-Lib", "talib": "TA-Lib", "pandas_ta": "pandas-ta",
    "pandas-ta": "pandas-ta", "quantstats": "QuantStats", "pyfolio": "pyfolio", "empyrical": "empyrical",
    "riskfolio-lib": "Riskfolio", "pyportfolioopt": "PyPortfolioOpt", "smartmoneyconcepts": "smartmoneyconcepts",
    # Infra
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "streamlit": "Streamlit", "dash": "Dash",
    "python-telegram-bot": "Telegram bot", "aiogram": "Telegram bot", "telebot": "Telegram bot",
    "pytelegrambotapi": "Telegram bot", "sqlalchemy": "SQLAlchemy", "redis": "Redis", "celery": "Celery",
    "docker": "Docker", "react": "React", "vue": "Vue", "next": "Next.js", "websockets": "WebSockets",
}

KEYWORDS = {
    "ai_models": {
        "LSTM": r"\blstm\b", "GRU": r"\bgru\b", "Transformer": r"transformer", "CNN": r"\bcnn\b|convolutional",
        "XGBoost/GBM": r"xgboost|lightgbm|gradient boost", "Random Forest": r"random forest",
        "PPO": r"\bppo\b", "DQN": r"\bdqn\b|deep q", "A2C/A3C": r"\ba2c\b|\ba3c\b", "SAC": r"\bsac\b",
        "LLM": r"\bllm\b|gpt-?4|large language model|claude|gemini|llama", "Sentiment/NLP": r"sentiment|nlp|finbert",
        "HMM/Regime": r"hidden markov|\bhmm\b|regime", "GARCH": r"garch", "Genetic": r"genetic|evolutionary",
    },
    "strategies": {
        "Trend following": r"trend[- ]follow|moving average cross|ema cross|\bsma\b", "Mean reversion": r"mean[- ]revers",
        "Breakout": r"breakout", "Momentum": r"momentum", "Arbitrage": r"arbitrage", "Market making": r"market[- ]mak",
        "Scalping": r"scalp", "Grid": r"\bgrid (trading|bot|strategy)", "Pairs/StatArb": r"pairs trading|statistical arbitrage|cointegrat",
        "SMC/ICT": r"order block|fair value gap|smart money|\bict\b|liquidity sweep", "Order flow": r"order ?flow|footprint|delta|order book|orderbook",
        "Price action": r"price action|support and resistance|candlestick pattern",
    },
    "learning": {
        "Supervisé": r"supervised|classification|regression|predict", "Reinforcement Learning": r"reinforcement|\brl\b|agent.*reward",
        "Agents LLM": r"llm agent|multi-agent|agentic|tool use", "Optimisation bayésienne": r"optuna|bayesian optim|hyperopt",
        "Walk-forward": r"walk[- ]forward", "Évolutionnaire": r"genetic|evolution", "Online learning": r"online learning|incremental",
    },
    "risk": {
        "Stop loss": r"stop[- ]?loss", "Take profit": r"take[- ]?profit", "Position sizing": r"position siz|lot siz|kelly",
        "Drawdown": r"drawdown", "Risque par trade": r"risk per trade|risk percent|% risk", "Trailing stop": r"trailing",
        "Limite journalière": r"daily (loss|limit)|max(imum)? daily", "VaR/CVaR": r"\bvar\b|value at risk|cvar",
        "Kill switch": r"kill[- ]switch|emergency stop|circuit breaker",
    },
}

PERMISSIVE = {"mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "unlicense", "mpl-2.0", "0bsd"}
DEP_FILES = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "environment.yml", "package.json", "Pipfile")


def gh_search(query: str, per_page: int = 30) -> list[dict]:
    cmd = ["gh", "api", "-X", "GET", "search/repositories", "-f", f"q={query}", "-f", "sort=stars",
           "-f", "order=desc", "-f", f"per_page={per_page}"]
    for attempt in range(4):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout).get("items", [])
        time.sleep(10 * (attempt + 1))
    print(f"  ! recherche échouée : {query}", file=sys.stderr)
    return []


def collect() -> list[dict]:
    OUT.mkdir(exist_ok=True)
    seen: dict[str, dict] = {}
    for cat, queries in CATEGORIES.items():
        for q in queries:
            for it in gh_search(q):
                if it.get("fork") or it.get("stargazers_count", 0) < 15:
                    continue
                key = it["full_name"]
                rec = seen.setdefault(key, {
                    "full_name": key, "url": it["html_url"], "description": it.get("description") or "",
                    "stars": it["stargazers_count"], "forks": it["forks_count"], "language": it.get("language"),
                    "topics": it.get("topics", []), "license": (it.get("license") or {}).get("spdx_id"),
                    "pushed_at": it["pushed_at"], "created_at": it["created_at"], "archived": it.get("archived", False),
                    "default_branch": it.get("default_branch", "main"), "size_kb": it.get("size", 0),
                    "categories": [], "queries": [],
                })
                if cat not in rec["categories"]:
                    rec["categories"].append(cat)
                rec["queries"].append(q)
            print(f"  [{cat}] '{q}' -> total uniques {len(seen)}", flush=True)
            time.sleep(2.5)
    # Sélection : top par catégorie (étoiles pondérées par la fraîcheur), puis union
    now = datetime.now(timezone.utc)

    def pre_score(r: dict) -> float:
        age_m = (now - datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))).days / 30
        return math.log10(r["stars"] + 1) * (1.0 if age_m < 12 else 0.8 if age_m < 36 else 0.6)

    selected: dict[str, dict] = {}
    for cat in CATEGORIES:
        pool = sorted((r for r in seen.values() if cat in r["categories"]), key=pre_score, reverse=True)
        for r in pool[:18]:
            selected[r["full_name"]] = r
    cands = sorted(selected.values(), key=lambda r: r["stars"], reverse=True)
    (OUT / "candidates.json").write_text(json.dumps(cands, indent=1, ensure_ascii=False))
    print(f"{len(seen)} dépôts trouvés, {len(cands)} sélectionnés pour analyse")
    return cands


def _git(args: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def fetch_repo(rec: dict) -> dict:
    """Clone sans blobs, puis lit uniquement l'arborescence, le README et les dépendances."""
    d = CACHE / rec["full_name"].replace("/", "__")
    if not (d / ".git").exists():
        d.mkdir(parents=True, exist_ok=True)
        _git(["clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout", rec["url"] + ".git", str(d)], timeout=180)
    files = [f for f in _git(["ls-tree", "-r", "HEAD", "--name-only"], cwd=d).splitlines() if f]
    readme_path = next((f for f in files if re.fullmatch(r"(?i)readme(\.(md|rst|txt))?", f)), None)
    readme = _git(["show", f"HEAD:{readme_path}"], cwd=d)[:60000] if readme_path else ""
    deps = ""
    for f in files:
        if Path(f).name in DEP_FILES and f.count("/") <= 1:
            deps += "\n" + _git(["show", f"HEAD:{f}"], cwd=d)[:20000]
    return {"files": files, "readme": readme, "deps": deps}


def analyze_one(rec: dict) -> dict:
    try:
        raw = fetch_repo(rec)
    except Exception as e:  # noqa: BLE001
        raw = {"files": [], "readme": "", "deps": "", "error": str(e)}
    files, readme, deps = raw["files"], raw["readme"], raw["deps"]
    text = " ".join([rec["description"], " ".join(rec["topics"]), readme]).lower()
    deps_l = deps.lower()

    ext = Counter(Path(f).suffix.lower() for f in files if Path(f).suffix)
    tech = sorted({name for key, name in TECH.items()
                   if re.search(rf"(^|[\s\"'=<>~!,\[]){re.escape(key)}([\s\"'=<>~!,\]\[]|$)", deps_l, re.M)})
    if any(Path(f).name.lower() in ("dockerfile", "docker-compose.yml", "compose.yml") for f in files):
        tech.append("Docker")
    if ext.get(".mq5") or ext.get(".mq4") or ext.get(".mqh"):
        tech.append("MQL4/MQL5")
    if ext.get(".cs"):
        tech.append("C#/.NET")
    found = {k: sorted(n for n, rx in pats.items() if re.search(rx, text)) for k, pats in KEYWORDS.items()}

    top_dirs = sorted({f.split("/")[0] for f in files if "/" in f})[:15]
    has_tests = any(re.search(r"(^|/)(tests?|spec)(/|_)|test_.*\.py$|_test\.py$", f) for f in files)
    has_ci = any(f.startswith(".github/workflows/") for f in files)
    has_docs = any(f.startswith(("docs/", "doc/")) for f in files) or len(readme) > 6000
    py_files = ext.get(".py", 0)
    now = datetime.now(timezone.utc)
    months_idle = (now - datetime.fromisoformat(rec["pushed_at"].replace("Z", "+00:00"))).days / 30.4
    lic = (rec["license"] or "NOASSERTION").lower()

    strengths, weaknesses = [], []
    if rec["stars"] >= 1000: strengths.append(f"Forte adoption ({rec['stars']:,} ★)")
    if has_tests: strengths.append("Tests automatisés présents")
    else: weaknesses.append("Aucun test détecté")
    if has_ci: strengths.append("Intégration continue (GitHub Actions)")
    if has_docs: strengths.append("Documentation conséquente")
    else: weaknesses.append("Documentation limitée")
    if months_idle < 6: strengths.append("Activement maintenu (< 6 mois)")
    elif months_idle > 24: weaknesses.append(f"Inactif depuis {months_idle/12:.1f} ans")
    if found["risk"]: strengths.append("Gestion du risque : " + ", ".join(found["risk"][:4]))
    else: weaknesses.append("Aucune gestion du risque explicite")
    if lic in PERMISSIVE: strengths.append(f"Licence permissive ({rec['license']})")
    elif lic in ("noassertion", "none", "other"): weaknesses.append("Licence absente/indéterminée : réutilisation du code exclue")
    else: weaknesses.append(f"Licence {rec['license']} : réutilisation du code contrainte (copyleft)")
    if rec["archived"]: weaknesses.append("Dépôt archivé")
    if py_files == 0 and rec["language"] != "Python": weaknesses.append(f"Pas en Python ({rec['language']})")

    # Scores (0-100)
    s_pop = min(1.0, math.log10(rec["stars"] + 1) / 4.5)
    s_fresh = 1.0 if months_idle < 6 else 0.75 if months_idle < 18 else 0.45 if months_idle < 36 else 0.2
    s_quality = (0.35 * has_tests + 0.2 * has_ci + 0.25 * has_docs + 0.2 * (len(top_dirs) >= 3))
    s_relevance = min(1.0, (len(found["strategies"]) + len(found["risk"]) + len(found["learning"]) + len(found["ai_models"])) / 12)
    s_reuse = (0.45 * (lic in PERMISSIVE) + 0.25 * (py_files > 0 or rec["language"] == "Python") + 0.15 * has_tests
               + 0.15 * (not rec["archived"]))
    score = round(100 * (0.25 * s_pop + 0.15 * s_fresh + 0.2 * s_quality + 0.2 * s_relevance + 0.2 * s_reuse), 1)
    reuse = ("Code réutilisable" if s_reuse >= 0.8 else "Idées / architecture à reprendre" if s_reuse >= 0.45
             else "Inspiration uniquement")
    return {
        **{k: rec[k] for k in ("full_name", "url", "description", "stars", "forks", "language", "license",
                               "pushed_at", "archived", "categories")},
        "files_count": len(files), "python_files": py_files, "top_dirs": top_dirs,
        "languages": dict(ext.most_common(5)), "technologies": sorted(set(tech)),
        "ai_models": found["ai_models"], "strategies": found["strategies"], "learning_methods": found["learning"],
        "risk_management": found["risk"], "has_tests": has_tests, "has_ci": has_ci, "has_docs": has_docs,
        "months_idle": round(months_idle, 1), "strengths": strengths, "weaknesses": weaknesses,
        "reusability": reuse,
        "scores": {"popularity": round(100 * s_pop), "freshness": round(100 * s_fresh), "quality": round(100 * s_quality),
                   "relevance": round(100 * s_relevance), "reusability": round(100 * s_reuse), "overall": score},
        "readme_chars": len(readme), "error": raw.get("error"),
    }


def analyze() -> list[dict]:
    cands = json.loads((OUT / "candidates.json").read_text())
    CACHE.mkdir(exist_ok=True)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, res in enumerate(ex.map(analyze_one, cands), 1):
            results.append(res)
            if i % 10 == 0:
                print(f"  analysé {i}/{len(cands)}", flush=True)
    results.sort(key=lambda r: r["scores"]["overall"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank
    (OUT / "catalog.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    with open(OUT / "catalog.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "repo", "score", "stars", "language", "license", "categories", "technologies", "ai_models",
                    "strategies", "learning", "risk", "tests", "ci", "months_idle", "reusability", "url"])
        for r in results:
            w.writerow([r["rank"], r["full_name"], r["scores"]["overall"], r["stars"], r["language"], r["license"],
                        "|".join(r["categories"]), "|".join(r["technologies"]), "|".join(r["ai_models"]),
                        "|".join(r["strategies"]), "|".join(r["learning_methods"]), "|".join(r["risk_management"]),
                        r["has_tests"], r["has_ci"], r["months_idle"], r["reusability"], r["url"]])
    ok = sum(1 for r in results if r["files_count"] > 0)
    print(f"{len(results)} dépôts analysés ({ok} avec arborescence lue)")
    return results


def _table(rows: list[dict], n: int) -> str:
    out = ["| # | Dépôt | Score | ★ | Langage | Licence | Réutilisation |", "|---|---|---|---|---|---|---|"]
    for r in rows[:n]:
        out.append(f"| {r['rank']} | [{r['full_name']}]({r['url']}) | {r['scores']['overall']} | {r['stars']:,} | "
                   f"{r['language'] or '-'} | {r['license'] or '-'} | {r['reusability']} |")
    return "\n".join(out)


def report() -> None:
    cat = json.loads((OUT / "catalog.json").read_text())
    n = len(cat)
    tech = Counter(t for r in cat for t in r["technologies"])
    models = Counter(t for r in cat for t in r["ai_models"])
    strats = Counter(t for r in cat for t in r["strategies"])
    learn = Counter(t for r in cat for t in r["learning_methods"])
    risk = Counter(t for r in cat for t in r["risk_management"])
    lines = [
        "# AI Research Lab — Rapport d'analyse", "",
        f"Généré le {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · **{n} dépôts GitHub analysés** "
        f"({sum(r['files_count'] > 0 for r in cat)} avec arborescence complète lue).", "",
        "Méthode : recherche GitHub sur 8 thèmes (36 requêtes), sélection des 18 meilleurs par thème "
        "(étoiles × fraîcheur), clone léger sans contenu des fichiers, lecture de l'arborescence, du README "
        "et des fichiers de dépendances. Extraction automatique (technologies, modèles IA, stratégies, "
        "apprentissage, gestion du risque) puis scoring 0-100 : popularité 25 %, qualité (tests/CI/docs) 20 %, "
        "pertinence 20 %, réutilisabilité (licence, Python, tests) 20 %, fraîcheur 15 %. Aucun code tiers exécuté.", "",
        "## Top 25 global", "", _table(cat, 25), "",
    ]
    for c in CATEGORIES:
        rows = [r for r in cat if c in r["categories"]]
        lines += [f"## Catégorie : {c} ({len(rows)} dépôts)", "", _table(rows, 8), ""]

    def freq(title: str, cnt: Counter) -> list[str]:
        out = [f"### {title}", "", "| Élément | Dépôts | % |", "|---|---|---|"]
        out += [f"| {k} | {v} | {100 * v / n:.0f} % |" for k, v in cnt.most_common(15)]
        return out + [""]

    lines += ["## Cartographie technologique", ""]
    lines += freq("Technologies", tech) + freq("Modèles IA", models) + freq("Stratégies", strats)
    lines += freq("Méthodes d'apprentissage", learn) + freq("Gestion du risque", risk)
    no_risk = sum(1 for r in cat if not r["risk_management"])
    no_tests = sum(1 for r in cat if not r["has_tests"])
    lines += ["## Constats clés", "",
              f"- {no_risk}/{n} dépôts ({100 * no_risk / n:.0f} %) ne documentent aucune gestion du risque.",
              f"- {no_tests}/{n} dépôts ({100 * no_tests / n:.0f} %) n'ont aucun test automatisé.",
              f"- {sum(1 for r in cat if r['reusability'] == 'Code réutilisable')} dépôts ont un code directement réutilisable "
              "(licence permissive + Python + tests).", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    kb = ["# Bibliothèque de connaissances — fiches des dépôts", ""]
    for r in cat:
        kb += [f"## {r['rank']}. [{r['full_name']}]({r['url']}) — score {r['scores']['overall']}", "",
               f"> {r['description']}", "",
               f"- **Catégories** : {', '.join(r['categories'])} · ★ {r['stars']:,} · {r['language']} · licence {r['license']}",
               f"- **Architecture** : {r['files_count']} fichiers, {r['python_files']} .py · dossiers : {', '.join(r['top_dirs']) or '-'}"
               f" · tests {'oui' if r['has_tests'] else 'non'} · CI {'oui' if r['has_ci'] else 'non'}",
               f"- **Technologies** : {', '.join(r['technologies']) or '-'}",
               f"- **Modèles IA** : {', '.join(r['ai_models']) or '-'}",
               f"- **Stratégies** : {', '.join(r['strategies']) or '-'}",
               f"- **Apprentissage** : {', '.join(r['learning_methods']) or '-'}",
               f"- **Gestion du risque** : {', '.join(r['risk_management']) or 'aucune détectée'}",
               f"- **Points forts** : {'; '.join(r['strengths']) or '-'}",
               f"- **Faiblesses** : {'; '.join(r['weaknesses']) or '-'}",
               f"- **Réutilisation** : {r['reusability']}", ""]
    (OUT / "KNOWLEDGE_BASE.md").write_text("\n".join(kb), encoding="utf-8")
    print(f"Rapports écrits : {OUT / 'REPORT.md'}, {OUT / 'KNOWLEDGE_BASE.md'}")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("collect", "all"):
        collect()
    if step in ("analyze", "all"):
        analyze()
    if step in ("report", "all"):
        report()
