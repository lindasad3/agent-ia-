/* Agent IA Forex — application Web (PWA). Vanilla JS, aucune dépendance externe. */
"use strict";

const NAV = [
  ["dashboard", "Dashboard", "▦"], ["market", "Market Analysis", "◔"], ["live", "Live Trading", "⚡"],
  ["history", "Trade History", "☰"], ["performance", "Performance Analytics", "↗"], ["learning", "AI Learning", "✦"],
  ["strategies", "Strategies", "♟"], ["risk", "Risk Management", "⛨"], ["settings", "Settings", "⚙"],
];
const MOBILE = [["dashboard", "Accueil", "▦"], ["market", "Marché", "◔"], ["live", "Trading", "⚡"], ["performance", "Perf", "↗"], ["more", "Plus", "☰"]];
const S = { token: null, status: null, symbol: null, timer: null, histFilter: {}, learning: null };

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* stockage indisponible */ } },
};
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const digits = (sym) => (sym || "").includes("JPY") ? 3 : (sym || "").startsWith("XAU") ? 2 : 5;
const px = (sym, v) => v == null ? "-" : Number(v).toFixed(digits(sym));
const num = (v, d = 2) => v == null || isNaN(v) ? "-" : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? "-" : `${num(100 * v, d)} %`;
const signed = (v, d = 2) => v == null ? "-" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${num(v, d)}</span>`;
const time = (iso) => iso ? new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-";
const dirBadge = (d) => `<span class="badge ${d === "BUY" ? "buy" : "sell"}">${esc(d)}</span>`;
const stBadge = (s) => `<span class="badge ${esc(s)}">${esc(s)}</span>`;

function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3500); }

async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: `Bearer ${S.token}` } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (r.status === 401) { logout(); throw new Error("Session expirée"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Erreur ${r.status}`);
  return data;
}

