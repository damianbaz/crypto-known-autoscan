from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any

import yaml  # <-- requiere pyyaml en requirements
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

def _norm(x, lo, hi):
    # normalización simple 0..1 robusta
    if x is None: return 0.0
    if hi == lo: return 0.0
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))

def _fetch_coingecko_markets(cg_ids: list[str]) -> list[dict]:
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    base = "https://pro-api.coingecko.com/api/v3" if api_key else "https://api.coingecko.com/api/v3"
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    params = {
        "vs_currency": "usd",
        "ids": ",".join(cg_ids),
        "order": "market_cap_desc",
        "per_page": len(cg_ids),
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
        "locale": "en",
    }

    url = f"{base}/coins/markets"
    r = requests.get(url, params=params, headers=headers, timeout=30)

    # Fallback automático al público si la pro falla por auth
    if r.status_code in (401, 403):
        base = "https://api.coingecko.com/api/v3"
        url = f"{base}/coins/markets"
        headers.pop("x-cg-pro-api-key", None)
        r = requests.get(url, params=params, headers=headers, timeout=30)

    r.raise_for_status()
    return r.json()

def _fetch_coingecko_markets(cg_ids: list[str]) -> list[dict]:
    api_key = os.getenv("COINGECKO_API_KEY", "").strip()
    base = "https://pro-api.coingecko.com/api/v3" if api_key else "https://api.coingecko.com/api/v3"
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    params = {
        "vs_currency": "usd",
        "ids": ",".join(cg_ids),
        "order": "market_cap_desc",
        "per_page": len(cg_ids),
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
        "locale": "en",
    }

    url = f"{base}/coins/markets"
    r = requests.get(url, params=params, headers=headers, timeout=30)

    # Fallback al público si falla auth en Pro
    if r.status_code in (401, 403):
        base = "https://api.coingecko.com/api/v3"
        url = f"{base}/coins/markets"
        headers.pop("x-cg-pro-api-key", None)
        r = requests.get(url, params=params, headers=headers, timeout=30)

    r.raise_for_status()
    return r.json()
    
def collect_projects() -> List[Dict[str, Any]]:
    """
    Construye proyectos reales desde config.watchlist con:
    - precio/variaciones/volumen desde CoinGecko
    - TVL (si hay defillama_slug) desde DeFiLlama
    - score básico (momentum precio + volumen + TVL + liquidez aproximada)
    """
    cfg = load_config()
    watch = cfg.get("watchlist") or []
    if not watch:
        return []

    # Mapea ids de CoinGecko a símbolos legibles (name aquí es tu ticker)
    cg_ids = [w["id"] for w in watch]
    sym_map = {w["id"]: w["name"] for w in watch}
    llama_slugs = {w["id"]: w.get("defillama_slug") for w in watch}

    # --- CoinGecko markets ---
    # endpoint: /coins/markets?vs_currency=usd&ids=...
    # requiere header 'x-cg-pro-api-key'
    api_key = os.environ.get("COINGECKO_API_KEY", "")
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    url = "https://pro-api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(cg_ids),
        "order": "market_cap_desc",
        "per_page": len(cg_ids),
        "page": 1,
        "price_change_percentage": "24h,7d,30d",
        "locale": "en",
    }
    #r = requests.get(url, params=params, headers=headers, timeout=30)
    #r.raise_for_status()
    #mkts = r.json()
    try:
        mkts = _fetch_coingecko_markets(cg_ids)
        print(f"[DEBUG] CoinGecko devolvió {len(mkts)} mercados")
    except requests.HTTPError as e:
        # Log mínimo y sigue con lista vacía (el reporte saldrá vacío ese día)
        print(f"[WARN] CoinGecko fetch failed: {e}")
        mkts = []
    by_id = {m.get("id"): m for m in mkts if isinstance(m, dict)}

    # index por id
    by_id = {m["id"]: m for m in mkts}

    # --- DeFiLlama TVL snapshots (simple: último y cambios) ---
    # si no hay slug, dejamos TVL en 0
    tvl_last = {}
    tvl_7d_chg = {}
    tvl_30d_chg = {}
    for cid, slug in llama_slugs.items():
        if not slug:
            continue
        try:
            lr = requests.get(f"https://api.llama.fi/protocol/{slug}", timeout=20)
            lr.raise_for_status()
            data = lr.json()
            chains = data.get("tvl", [])
            if not chains:
                continue
            # último valor
            tvl_usd = chains[-1].get("totalLiquidityUSD")
            tvl_last[cid] = tvl_usd or 0.0

            # cambios % 7d/30d (aprox con snapshots si existen)
            def pct(old, new):
                if not old or old <= 0: return 0.0
                return (new - old) / old

            # busca indices 7 y 30 atrás si hay suficientes puntos
            if len(chains) >= 8:
                tvl_7d_chg[cid] = pct(chains[-8]["totalLiquidityUSD"], chains[-1]["totalLiquidityUSD"])
            else:
                tvl_7d_chg[cid] = 0.0
            if len(chains) >= 31:
                tvl_30d_chg[cid] = pct(chains[-31]["totalLiquidityUSD"], chains[-1]["totalLiquidityUSD"])
            else:
                tvl_30d_chg[cid] = 0.0
        except Exception:
            # si falla, deja TVL en 0
            tvl_last[cid] = tvl_last.get(cid, 0.0)
            tvl_7d_chg[cid] = tvl_7d_chg.get(cid, 0.0)
            tvl_30d_chg[cid] = tvl_30d_chg.get(cid, 0.0)

    # --- construir proyectos con métrica y score simple ---
    projects: List[Dict[str, Any]] = []
    # para normalizar, juntamos rangos rápidos
    vols = [ (by_id[i].get("total_volume") or 0.0) for i in cg_ids if i in by_id ]
    vol_lo, vol_hi = (min(vols) if vols else 0.0), (max(vols) if vols else 1.0)
    tvls = [ (tvl_last.get(i) or 0.0) for i in cg_ids ]
    tvl_lo, tvl_hi = (min(tvls) if tvls else 0.0), (max(tvls) if tvls else 1.0)

    for cid in cg_ids:
        m = by_id.get(cid)
        if not m:
            continue
        sym = sym_map.get(cid, (m.get("symbol") or "").upper())
        price = m.get("current_price") or 0.0

        # price changes (% en decimales)
        chg_24h = (m.get("price_change_percentage_24h_in_currency") or 0.0) / 100.0
        chg_7d  = (m.get("price_change_percentage_7d_in_currency") or 0.0) / 100.0
        chg_30d = (m.get("price_change_percentage_30d_in_currency") or 0.0) / 100.0

        vol_24h = m.get("total_volume") or 0.0
        tvl_usd = tvl_last.get(cid, 0.0)
        tvl_chg7 = tvl_7d_chg.get(cid, 0.0)
        tvl_chg30 = tvl_30d_chg.get(cid, 0.0)

        # “liquidez” proxy: market cap rank bajo y volumen alto (muy básico)
        liq_proxy = _norm(vol_24h, vol_lo, vol_hi)

        # score simple 0..100: momentum precio (24h/7d/30d), volumen, TVL, liquidez
        # pesos razonables (ajustables): 24h 0.25, 7d 0.25, 30d 0.15, vol 0.15, tvl nivel 0.10, tvl momentum 0.10
        s_price = max(0.0, 0.25*(chg_24h*100) + 0.25*(chg_7d*100) + 0.15*(chg_30d*100)) / 10.0
        s_vol   = 0.15 * liq_proxy * 10
        s_tvl   = 0.10 * _norm(tvl_usd, tvl_lo, tvl_hi) * 10
        s_tmv   = 0.10 * max(0.0, (tvl_chg7*100 + tvl_chg30*100)/2.0) / 10.0
        total   = max(0.0, min(100.0, s_price + s_vol + s_tvl + s_tmv))

        projects.append({
            "symbol": sym.upper(),
            "name": (m.get("name") or sym),
            "score": {
                "total": round(total, 1),
                "price_momentum": round(max(0.0, (chg_24h+chg_7d+chg_30d)/3.0), 4),
                "tvl_momentum": round(max(0.0, (tvl_chg7 + tvl_chg30)/2.0), 4),
                "volume_momentum": round(liq_proxy, 4),
                "liquidity_quality": round(liq_proxy, 4),
                "holder_concentration": None,  # sin onchain aquí
            },
            "metrics": {
                "price_usd": price,
                "chg_24h": chg_24h,
                "chg_7d": chg_7d,
                "chg_30d": chg_30d,
                "volume_24h_usd": vol_24h,
                "volume_chg_24h": None,   # si quieres, calcula vs ayer guardado
                "tvl_usd": tvl_usd,
                "tvl_chg_7d": tvl_chg7,
                "tvl_chg_30d": tvl_chg30,
                "liq_cex_depth_2pct_usd": None,  # sin orderbook aquí
                "liq_dex_pool_usd": None,
            },
            "risk_flags": [],  # puedes añadir reglas (baja liquidez, etc.)
            "sources": ["coingecko"] + (["defillama"] if llama_slugs.get(cid) else []),
        })

    return projects
