# main_known.py (fragmento ejemplo)
from writer import build_payload, write_latest_json, write_latest_md, write_dated, publish_to_docs
from aggregator import make_weights, build_weighted
from writer import DOCS_DIR

def collect_projects() -> list[dict]:
    # TODO: tu lógica de recolección/calculo métrico.
    # Ejemplo de estructura mínima para 3 proyectos:
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
                "holder_concentration": 0.35
            },
            "metrics": {
                "price_usd": 2.34,
                "chg_24h": 0.082,
                "chg_7d": 0.215,
                "chg_30d": 0.405,
                "volume_24h_usd": 12345678,
                "volume_chg_24h": 0.32,
                "tvl_usd": 4567890,
                "tvl_chg_7d": 0.28,
                "tvl_chg_30d": 0.62,
                "liq_cex_depth_2pct_usd": 950000,
                "liq_dex_pool_usd": 420000
            },
            "risk_flags": ["holder_concentration_high(>40%)"],
            "sources": ["coingecko", "defillama"]
        },
        # ... más proyectos
    ]

def after_publish_weighted(config):
    mode = config.get("weights_mode", "exp")
    alpha = float(config.get("weights_alpha", 0.8))
    fixed = config.get("weights_fixed")
    weights = make_weights(mode=mode, alpha=alpha, fixed=fixed, n=14)
    agg = build_weighted(n=14, weights=weights)

    # guarda JSON
    (DOCS_DIR / "weighted-14d.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # opcional: también MD
    lines = []
    lines.append(f"# Weighted Top (14d) — {agg['dates'][-1] if agg['dates'] else ''}")
    lines.append(f"Pesos usados: {weights} (más reciente primero)")
    # ordenar por weighted_score_14d desc
    items = sorted(
        agg["symbols"].items(),
        key=lambda kv: (kv[1]["weighted_score_14d"] or 0),
        reverse=True
    )[:10]
    for i, (sym, s) in enumerate(items, 1):
        lines.append(f"{i}. **{sym}** ({s['name']}) — wScore: {s['weighted_score_14d']}, días presentes: {s['days_present']}")
    (DOCS_DIR / "weighted-14d.md").write_text("\n".join(lines), encoding="utf-8")
    
def main():
    projects = collect_projects()  # tu lógica
    payload = build_payload(universe="top_200_coingecko_filtered", projects=projects)

    # latest
    write_latest_json(payload)
    write_latest_md(payload)

    # histórico del día
    write_dated(payload)

    # copiar a docs/
    publish_to_docs()

if __name__ == "__main__":
    main()
