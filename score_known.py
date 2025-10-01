# -*- coding: utf-8 -*-
from typing import Dict


# Pesos sencillos y transparentes
W_PRICE_MOM = 0.20 # 7d/30d
W_TVL = 0.25 # 7d/30d
W_VOL = 0.10 # volumen relativo
W_MISC = 0.45 # placeholder (on-chain, dev, noticias) -> 0 en MVP




def _nz(x):
return 0.0 if x is None else float(x)




def score_entry(mkt: Dict, tvl: Dict) -> Dict:
# Momentum de precio: media de chg_7d y chg_30d, limitado a [-50, +50]
pm = max(-50.0, min(50.0, (_nz(mkt.get("chg_7d")) + _nz(mkt.get("chg_30d"))) / 2.0))
pm_norm = (pm + 50.0) / 100.0 # 0..1


# TVL momentum: media de chg_7d y chg_30d, limitado
tv = max(-50.0, min(50.0, (_nz(tvl.get("tvl_chg_7d")) + _nz(tvl.get("tvl_chg_30d"))) / 2.0))
tv_norm = (tv + 50.0) / 100.0


# Volumen relativo: log(volumen / 1e6) recortado a [0,1]
vol = _nz(mkt.get("volume"))
if vol <= 0:
vol_norm = 0.0
else:
import math
vol_norm = max(0.0, min(1.0, (math.log10(vol) - 5.5))) # ~1 a partir de ~3.2M usd


misc = 0.0 # placeholder para v2


# Score 0..100
score = 100.0 * (W_PRICE_MOM*pm_norm + W_TVL*tv_norm + W_VOL*vol_norm + W_MISC*misc)


return {
"price_momentum": pm,
"tvl_momentum": tv,
"vol_norm": vol_norm,
"score": round(score, 2),
}
