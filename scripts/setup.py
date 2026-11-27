"""Assistant de configuration (à lancer sur le VPS) : écrit le fichier .env pas à pas.

    python scripts/setup.py mt5        # identifiants MT5, test de connexion, détection des symboles
    python scripts/setup.py telegram   # token du bot, détection automatique du chat_id, message de test
    python scripts/setup.py secrets    # mots de passe / jetons de l'application générés aléatoirement
    python scripts/setup.py all

Les secrets sont saisis localement (mot de passe masqué) et ne quittent jamais le VPS.
"""
from __future__ import annotations

import getpass
import json
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Console / journaux Windows : forcer l'UTF-8 (émojis, accents) même si la sortie est redirigée
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
ENV = ROOT / ".env"
DEFAULT_MT5 = r"C:\Program Files\MetaTrader 5\terminal64.exe"


# ---------------------------------------------------------------- .env
def _quote(v: str) -> str:
    if v == "" or all(c.isalnum() or c in "._-:/\\" for c in v):
        return v
    if "'" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_env() -> dict[str, str]:
    from dotenv import dotenv_values  # fourni par pydantic-settings
    return {k: v or "" for k, v in dotenv_values(ENV).items()} if ENV.exists() else {}


def write_env(values: dict[str, str]) -> None:
    if not ENV.exists():
        src = ROOT / ".env.example"
        ENV.write_text(src.read_text(encoding="utf-8") if src.exists() else "", encoding="utf-8")
    lines = ENV.read_text(encoding="utf-8").splitlines()
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={_quote(values[key])}"
            done.add(key)
    for k, v in values.items():
        if k not in done:
            lines.append(f"{k}={_quote(v)}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    label = f"{prompt}" + (f" [{default}]" if default and not secret else "") + " : "
    for _ in range(3):
        v = getpass.getpass(label) if secret else input(label)
        if secret and any(ord(c) < 32 for c in v):
            # Ctrl+V dans une saisie masquée Windows envoie le caractère de contrôle, pas le texte
            print("   ⚠ Ctrl+V ne fonctionne pas dans une saisie masquée : faites un CLIC DROIT pour coller, ou tapez.")
            continue
        return v.strip() or (default or "")
    return default or ""


TOKEN_RX = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def clean_token(raw: str) -> str:
    """Nettoie un token collé : espaces, guillemets, caractères invisibles, préfixe « bot » d'une URL."""
    t = "".join(c for c in raw if c.isprintable()).strip().strip("'\"<>").strip()
    if t.lower().startswith("bot") and ":" in t:
        t = t[3:]
    return t


def ask_token(existing: str = "") -> str:
    print("   (le token s'affiche à l'écran pour que Ctrl+V fonctionne ; cette session est privée)")
    for _ in range(3):
        raw = input("Collez le token du bot puis Entrée : ")
        t = clean_token(raw)
        if not t and existing and yes("   Garder le token déjà enregistré ?"):
            return existing
        if TOKEN_RX.match(t):
            return t
        print(f"   ✖ Format invalide ({len(t)} caractères reçus). Attendu : 123456789:AAH... (copiez tout le token "
              f"envoyé par @BotFather)")
    return ""


def yes(prompt: str, default: bool = True) -> bool:
    v = input(f"{prompt} [{'O/n' if default else 'o/N'}] : ").strip().lower()
    return default if not v else v in ("o", "oui", "y", "yes")


# ---------------------------------------------------------------- secrets
def setup_secrets(force: bool = False) -> None:
    env = read_env()
    out = {}
    pw = env.get("FXA_API_PASSWORD", "")
    if force or not pw or pw.startswith("change-me"):
        out["FXA_API_PASSWORD"] = secrets.token_urlsafe(12)
    for k in ("FXA_JWT_SECRET", "FXA_QUANTOWER_TOKEN"):
        if force or not env.get(k) or env.get(k, "").startswith("change-me"):
            out[k] = secrets.token_hex(24)
    if out:
        write_env(out)
    pw = out.get("FXA_API_PASSWORD") or pw
    print(f"✔ Secrets de l'application prêts. Connexion à l'app : utilisateur « {env.get('FXA_API_USER') or 'admin'} », "
          f"mot de passe « {pw} » (notez-le, il est aussi dans .env).")


# ---------------------------------------------------------------- MT5
def setup_mt5() -> bool:
    print("\n=== MetaTrader 5 ===")
    print("Arrêtez l'agent avant : powershell -ExecutionPolicy Bypass -File deploy\\windows\\stop.ps1")
    print("Prérequis : terminal MT5 installé ET ouvert une première fois, connecté à votre compte DÉMO,")
    print("et bouton « Algo Trading » activé (vert) dans la barre d'outils.\n")
    env = read_env()
    path = ask("Chemin du terminal MT5", env.get("FXA_MT5_PATH") or DEFAULT_MT5)
    if not Path(path).exists():
        print(f"⚠ Fichier introuvable : {path} (installez MT5 ou corrigez le chemin)")
    login = ask("Numéro de compte (login)", env.get("FXA_MT5_LOGIN") if env.get("FXA_MT5_LOGIN") not in ("", "12345678") else None)
    password = ask("Mot de passe du compte (masqué, clic droit pour coller)", secret=True) or env.get("FXA_MT5_PASSWORD", "")
    server = ask("Serveur (exactement comme affiché dans MT5)", env.get("FXA_MT5_SERVER") or "MetaQuotes-Demo")
    if not login.isdigit():
        print("✖ Le login doit être un nombre.")
        return False
    values = {"FXA_BROKER": "mt5", "FXA_MT5_PATH": path, "FXA_MT5_LOGIN": login, "FXA_MT5_PASSWORD": password,
              "FXA_MT5_SERVER": server}
    try:
        from forex_agent.config import Settings
        from forex_agent.execution.mt5_broker import MT5Broker
        symbols = Settings().symbols
        b = MT5Broker(int(login), password, server, path, allow_live=False, symbols=symbols)
        print("Connexion au terminal...")
        if not b.connect():
            print(f"✖ {b.last_error}")
            write_env(values)
            print("Identifiants enregistrés malgré tout ; corrigez puis relancez : python scripts/setup.py mt5")
            return False
        okc, msg = b.verify_credentials()
        print(f"  {'✔' if okc else '✖'} Mot de passe : {msg}")
        if not okc:
            write_env(values)
            print("Corrigez le mot de passe (clic droit pour coller) puis relancez : python scripts/setup.py mt5")
            return False
        d = b.diagnostics()
        acc = b.account()
        print(f"✔ Connecté : compte {acc.login} @ {acc.server} — {'DÉMO' if acc.is_demo else 'RÉEL'} — "
              f"{acc.balance:,.2f} {acc.currency}, levier 1:{acc.leverage}")
        algo = d.get("terminal", {}).get("algo_trading_enabled")
        print(f"  Algo Trading dans le terminal : {'✔ activé' if algo else '✖ DÉSACTIVÉ — cliquez sur « Algo Trading » dans MT5'}")
        print(f"  Symboles : {json.dumps(b.symbol_map)}")
        if b.missing_symbols:
            print(f"  ⚠ Introuvables chez ce broker (ils seront ignorés) : {', '.join(b.missing_symbols)}")
            values["FXA_SYMBOLS"] = json.dumps([s for s in symbols if s not in b.missing_symbols])
        values["FXA_MT5_SYMBOL_MAP"] = json.dumps(b.symbol_map)
        print(f"  Décalage heure serveur : {b.server_offset} ({b.offset_source})")
        if b.offset_source == "détecté":
            values["FXA_MT5_SERVER_UTC_OFFSET"] = str(b.server_offset.total_seconds() / 3600)
        if acc.is_demo:
            print("\nAucune version du modèle n'a encore passé la validation hors échantillon.")
            if yes("Autoriser la version non validée sur ce compte DÉMO (nécessaire pour la démonstration) ?"):
                values["FXA_ALLOW_UNVALIDATED_MODEL"] = "true"
        b.disconnect()
    except ImportError:
        print("⚠ Module MetaTrader5 absent : lancez d'abord deploy\\windows\\install.ps1 (ou pip install MetaTrader5).")
    write_env(values)
    print("✔ Configuration MT5 enregistrée dans .env. Test complet : python scripts/mt5_check.py --test-order")
    return True


# ---------------------------------------------------------------- Telegram
def setup_telegram() -> bool:
    import httpx
    print("\n=== Telegram ===")
    print("1. Dans Telegram, ouvrez @BotFather, envoyez /newbot, choisissez un nom : il vous donne un TOKEN.")
    env = read_env()
    token = ask_token(env.get("FXA_TELEGRAM_TOKEN", ""))
    if not token:
        print("✖ Aucun token valide. Relancez : .venv\\Scripts\\python.exe scripts\\setup.py telegram")
        return False
    api = f"https://api.telegram.org/bot{token}"
    with httpx.Client(timeout=40) as c:
        try:
            me = c.get(f"{api}/getMe").json()
        except Exception as e:  # noqa: BLE001
            print(f"✖ Telegram injoignable depuis ce serveur : {e}")
            return False
        if not me.get("ok"):
            print(f"✖ Token refusé par Telegram : {me.get('description')}")
            return False
        bot = me["result"]["username"]
        print(f"✔ Bot reconnu : @{bot}")
        c.post(f"{api}/deleteWebhook")  # l'agent lit les messages en long polling
        print(f"2. Ouvrez https://t.me/{bot} et envoyez-lui /start (depuis chaque compte à autoriser).")
        input("   Appuyez sur Entrée une fois le message envoyé...")
        chats: dict[int, str] = {}
        deadline = time.time() + 90
        while time.time() < deadline and not chats:
            upd = c.get(f"{api}/getUpdates", params={"timeout": 20}).json()
            for u in upd.get("result", []):
                m = u.get("message") or {}
                ch = m.get("chat") or {}
                if ch.get("type") == "private":
                    chats[int(ch["id"])] = " ".join(filter(None, [ch.get("first_name"), ch.get("last_name"),
                                                                    f"@{ch['username']}" if ch.get("username") else None]))
            if not chats:
                print("   ...aucun message reçu pour l'instant, envoyez /start au bot")
        if not chats:
            print("✖ Aucun chat détecté. Relancez : python scripts/setup.py telegram")
            return False
        allowed = []
        for cid, name in chats.items():
            if yes(f"   Autoriser {name} (chat_id {cid}) à piloter l'agent ?"):
                allowed.append(cid)
        if not allowed:
            print("✖ Aucun chat autorisé.")
            return False
        for cid in allowed:
            c.post(f"{api}/sendMessage", json={"chat_id": cid, "parse_mode": "HTML",
                                                "text": "✅ <b>Agent IA Forex</b> : notifications activées pour ce chat."})
        # marque les messages comme lus pour que l'agent ne les retraite pas
        last = c.get(f"{api}/getUpdates").json().get("result", [])
        if last:
            c.get(f"{api}/getUpdates", params={"offset": last[-1]["update_id"] + 1})
    values = {"FXA_TELEGRAM_TOKEN": token, "FXA_TELEGRAM_CHAT_IDS": json.dumps(allowed)}
    pin = ask("PIN pour les commandes sensibles (/pause /resume /mode /auto /stop /close), vide = aucun", "")
    values["FXA_TELEGRAM_PIN"] = pin
    write_env(values)
    print(f"✔ Telegram configuré ({len(allowed)} chat(s) autorisé(s)). Un message de test vient d'être envoyé.")
    return True


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step in ("secrets", "all"):
        setup_secrets()
    if step in ("mt5", "all"):
        setup_mt5()
    if step in ("telegram", "all"):
        setup_telegram()
    if step not in ("secrets", "mt5", "telegram", "all"):
        print(__doc__)
