# -*- coding: utf-8 -*-
import os, time, requests
from typing import List, Dict


API_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")


# Helper para rate limits suaves
_DEF_SLEEP = float(os.getenv("CG_SLEEP_SEC", "1.2"))


_HEADERS = {"Accept": "application/json"}
if COINGECKO_API_KEY:
_HEADERS["x-cg-pro-api-key"] = COINGECKO_API_KEY




def chunk(lst: List[str], n: int):
for i in range(0, len(lst), n):
yield lst[i:i+n]




def fetch_markets(ids: List[str]) -> Dict[str, dict]:
"""Devuelve mapa id -> métricas de mercado.
Usa /coins/markets con % de cambio 24h/7d/30d.
"""
out: Dict[str, dict] = {}
# CoinGecko acepta lista de ids separada por coma; evitamos chunks gigantes
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
r = requests.get(f"{API_BASE}/coins/markets", params=params, headers=_HEADERS, timeout=30)
r.raise_for_status()
for row in r.json():
out[row["id"]] = {
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
