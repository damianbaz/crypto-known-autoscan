# -*- coding: utf-8 -*-

def _fmt_usd(n):
    if n is None: return "—"
    try: return f"${n:,.2f}"
    except: return str(n)

def render_markdown(now_iso: str, rows: list, min_rows: int = 10, signals_only: bool = True, plan: dict = None) -> str:
    md = []
    md.append("# Daily Crypto Signals — Solo Buy/Sell")
    md.append(f"Fecha: {now_iso}\n")

    # Bloque de acciones concretas
    if plan:
        before = plan["before"]; after = plan["after"]; orders = plan["orders"]
        acts = plan.get("actions_text", {"sell": {}, "buy": {}})

        sell_lines = [f"{sym}: {_fmt_usd(usd)}" for sym, usd in acts.get("sell", {}).items()]
        buy_lines  = [f"{sym}: {_fmt_usd(usd)}" for sym, usd in acts.get("buy", {}).items()]

        md.append("## Acciones del día")
        if sell_lines:
            md.append("**Vender** → " + ", ".join(sell_lines))
        else:
            md.append("**Vender** → _nada_")

        if buy_lines:
            md.append("**Comprar** → " + ", ".join(buy_lines))
        else:
            md.append("**Comprar** → _nada_")

        md.append("")
        md.append("## Estado de cartera (simulado)")
        md.append(f"- Valor antes: **{_fmt_usd(before['total_usd'])}**  |  Cash: {_fmt_usd(before['cash_usd'])}")
        md.append(f"- Valor después: **{_fmt_usd(after['total_usd'])}**  |  Cash: {_fmt_usd(after['cash_usd'])}\n")

    md.append("_Disclaimer: señales cuantitativas simuladas; no es asesoramiento financiero._")
    return "\n".join(md)
