"""Calendrier économique : bloque les nouvelles entrées autour des annonces à fort impact."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..symbols import get_spec

log = logging.getLogger("calendar")


class EconomicCalendar:
    def __init__(self, url: str | None, block_minutes: int = 30, refresh_sec: int = 3600):
        self.url = url
        self.block = timedelta(minutes=block_minutes)
        self.refresh_sec = refresh_sec
        self.events: list[dict] = []
        self.fetched_at: float | None = None
        self.error = ""
        self.lock = threading.Lock()

    def load(self, events: list[dict]) -> None:
        parsed = []
        for e in events:
            try:
                dt = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00")).astimezone(timezone.utc)
            except (KeyError, ValueError):
                continue
            parsed.append({"time": dt, "currency": str(e.get("country", "")).upper(), "impact": e.get("impact", ""),
                           "title": e.get("title", "")})
        with self.lock:
            self.events = parsed
            self.fetched_at = time.time()

    def refresh(self) -> None:
        if not self.url or (self.fetched_at and time.time() - self.fetched_at < self.refresh_sec):
            return
        try:
            r = httpx.get(self.url, timeout=15)
            r.raise_for_status()
            self.load(r.json())
            self.error = ""
        except Exception as e:  # noqa: BLE001 - calendrier optionnel, l'agent continue
            self.error = str(e)
            self.fetched_at = time.time()  # évite de marteler la source

    def block_reason(self, symbol: str, now: datetime | None = None) -> str | None:
        now = now or datetime.now(timezone.utc)
        spec = get_spec(symbol)
        with self.lock:
            evs = list(self.events)
        for e in evs:
            if e["impact"] != "High" or e["currency"] not in (spec.base, spec.quote):
                continue
            if abs(e["time"] - now) <= self.block:
                return f"annonce majeure {e['currency']} « {e['title']} » à {e['time']:%H:%M} UTC"
        return None

    def upcoming(self, hours: int = 48) -> list[dict]:
        now = datetime.now(timezone.utc)
        with self.lock:
            return [{**e, "time": e["time"].isoformat()} for e in self.events
                    if now - timedelta(hours=1) <= e["time"] <= now + timedelta(hours=hours) and e["impact"] in ("High", "Medium")]
