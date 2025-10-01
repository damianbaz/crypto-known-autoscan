# -*- coding: utf-8 -*-
import os
import time
import requests
from typing import List, Dict

API_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

# cabeceras base
_HEADERS = {"Accept": "application/json"}
if COINGECKO_API_KEY:
    _HEADERS["x-cg-pro-api-key"] = COINGECKO_API_KEY  # <- correctamente indentado

# espera mínima entre llamadas para no pegarle al rate limit
_DEF_SLEEP = float(os.getenv("CG_SLEEP_SEC", "1.2"))


def chunk(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _get_with_retry(url: str, params: dict, headers: dict, retries: int = 3, backoff: float = 2.0):
    """GET con reintentos básicos para 429/5xx."""
    last = None
    for i in range(retries):
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            wait = backoff * (i + 1)
            print(f"[coingecko] {r.status_code} reintento en {wait}s…")
            time.sleep(wait)
            last = r
            continue
        print(f"[coingecko] status {r.status_code}: {r.text[:200]}")
        return r
    return last


def fetch_markets(ids: List[str]) -> Dict[str, dict]:
    """
    Devuelve mapa id -> métricas de mercado usando /coins/markets
    con % cambio 24h/7d/30d.
    """
    out: Dict[str, dict] = {}
    if not ids:
        return out

    for part in chunk(ids, 150):
        params = {
            "vs_currency": "usd",
            "ids": ",".join(part),
            "order": "market_cap_desc",
            "per_page": len(part),
            "page": 1,
            "price_change_percentage": "24h,7d,30d",
            "sparkline": "false",
        }
        r = _get_with_retry(f"{API_BASE}/coins/markets", params, _HEADERS)
        if not r or r.status_code != 200:
            print("[coingecko] fallo definitivo en este chunk; continuo sin él")
            continue

        try:
            data = r.json()
        except Exception as e:
            print("[coingecko] json error:", e)
            data = []

        for row in data:
            out[row.get("id")] = {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "price": row.get("current_price"),
                "market_cap": row.get("market_cap"),
                "volume": row.get("total_volume"),
                "chg_24h": row.get("price_change_percentage_24h_in_currency"),
                "chg_7d": row.get("price_change_percentage_7d_in_currency"),
                "chg_30d": row.get("price_change_percentage_30d_in_currency"),
            }

        time.sleep(_DEF_SLEEP)

    return out
