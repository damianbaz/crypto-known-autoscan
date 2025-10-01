# -*- coding: utf-8 -*-
import requests, math
from typing import Optional, Dict


API_BASE = "https://api.llama.fi" # sin auth para endpoints públicos




def _pct(old: Optional[float], new: Optional[float]) -> Optional[float]:
if old is None or new is None:
return None
if old == 0:
return None
return 100.0 * (new - old) / old




def _nearest_ts(series, target_ts):
# series: lista de {"date": int, "totalLiquidityUSD": float}
# devuelve valor más cercano al timestamp target
best = None
best_dt = None
for p in series:
dt = abs(p["date"] - target_ts)
if best is None or dt < best_dt:
best = p
best_dt = dt
return best




def fetch_tvl_deltas(slug: str) -> Dict[str, float]:
"""Devuelve TVL actual y %∆ 7d/30d aprox.
Usa /protocol/{slug} que retorna un array tvl con timestamps diarios.
"""
r = requests.get(f"{API_BASE}/protocol/{slug}", timeout=30)
r.raise_for_status()
data = r.json()
series = data.get("tvl", [])
if not series:
return {"tvl": None, "tvl_chg_7d": None, "tvl_chg_30d": None}


series_sorted = sorted(series, key=lambda x: x["date"]) # por si acaso
latest = series_sorted[-1]
latest_ts = latest["date"]


# 7 días y 30 días en segundos (aprox.)
d7 = _nearest_ts(series_sorted, latest_ts - 7*24*3600)
d30 = _nearest_ts(series_sorted, latest_ts - 30*24*3600)


tvl_now = latest.get("totalLiquidityUSD")
tvl_7 = d7.get("totalLiquidityUSD") if d7 else None
tvl_30 = d30.get("totalLiquidityUSD") if d30 else None


return {
"tvl": tvl_now,
"tvl_chg_7d": _pct(tvl_7, tvl_now),
"tvl_chg_30d": _pct(tvl_30, tvl_now),
}
