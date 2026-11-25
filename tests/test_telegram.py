import json

import httpx

from forex_agent.notify.telegram import TelegramBot


def make_bot(pin=None):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"ok": True, "result": []})
    bot = TelegramBot("TOKEN", [42], pin, transport=httpx.MockTransport(handler))
    bot.handlers = {"/status": lambda a: "ok-status", "/pause": lambda a: "paused"}
    bot.callback_handler = lambda action, sid: f"{action}:{sid}"
    alerts = []
    bot.on_security = alerts.append
    return bot, calls, alerts


def test_unauthorized_chat_rejected():
    bot, _, alerts = make_bot()
    assert "non autorisé" in bot.handle_text(999, "/status")
    assert alerts and "999" in alerts[0]


def test_pin_required_for_sensitive():
    bot, _, alerts = make_bot(pin="1234")
    assert bot.handle_text(42, "/status") == "ok-status"
    assert "PIN" in bot.handle_text(42, "/pause")
    assert bot.handle_text(42, "/pause 1234") == "paused"
    assert alerts


def test_callback_auth():
    bot, _, _ = make_bot()
    assert bot.handle_callback(42, "approve:abc") == "approve:abc"
    assert "non autorisé" in bot.handle_callback(7, "approve:abc")


def test_send_with_buttons():
    bot, calls, _ = make_bot()
    bot._send_now("<b>hello</b>", [[("✅", "approve:1")]])
    path, payload = calls[0]
    assert path.endswith("/sendMessage") and payload["chat_id"] == 42 and payload["parse_mode"] == "HTML"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "approve:1"
