from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import glob
import time
import yaml  # <-- requiere pyyaml en requirements
from zoneinfo import ZoneInfo  # stdlib en Python 3.9+
from writer import (
    build_payload, write_latest_json, write_latest_md,
    write_dated, publish_to_docs, DOCS_DIR
)
from aggregator import make_weights, build_weighted

import os, requests, math

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"

# -----------------------------
# Config
# -----------------------------
DEFAULTS = {
    "run": {
        "top_n": 10,
        "signals_only": True,
        "min_score": 70,
        "min_volume_24h_usd": 1_000_000,
        "min_tvl_growth_7d": 0.0,
        "exclude_stables": True,
        "stables": ["USDT", "USDC", "DAI", "TUSD", "USDP", "FDUSD"],
        "weights_mode": "exp",
        "weights_alpha": 0.8,
        # "weights_fixed": [40,20,10,7,5,4,3,3,2,2,1,1,1,1],
    }
}

def _norm(x, lo, hi):
    # normalización simple 0..1 robusta
    if x is None: return 0.0
    if hi == lo: return 0.0
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))

def _print_stat(label: str, p: Path):
    try:
        if p and p.exists():
            st = p.stat()
            print(f"[STAT] {label}: {p.resolve()} size={st.st_size} mtime={st.st_mtime}")
        else:
            print(f"[STAT] {label}: (no existe)")
    except Exception as e:
        print(f"[STAT] {label}: error stat -> {e}")
        
def _append_discovery_to_md_text(md_text: str, discovery_payload: dict) -> str:
    samp = discovery_payload.get("discovery_sample") or []
    quick = discovery_payload.get("quick_suggestions") or []
    lines = []
    lines.append("\n---\n")
    lines.append("## Discovery & Quick Suggestions\n")
    lines.append(f"**Muestras (top por score, máx 10): {len(samp)}**")
    for i, item in enumerate(samp, 1):
        sym = item.get("symbol", "?")
        sc  = item.get("score", 0)
        vol = item.get("vol", 0)
        lines.append(f"{i}. **{sym}** — score {sc}, vol24h ${vol:,}")
    lines.append("")
    lines.append(f"**Quick suggestions (máx 10): {len(quick)}**")
    for i, q in enumerate(quick, 1):
        act = q.get("action", "?")
        sym = q.get("symbol", "?")
        rsn = q.get("reason", "")
        tpv = q.get("tp_pct", 0) or 0
        slv = q.get("sl_pct", 0) or 0
        lines.append(f"{i}. {act} **{sym}** — {rsn} (TP {int(tpv*100)}%, SL {int(slv*100)}%)")
    return md_text.rstrip() + "\n" + "\n".join(lines) + "\n"

