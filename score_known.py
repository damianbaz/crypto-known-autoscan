# -*- coding: utf-8 -*-
from typing import Dict

# Pesos del score (puedes afinarlos luego)
W_PRICE_MOM = 0.20   # 7d/30d
W_TVL       = 0.25   # 7d/30d
W_VOL       = 0.10   # volumen relativo
W_MISC      = 0.45   # reservado para on-chain/dev/news en v2 (por ahora 0)

def _nz(x):
    return 0.0 if x is None else float(x)

def score_entry(mkt: Dict, tvl: Dict) -> Dict:
    """
    Calcula un score 0..100 combinando momentum de precio, momentum de TVL
    y volumen normalizado. Transparente y robusto (con límites).
    """
    # Momentum de precio: media de 7d y 30d, limitado a [-50, +50] y normalizado a [0,1]
    pm = max(-50.0, min(50.0, (_nz(mkt.get("chg_7d")) + _nz(mkt.get("chg_30d"))) / 2.0))
    pm_norm = (pm + 50.0) / 100.0

    # Momentum de TVL: media de 7d y 30d, limitado a [-50, +50] y normalizado a [0,1]
    tv = max(-50.0, min(50.0, (_nz(tvl.get("tvl_chg_7d")) + _nz(tvl.get("tvl_chg_30d"))) / 2.0))
    tv_norm = (tv + 50.0) / 100.0

    # Volumen relativo: log10(vol) linealizado a [0,1] aprox (umbral ~3.2M USD)
    vol = _nz(mkt.get("volume"))
    if vol <= 0:
        vol_norm = 0.0
    else:
        import math
        vol_norm = max(0.0, min(1.0, (math.log10(vol) - 5.5)))  # ~1 alrededor de 3.2M

    misc = 0.0  # placeholder v2

    score = 100.0 * (W_PRICE_MOM * pm_norm + W_TVL * tv_norm + W_VOL * vol_norm + W_MISC * misc)
    return {
        "price_momentum": pm,
        "tvl_momentum": tv,
        "vol_norm": vol_norm,
        "score": round(score, 2),
    }
