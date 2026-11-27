"""Bot Telegram : notifications instantanées + commandes authentifiées.

Sécurité :
  * Seuls les chat_id autorisés (FXA_TELEGRAM_CHAT_IDS) peuvent interagir ; toute autre tentative est rejetée
    et signalée comme alerte de sécurité.
  * Commandes sensibles (/pause, /resume, /mode, /auto, /stop, /close, validation de trade) : chat autorisé +
    code PIN si FXA_TELEGRAM_PIN est défini (ex. « /resume 1234 »).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

import httpx

from ..logging_setup import redact

log = logging.getLogger("telegram")

SENSITIVE = {"/pause", "/resume", "/mode", "/auto", "/stop", "/close"}
HELP = ("Commandes : /status · /positions · /performance · /signals · /pause · /resume · /mode [manual|auto] · "
        "/auto [on|off] · /stop (arrêt d'urgence) · /close &lt;ticket&gt; · /help")


class TelegramBot:
    def __init__(self, token: str | None, chat_ids: list[int], pin: str | None = None,
                 api_base: str = "https://api.telegram.org", transport: httpx.BaseTransport | None = None):
        self.token = token
        self.chat_ids = [int(c) for c in chat_ids]
        self.pin = pin or None
        self.enabled = bool(token and self.chat_ids)
        self.base = f"{api_base}/bot{token}" if token else ""
        self.client = httpx.Client(timeout=35, transport=transport) if self.enabled else None
        self.q: queue.Queue = queue.Queue()
        self.handlers: dict[str, Callable[[list[str]], str]] = {}
        self.callback_handler: Callable[[str, str], str] | None = None
        self.on_security: Callable[[str], None] | None = None
        self._offset = 0
        self._stop = threading.Event()
        self.last_ok: float | None = None
        self.last_error = ""
        self.sent = 0

    # ---------- envoi ----------
    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        if not self.enabled:
            log.info("[telegram désactivé] %s", text.replace("\n", " | ")[:200])
            return
        self.q.put((text, buttons))

    def _post(self, method: str, payload: dict) -> dict | None:
        for attempt in range(3):
            try:
                r = self.client.post(f"{self.base}/{method}", json=payload)
                data = r.json()
                if data.get("ok"):
                    self.last_ok = time.time()
                    return data
                self.last_error = redact(str(data.get("description")))
                if r.status_code == 429:
                    time.sleep(float(data.get("parameters", {}).get("retry_after", 2)))
                    continue
                return None
            except Exception as e:  # noqa: BLE001 - réseau
                self.last_error = redact(str(e))
                time.sleep(1.5 * (attempt + 1))
        return None

    def _send_now(self, text: str, buttons: list[list[tuple[str, str]]] | None) -> None:
        for chat in self.chat_ids:
            payload: dict[str, Any] = {"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                                       "disable_web_page_preview": True}
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                                               for row in buttons]}
            if self._post("sendMessage", payload):
                self.sent += 1

    def _sender(self) -> None:
        while not self._stop.is_set():
            try:
                text, buttons = self.q.get(timeout=1)
            except queue.Empty:
                continue
            self._send_now(text, buttons)

    # ---------- réception ----------
    def authorized(self, chat_id: int) -> bool:
        return int(chat_id) in self.chat_ids

    def handle_text(self, chat_id: int, text: str) -> str:
        if not self.authorized(chat_id):
            if self.on_security:
                self.on_security(f"Commande Telegram refusée depuis chat_id {chat_id} : {text[:40]}")
            return "⛔ Accès non autorisé."
        parts = text.strip().split()
        if not parts:
            return HELP
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        if cmd in SENSITIVE and self.pin:
            if not args or args[-1] != self.pin:
                if self.on_security:
                    self.on_security(f"PIN invalide pour {cmd} (chat {chat_id})")
                return "🔐 Code PIN requis : ajoutez votre PIN à la fin de la commande."
            args = args[:-1]
        h = self.handlers.get(cmd)
        if h is None:
            return HELP
        try:
            return h(args)
        except Exception as e:  # noqa: BLE001
            return f"Erreur : {e}"

    def handle_callback(self, chat_id: int, data: str) -> str:
        if not self.authorized(chat_id):
            if self.on_security:
                self.on_security(f"Validation Telegram refusée depuis chat_id {chat_id}")
            return "⛔ Accès non autorisé."
        if self.callback_handler is None:
            return "Indisponible"
        action, _, sid = data.partition(":")
        return self.callback_handler(action, sid)

    def _poller(self) -> None:
        while not self._stop.is_set():
            data = self._post("getUpdates", {"offset": self._offset, "timeout": 25,
                                             "allowed_updates": ["message", "callback_query"]})
            if not data:
                time.sleep(3)
                continue
            for upd in data.get("result", []):
                self._offset = upd["update_id"] + 1
                if "message" in upd and "text" in upd["message"]:
                    m = upd["message"]
                    reply = self.handle_text(m["chat"]["id"], m["text"])
                    self._post("sendMessage", {"chat_id": m["chat"]["id"], "text": reply, "parse_mode": "HTML"})
                elif "callback_query" in upd:
                    cq = upd["callback_query"]
                    chat = cq["message"]["chat"]["id"]
                    reply = self.handle_callback(chat, cq.get("data", ""))
                    self._post("answerCallbackQuery", {"callback_query_id": cq["id"], "text": reply[:190]})
                    self._post("sendMessage", {"chat_id": chat, "text": reply, "parse_mode": "HTML"})

    def start(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._sender, name="tg-sender", daemon=True).start()
        threading.Thread(target=self._poller, name="tg-poller", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def connected(self) -> bool:
        return self.enabled and self.last_ok is not None and time.time() - self.last_ok < 120

    def test(self) -> bool:
        if not self.enabled:
            return False
        return self._post("getMe", {}) is not None