def _find_todays_report_files(today_iso: str | None = None) -> Dict[str, Path]:
    if today_iso:
        md_candidates = sorted(DOCS_DIR.glob(f"report-{today_iso}*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        json_candidates = sorted(DOCS_DIR.glob(f"report-{today_iso}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        # Laxa: cualquier report-YYYY-MM-DD*.md/json, el más reciente
        md_candidates = sorted(DOCS_DIR.glob("report-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        json_candidates = sorted(DOCS_DIR.glob("report-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "md": md_candidates[0] if md_candidates else None,
        "json": json_candidates[0] if json_candidates else None,
    }

def _md_discovery_block(discovery: dict) -> str:
    if not discovery:
        return ""
    samp = discovery.get("discovery_sample") or []
    quick = discovery.get("quick_suggestions") or []
    lines = []
    lines.append("\n---\n")
    lines.append("## Discovery & Quick Suggestions\n")
    lines.append(f"**Muestras (top por score, máx 10): {len(samp)}**")
    for i, it in enumerate(samp, 1):
        sym = it.get("symbol","?")
        sc  = it.get("score", 0)
        vol = it.get("vol", 0)
        lines.append(f"{i}. **{sym}** — score {sc}, vol24h ${vol:,}")
    lines.append("")
    lines.append(f"**Quick suggestions (máx 10): {len(quick)}**")
    for i, q in enumerate(quick, 1):
        act = q.get("action","?")
        sym = q.get("symbol","?")
        rsn = q.get("reason","")
        tp  = int((q.get("tp_pct") or 0)*100)
        sl  = int((q.get("sl_pct") or 0)*100)
        lines.append(f"{i}. {act} **{sym}** — {rsn} (TP {tp}%, SL {sl}%)")
    return "\n".join(lines) + "\n"
    
def _append_discovery_to_latest_and_dated(discovery_payload: dict, cfg: Dict[str, Any]) -> None:
    if not discovery_payload:
        print("[APPEND] discovery vacío; no se modifica nada")
        return

    # --- ALWAYS update latest.*
    latest_md = DOCS_DIR / "latest.md"
    latest_json = DOCS_DIR / "latest.json"

    # latest.md
    try:
        _print_stat("before latest.md", latest_md)
        if latest_md.exists():
            md_text = latest_md.read_text(encoding="utf-8")
            if _has_discovery_section(md_text):
                print("[APPEND] Discovery ya presente en latest.md; no duplico")
            else:
                md_new = _append_discovery_to_md_text(md_text, discovery_payload)
                latest_md.write_text(md_new, encoding="utf-8")
                print("[APPEND] Discovery agregado en latest.md")
            _print_tail("latest.md", latest_md)
    except Exception as e:
        print(f"[WARN] No se pudo actualizar latest.md: {e}")

    # latest.json
    try:
        _print_stat("before latest.json", latest_json)
        if latest_json.exists():
            data = json.loads(latest_json.read_text(encoding="utf-8") or "{}")
            data["discovery"] = discovery_payload
            latest_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[APPEND] Discovery agregado en latest.json")
        else:
            print("[APPEND] WARN: latest.json no existe; salto")
        _print_stat("after  latest.json", latest_json)
    except Exception as e:
        print(f"[WARN] No se pudo actualizar latest.json: {e}")

    # --- Try to update today's dated file (local tz first, then lax fallback)
    tzname = (cfg.get("run", {}) or {}).get("timezone") or "UTC"
    try:
        today_local = datetime.now(ZoneInfo(tzname)).date().isoformat()
    except Exception:
        today_local = datetime.utcnow().date().isoformat()
        print(f"[APPEND] WARN: ZoneInfo({tzname}) falló, uso UTC {today_local}")

    files = _find_todays_report_files(today_local)
    md_path = files["md"]
    json_path = files["json"]

    if not md_path and not json_path:
        # Fallback “laxo”: quizás el escritor usó UTC o un patrón distinto
        print("[APPEND] No encontré reporte fechado por fecha local; pruebo modo laxo (último report-*.md/json)")
        files = _find_todays_report_files(None)  # <— usa tu modo laxo
        md_path = files["md"]
        json_path = files["json"]

    # MD fechado
    if md_path and md_path.exists():
        try:
            _print_stat("before dated.md", md_path)
            md_text = md_path.read_text(encoding="utf-8")
            if _has_discovery_section(md_text):
                print(f"[APPEND] Discovery ya presente en {md_path.name}; no duplico")
            else:
                md_new = _append_discovery_to_md_text(md_text, discovery_payload)
                md_path.write_text(md_new, encoding="utf-8")
                print(f"[APPEND] Discovery agregado en {md_path.name}")
            _print_stat("after  dated.md", md_path)
            _print_tail(md_path.name, md_path)
        except Exception as e:
            print(f"[WARN] No se pudo actualizar {md_path.name}: {e}")

    # JSON fechado
    if json_path and json_path.exists():
        try:
            _print_stat("before dated.json", json_path)
            data = json.loads(json_path.read_text(encoding="utf-8") or "{}")
            data["discovery"] = discovery_payload
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[APPEND] Discovery agregado en {json_path.name}")
            _print_stat("after  dated.json", json_path)
        except Exception as e:
            print(f"[WARN] No se pudo actualizar {json_path.name}: {e}")
            
def _fetch_coingecko_markets(cg_ids: list[str]) -> list[dict]:
    import os, requests
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    base = "https://pro-api.coingecko.com/api/v3" if api_key else "https://api.coingecko.com/api/v3"
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    params = {
        "vs_currency": "usd",
        "ids": ",".join(cg_ids),
        "order": "market_cap_desc",
        "per_page": max(1, len(cg_ids)),
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
        "locale": "en",
    }

    url = f"{base}/coins/markets"
    r = requests.get(url, params=params, headers=headers, timeout=30)

    # Fallback si Pro falla por auth
    if r.status_code in (401, 403):
        base = "https://api.coingecko.com/api/v3"
        url = f"{base}/coins/markets"
        headers.pop("x-cg-pro-api-key", None)
        r = requests.get(url, params=params, headers=headers, timeout=30)

    r.raise_for_status()
    return r.json()

def _append_discovery_to_reports(dated_basename: str, discovery_payload: dict):
    """
    Añade 'Discovery' y 'Quick suggestions' al final de los reportes fechados:
      docs/{dated_basename}.md  y  docs/{dated_basename}.json
    No rompe si faltan archivos.
    """
    if not discovery_payload:
        return

    md_path = DOCS_DIR / f"{dated_basename}.md"
    json_path = DOCS_DIR / f"{dated_basename}.json"

    # ------- MD -------
    try:
        samp = discovery_payload.get("discovery_sample") or []
        quick = discovery_payload.get("quick_suggestions") or []

        lines = []
        lines.append("\n---\n")
        lines.append("## Discovery & Quick Suggestions")
        lines.append("")
        # sample
        lines.append(f"**Muestras (top por score, máx 10): {len(samp)}**")
        for i, item in enumerate(samp, 1):
            sym = item.get("symbol", "?")
            sc  = item.get("score", 0)
            vol = item.get("vol", 0)
            lines.append(f"{i}. **{sym}** — score {sc}, vol24h ${vol:,}")

        lines.append("")
        # quick
        lines.append(f"**Quick suggestions (máx 10): {len(quick)}**")
        for i, q in enumerate(quick, 1):
            act = q.get("action", "?")
            sym = q.get("symbol", "?")
            rsn = q.get("reason", "")
            tp  = int((q.get("tp_pct") or 0) * 100)
            sl  = int((q.get("sl_pct") or 0) * 100)
            lines.append(f"{i}. {act} **{sym}** — {rsn} (TP {tp}%, SL {sl}%)")

        if md_path.exists():
            md_path.write_text(md_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] append discovery to MD failed: {e}")

    # ------- JSON -------
    try:
        if json_path.exists():
            import json as _json
            data = _json.loads(json_path.read_text(encoding="utf-8") or "{}")
            data["discovery"] = discovery_payload
            json_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] append discovery to JSON failed: {e}")
        
def _write_discovery_artifacts(discovery_payload: dict):
    """Escribe discovery a archivos dedicados y deja un resumen en logs."""
    if not discovery_payload:
        print("[DISCOVERY] vacío")
        return
    from datetime import datetime
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # JSON
    (DOCS_DIR / "discovery-latest.json").write_text(
        json.dumps({
            "timestamp_utc": ts,
            **discovery_payload
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # MD
    lines = []
    lines.append(f"# Discovery — {ts}")
    samp = discovery_payload.get("discovery_sample") or []
    lines.append(f"\n**Muestras (top por score, máx 10): {len(samp)}**\n")
    for i, item in enumerate(samp, 1):
        lines.append(f"{i}. **{item['symbol']}** — score {item['score']}, vol24h ${item['vol']:,}")

    quick = discovery_payload.get("quick_suggestions") or []
    lines.append(f"\n**Quick suggestions (máx 10): {len(quick)}**\n")
    for i, q in enumerate(quick, 1):
        tp_pct = q.get("tp_pct", 0) or 0
        sl_pct = q.get("sl_pct", 0) or 0
        lines.append(
            f"{i}. {q.get('action','?')} **{q.get('symbol','?')}** — {q.get('reason','')}"
            f" (TP {int(tp_pct*100)}%, SL {int(sl_pct*100)}%)"
        )

    (DOCS_DIR / "discovery-latest.md").write_text("\n".join(lines), encoding="utf-8")

    # Logs útiles en CI
    print(f"[DISCOVERY] sample={len(samp)} quick={len(quick)}")
    if samp:
        tops = ", ".join([s["symbol"] for s in samp[:5]])
        print(f"[DISCOVERY] top sample: {tops}")
    if quick:
        qtops = ", ".join([f"{q['action']}:{q['symbol']}" for q in quick[:5]])
        print(f"[DISCOVERY] quick: {qtops}")
        
def _fetch_coingecko_top_by_volume(limit: int = 100) -> list[dict]:
    """Top por volumen (usd) sin 'ids' para discovery."""
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    base = "https://pro-api.coingecko.com/api/v3" if api_key else "https://api.coingecko.com/api/v3"
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    url = f"{base}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "volume_desc",
        "per_page": min(250, limit),  # CG máx 250 por página; ajusta si haces paginado
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
        "locale": "en",
    }
    r = requests.get(url, params=params, headers=headers, timeout=30)
    if r.status_code in (401, 403):
        base = "https://api.coingecko.com/api/v3"
        url = f"{base}/coins/markets"
        headers.pop("x-cg-pro-api-key", None)
        r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()

def _fetch_coinbase_usd_bases() -> set[str]:
    """
    Devuelve símbolos (base) que tienen par -USD en Coinbase Exchange.
    Ej: 'BTC', 'SOL', 'DOGE' si existen BTC-USD, SOL-USD, DOGE-USD.
    """
    try:
        url = "https://api.exchange.coinbase.com/products"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        products = r.json()
    except Exception:
        return set()
    bases = set()
    for p in products:
        try:
            base = (p.get("base_currency") or "").upper()
            quote = (p.get("quote_currency") or "").upper()
            if quote == "USD" and base:
                bases.add(base)
        except Exception:
            continue
    return bases

def build_projects_from_markets(markets: list[dict],
                                llama_slugs_map: dict[str, str] | None = None) -> list[dict]:
    """
    Construye objetos 'project' tipo discovery desde CoinGecko markets.
    Si provees llama_slugs_map, intenta TVL; si no, TVL=0 y no bloquea.
    """
    import requests

    llama_slugs_map = llama_slugs_map or {}

    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    def _norm(x, lo, hi):
        if x is None: return 0.0
        if hi == lo: return 0.0
        v = (x - lo) / (hi - lo)
        return max(0.0, min(1.0, v))

    by_id = {m.get("id"): m for m in markets if isinstance(m, dict)}
    cg_ids = list(by_id.keys())

    # --- TVL de DeFiLlama si hay slug asignado ---
    tvl_last, tvl_7d_chg, tvl_30d_chg = {}, {}, {}
    for cid in cg_ids:
        slug = llama_slugs_map.get(cid)
        if not slug:
            continue
        try:
            lr = requests.get(f"https://api.llama.fi/protocol/{slug}", timeout=20)
            lr.raise_for_status()
            data = lr.json()
            snaps = data.get("tvl", []) or []
            if not snaps:
                continue
            tvl_last[cid] = _llama_current_tvl(slug)
            def pct(old, new):
                if not old or old <= 0: return 0.0
                return (new - old) / old
            tvl_7d_chg[cid]  = pct(snaps[-8]["totalLiquidityUSD"],  snaps[-1]["totalLiquidityUSD"])  if len(snaps) >= 8  else 0.0
            tvl_30d_chg[cid] = pct(snaps[-31]["totalLiquidityUSD"], snaps[-1]["totalLiquidityUSD"]) if len(snaps) >= 31 else 0.0
        except Exception as e:
            print(f"[WARN] DefiLlama fail for {slug}: {e}")

    # Normalizaciones (dentro del set)
    vols = [(by_id[i].get("total_volume") or 0.0) for i in cg_ids]
    vol_lo, vol_hi = (min(vols) if vols else 0.0), (max(vols) if vols else 1.0)
    tvls = [(tvl_last.get(i) or 0.0) for i in cg_ids]
    tvl_lo, tvl_hi = (min(tvls) if tvls else 0.0), (max(tvls) if tvls else 1.0)

    projects = []
    for cid in cg_ids:
        m = by_id[cid]
        sym = (m.get("symbol") or "").upper()
        name = m.get("name") or sym
        price = m.get("current_price") or 0.0

        chg_24h = (m.get("price_change_percentage_24h_in_currency") or 0.0) / 100.0
        chg_7d  = (m.get("price_change_percentage_7d_in_currency")  or 0.0) / 100.0
        chg_30d = (m.get("price_change_percentage_30d_in_currency") or 0.0) / 100.0

        p24c = _clip(chg_24h*100.0, -50, 50)
        p7c  = _clip(chg_7d *100.0, -50, 50)
        p30c = _clip(chg_30d*100.0, -50, 50)

        p_price_points = 0.44*p24c + 0.31*p7c + 0.10*p30c
        p_price_points = max(-5.0, p_price_points)  # or remove the floor entirely
        s_price = _clip((p_price_points/42.5)*100.0, 0.0, 100.0)

        vol_24h = m.get("total_volume") or 0.0
        s_vol = 100.0 * _norm(vol_24h, vol_lo, vol_hi)

        tvl_usd = tvl_last.get(cid, 0.0)
        s_tvl_lvl = 100.0 * _norm(tvl_usd, tvl_lo, tvl_hi)
        tvl_chg7 = tvl_7d_chg.get(cid, 0.0)
        tvl_chg30 = tvl_30d_chg.get(cid, 0.0)
        tvl_mom_pct = ((tvl_chg7 or 0.0) + (tvl_chg30 or 0.0))/2.0 * 100.0
        s_tvl_mom = _clip(max(0.0, tvl_mom_pct), 0.0, 100.0)

        total = 0.60*s_price + 0.07*s_vol + 0.16*s_tvl_lvl + 0.17*s_tvl_mom
        total = round(_clip(total, 0.0, 100.0), 1)

        projects.append({
            "symbol": sym,
            "name": name,
            "score": {
                "total": total,
                "price_momentum": round((p_price_points/42.5) if 42.5 else 0.0, 4),
                "tvl_momentum": round(max(0.0, ((tvl_chg7 or 0.0)+(tvl_chg30 or 0.0))/2.0), 4),
                "volume_momentum": round(_norm(vol_24h, vol_lo, vol_hi), 4),
                "liquidity_quality": round(_norm(vol_24h, vol_lo, vol_hi), 4),
                "holder_concentration": None,
            },
            "metrics": {
                "price_usd": price,
                "chg_24h": chg_24h,
                "chg_7d": chg_7d,
                "chg_30d": chg_30d,
                "volume_24h_usd": vol_24h,
                "volume_chg_24h": None,
                "tvl_usd": tvl_usd,
                "tvl_chg_7d": tvl_chg7,
                "tvl_chg_30d": tvl_chg30,
                "liq_cex_depth_2pct_usd": None,
                "liq_dex_pool_usd": None,
            },
            "risk_flags": [],
            "sources": ["coingecko"] + (["defillama"] if llama_slugs_map.get(cid) else []),
            "origin": "discovery",
            "cg_id": cid,
        })
    return projects

def build_quick_suggestions(portfolio_symbols: set[str],
                            projects: list[dict],
                            cfg_run: dict) -> list[dict]:
    """
    Genera 'comprar/vender unos pocos dólares' para discovery,
    siguiendo reglas de quick_trade. Evita parsear 'reason'.
    Ordena por score numérico seguro.
    """
    qt = (cfg_run or {}).get("quick_trade", {}) or {}
    min_score = float(qt.get("buy_score_min", (cfg_run or {}).get("min_score", 25)))
    min_vol = float((cfg_run or {}).get("min_volume_24h_usd", 10_000_000))
    chg24_min = float(qt.get("buy_chg_24h_min", 0.0))
    chg7_min  = float(qt.get("buy_chg_7d_min", 0.0))
    sell_score_max = float(qt.get("sell_score_max", 10))
    tp = float(qt.get("take_profit_pct", 0.20))
    sl = float(qt.get("stop_pct", 0.10))

    # Índices por símbolo para acceso O(1)
    sym_to_score = {}
    sym_to_metrics = {}
    for p in projects or []:
        sym = (p.get("symbol") or "").upper()
        sym_to_score[sym] = ((p.get("score") or {}).get("total") or 0.0)
        sym_to_metrics[sym] = p.get("metrics") or {}

    buys = []
    for p in projects or []:
        sym = (p.get("symbol") or "").upper()
        score = sym_to_score.get(sym, 0.0)
        met = sym_to_metrics.get(sym, {})
        vol = met.get("volume_24h_usd") or 0.0
        ch24 = met.get("chg_24h") or 0.0
        ch7  = met.get("chg_7d") or 0.0

        if score >= min_score and vol >= min_vol and ch24 >= chg24_min and ch7 >= chg7_min:
            buys.append({
                "action": "BUY_SMALL",
                "symbol": sym,
                "score": score,  # ← clave numérica para ordenar sin parsear texto
                "reason": f"score {score:.1f}, 24h {ch24*100:+.1f}%, 7d {ch7*100:+.1f}%",
                "tp_pct": tp,
                "sl_pct": sl,
                "origin": p.get("origin"),
            })

    sells = []
    have = {s.upper() for s in (portfolio_symbols or set())}
    for p in projects or []:
        sym = (p.get("symbol") or "").upper()
        score = sym_to_score.get(sym, 100.0)
        if sym in have and score <= sell_score_max and (p.get("origin") != "discovery"):
            sells.append({
                "action": "SELL_SMALL",
                "symbol": sym,
                "score": score,  # por consistencia
                "reason": f"score cayó a {score:.1f} (≤ {sell_score_max})",
                "tp_pct": tp,          # ← añade estos dos
                "sl_pct": sl,          # ← añade estos dos
                "origin": p.get("origin"),
            })

    # Orden estable y segura por score desc
    buys.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    sells.sort(key=lambda x: x.get("score", 0.0))  # si quieres priorizar los más hundidos

    out = (buys + sells)[:10]
    return out
                                
def collect_projects() -> List[Dict[str, Any]]:
    """
    Construye proyectos desde config.watchlist con:
      - precio/variaciones/volumen desde CoinGecko (Pro o público con fallback)
      - TVL (si hay defillama_slug) desde DeFiLlama (tolerante a fallos/timeout)
      - score 0..100 (price momentum + volumen + TVL nivel + TVL momentum)

    Notas de robustez:
      - Si CG no devuelve algún id del watchlist, se ignora sin romper.
      - Si DeFiLlama falla o devuelve 400/timeout para algún slug, TVL=0 (no rompe).
      - Normalizaciones de volumen/TVL se hacen sobre lo efectivamente recibido.
    """
    import requests

    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    def _norm(x, lo, hi):
        if x is None:
            return 0.0
        if hi == lo:
            return 0.0
        v = (x - lo) / (hi - lo)
        return max(0.0, min(1.0, v))

    cfg = load_config()
    watch = cfg.get("watchlist") or []
    if not watch:
        print("[WARN] watchlist vacío en config.yaml")
        return []

    # Mapea ids del watchlist y slugs (solo para TVL)
    cg_ids: List[str] = []
    sym_map: Dict[str, str] = {}
    llama_slugs: Dict[str, str | None] = {}

    for w in watch:
        cid = (w.get("id") or "").strip()
        if not cid:
            continue
        cg_ids.append(cid)
        # Usa 'name' del YAML (ticker legible) o el que venga de CG si falta
        sym_map[cid] = (w.get("name") or "").strip() or cid.upper()
        slug = w.get("defillama_slug")
        llama_slugs[cid] = slug if (slug and str(slug).strip()) else None

    # --- CoinGecko markets (con fallback automático) ---
    mkts: List[dict] = []
    try:
        if cg_ids:
            mkts = _fetch_coingecko_markets(cg_ids)
            print(f"[DEBUG] CoinGecko devolvió {len(mkts)} mercados de {len(cg_ids)} solicitados")
    except requests.HTTPError as e:
        print(f"[WARN] CoinGecko fetch failed: {e}; continuo con mkts vacíos")
        mkts = []
    except Exception as e:
        print(f"[WARN] CoinGecko error inesperado: {e}; continuo con mkts vacíos")
        mkts = []

    by_id = {m.get("id"): m for m in mkts if isinstance(m, dict) and m.get("id")}

    # Loguea ids faltantes (no rompe)
    missing = [cid for cid in cg_ids if cid not in by_id]
    if missing:
        print(f"[WARN] CG no devolvió {len(missing)} ids del watchlist: {missing[:10]}{' ...' if len(missing)>10 else ''}")

    # --- DeFiLlama TVL snapshots (último y cambios %) ---
    tvl_last: Dict[str, float] = {}
    tvl_7d_chg: Dict[str, float] = {}
    tvl_30d_chg: Dict[str, float] = {}

    def _pct(old, new):
        try:
            if old and old > 0:
                return (new - old) / old
        except Exception:
            pass
        return 0.0

    for cid, slug in llama_slugs.items():
        if not slug:
            continue
        try:
            lr = requests.get(f"https://api.llama.fi/protocol/{slug}", timeout=20)
            lr.raise_for_status()
            data = lr.json()
            snaps = data.get("tvl", []) or []
            if not snaps:
                # Sin serie -> TVL=0
                continue
            last = snaps[-1].get("totalLiquidityUSD")
            tvl_last[cid] = last or 0.0
            if len(snaps) >= 8 and snaps[-8].get("totalLiquidityUSD") is not None:
                tvl_7d_chg[cid] = _pct(snaps[-8]["totalLiquidityUSD"], snaps[-1]["totalLiquidityUSD"])
            else:
                tvl_7d_chg[cid] = 0.0
            if len(snaps) >= 31 and snaps[-31].get("totalLiquidityUSD") is not None:
                tvl_30d_chg[cid] = _pct(snaps[-31]["totalLiquidityUSD"], snaps[-1]["totalLiquidityUSD"])
            else:
                tvl_30d_chg[cid] = 0.0
        except requests.HTTPError as e:
            print(f"[WARN] DefiLlama fail for {slug}: {e}")
        except requests.Timeout:
            print(f"[WARN] DefiLlama timeout para {slug}")
        except Exception as e:
            print(f"[WARN] DefiLlama error inesperado para {slug}: {e}")

    # --- construir proyectos con score 0..100 ---
    projects: List[Dict[str, Any]] = []

    # rangos para normalizar volumen/TVL dentro de lo recibido
    vols = [(by_id[i].get("total_volume") or 0.0) for i in by_id]
    vol_lo, vol_hi = (min(vols) if vols else 0.0), (max(vols) if vols else 1.0)

    tvls = [(tvl_last.get(i) or 0.0) for i in cg_ids]
    tvl_lo, tvl_hi = (min(tvls) if tvls else 0.0), (max(tvls) if tvls else 1.0)

    for cid in cg_ids:
        m = by_id.get(cid)
        if not m:
            # Este id no vino en CoinGecko -> lo saltamos sin romper
            continue

        # símbolo legible preferido desde YAML; si no, de CG
        sym = sym_map.get(cid, (m.get("symbol") or "").upper()).upper()
        name = m.get("name") or sym
        price = m.get("current_price") or 0.0

        # CoinGecko entrega porcentajes ya en %, los pasamos a fracción para métricas
        chg_24h = (m.get("price_change_percentage_24h_in_currency") or 0.0) / 100.0
        chg_7d  = (m.get("price_change_percentage_7d_in_currency") or 0.0) / 100.0
        chg_30d = (m.get("price_change_percentage_30d_in_currency") or 0.0) / 100.0

        # puntos % acotados para evitar pumps extremos + pesos internos (coinciden con tu lógica)
        p24c = _clip(chg_24h * 100.0, -50, 50)
        p7c  = _clip(chg_7d  * 100.0, -50, 50)
        p30c = _clip(chg_30d * 100.0, -50, 50)

        p_price_points = 0.44 * p24c + 0.31 * p7c + 0.10 * p30c
        p_price_points = max(0.0, p_price_points)  # no penaliza bajadas

        # normalización a 0..100 (máx teórico ≈ 42.5)
        s_price = _clip((p_price_points / 42.5) * 100.0, 0.0, 100.0)

        vol_24h = m.get("total_volume") or 0.0
        s_vol = 100.0 * _norm(vol_24h, vol_lo, vol_hi)

        tvl_usd = tvl_last.get(cid, 0.0)
        s_tvl_lvl = 100.0 * _norm(tvl_usd, tvl_lo, tvl_hi)

        t7 = tvl_7d_chg.get(cid, 0.0)
        t30 = tvl_30d_chg.get(cid, 0.0)
        tvl_mom_pct = ((t7 or 0.0) + (t30 or 0.0)) / 2.0 * 100.0
        s_tvl_mom = _clip(max(0.0, tvl_mom_pct), 0.0, 100.0)

        # Ponderación final (tu mezcla “CAMBIADA”)
        total = 0.60 * s_price + 0.07 * s_vol + 0.16 * s_tvl_lvl + 0.17 * s_tvl_mom
        total = round(_clip(total, 0.0, 100.0), 1)

        proj = {
            "symbol": sym,
            "name": name,
            "score": {
                "total": total,
                "price_momentum": round((p_price_points / 42.5) if 42.5 else 0.0, 4),  # 0..1 aprox
                "tvl_momentum": round(max(0.0, ((t7 or 0.0) + (t30 or 0.0)) / 2.0), 4),  # fracción 0..1
                "volume_momentum": round(_norm(vol_24h, vol_lo, vol_hi), 4),
                "liquidity_quality": round(_norm(vol_24h, vol_lo, vol_hi), 4),
                "holder_concentration": None,
            },
            "metrics": {
                "price_usd": price,
                "chg_24h": chg_24h,
                "chg_7d": chg_7d,
                "chg_30d": chg_30d,
                "volume_24h_usd": vol_24h,
                "volume_chg_24h": None,
                "tvl_usd": tvl_usd,
                "tvl_chg_7d": t7,
                "tvl_chg_30d": t30,
                "liq_cex_depth_2pct_usd": None,
                "liq_dex_pool_usd": None,
            },
            "risk_flags": [],
            "sources": ["coingecko"] + (["defillama"] if llama_slugs.get(cid) else []),
        }
        projects.append(proj)

        # debug resumido por símbolo
        print(f"[DEBUG] {sym}: score={proj['score']['total']:.1f}, "
              f"s_price={s_price:.1f}, s_vol={s_vol:.1f}, s_tvl_lvl={s_tvl_lvl:.1f}, s_tvl_mom={s_tvl_mom:.1f}")

    return projects

# -----------------------------
# Filtro de señales fuertes
# -----------------------------
def strong_signals(projects, cfg):
    r = cfg["run"]
    min_score = float(r.get("min_score", 70))
    min_vol = float(r.get("min_volume_24h_usd", 1_000_000))
    min_tvl_7d = float(r.get("min_tvl_growth_7d", 0.0))
    exclude_stables = bool(r.get("exclude_stables", True))
    stables = set((r.get("stables") or []))
    top_n = int(r.get("top_n", 10))

    base = []
    for p in projects:
        sym = (p.get("symbol") or "").upper()
        if exclude_stables and sym in stables:
            continue
        met = p.get("metrics") or {}
        if (met.get("volume_24h_usd") or 0) < min_vol:
            continue
        base.append(p)

    # Filtro fuerte por score/TVL
    filtered = [p for p in base
                if (p.get("score") or {}).get("total", 0) >= min_score
                and (p.get("metrics") or {}).get("tvl_chg_7d", 0.0) >= min_tvl_7d]

    filtered.sort(key=lambda x: (x.get("score") or {}).get("total", 0.0), reverse=True)
    if filtered:
        return filtered[:top_n]

    # Fallback: si nadie pasó min_score/min_tvl, devuelve Top-N por score (con volumen y sin stables)
    base.sort(key=lambda x: (x.get("score") or {}).get("total", 0.0), reverse=True)
    return base[:top_n]

def diag_counts(projects_all: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    r = cfg["run"]
    min_score = float(r.get("min_score", 70))
    min_vol = float(r.get("min_volume_24h_usd", 1_000_000))
    min_tvl_7d = float(r.get("min_tvl_growth_7d", 0.0))
    exclude_stables = bool(r.get("exclude_stables", True))
    stables = set((r.get("stables") or []))
    top_n = int(r.get("top_n", 10))

    total = len(projects_all)

    # 1) Excluir stables
    no_stables = []
    excl_stables = 0
    for p in projects_all:
        sym = (p.get("symbol") or "").upper()
        if exclude_stables and sym in stables:
            excl_stables += 1
        else:
            no_stables.append(p)

    # 2) Excluir por volumen mínimo
    vol_ok = []
    below_vol = 0
    for p in no_stables:
        v = (p.get("metrics") or {}).get("volume_24h_usd") or 0
        if v >= min_vol:
            vol_ok.append(p)
        else:
            below_vol += 1

    # 3) Filtro “fuerte” por score y TVL 7d
    strong = []
    below_score = 0
    below_tvl = 0
    for p in vol_ok:
        s = (p.get("score") or {}).get("total", 0.0)
        t = (p.get("metrics") or {}).get("tvl_chg_7d", 0.0)
        if s < min_score:
            below_score += 1
            continue
        if t < min_tvl_7d:
            below_tvl += 1
            continue
        strong.append(p)

    # 4) Ordenar por score y aplicar top_n
    strong_sorted = sorted(strong, key=lambda x: (x.get("score") or {}).get("total", 0.0), reverse=True)
    fallback_used = False
    if not strong_sorted:
        # fallback: Top-N por score desde vol_ok
        fallback_used = True
        strong_sorted = sorted(vol_ok, key=lambda x: (x.get("score") or {}).get("total", 0.0), reverse=True)

    returned = strong_sorted[:top_n]

    return {
        "params": {
            "min_score": min_score,
            "min_volume_24h_usd": min_vol,
            "min_tvl_growth_7d": min_tvl_7d,
            "top_n": top_n,
            "exclude_stables": exclude_stables,
            "stables": sorted(list(stables)),
        },
        "counts": {
            "total_fetched": total,
            "excluded_stables": excl_stables,
            "below_min_volume": below_vol,
            "below_min_score": below_score,
            "below_min_tvl_7d": below_tvl,
            "passed_strong": len(strong),
            "top_returned": len(returned),
        },
        "fallback_used": fallback_used,
        # opcional: incluye símbolos devueltos (útil al depurar)
        "returned_symbols": [ (p.get("symbol"), (p.get("score") or {}).get("total", 0)) for p in returned ],
    }

# -----------------------------
# Agregado ponderado (14d)
# -----------------------------
def after_publish_weighted(cfg: Dict[str, Any] | None = None):
    cfg = cfg or {}
    r = cfg.get("run", {})
    mode = r.get("weights_mode", "exp")
    alpha = float(r.get("weights_alpha", 0.8))
    fixed = r.get("weights_fixed")

    weights = make_weights(mode=mode, alpha=alpha, fixed=fixed, n=14)
    agg = build_weighted(n=14, weights=weights)

    # JSON
    (DOCS_DIR / "weighted-14d.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # MD (opcional)
    lines = []
    lines.append(f"# Weighted Top (14d) — {agg['dates'][-1] if agg['dates'] else ''}")
    lines.append(f"Pesos usados (más reciente primero): {weights}")
    items = sorted(
        agg["symbols"].items(),
        key=lambda kv: (kv[1].get("weighted_score_14d") or 0),
        reverse=True
    )[:10]
    for i, (sym, s) in enumerate(items, 1):
        lines.append(f"{i}. **{sym}** ({s['name']}) — wScore: {s['weighted_score_14d']}, días: {s['days_present']}")
    (DOCS_DIR / "weighted-14d.md").write_text("\n".join(lines), encoding="utf-8")

def _has_discovery_section(text: str) -> bool:
    return "## Discovery & Quick Suggestions" in (text or "")

def _print_tail(label: str, p: Path, n: int = 40):
    try:
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            tail = "\n".join(lines[-n:])
            print(f"[TAIL] {label} (últimas {n} líneas):\n{tail}\n---")
    except Exception as e:
        print(f"[TAIL] {label}: error -> {e}")
        
def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}
    # merge mínimos
    run = {**DEFAULTS["run"], **(cfg.get("run") or {})}
    cfg["run"] = run
    return cfg

def _llama_current_tvl(slug: str) -> float:
    try:
        r = requests.get(f"https://api.llama.fi/protocol/{slug}", timeout=20)
        r.raise_for_status()
        d = r.json()
        # Preferred: explicit per-chain dictionary
        if isinstance(d.get("currentChainTvls"), dict):
            return float(sum((v or 0.0) for v in d["currentChainTvls"].values()))
        # Fallback: last entry of 'tvl' timeseries (array of dicts with 'totalLiquidityUSD')
        snaps = d.get("tvl") or []
        if snaps and isinstance(snaps[-1], dict) and "totalLiquidityUSD" in snaps[-1]:
            return float(snaps[-1]["totalLiquidityUSD"] or 0.0)
    except Exception as e:
        print(f"[WARN] DefiLlama parse for {slug}: {e}")
    return 0.0
    
# -----------------------------
# Main
# -----------------------------
def main():
    cfg = load_config()
    print(f"[INFO] DOCS_DIR -> {DOCS_DIR.resolve()}")

    # 1) recolectar universo base (watchlist)
    projects_all = collect_projects()

    # 2) diagnóstico y filtro oficial (usa todo lo ya recolectado)
    diagnostics = diag_counts(projects_all, cfg)
    projects = strong_signals(projects_all, cfg)

    # 3) payload listo
    payload = build_payload(universe="top_200_coingecko_filtered", projects=projects)
    payload["diagnostics"] = diagnostics

    # === DISCOVERY: inicializa con llaves vacías ===
    r = cfg.get("run", {}) or {}
    discovery_payload: Dict[str, Any] = {
        "discovery_sample": [],
        "quick_suggestions": []
    }

    if r.get("discovery_enabled", False):
        try:
            limit = int(r.get("discovery_limit", 100))
            min_vol_disc = float(r.get("discovery_min_volume_usd", 50_000_000))
            require_cb = bool(r.get("discovery_require_coinbase_usd", True))
            exclude_watchlist = bool(r.get("discovery_exclude_watchlist", True))

            cg_top = _fetch_coingecko_top_by_volume(limit=limit)

            # Excluir stables
            stables = {s.upper() for s in (r.get("stables") or [])}
            cg_top = [
                m for m in cg_top
                if (m.get("total_volume") or 0.0) >= min_vol_disc
                and (m.get("symbol") or "").upper() not in stables
            ]

            # Requerir par -USD en Coinbase si corresponde
            if require_cb:
                cb_bases = {b.upper() for b in _fetch_coinbase_usd_bases()}
                print(f"[DISCOVERY] coinbase USD bases: {len(cb_bases)}")

                # Before filtering
                syms_before = { (m.get("symbol") or "").upper() for m in cg_top }
                cg_top = [m for m in cg_top if (m.get("symbol") or "").upper() in cb_bases]
                syms_after = { (m.get("symbol") or "").upper() for m in cg_top }
                dropped = sorted(syms_before - syms_after)
                print(f"[DISCOVERY] filtered-out (no USD pair on CB): {dropped[:20]}{' ...' if len(dropped)>20 else ''}")

            # Evitar duplicados con watchlist si aplica
            wl_syms = {(p.get("symbol") or "").upper() for p in projects_all}
            if exclude_watchlist:
                cg_top = [m for m in cg_top if (m.get("symbol") or "").upper() not in wl_syms]

            # Deduplicado interno por símbolo
            seen = set()
            cg_top_dedup = []
            for m in cg_top:
                sym = (m.get("symbol") or "").upper()
                if sym in seen:
                    continue
                seen.add(sym)
                cg_top_dedup.append(m)
            cg_top = cg_top_dedup

            # Construir proyectos discovery
            projects_disc = build_projects_from_markets(cg_top, llama_slugs_map={})

            # Quick suggestions
            portfolio_syms = {(p.get("symbol") or "").upper() for p in projects_all}
            quick = build_quick_suggestions(portfolio_syms, projects_disc, r)

            discovery_payload = {
                "discovery_sample": sorted(
                    [
                        {
                            "symbol": p["symbol"],
                            "score": (p.get("score") or {}).get("total", 0.0),
                            "vol": (p.get("metrics") or {}).get("volume_24h_usd", 0.0),
                        }
                        for p in projects_disc
                    ],
                    key=lambda x: x["score"],
                    reverse=True,
                )[:10],
                "quick_suggestions": quick,
            }
        except Exception as e:
            print(f"[WARN] discovery failed: {e}")

    # 4) **Ahora** incorpora discovery al payload y escribe artefactos canónicos
    payload["discovery"] = discovery_payload
    write_latest_json(payload)
    write_latest_md(payload)
    write_dated(payload)
    _write_discovery_artifacts(discovery_payload)  # opcional: archivos dedicados
    # _append_discovery_to_latest_and_dated(discovery_payload, cfg)

    # 6) agregados ponderados (escriben archivos en docs/)
    after_publish_weighted(cfg)

    # 7) **AHORA SÍ**: publicar TODO al repo (incluye los append recién hechos)
    publish_to_docs()

    # Log útil
    print(
        f"[DONE] discovery_sample={len(discovery_payload.get('discovery_sample', []))} "
        f"quick={len(discovery_payload.get('quick_suggestions', []))}"
    )

if __name__ == "__main__":
    main()
