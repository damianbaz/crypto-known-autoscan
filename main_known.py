# main_known.py (fragmento ejemplo)
from writer import build_payload, write_latest_json, write_latest_md, publish_to_docs

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

def main():
    projects = collect_projects()
    payload = build_payload(universe="top_200_coingecko_filtered", projects=projects)
    write_latest_json(payload)
    write_latest_md(payload)
    publish_to_docs()

if __name__ == "__main__":
    main()
