import logging

import httpx

from forex_agent.logging_setup import RedactFilter, redact, setup
from forex_agent.notify.telegram import TelegramBot

TOKEN = "1234567890:AAFakeTokenForTestsOnly_abcdefghijklmno"  # faux token (format réel)


def test_redact_token_in_url_and_alone():
    url = f'POST https://api.telegram.org/bot{TOKEN}/getUpdates "HTTP/1.1 200 OK"'
    assert TOKEN not in redact(url) and "bot***" in redact(url)
    assert TOKEN not in redact(f"token is {TOKEN}")
    assert "secret123" not in redact("https://x/?password=secret123&a=1")


def test_log_records_are_redacted(tmp_path, caplog):
    setup(tmp_path)
    assert logging.getLogger("httpx").level == logging.WARNING  # les URL avec token ne sont plus journalisées
    caplog.handler.addFilter(RedactFilter())
    logging.getLogger("agent").warning("erreur sur https://api.telegram.org/bot%s/sendMessage", TOKEN)
    assert TOKEN not in caplog.text
    content = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert TOKEN not in content and "bot***" in content


def test_telegram_error_message_redacted():
    def boom(request):
        raise httpx.ConnectError(f"connexion impossible à {request.url}")
    bot = TelegramBot(TOKEN, [1], transport=httpx.MockTransport(boom))
    bot._post("getMe", {})
    assert bot.last_error and TOKEN not in bot.last_error
