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

# -----------------------------
# Colector (demo)
# -----------------------------
def collect_projects() -> List[Dict[str, Any]]:
    # TODO: reemplaza por tu lógica real de fetch + cálculo
    return [
        {
            "symbol": "ABC",
            "name": "Alpha Beta Coin",
            "score": {
                "total": 78.4,
                "price_momentum": 0.66,
                "tvl_momentum": 0.52,
                "volume_momentum": 0.71,
                "liquidity_quality": 0.60,
                "holder_concentration": 0.35,
            },
            "metrics": {
                "price_usd": 2.34,
                "chg_24h": 0.082,
                "chg_7d": 0.215,
                "chg_30d": 0.405,
                "volume_24h_usd": 12_345_678,
                "volume_chg_24h": 0.32,
                "tvl_usd": 4_567_890,
                "tvl_chg_7d": 0.28,
                "tvl_chg_30d": 0.62,
                "liq_cex_depth_2pct_usd": 950_000,
                "liq_dex_pool_usd": 420_000,
            },
            "risk_flags": ["holder_concentration_high(>40%)"],
            "sources": ["coingecko", "defillama"],
        },
        # ... agrega más proyectos reales
    ]

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
