"""Assistant de configuration : écriture du .env et configuration Telegram (API Telegram simulée)."""
import importlib.util
import json
from pathlib import Path

import httpx

SPEC = importlib.util.spec_from_file_location("fxa_setup", Path(__file__).resolve().parents[1] / "scripts" / "setup.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def test_env_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "ENV", tmp_path / ".env")
    (tmp_path / ".env").write_text("# commentaire\nFXA_BROKER=paper   # mode\nFXA_MT5_PASSWORD=change-me\n")
    setup.write_env({"FXA_BROKER": "mt5", "FXA_MT5_PASSWORD": "p@ss#'x\"y", "FXA_SYMBOLS": json.dumps(["EURUSD"])})
    env = setup.read_env()
    assert env["FXA_BROKER"] == "mt5" and env["FXA_MT5_PASSWORD"] == "p@ss#'x\"y"
    assert json.loads(env["FXA_SYMBOLS"]) == ["EURUSD"]
    assert (tmp_path / ".env").read_text().startswith("# commentaire")


def test_secrets_generated(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "ENV", tmp_path / ".env")
    (tmp_path / ".env").write_text("FXA_API_PASSWORD=change-me-long-password\nFXA_QUANTOWER_TOKEN=change-me\n")
    setup.setup_secrets()
    env = setup.read_env()
    assert len(env["FXA_API_PASSWORD"]) >= 12 and not env["FXA_API_PASSWORD"].startswith("change-me")
    assert len(env["FXA_JWT_SECRET"]) == 48 and len(env["FXA_QUANTOWER_TOKEN"]) == 48


def test_telegram_setup_detects_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "ENV", tmp_path / ".env")
    (tmp_path / ".env").write_text("")
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        m = req.url.path.rsplit("/", 1)[-1]
        if m == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "mon_agent_bot"}})
        if m == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 7, "message": {"chat": {"id": 555, "type": "private", "first_name": "Youcef"}, "text": "/start"}}]})
        if m == "sendMessage":
            sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    real = httpx.Client
    monkeypatch.setattr(setup.httpx if hasattr(setup, "httpx") else httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler)))
    tok = "1234567890:AAFakeTokenForTestsOnly_abcdefghijklmno"
    # 1er essai : Ctrl+V reçu comme caractère de contrôle ; 2e : token collé avec espaces et préfixe "bot"
    answers = iter(["\x16", f"  bot{tok}\u200b ", "", "o", "1234"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert setup.setup_telegram()
    env = setup.read_env()
    assert env["FXA_TELEGRAM_TOKEN"] == tok and json.loads(env["FXA_TELEGRAM_CHAT_IDS"]) == [555]
    assert env["FXA_TELEGRAM_PIN"] == "1234" and sent and sent[0]["chat_id"] == 555


def test_secret_prompt_rejects_ctrl_v(monkeypatch, capsys):
    vals = iter(["\x16", "MonMotDePasse"])
    monkeypatch.setattr(setup.getpass, "getpass", lambda *a: next(vals))
    assert setup.ask("Mot de passe", secret=True) == "MonMotDePasse"
    assert "clic droit" in capsys.readouterr().out.lower()
