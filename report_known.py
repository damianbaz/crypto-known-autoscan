# -*- coding: utf-8 -*-
from datetime import datetime




def format_usd(n):
if n is None:
return "—"
try:
return f"${n:,.0f}"
except Exception:
return str(n)




def render_markdown(now_iso: str, rows: list, min_rows: int = 10) -> str:
rows_sorted = sorted(rows, key=lambda r: r["score"], reverse=True)
rows_sorted = rows_sorted[: max(min_rows, len(rows_sorted))]


md = []
md.append(f"# Daily Crypto Brief — Conocidos\n")
md.append(f"Fecha: {now_iso}\n")
md.append("")
md.append("| Ticker | Precio | MCAP | TVL | ∆7d Px | ∆30d Px | ∆7d TVL | ∆30d TVL | Score |")
md.append("|:------:|------:|-----:|----:|------:|--------:|--------:|---------:|------:|")


for r in rows_sorted:
md.append(
"| {name} | {price} | {mcap} | {tvl} | {p7:.2f}% | {p30:.2f}% | {t7:.2f}% | {t30:.2f}% | {score:.2f} |".format(
name=r["name"],
price=("$%.4f" % r["price"]) if isinstance(r["price"], (int, float)) else "—",
mcap=format_usd(r.get("market_cap")),
tvl=format_usd(r.get("tvl")),
p7=r.get("chg_7d", 0.0) or 0.0,
p30=r.get("chg_30d", 0.0) or 0.0,
t7=r.get("tvl_chg_7d", 0.0) or 0.0,
t30=r.get("tvl_chg_30d", 0.0) or 0.0,
score=r.get("score", 0.0) or 0.0,
)
)


# Sugerencias simple: top 3 y motivos
md.append("\n## Sugerencias (heurísticas)\n")
top3 = rows_sorted[:3]
for i, r in enumerate(top3, 1):
reasons = []
if (r.get("chg_7d") or 0) > 0 and (r.get("chg_30d") or 0) > 0:
reasons.append("precio con momentum 7d/30d")
if (r.get("tvl_chg_7d") or 0) > 0 and (r.get("tvl_chg_30d") or 0) > 0:
reasons.append("TVL en crecimiento 7d/30d")
if (r.get("vol_norm") or 0) > 0.6:
reasons.append("volumen saludable")
if not reasons:
reasons.append("señales mixtas; vigilar")
md.append(f"**{i}. {r['name']}** — {', '.join(reasons)} (score {r['score']}).")


md.append("\n_Disclaimer: esto es un radar cuantitativo; no es consejo de inversión._\n")
return "\n".join(md)
