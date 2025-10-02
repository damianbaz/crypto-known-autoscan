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
    - precio/variaciones/volumen desde CoinGecko (Pro o público con fallback)
    - TVL (si hay defillama_slug) desde DeFiLlama
    - score 0..100 (momentum precio + volumen + TVL nivel + TVL momentum)
    """
    def _clip(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    cfg = load_config()
    watch = cfg.get("watchlist") or []
    if not watch:
        return []

    # Mapea ids de CoinGecko a símbolos legibles (ticker) y slugs de DeFiLlama
    cg_ids = [w["id"] for w in watch]
    sym_map = {w["id"]: w["name"] for w in watch}
    llama_slugs = {w["id"]: w.get("defillama_slug") for w in watch}

    # --- CoinGecko markets (con fallback automático) ---
    try:
        mkts = _fetch_coingecko_markets(cg_ids)
        print(f"[DEBUG] CoinGecko devolvió {len(mkts)} mercados")
    except requests.HTTPError as e:
        print(f"[WARN] CoinGecko fetch failed: {e}")
        mkts = []

    by_id = {m.get("id"): m for m in mkts if isinstance(m, dict)}

    # --- DeFiLlama TVL snapshots (último y cambios %) ---
    tvl_last: Dict[str, float] = {}
    tvl_7d_chg: Dict[str, float] = {}
    tvl_30d_chg: Dict[str, float] = {}

    for cid, slug in llama_slugs.items():
        if not slug:
            continue
        try:
            lr = requests.get(f"https://api.llama.fi/protocol/{slug}", timeout=20)
            lr.raise_for_status()
            data = lr.json()
            chains = data.get("tvl", [])  # lista de snapshots, cada uno con totalLiquidityUSD
            if not chains:
                continue

            # último valor
            tvl_usd = chains[-1].get("totalLiquidityUSD")
            tvl_last[cid] = tvl_usd or 0.0

            def pct(old, new):
                if not old or old <= 0:
                    return 0.0
                return (new - old) / old

            # cambios % 7d/30d si hay suficientes puntos
            tvl_7d_chg[cid] = pct(chains[-8]["totalLiquidityUSD"], chains[-1]["totalLiquidityUSD"]) if len(chains) >= 8 else 0.0
            tvl_30d_chg[cid] = pct(chains[-31]["totalLiquidityUSD"], chains[-1]["totalLiquidityUSD"]) if len(chains) >= 31 else 0.0
        except Exception as e:
            # si falla, deja 0 y sigue
            print(f"[WARN] DefiLlama fail for {slug}: {e}")
            tvl_last[cid] = tvl_last.get(cid, 0.0)
            tvl_7d_chg[cid] = tvl_7d_chg.get(cid, 0.0)
            tvl_30d_chg[cid] = tvl_30d_chg.get(cid, 0.0)

    # --- construir proyectos con score 0..100 ---
    projects: List[Dict[str, Any]] = []

    # rangos para normalizar volumen/TVL dentro del watchlist
    vols = [(by_id[i].get("total_volume") or 0.0) for i in cg_ids if i in by_id]
    vol_lo, vol_hi = (min(vols) if vols else 0.0), (max(vols) if vols else 1.0)

    tvls = [(tvl_last.get(i) or 0.0) for i in cg_ids]
    tvl_lo, tvl_hi = (min(tvls) if tvls else 0.0), (max(tvls) if tvls else 1.0)

    for cid in cg_ids:
        m = by_id.get(cid)
        if not m:
            continue

        sym = sym_map.get(cid, (m.get("symbol") or "").upper())
        price = m.get("current_price") or 0.0

        # cambios de precio en DECIMALES desde CG (convertir a PUNTOS %)
        chg_24h = (m.get("price_change_percentage_24h_in_currency") or 0.0) / 100.0
        chg_7d  = (m.get("price_change_percentage_7d_in_currency") or 0.0) / 100.0
        chg_30d = (m.get("price_change_percentage_30d_in_currency") or 0.0) / 100.0

        p24 = chg_24h * 100.0
        p7  = chg_7d  * 100.0
        p30 = chg_30d * 100.0

        # limitar outliers para no premiar pumps extremos
        p24c = _clip(p24, -50, 50)
        p7c  = _clip(p7,  -50, 50)
        p30c = _clip(p30, -50, 50)

        # Señal de precio (puntos), sólo positivos (no penaliza bajadas)
        # pesos: 24h 0.35, 7d 0.35, 30d 0.15 (total 0.85)
        p_price_points = 0.35 * p24c + 0.35 * p7c + 0.15 * p30c
        p_price_points = max(0.0, p_price_points)  # solo momentum positivo

        # Max teórico ≈ 50*(0.35+0.35+0.15)=42.5 → normaliza a 0..100
        s_price = (p_price_points / 42.5) * 100.0
        s_price = _clip(s_price, 0.0, 100.0)

        vol_24h = m.get("total_volume") or 0.0
        s_vol = 100.0 * _norm(vol_24h, vol_lo, vol_hi)

        tvl_usd = tvl_last.get(cid, 0.0)
        s_tvl_lvl = 100.0 * _norm(tvl_usd, tvl_lo, tvl_hi)

        tvl_chg7 = tvl_7d_chg.get(cid, 0.0)
        tvl_chg30 = tvl_30d_chg.get(cid, 0.0)
        tvl_mom_pct = ((tvl_chg7 or 0.0) + (tvl_chg30 or 0.0)) / 2.0 * 100.0
        s_tvl_mom = _clip(max(0.0, tvl_mom_pct), 0.0, 100.0)

        # Ponderación final (suma 1.0):
        # precio 0.45, volumen 0.25, TVL nivel 0.15, TVL momentum 0.15
        total = 0.45 * s_price + 0.25 * s_vol + 0.15 * s_tvl_lvl + 0.15 * s_tvl_mom
        total = round(_clip(total, 0.0, 100.0), 1)

        proj = {
            "symbol": sym.upper(),
            "name": (m.get("name") or sym),
            "score": {
                "total": total,
                # sub-scores 0..1 útiles para depurar
                "price_momentum": round((p_price_points / 42.5) if 42.5 else 0.0, 4),
                "tvl_momentum": round(max(0.0, ((tvl_chg7 or 0.0) + (tvl_chg30 or 0.0)) / 2.0), 4),
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
            "sources": ["coingecko"] + (["defillama"] if llama_slugs.get(cid) else []),
        }
        projects.append(proj)

        # debug por símbolo
        print(f"[DEBUG] {proj['symbol']}: score={proj['score']['total']:.1f}, "
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

# -----------------------------
# Main
# -----------------------------
def main():
    cfg = load_config()

    # 1) recolectar universo
    projects_all = collect_projects()

    # 2) diagnóstico (antes de filtrar “oficialmente”)
    diagnostics = diag_counts(projects_all, cfg)

    # 3) filtro oficial (señales fuertes o fallback)
    projects = strong_signals(projects_all, cfg)

    # 4) payload + diagnóstico (NO volver a pisarlo después)
    payload = build_payload(universe="top_200_coingecko_filtered", projects=projects)
    payload["diagnostics"] = diagnostics

    # (debug opcional)
    for p in projects_all:
        print(f"[DEBUG] {p['symbol']}: score={p['score']['total']}, vol={p['metrics']['volume_24h_usd']}, tvl7d={p['metrics']['tvl_chg_7d']}")

    # 5) escribir latest + dated
    write_latest_json(payload)
    write_latest_md(payload)
    write_dated(payload)

    # 6) publicar a docs/
    publish_to_docs()

    # 7) agregados ponderados
    after_publish_weighted(cfg)

if __name__ == "__main__":
    main()
