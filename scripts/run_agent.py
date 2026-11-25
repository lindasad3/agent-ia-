"""Démarre l'agent (boucle de trading + API + application Web) en un seul processus."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Console / journaux Windows : forcer l'UTF-8 (émojis, accents) même si la sortie est redirigée
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import uvicorn  # noqa: E402

from forex_agent.main import build  # noqa: E402

if __name__ == "__main__":
    app, rt, st = build()
    uvicorn.run(app, host=st.api_host, port=st.api_port, log_level="info", proxy_headers=True)