/* ---------------- graphiques SVG ---------------- */
function lineChart(points, { h = 180, color = "var(--accent)", fmtY = (v) => num(v, 0) } = {}) {
  if (!points || points.length < 2) return `<div class="empty">Pas encore assez de données</div>`;
  const W = 800, H = h, P = 30;
  const ys = points.map((p) => p.v), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const x = (i) => P + (i / (points.length - 1)) * (W - P - 6), y = (v) => H - 18 - ((v - lo) / span) * (H - 30);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const area = `${d}L${x(points.length - 1)},${H - 18}L${x(0)},${H - 18}Z`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Courbe">
    <defs><linearGradient id="g${h}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#g${h})"/><path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>
    <text x="2" y="${y(hi) + 4}" fill="var(--muted)" font-size="11">${fmtY(hi)}</text>
    <text x="2" y="${y(lo) + 4}" fill="var(--muted)" font-size="11">${fmtY(lo)}</text>
    <text x="${P}" y="${H - 3}" fill="var(--muted)" font-size="11">${esc(time(points[0].t))}</text>
    <text x="${W - 6}" y="${H - 3}" fill="var(--muted)" font-size="11" text-anchor="end">${esc(time(points[points.length - 1].t))}</text></svg>`;
}

function candleChart(bars, sym, ov = {}) {
  if (!bars || !bars.length) return `<div class="empty">Pas de données</div>`;
  const W = 900, H = 360, R = 70;
  const vals = bars.flatMap((b) => [b.h, b.l]);
  (ov.levels || []).forEach((l) => vals.push(l.price));
  const lo0 = Math.min(...bars.map((b) => b.l)), hi0 = Math.max(...bars.map((b) => b.h));
  const pad = (hi0 - lo0) * 0.15, lo = lo0 - pad, hi = hi0 + pad;
  const y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (H - 24);
  const bw = (W - R) / bars.length, x = (i) => i * bw + bw / 2;
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Graphique ${esc(sym)}">`;
  (ov.zones || []).forEach((z) => {
    if (z.top == null || z.bot == null || z.top < lo || z.bot > hi) return;
    s += `<rect x="0" y="${y(z.top)}" width="${W - R}" height="${Math.max(2, y(z.bot) - y(z.top))}" fill="${z.color}" opacity=".16"/>
          <text x="4" y="${y(z.top) + 11}" fill="${z.color}" font-size="10">${esc(z.label)}</text>`;
  });
  const used = [];
  (ov.levels || []).filter((l) => l.price != null && l.price >= lo && l.price <= hi).sort((a, b) => b.price - a.price).forEach((l) => {
    s += `<line x1="0" x2="${W - R}" y1="${y(l.price)}" y2="${y(l.price)}" stroke="${l.color}" stroke-dasharray="${l.dash || "4 4"}" stroke-width="1"/>`;
    if (used.some((u) => Math.abs(u - y(l.price)) < 12)) return;  // évite le chevauchement des étiquettes
    used.push(y(l.price));
    s += `<text x="${W - R + 4}" y="${y(l.price) + 3}" fill="${l.color}" font-size="10">${esc(l.label)} ${px(sym, l.price)}</text>`;
  });
  bars.forEach((b, i) => {
    const up = b.c >= b.o, c = up ? "var(--up)" : "var(--down)";
    s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(b.h)}" y2="${y(b.l)}" stroke="${c}"/>
          <rect x="${x(i) - bw * 0.35}" y="${y(Math.max(b.o, b.c))}" width="${bw * 0.7}" height="${Math.max(1, Math.abs(y(b.o) - y(b.c)))}" fill="${c}"/>`;
  });
  const last = bars[bars.length - 1];
  s += `<text x="${W - R + 4}" y="${y(last.c) + 3}" fill="var(--text)" font-size="11" font-weight="700">${px(sym, last.c)}</text>`;
  return s + `</svg>`;
}

/* ---------------- shell ---------------- */
function renderNav() {
  const r = route();
  $("#nav").innerHTML = NAV.map(([k, l, i]) => `<a href="#/${k}" class="${r === k ? "active" : ""}"><span>${i}</span>${l}</a>`).join("");
  $("#bottomnav").innerHTML = MOBILE.map(([k, l, i]) => `<a href="#/${k}" class="${r === k ? "active" : ""}"><span>${i}</span>${l}</a>`).join("");
}
const route = () => (location.hash.replace("#/", "") || "dashboard").split("?")[0];

function renderPills(st) {
  if (!st) return;
  const a = st.account || {};
  const p = (on, label, cls) => `<span class="pill"><i class="dot ${cls || (on ? "on" : "off")}"></i>${label}</span>`;
  $("#pills").innerHTML = [
    p(st.running && !st.halted, st.halted ? "Agent : sécurité" : st.paused ? "Agent : pause" : "Agent actif", st.halted ? "off" : st.paused ? "idle" : "on"),
    p(st.broker.connected, `${esc(st.broker.name)}${a.is_demo ? " démo" : a.is_demo === false ? " RÉEL" : ""}`),
    p(st.quantower.connected, "Quantower", st.quantower.connected ? "on" : "idle"),
    p(st.telegram.connected, "Telegram", st.telegram.enabled ? (st.telegram.connected ? "on" : "idle") : "idle"),
    `<span class="pill">Mode <b>${st.mode === "auto" ? "AUTO" : "MANUEL"}</b></span>`,
    `<span class="pill">Exécution auto <b class="${st.auto_trading ? "up" : "muted"}">${st.auto_trading ? "ON" : "OFF"}</b></span>`,
  ].join("");
  let b = "";
  if (st.simulation) b += `<div class="banner info">🧪 SIMULATION — broker papier rejouant des données historiques réelles (horloge ${esc(time(st.clock))}). Aucun ordre réel.</div>`;
  if (st.halted) b += `<div class="banner err">⛔ Arrêt de sécurité : ${esc(st.halt_reason)}</div>`;
  if (st.model && st.model.warning) b += `<div class="banner warn">⚠ ${esc(st.model.warning)}</div>`;
  $("#banner").innerHTML = b;
  $("#sideFoot").innerHTML = `Modèle : ${esc(st.model?.version || "aucun")}<br>Dernier cycle : ${esc(time(st.last_cycle))}`;
}

async function refreshStatus() {
  try { S.status = await api("/api/status"); renderPills(S.status); } catch (e) { /* hors ligne */ }
}

async function show() {
  renderNav();
  const r = route();
  const view = $("#view");
  try {
    const fn = PAGES[r] || PAGES.dashboard;
    view.innerHTML = await fn();
  } catch (e) { view.innerHTML = `<div class="card err">${esc(e.message)}</div>`; }
}

function schedule() {
  clearInterval(S.timer);
  S.timer = setInterval(async () => {
    if (document.hidden) return;
    await refreshStatus();
    if (["dashboard", "live", "market", "risk"].includes(route())) show();
  }, 6000);
}

/* ---------------- pages ---------------- */
const PAGES = {};

PAGES.dashboard = async () => {
  const d = await api("/api/dashboard");
  const st = d.status, a = st.account || {}, p = d.performance || {}, cur = a.currency || "";
  const eq = (d.equity || []).map((e) => ({ t: e.t, v: e.equity }));
  return `<h1>Dashboard</h1>
  <div class="grid g6">
    ${kpi("Solde", `${num(a.balance)} ${esc(cur)}`, a.is_demo ? "compte démo" : esc(a.server || ""))}
    ${kpi("Equity", `${num(a.equity)} ${esc(cur)}`, `marge libre ${num(a.margin_free, 0)}`)}
    ${kpi("P&L du jour", signed(st.pnl_today), "depuis l'ouverture du jour")}
    ${kpi("Positions ouvertes", st.open_positions, `${st.pending_signals} signal(s) en attente`)}
    ${kpi("Profit net", signed(p.net_profit), `${p.n_trades || 0} trades clôturés`)}
    ${kpi("Win rate · PF", `${pct(p.win_rate, 0)} · ${p.profit_factor ?? "-"}`, "trades réellement enregistrés")}
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><div class="row spread"><h2>Contrôle</h2><span class="muted">${esc(st.symbols.length)} paires · ${esc(st.entry_tf)}</span></div>
      <div class="row" style="gap:18px;margin:6px 0 10px">
        <div><h3>Mode</h3><div class="seg"><button data-act="control" data-a="mode_manual" class="${st.mode === "manual" ? "on" : ""}">Manuel</button><button data-act="control" data-a="mode_auto" class="${st.mode === "auto" ? "on" : ""}">Automatique</button></div></div>
        <div><h3>Exécution auto</h3><label class="switch"><input type="checkbox" data-act="auto" ${st.auto_trading ? "checked" : ""}><span></span></label></div>
        <div><h3>Entrées</h3>${st.paused ? `<button class="btn ok sm" data-act="control" data-a="resume">▶ Reprendre</button>` : `<button class="btn sm" data-act="control" data-a="pause">⏸ Pause</button>`}</div>
      </div>
      <h3>Connexions</h3>
      <div class="kv">
        <div><b>Broker</b>${esc(st.broker.name)} ${st.broker.connected ? "🟢" : "🔴"}</div>
        <div><b>Quantower</b>${st.quantower.connected ? "🟢 flux actif" : "⚪ non connecté"}</div>
        <div><b>Telegram</b>${st.telegram.enabled ? (st.telegram.connected ? "🟢" : "🟡 en attente") : "⚪ non configuré"}</div>
        <div><b>Modèle</b>${esc(st.model?.version || "aucun")} ${st.model?.validated ? "✅" : "⚠"}</div>
      </div></div>
    <div class="card"><h2>Évolution du capital</h2>${lineChart(eq, { h: 170 })}</div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h2>Signaux en attente de validation</h2>${pendingList(d.pending)}</div>
    <div class="card"><h2>Positions ouvertes</h2>${positionsTable(d.positions, cur, true)}</div>
  </div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h2>Dernières décisions de l'IA</h2>${decisionsTable(d.decisions)}</div>
    <div class="card"><h2>Activité récente & alertes</h2>${feed(d.events)}</div>
  </div>`;
};

const kpi = (l, v, sub) => `<div class="card kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub ?? ""}</div></div>`;

function pendingList(list) {
  if (!list || !list.length) return `<div class="empty">Aucun signal en attente</div>`;
  return list.map((s) => `<div class="signal">
    <div class="row spread"><div class="row"><b>${esc(s.symbol)}</b>${dirBadge(s.direction)}<span class="badge">${esc(s.strategy)}</span><span class="muted">${esc(s.timeframe)}</span></div>
      <span class="muted">valide jusqu'à ${esc(time(s.valid_until))}</span></div>
    <div class="kv">
      <div><b>Entrée</b><span class="mono">${px(s.symbol, s.entry)}</span></div><div><b>Stop Loss</b><span class="mono">${px(s.symbol, s.sl)}</span></div>
      <div><b>Take Profit</b><span class="mono">${px(s.symbol, s.tp)}</span></div><div><b>Volume</b>${num(s.volume)} lot</div>
      <div><b>Risque</b>${num(s.risk_pct)} % (${num(s.risk_amount)})</div><div><b>R/R</b>1:${num(s.rr)}</div>
      <div><b>Score</b>${num(s.score, 0)}/100</div><div><b>Probabilité</b>${pct(s.probability, 0)}</div></div>
    <div class="expl">${esc(s.explanation)}</div>
    <div class="row"><button class="btn ok" data-act="approve" data-id="${esc(s.id)}">✅ Valider et exécuter</button><button class="btn" data-act="reject" data-id="${esc(s.id)}">❌ Refuser</button></div></div>`).join("");
}

function positionsTable(pos, cur, compact) {
  if (!pos || !pos.length) return `<div class="empty">Aucune position ouverte</div>`;
  return `<div class="table-wrap"><table><tr><th>Ticket</th><th>Paire</th><th>Sens</th><th>Volume</th><th>Entrée</th><th>SL</th><th>TP</th><th>Prix</th><th>P&L</th>${compact ? "" : "<th>Stratégie</th>"}<th></th></tr>
    ${pos.map((p) => `<tr><td class="mono">${p.ticket}</td><td><b>${esc(p.symbol)}</b></td><td>${dirBadge(p.direction)}</td><td>${num(p.volume)}</td>
      <td class="mono">${px(p.symbol, p.entry_price)}</td><td class="mono">${px(p.symbol, p.sl)}</td><td class="mono">${px(p.symbol, p.tp)}</td>
      <td class="mono">${px(p.symbol, p.price_current)}</td><td>${signed(p.profit)} ${esc(cur || "")}</td>${compact ? "" : `<td>${esc(p.strategy || "-")}</td>`}
      <td><button class="btn sm" data-act="close" data-id="${p.ticket}">Clôturer</button></td></tr>`).join("")}</table></div>`;
}

function decisionsTable(dec) {
  if (!dec || !dec.length) return `<div class="empty">En attente du premier cycle d'analyse</div>`;
  return `<div class="table-wrap"><table><tr><th>Paire</th><th>Régime</th><th>Meilleure opportunité</th><th>Décision</th></tr>
    ${dec.map((d) => {
      const best = (d.candidates || []).slice().sort((a, b) => b.score - a.score)[0];
      return `<tr><td><a href="#/market" data-act="sym" data-id="${esc(d.symbol)}"><b>${esc(d.symbol)}</b></a></td><td>${esc(d.regime_label)}</td>
      <td>${best ? `${dirBadge(best.direction)} ${esc(best.strategy)} · ${num(best.score, 0)}` : '<span class="muted">aucune</span>'}</td>
      <td>${d.signal ? '<span class="badge executed">SIGNAL</span>' : d.blocked && d.blocked.length ? `<span class="muted" title="${esc(d.blocked.join(" | "))}">filtré</span>` : '<span class="muted">attente</span>'}</td></tr>`;
    }).join("")}</table></div>`;
}

function feed(ev) {
  if (!ev || !ev.length) return `<div class="empty">Aucune activité</div>`;
  return `<div class="feed">${ev.map((e) => `<div><time>${esc(time(e.ts))}</time><span class="badge ${e.level === "error" ? "failed" : e.level === "warning" ? "pending" : ""}">${esc(e.category)}</span> ${esc(e.message)}</div>`).join("")}</div>`;
}

PAGES.market = async () => {
  const st = S.status || await api("/api/status");
  S.symbol = S.symbol || st.symbols[0];
  const a = await api(`/api/analysis/${S.symbol}`);
  const sym = a.symbol, r = a.regime, smc = a.smc, of = a.orderflow, d = a.decision || {};
  const zones = [
    { top: smc.fvg_bull_top, bot: smc.fvg_bull_bot, color: "var(--up)", label: "FVG haussier" },
    { top: smc.fvg_bear_top, bot: smc.fvg_bear_bot, color: "var(--down)", label: "FVG baissier" },
    { top: smc.ob_bull_top, bot: smc.ob_bull_bot, color: "var(--accent2)", label: "Order Block +" },
    { top: smc.ob_bear_top, bot: smc.ob_bear_bot, color: "var(--warn)", label: "Order Block −" }];
  const levels = [...(a.levels || []).slice(0, 5).map((l) => ({ price: l.price, label: l.type[0] + l.touches, color: "var(--muted)" })),
    { price: of.poc, label: "POC", color: "var(--pending)", dash: "2 3" }, { price: smc.bsl, label: "BSL", color: "var(--up)", dash: "1 3" },
    { price: smc.ssl, label: "SSL", color: "var(--down)", dash: "1 3" }];
  const trend = (v) => v > 0 ? '<span class="up">haussier</span>' : v < 0 ? '<span class="down">baissier</span>' : "neutre";
  const cands = (d.candidates || []).slice().sort((x, y) => y.score - x.score);
  return `<h1>Market Analysis</h1>
  <div class="tabs">${st.symbols.map((s) => `<button data-act="sym" data-id="${esc(s)}" class="${s === sym ? "on" : ""}">${esc(s)}</button>`).join("")}</div>
  <div class="card"><div class="row spread"><h2>${esc(sym)} · ${esc(a.timeframe)}</h2><span class="muted">dernière bougie ${esc(time(a.bars.at(-1)?.t))}</span></div>
    ${candleChart(a.bars, sym, { zones, levels })}</div>
  <div class="grid g4" style="margin-top:14px">
    <div class="card"><h3>Régime de marché</h3><div class="kpi"><div class="value" style="font-size:18px">${esc(r.label)}</div></div>
      <div class="kv"><div><b>ADX</b>${num(r.adx, 0)}</div><div><b>Efficacité</b>${num(r.er)}</div><div><b>Volatilité</b>${esc(r.vol_state)}</div>
      <div><b>Session</b>${esc(r.session)}</div><div><b>RSI</b>${num(r.rsi, 0)}</div><div><b>ATR</b>${px(sym, r.atr)}</div>
      <div><b>Contexte 1</b>${trend(r.htf1_trend)}</div><div><b>Contexte 2</b>${trend(r.htf2_trend)}</div><div><b>Structure</b>${trend(r.structure)}</div></div></div>
    <div class="card"><h3>Smart Money Concepts</h3><div class="kv">
      <div><b>FVG haussier</b>${smc.fvg_bull_top ? px(sym, smc.fvg_bull_bot) + "–" + px(sym, smc.fvg_bull_top) : "-"}</div>
      <div><b>FVG baissier</b>${smc.fvg_bear_top ? px(sym, smc.fvg_bear_bot) + "–" + px(sym, smc.fvg_bear_top) : "-"}</div>
      <div><b>Order Block +</b>${smc.ob_bull_top ? px(sym, smc.ob_bull_bot) + "–" + px(sym, smc.ob_bull_top) : "-"}</div>
      <div><b>Order Block −</b>${smc.ob_bear_top ? px(sym, smc.ob_bear_bot) + "–" + px(sym, smc.ob_bear_top) : "-"}</div>
      <div><b>Buy-side liq.</b>${px(sym, smc.bsl)}</div><div><b>Sell-side liq.</b>${px(sym, smc.ssl)}</div>
      <div><b>Premium/Discount</b>${smc.pd_pos == null ? "-" : smc.pd_pos > 1 ? "Au-dessus du range" : smc.pd_pos < 0 ? "Sous le range" : smc.pd_pos > 0.5 ? "Premium " + pct(smc.pd_pos, 0) : "Discount " + pct(smc.pd_pos, 0)}</div>
      <div><b>Sweep récent</b>${smc.since_sweep_bull < 10 ? "SSL (" + smc.since_sweep_bull + " b.)" : smc.since_sweep_bear < 10 ? "BSL (" + smc.since_sweep_bear + " b.)" : "-"}</div></div></div>
    <div class="card"><h3>Order Flow</h3><p class="muted" style="margin:0 0 6px">Source : ${esc(of.source)}</p><div class="kv">
      <div><b>Delta</b>${signed(of.delta, 0)}</div><div><b>Delta cumulé</b>${signed(of.cum_delta, 0)}</div>
      <div><b>DOM bid/ask</b>${of.dom_imbalance == null ? "-" : signed(of.dom_imbalance)}</div><div><b>Volume relatif</b>${num(of.vol_z)} σ</div>
      <div><b>POC</b>${px(sym, of.poc)}</div><div><b>VAH / VAL</b>${px(sym, of.vah)} / ${px(sym, of.val)}</div>
      <div><b>Absorption</b>${of.absorption ? "oui" : "non"}</div></div></div>
    <div class="card"><h3>Niveaux clés & calendrier</h3>
      ${(a.levels || []).slice(0, 6).map((l) => `<div class="row spread"><span>${esc(l.type)} (${l.touches} touches)</span><span class="mono">${px(sym, l.price)}</span></div>`).join("") || '<div class="empty">-</div>'}
      <h3 style="margin-top:10px">Annonces</h3>${(a.calendar || []).slice(0, 4).map((e) => `<div class="muted">${esc(time(e.time))} ${esc(e.currency)} ${esc(e.title)} (${esc(e.impact)})</div>`).join("") || '<div class="muted">Aucune annonce à venir (ou calendrier indisponible)</div>'}</div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Raisonnement du cerveau IA</h2><p class="expl">${esc(d.explanation || "En attente d'analyse")}</p>
    ${d.blocked && d.blocked.length ? `<p class="muted">Filtres : ${esc(d.blocked.join(" · "))}</p>` : ""}
    ${cands.length ? `<div class="table-wrap"><table><tr><th>Stratégie</th><th>Sens</th><th>Entrée</th><th>SL</th><th>TP</th><th>R/R</th><th>Score</th><th>Proba</th><th>EV</th><th>Base statistique</th></tr>
      ${cands.map((c) => `<tr title="${esc((c.reasons || []).join(" · "))}"><td>${esc(c.strategy)}</td><td>${dirBadge(c.direction)}</td><td class="mono">${px(sym, c.entry)}</td><td class="mono">${px(sym, c.sl)}</td><td class="mono">${px(sym, c.tp)}</td><td>${num(c.rr)}</td><td>${num(c.score, 0)}</td><td>${pct(c.probability, 0)}</td><td>${signed(c.ev_r)}R</td><td class="muted">${esc(c.source)}</td></tr>`).join("")}</table></div>` : '<div class="empty">Aucune stratégie ne propose de trade sur la dernière bougie</div>'}</div>`;
};

PAGES.live = async () => {
  const d = await api("/api/positions");
  const st = S.status || {};
  const cur = st.account?.currency || "";
  return `<h1>Live Trading</h1>
  <div class="card"><div class="row spread"><h2>Signaux en attente (mode manuel)</h2><button class="btn sm" data-act="control" data-a="analyze_now">↻ Analyser maintenant</button></div>
    <p class="muted">Tous les paramètres nécessaires au placement de l'ordre sont fournis. Validation = re-contrôle du risque au prix courant puis exécution vérifiée dans MT5.</p>${pendingList(d.pending_signals)}</div>
  <div class="card" style="margin-top:14px"><h2>Positions ouvertes</h2>${positionsTable(d.positions, cur, false)}
    ${(d.positions || []).filter((p) => p.events && p.events.length).map((p) => `<p class="muted">#${p.ticket} : ${p.events.map((e) => esc(e.event)).join(" → ")}</p>`).join("")}</div>
  <div class="card" style="margin-top:14px"><h2>Ordres en attente (broker)</h2>${(d.orders || []).length ? `<div class="table-wrap"><table><tr><th>Ticket</th><th>Symbole</th><th>Volume</th><th>Prix</th><th>SL</th><th>TP</th></tr>${d.orders.map((o) => `<tr><td>${o.ticket}</td><td>${esc(o.symbol)}</td><td>${num(o.volume)}</td><td>${o.price}</td><td>${o.sl}</td><td>${o.tp}</td></tr>`).join("")}</table></div>` : '<div class="empty">Aucun ordre en attente</div>'}</div>
  <div class="card" style="margin-top:14px"><h2>Sécurité</h2><div class="row">
    <button class="btn danger" data-act="kill">⏻ Arrêt d'urgence (tout clôturer)</button>
    <button class="btn" data-act="control" data-a="pause">⏸ Suspendre les entrées</button><button class="btn ok" data-act="control" data-a="resume">▶ Reprendre</button></div></div>`;
};

PAGES.history = async () => {
  const f = S.histFilter;
  const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const rows = await api(`/api/trades?limit=500&${q}`);
  S.lastTrades = rows;
  const st = S.status || {};
  return `<h1>Trade History</h1>
  <div class="card"><div class="row" style="margin-bottom:10px">
    <select data-act="filter" data-k="symbol" style="width:auto"><option value="">Toutes paires</option>${(st.symbols || []).map((s) => `<option ${f.symbol === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <select data-act="filter" data-k="status" style="width:auto"><option value="">Tous statuts</option><option ${f.status === "open" ? "selected" : ""} value="open">Ouverts</option><option ${f.status === "closed" ? "selected" : ""} value="closed">Clôturés</option></select>
    <button class="btn sm" data-act="csv">⬇ Export CSV</button><span class="muted">${rows.length} transaction(s)</span></div>
  ${rows.length ? `<div class="table-wrap"><table><tr><th>Ouverture</th><th>Paire</th><th>Sens</th><th>Stratégie</th><th>TF</th><th>Volume</th><th>Entrée</th><th>Sortie</th><th>SL init.</th><th>TP init.</th><th>Risque</th><th>Résultat</th><th>R</th><th>Durée</th><th>Motif</th><th>Broker</th></tr>
    ${rows.map((t) => { const dur = t.close_time ? Math.round((new Date(t.close_time) - new Date(t.open_time)) / 60000) : null;
      return `<tr title="${esc(t.analysis?.explanation || "")}"><td>${esc(time(t.open_time))}</td><td><b>${esc(t.symbol)}</b></td><td>${dirBadge(t.direction)}</td><td>${esc(t.strategy)}</td><td>${esc(t.timeframe)}</td><td>${num(t.volume)}</td>
      <td class="mono">${px(t.symbol, t.entry_price)}</td><td class="mono">${px(t.symbol, t.exit_price)}</td><td class="mono">${px(t.symbol, t.sl_initial)}</td><td class="mono">${px(t.symbol, t.tp_initial)}</td>
      <td>${num(t.risk_amount)} (${num(t.risk_pct)}%)</td><td>${t.pnl == null ? stBadge(t.status) : signed(t.pnl)}</td><td>${t.pnl_r == null ? "-" : signed(t.pnl_r)}</td>
      <td>${dur == null ? "-" : dur >= 1440 ? (dur / 1440).toFixed(1) + " j" : dur >= 60 ? (dur / 60).toFixed(1) + " h" : dur + " min"}</td><td>${esc(t.close_reason || "-")}</td><td>${esc(t.broker)}</td></tr>`; }).join("")}</table></div>` : '<div class="empty">Aucune transaction enregistrée</div>'}</div>`;
};

function groupTable(obj, title) {
  const rows = Object.entries(obj || {});
  if (!rows.length) return `<div class="card"><h2>${title}</h2><div class="empty">-</div></div>`;
  return `<div class="card"><h2>${title}</h2><div class="table-wrap"><table><tr><th></th><th>Trades</th><th>Net</th><th>Win rate</th><th>PF</th><th>Esp. R</th></tr>
    ${rows.map(([k, v]) => `<tr><td><b>${esc(k)}</b></td><td>${v.n}</td><td>${signed(v.net)}</td><td>${pct(v.win_rate, 0)}</td><td>${v.profit_factor ?? "-"}</td><td>${v.expectancy_r == null ? "-" : signed(v.expectancy_r)}</td></tr>`).join("")}</table></div></div>`;
}

PAGES.performance = async () => {
  const [p, l] = await Promise.all([api("/api/performance"), api("/api/learning")]);
  const m = p.live || {};
  const eq = (p.equity || []).map((e) => ({ t: e.t, v: e.equity }));
  const v = (l.versions || []).find((x) => x.version === (l.loaded?.version || l.active)) || (l.versions || []).at(-1);
  const om = v?.oos_metrics || {};
  return `<h1>Performance Analytics</h1>
  <p class="muted">Statistiques calculées uniquement à partir des transactions réellement enregistrées et confirmées par le broker${S.status?.simulation ? " (ici : broker papier — SIMULATION)" : ""}.</p>
  <div class="grid g6">${kpi("Profit net", signed(m.net_profit), `${m.n_trades || 0} transactions`)}${kpi("Win rate", pct(m.win_rate, 1), `${m.max_consecutive_losses ?? 0} pertes consécutives max`)}
    ${kpi("Profit factor", m.profit_factor ?? "-", "gains bruts / pertes brutes")}${kpi("Expectancy", signed(m.expectancy), m.expectancy_r == null ? "" : `${num(m.expectancy_r)} R / trade`)}
    ${kpi("Drawdown max", num(m.max_drawdown), m.max_drawdown_pct == null ? "" : `${num(m.max_drawdown_pct)} %`)}${kpi("Gain / perte moyens", `${signed(m.avg_win)} / ${signed(m.avg_loss)}`, "")}</div>
  <div class="card" style="margin-top:14px"><h2>Évolution du capital (equity)</h2>${lineChart(eq, { h: 220 })}</div>
  <div class="grid g3" style="margin-top:14px">${groupTable(m.by_strategy, "Par stratégie")}${groupTable(m.by_symbol, "Par paire")}${groupTable(m.by_timeframe, "Par timeframe")}</div>
  <div class="card" style="margin-top:14px"><h2>Référence : test hors échantillon du modèle ${esc(v?.version || "-")} (backtest, pas des trades réels)</h2>
    <div class="kv"><div><b>Trades OOS</b>${om.n_trades ?? "-"}</div><div><b>Rendement</b>${om.return_pct == null ? "-" : num(om.return_pct) + " %"}</div><div><b>Profit factor</b>${om.profit_factor ?? "-"}</div>
    <div><b>Win rate</b>${pct(om.win_rate, 1)}</div><div><b>Drawdown max</b>${om.max_drawdown_pct == null ? "-" : num(om.max_drawdown_pct) + " %"}</div><div><b>Statut</b>${stBadge(v?.status || "-")}</div></div>
    ${lineChart((v?.oos_equity_curve || []).map((e) => ({ t: e.t, v: e.equity })), { h: 160, color: "var(--accent2)" })}</div>`;
};

PAGES.learning = async () => {
  const [l, r] = await Promise.all([api("/api/learning"), api("/api/research")]);
  S.learning = l;
  const vs = (l.versions || []).slice().reverse();
  const cur = vs[0];
  const rev = l.journal_review;
  return `<h1>AI Learning — laboratoire offline</h1>
  <div class="card"><div class="row spread"><div><h2>Modèle chargé : ${esc(l.loaded?.version || "aucun")} ${l.loaded?.validated ? "✅ validé" : "⚠ non validé"}</h2>
    <p class="muted" style="margin:0">L'agent ne modifie jamais son modèle en trading réel. Chaque nouvelle version est entraînée offline (backtest M1 réel, Optuna imbriqué, walk-forward, méta-modèle) et n'est activée que si elle passe les critères hors échantillon.</p></div>
    <div class="row"><button class="btn primary" data-act="learn" ${l.running ? "disabled" : ""}>${l.running ? "Apprentissage en cours…" : "▶ Lancer un apprentissage"}</button><button class="btn" data-act="review">Analyser le journal réel</button></div></div></div>
  <div class="card" style="margin-top:14px"><h2>Versions du modèle</h2><div class="table-wrap"><table><tr><th>Version</th><th>Statut</th><th>Créée</th><th>TF</th><th>OOS trades</th><th>OOS rendement</th><th>OOS PF</th><th>OOS DD</th><th>WF PF</th><th>Méta-modèle</th><th>Décision du registre</th></tr>
    ${vs.map((v) => `<tr><td><b>${esc(v.version)}</b></td><td>${stBadge(v.status)}</td><td>${esc(time(v.created_at))}</td><td>${esc(v.entry_tf)}</td><td>${v.oos_metrics?.n_trades ?? "-"}</td>
      <td>${v.oos_metrics?.return_pct == null ? "-" : signed(v.oos_metrics.return_pct) + " %"}</td><td>${v.oos_metrics?.profit_factor ?? "-"}</td><td>${num(v.oos_metrics?.max_drawdown_pct)} %</td>
      <td>${v.walkforward?.metrics?.profit_factor ?? "-"}</td><td>${esc(v.meta_model?.status || "-")} (AUC ${v.meta_model?.auc ?? "-"})</td><td class="muted" style="white-space:normal">${esc((v.gate || []).join(" ; "))}</td></tr>`).join("") || '<tr><td colspan="11" class="empty">Aucune version</td></tr>'}</table></div></div>
  ${cur ? `<div class="grid g2" style="margin-top:14px">
    <div class="card"><h2>Walk-forward (${esc(cur.version)})</h2><div class="table-wrap"><table><tr><th>Fenêtre test</th><th>Trades</th><th>Net</th><th>PF</th><th>Win</th><th>DD</th></tr>
      ${(cur.walkforward?.windows || []).map((w) => `<tr><td>${esc(w.test_start)} → ${esc(w.test_end)}</td><td>${w.trades}</td><td>${signed(w.net)}</td><td>${w.profit_factor ?? "-"}</td><td>${pct(w.win_rate, 0)}</td><td>${num(w.max_dd_pct)}%</td></tr>`).join("")}</table></div>
      ${lineChart((cur.walkforward?.equity_curve || []).map((e) => ({ t: e.t, v: e.equity })), { h: 140, color: "var(--pending)" })}</div>
    <div class="card"><h2>Stratégies : IS vs OOS (${esc(cur.version)})</h2><div class="table-wrap"><table><tr><th>Stratégie</th><th>IS trades</th><th>IS esp.</th><th>OOS trades</th><th>OOS esp.</th><th>Optuna</th></tr>
      ${(cur.raw_is || []).map((s) => { const o = (cur.raw_oos || []).find((x) => x.strategy === s.strategy) || {}; const op = (cur.optimization || []).find((x) => x.strategy === s.strategy);
        return `<tr><td>${esc(s.strategy)}</td><td>${s.trades}</td><td>${signed(s.exp_r, 3)}</td><td>${o.trades ?? 0}</td><td>${signed(o.exp_r ?? 0, 3)}</td><td>${op ? (op.adopted ? "adopté" : "rejeté") : "-"}</td></tr>`; }).join("")}</table></div>
      <h3 style="margin-top:12px">Pré-validation</h3><p class="muted">Look-ahead : ${cur.validation?.no_lookahead?.ok ? "✅ aucun" : "❌"} · MTF : ${cur.validation?.mtf?.ok ? "✅" : "❌"} · Données : ${(cur.validation?.data || []).filter((x) => x.ok).length}/${(cur.validation?.data || []).length} instruments valides</p></div></div>` : ""}
  <div class="card" style="margin-top:14px"><h2>Analyse du journal réel</h2>${rev ? `<p class="muted">${esc(time(rev.generated_at))} · ${rev.n_closed} trades clôturés</p><ul>${(rev.findings || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    ${rev.rejection_reasons ? `<p class="muted">Motifs de refus du Risk Engine : ${Object.entries(rev.rejection_reasons).map(([k, v]) => `${esc(k)} (${v})`).join(" · ")}</p>` : ""}` : '<div class="empty">Cliquez sur « Analyser le journal réel »</div>'}</div>
  <div class="card" style="margin-top:14px"><h2>AI Research Lab — ${r.count} dépôts GitHub analysés</h2>
    <div class="table-wrap"><table><tr><th>#</th><th>Dépôt</th><th>Score</th><th>★</th><th>Licence</th><th>Catégories</th><th>Réutilisation</th></tr>
    ${(r.top || []).slice(0, 15).map((x) => `<tr><td>${x.rank}</td><td><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.full_name)}</a></td><td>${x.scores.overall}</td><td>${x.stars.toLocaleString("fr-FR")}</td><td>${esc(x.license || "-")}</td><td class="muted">${esc(x.categories.join(", "))}</td><td>${esc(x.reusability)}</td></tr>`).join("")}</table></div></div>`;
};

PAGES.strategies = async () => {
  const list = await api("/api/strategies");
  const regs = ["TREND_UP", "TREND_DOWN", "RANGE", "TRANSITION"];
  const lab = { TREND_UP: "Tend. ↑", TREND_DOWN: "Tend. ↓", RANGE: "Range", TRANSITION: "Transition" };
  const cell = (c) => !c ? `<td class="muted">-</td>` : `<td style="background:${c.exp_r > 0 ? "color-mix(in srgb,var(--up) " + Math.min(40, 8 + c.exp_r * 80) + "%,transparent)" : "color-mix(in srgb,var(--down) " + Math.min(40, 8 - c.exp_r * 80) + "%,transparent)"}">${signed(c.exp_r, 2)}<br><span class="muted">n=${c.n}</span></td>`;
  return `<h1>Strategies</h1><p class="muted">Le cerveau IA arbitre entre toutes les stratégies selon le régime et leurs statistiques apprises offline (espérance en R nette de coûts). Une case négative avec ≥ 30 trades désactive automatiquement la stratégie dans ce contexte.</p>
  <div class="grid g2">${list.map((s) => `<div class="card"><div class="row spread"><h2>${esc(s.label)}</h2><label class="switch" title="Activer/désactiver"><input type="checkbox" data-act="strat" data-id="${esc(s.name)}" ${s.enabled ? "checked" : ""}><span></span></label></div>
    <p>${esc(s.description)}</p><p class="expl"><b>Hypothèse :</b> ${esc(s.hypothesis)}</p>
    <div class="chips">${s.regimes.map((r) => `<span class="badge">${esc(lab[r] || r)}</span>`).join("")}${s.needs_orderflow ? '<span class="badge">Order flow</span>' : ""}</div>
    <div class="kv">${Object.entries(s.params || {}).map(([k, v]) => `<div><b>${esc(k)}</b>${num(v)}</div>`).join("")}</div>
    <h3>Espérance apprise par régime</h3><div class="table-wrap"><table class="heat"><tr>${regs.map((r) => `<th>${lab[r]}</th>`).join("")}<th>Global</th></tr><tr>${regs.map((r) => cell(s.learned?.by_regime?.[r])).join("")}${cell(s.learned?.all)}</tr></table></div>
    <p class="muted">En réel : ${s.live ? `${s.live.n} trades, net ${num(s.live.net)}, win ${pct(s.live.win_rate, 0)}` : "aucun trade"}</p></div>`).join("")}</div>`;
};

PAGES.risk = async () => {
  const r = await api("/api/risk");
  const c = r.config, u = r.usage;
  const g = (val, lim, label) => { const f = Math.max(0, Math.min(1, (val || 0) / lim)); return `<div class="card kpi"><div class="label">${label}</div><div class="value">${num(Math.max(0, val || 0))} % <span class="muted" style="font-size:13px">/ ${num(lim, 1)} %</span></div><div class="gauge"><i class="${f > 0.8 ? "hi" : f > 0.5 ? "mid" : ""}" style="width:${100 * f}%"></i></div></div>`; };
  const ex = Object.entries(u.exposure || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const field = (k, l, step = "0.1") => `<label>${l}<input name="${k}" type="number" step="${step}" value="${c[k]}"></label>`;
  return `<h1>Risk Management Engine</h1>
  <p class="muted">Module indépendant du cerveau IA : il recalcule volume, risque et R/R au prix réel et peut refuser tout ordre proposé.</p>
  ${r.halted ? `<div class="card" style="border-color:var(--down)"><b class="down">⛔ ${esc(r.halt_reason)}</b> <button class="btn sm" data-act="control" data-a="reset_safety">Réinitialiser les compteurs</button></div>` : ""}
  <div class="grid g4" style="margin-top:10px">${g(u.daily_loss_pct, c.max_daily_loss_pct, "Perte du jour")}${g(u.weekly_loss_pct, c.max_weekly_loss_pct, "Perte de la semaine")}
    ${g(u.drawdown_pct, c.max_drawdown_pct, "Drawdown depuis le pic")}${g(u.open_risk_pct, c.max_total_risk_pct, "Risque ouvert total")}</div>
  <div class="grid g2" style="margin-top:14px">
    <div class="card"><h2>Limites</h2><form id="riskForm" class="grid g2">
      ${field("risk_per_trade_pct", "Risque par trade (%)", "0.05")}${field("min_rr", "R/R minimum")}${field("max_daily_loss_pct", "Perte journalière max (%)")}
      ${field("max_weekly_loss_pct", "Perte hebdo max (%)")}${field("max_drawdown_pct", "Drawdown max (%)")}${field("max_open_positions", "Positions simultanées max", "1")}
      ${field("max_total_risk_pct", "Exposition globale max (%)")}${field("max_currency_exposure_pct", "Exposition par devise max (%)")}
      ${field("max_slippage_pips", "Slippage max (pips)")}${field("duplicate_window_min", "Fenêtre anti-doublon (min)", "1")}
      <div><button class="btn primary" type="submit">Enregistrer</button></div></form>
      <p class="muted">Spread max : ${Object.entries(c.max_spread_pips).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")} pips, et ≤ ${num(100 * c.max_spread_sl_ratio, 0)} % du SL.</p></div>
    <div class="card"><h2>Exposition par devise</h2>${ex.length ? ex.map(([k, v]) => `<div class="row spread"><b>${esc(k)}</b><span>${signed(v)} %</span></div><div class="gauge"><i class="${Math.abs(v) > c.max_currency_exposure_pct * 0.8 ? "hi" : ""}" style="width:${Math.min(100, 100 * Math.abs(v) / c.max_currency_exposure_pct)}%"></i></div>`).join("") : '<div class="empty">Aucune exposition</div>'}
      <h2 style="margin-top:16px">Derniers refus</h2>${(r.recent_rejections || []).length ? r.recent_rejections.map((s) => `<div class="signal"><div class="row"><b>${esc(s.symbol)}</b>${dirBadge(s.direction)}<span class="badge">${esc(s.strategy)}</span><span class="muted">${esc(time(s.created_at))}</span></div>
        <div class="muted">${(s.risk_checks || []).filter((x) => !x.ok).map((x) => `⛔ ${esc(x.name)} : ${esc(x.detail)}`).join("<br>")}</div></div>`).join("") : '<div class="empty">Aucun refus récent</div>'}</div></div>`;
};

PAGES.settings = async () => {
  const st = await api("/api/status");
  const a = st.account || {};
  const theme = store.get("theme") || "auto";
  return `<h1>Settings</h1><div class="grid g2">
  <div class="card"><h2>Fonctionnement</h2><div class="kv">
    <div><b>Mode</b>${st.mode === "auto" ? "Automatique" : "Manuel"}</div><div><b>Exécution auto</b>${st.auto_trading ? "ON" : "OFF"}</div>
    <div><b>Paires</b>${esc(st.symbols.join(", "))}</div><div><b>Timeframe</b>${esc(st.entry_tf)}</div><div><b>Démarré</b>${esc(time(st.started_at))}</div><div><b>Erreurs cycle</b>${st.cycle_errors}</div></div>
    <div class="row"><div class="seg"><button data-act="control" data-a="mode_manual" class="${st.mode === "manual" ? "on" : ""}">Manuel</button><button data-act="control" data-a="mode_auto" class="${st.mode === "auto" ? "on" : ""}">Automatique</button></div>
      <button class="btn" data-act="control" data-a="${st.auto_trading ? "auto_off" : "auto_on"}">${st.auto_trading ? "Désactiver" : "Activer"} l'exécution auto</button></div></div>
  <div class="card"><h2>Compte broker (${esc(st.broker.name)})</h2><div class="kv"><div><b>Connexion</b>${st.broker.connected ? "🟢 connecté" : "🔴 déconnecté"}</div><div><b>Compte</b>${esc(a.login ?? "-")}</div>
    <div><b>Serveur</b>${esc(a.server || "-")}</div><div><b>Type</b>${a.is_demo ? "Démo" : "RÉEL"}</div><div><b>Levier</b>1:${esc(a.leverage ?? "-")}</div><div><b>Devise</b>${esc(a.currency || "-")}</div></div>
    ${st.broker.error ? `<p class="err">${esc(st.broker.error)}</p>` : ""}<p class="muted">Identifiants MT5 configurés côté serveur (.env), jamais exposés à l'application.</p></div>
  <div class="card"><h2>Telegram</h2><div class="kv"><div><b>Configuré</b>${st.telegram.enabled ? "oui" : "non"}</div><div><b>Connexion</b>${st.telegram.connected ? "🟢" : "⚪"}</div><div><b>Messages envoyés</b>${st.telegram.sent}</div></div>
    ${st.telegram.error ? `<p class="err">${esc(st.telegram.error)}</p>` : ""}<button class="btn" data-act="control" data-a="telegram_test">Envoyer un message de test</button></div>
  <div class="card"><h2>Quantower & données</h2><div class="kv"><div><b>Bridge Quantower</b>${st.quantower.connected ? "🟢 flux reçu" : "⚪ aucun flux"}</div><div><b>Instruments reçus</b>${esc(st.quantower.symbols.join(", ") || "-")}</div>
    <div><b>Calendrier éco.</b>${st.calendar.events} annonces</div></div>${st.calendar.error ? `<p class="muted">Calendrier : ${esc(st.calendar.error)}</p>` : ""}</div>
  <div class="card"><h2>Application</h2><label>Thème<select data-act="theme"><option value="auto" ${theme === "auto" ? "selected" : ""}>Automatique</option><option value="dark" ${theme === "dark" ? "selected" : ""}>Sombre</option><option value="light" ${theme === "light" ? "selected" : ""}>Clair</option></select></label>
    <p class="muted">Installation sur téléphone : menu du navigateur → « Ajouter à l'écran d'accueil » (HTTPS requis).</p><button class="btn" data-act="logout">Se déconnecter</button></div>
  <div class="card"><h2>Modèle IA</h2><div class="kv"><div><b>Version</b>${esc(st.model?.version || "aucune")}</div><div><b>Statut</b>${esc(st.model?.status || "-")}</div></div>${st.model?.warning ? `<p class="warn">${esc(st.model.warning)}</p>` : ""}</div></div>`;
};

PAGES.more = async () => `<h1>Menu</h1><div class="grid">${NAV.map(([k, l, i]) => `<a class="card" href="#/${k}" style="text-decoration:none;color:var(--text)"><b>${i} ${l}</b></a>`).join("")}</div>`;

/* ---------------- actions ---------------- */
async function act(el) {
  const a = el.dataset.act, id = el.dataset.id;
  try {
    if (a === "approve") { if (!confirm("Valider et exécuter ce trade ?")) return; const r = await api(`/api/signals/${id}/approve`, { method: "POST" }); toast(r.message || (r.ok ? "Ordre exécuté" : "Échec")); }
    else if (a === "reject") { const r = await api(`/api/signals/${id}/reject`, { method: "POST" }); toast(r.message); }
    else if (a === "close") { if (!confirm(`Clôturer la position #${id} ?`)) return; const r = await api("/api/control/close", { method: "POST", body: { ticket: Number(id) } }); toast(r.message); }
    else if (a === "control") { const r = await api(`/api/control/${el.dataset.a}`, { method: "POST", body: {} }); toast(r.message); }
    else if (a === "auto") { const r = await api(`/api/control/${el.checked ? "auto_on" : "auto_off"}`, { method: "POST", body: {} }); toast(r.message); }
    else if (a === "kill") { if (!confirm("ARRÊT D'URGENCE : désactiver l'exécution auto, bloquer les entrées et clôturer toutes les positions de l'agent ?")) return; const r = await api("/api/control/emergency_stop", { method: "POST", body: { close_all: true } }); toast(r.message); }
    else if (a === "sym") { S.symbol = id; if (route() !== "market") { location.hash = "#/market"; return; } }
    else if (a === "strat") { await api(`/api/strategies/${id}`, { method: "PUT", body: { enabled: el.checked } }); toast(`Stratégie ${el.checked ? "activée" : "désactivée"}`); }
    else if (a === "learn") { const r = await api("/api/learning/run", { method: "POST" }); toast(r.message); }
    else if (a === "review") { await api("/api/learning/review", { method: "POST" }); toast("Journal analysé"); }
    else if (a === "csv") { exportCsv(); return; }
    else if (a === "logout") { logout(); return; }
    else return;
    await refreshStatus(); show();
  } catch (e) { toast(e.message); }
}

function exportCsv() {
  const rows = S.lastTrades || [];
  if (!rows.length) return toast("Rien à exporter");
  const cols = ["open_time", "close_time", "symbol", "direction", "strategy", "timeframe", "volume", "entry_price", "exit_price", "sl_initial", "tp_initial", "risk_amount", "pnl", "pnl_r", "close_reason", "ticket", "broker"];
  const csv = [cols.join(";"), ...rows.map((r) => cols.map((c) => r[c] ?? "").join(";"))].join("\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "trades.csv"; a.click();
}

document.addEventListener("click", (e) => { const el = e.target.closest("[data-act]"); if (el && !["auto", "strat", "filter", "theme"].includes(el.dataset.act)) { if (el.tagName === "A") e.preventDefault(); act(el); } });
document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset.act === "auto" || el.dataset.act === "strat") act(el);
  else if (el.dataset.act === "filter") { S.histFilter[el.dataset.k] = el.value; show(); }
  else if (el.dataset.act === "theme") { store.set("theme", el.value); applyTheme(); }
});
document.addEventListener("submit", async (e) => {
  if (e.target.id === "riskForm") {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(e.target)].map(([k, v]) => [k, Number(v)]));
    try { await api("/api/risk", { method: "PUT", body }); toast("Limites de risque enregistrées"); show(); } catch (err) { toast(err.message); }
  }
});
$("#killBtn").addEventListener("click", () => act({ dataset: { act: "kill" } }));

/* ---------------- auth & démarrage ---------------- */
function applyTheme() { const t = store.get("theme"); if (t && t !== "auto") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
function logout() { S.token = null; store.set("token", null); $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); clearInterval(S.timer); }
async function enter() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); await refreshStatus(); show(); schedule(); }

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const r = await api("/api/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
    S.token = r.token; store.set("token", r.token); $("#loginErr").textContent = ""; enter();
  } catch (err) { $("#loginErr").textContent = err.message; }
});
window.addEventListener("hashchange", show);
applyTheme();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
S.token = store.get("token");
if (S.token) enter(); else logout();