# -----------------------------
# Filtro de señales fuertes
# -----------------------------
def strong_signals(projects: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = cfg["run"]
    min_score = float(r.get("min_score", 70))
    min_vol = float(r.get("min_volume_24h_usd", 1_000_000))
    min_tvl_7d = float(r.get("min_tvl_growth_7d", 0.0))
    exclude_stables = bool(r.get("exclude_stables", True))
    stables = set((r.get("stables") or []))

    # filtro base
    filtered = []
    for p in projects:
        sym = (p.get("symbol") or "").upper()
        if exclude_stables and sym in stables:
            continue

        score = (p.get("score") or {}).get("total", 0.0)
        met = p.get("metrics") or {}
        vol_ok = (met.get("volume_24h_usd") or 0) >= min_vol
        tvl_ok = (met.get("tvl_chg_7d") or 0.0) >= min_tvl_7d

        if score >= min_score and vol_ok and tvl_ok:
            filtered.append(p)

    # ordenar por score desc y recortar top_n
    filtered.sort(key=lambda x: (x.get("score") or {}).get("total", 0.0), reverse=True)
    top_n = int(r.get("top_n", 10))
    return filtered[:top_n]

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

# -----------------------------
# Main
# -----------------------------
def main():
    cfg = load_config()

    # 1) recolectar universo
    projects_all = collect_projects()

    for p in projects_all:
        print(f"[DEBUG] {p['symbol']}: score={p['score']['total']}, vol={p['metrics']['volume_24h_usd']}, tvl7d={p['metrics']['tvl_chg_7d']}")

    # 2) filtrar solo señales fuertes (reporte corto)
    projects = strong_signals(projects_all, cfg)

    # 3) construir payload y escribir latest + dated
    payload = build_payload(universe="top_200_coingecko_filtered", projects=projects)
    write_latest_json(payload)
    write_latest_md(payload)
    write_dated(payload)

    # 4) publicar a docs/
    publish_to_docs()

    # 5) generar agregados ponderados (lee desde docs/)
    after_publish_weighted(cfg)

if __name__ == "__main__":
    main()
