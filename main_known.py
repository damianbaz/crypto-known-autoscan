# -*- coding: utf-8 -*-
import os, yaml, json
from datetime import datetime
from fetch_coingecko import fetch_markets
from fetch_defillama import fetch_tvl_deltas
from score_known import score_entry
from report_known import render_markdown
from portfolio import load_cfg as load_portfolio_cfg, load_state, save_state, plan_rebalance
from notify_telegram import send_message  # no falla si no setean creds

OUT_DIR = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT_DIR, exist_ok=True)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(path_cfg: str = "config.yaml"):
    cfg = load_config(path_cfg) or {}
    wl = cfg.get("watchlist", [])
    if not wl:
        print("[warn] watchlist vacío en config.yaml")

    ids = [w["id"] for w in wl]
    markets = {}
    try:
        markets = fetch_markets(ids)
    except Exception as e:
        print("[error] fetch_markets falló:", e)

    if not markets:
        print("[warn] CoinGecko devolvió vacío; sigo con placeholders de precio.")

    rows = []
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    for w in wl:
        cid = w["id"]
        name = w.get("name", cid)
        mkt = markets.get(cid, {})

        tvl = {"tvl": None, "tvl_chg_7d": None, "tvl_chg_30d": None}
        slug = w.get("defillama_slug")
        if slug:
            try:
                tvl = fetch_tvl_deltas(slug)
            except Exception as e:
                print("[defillama] fallo", slug, e)

        sc = score_entry(mkt, tvl)
        row = {
            "id": cid,
            "name": name,
            **mkt,
            **tvl,
            **sc,
        }
        rows.append(row)

    # mapear precios actuales por símbolo "name" (BTC, ETH, etc.)
    price_map = {r["name"]: r.get("price") for r in rows if r.get("price")}

    # cargar configuración de cartera & estado previo
    port_cfg = {}
    try:
        port_cfg = load_portfolio_cfg("portfolio.yaml")
    except Exception as e:
        print("[portfolio] no se pudo cargar portfolio.yaml:", e)

    state = load_state()  # {cash_usd, holdings{SYM:qty}}
    targets = (port_cfg.get("portfolio") or {}).get("targets", {})

    plan, prices_used = plan_rebalance(price_map, targets, port_cfg, state)

    # actualizar y persistir estado simulado
    new_state = {
        "cash_usd": plan["after"]["cash_usd"],
        "holdings": plan["after"]["holdings"],
        "last_prices": prices_used,
    }
    save_state(new_state)

    # <<< AÑADIR ESTA LÍNEA >>>
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    signals_only = ((cfg.get("run") or {}).get("signals_only", True) is True)
    md = render_markdown(now, rows, cfg.get("run", {}).get("min_rows_in_report", 10), signals_only, plan)

    # guardar salida
    day = datetime.utcnow().strftime("%Y-%m-%d")
    out_md = os.path.join(OUT_DIR, f"report-{day}.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md)

    out_json = os.path.join(OUT_DIR, f"report-{day}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    # alerta si hay score alto
    thr = (cfg.get("run", {}) or {}).get("alert_threshold_score", 70)
    top = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)[:3]
    if top and (top[0].get("score", 0) >= thr):
        msg_lines = [
            "*Daily Crypto Brief — Conocidos*",
            f"Fecha: {now}",
            f"Top: {top[0]['name']} (score {top[0]['score']})",
        ]
        if len(top) > 1:
            msg_lines.append(f"2do: {top[1]['name']} (score {top[1]['score']})")
        if len(top) > 2:
            msg_lines.append(f"3ro: {top[2]['name']} (score {top[2]['score']})")
        try:
            send_message("\n".join(msg_lines))
        except Exception as e:
            print("[telegram] no enviado:", e)

    def _fmt_usd(x): 
        try: return f"${float(x):,.2f}"
        except: return str(x)

    def build_coinbase_steps(plan):
        acts = plan.get("actions_text", {"sell": {}, "buy": {}})
        sells = acts.get("sell", {})
        buys  = acts.get("buy", {})

        sold_total = plan.get("totals", {}).get("sold_usd", 0.0)
        bought_total = plan.get("totals", {}).get("bought_usd", 0.0)
        before = plan.get("before", {})
        after  = plan.get("after", {})

        # Compact action lines
        sell_txt = ", ".join([f"{k}: {_fmt_usd(v)}" for k,v in sells.items()]) if sells else "nada"
        buy_txt  = ", ".join([f"{k}: {_fmt_usd(v)}" for k,v in buys.items()])   if buys  else "nada"

        # Asset counts after
        holdings_after = after.get("holdings", {})
        assets_after = sum(1 for _, q in holdings_after.items() if q and q > 0)

        lines = []
        lines.append("*Acciones de hoy*")
        lines.append(f"Vender → {sell_txt}")
        lines.append(f"Comprar → {buy_txt}")
        lines.append("")
        lines.append(f"*Totales*  |  Vendido: {_fmt_usd(sold_total)}  ·  Comprado: {_fmt_usd(bought_total)}")
        lines.append(f"Valor antes: {_fmt_usd(before.get('total_usd',0))}  ·  Valor después: {_fmt_usd(after.get('total_usd',0))}")
        lines.append(f"Activos en cartera: {assets_after}")
        lines.append("")
        lines.append("*Paso a paso (Coinbase Advanced)*")
        lines.append("1) Abrí Coinbase → Trade (⇄) → *Advanced*.")
        if sells:
            lines.append("2) Para cada venta: elegí el par *SYMBOL-USD* → *Sell* → monto indicado.")
        if buys:
            lines.append("3) Para cada compra: elegí el par *SYMBOL-USD* → *Buy* → monto indicado.")
        lines.append("4) *Market* = más rápido (taker). *Limit* puede pagar menos (maker) si no ejecuta al toque.")
        return "\n".join(lines)

    # Enviar mensaje diario si está activado
    if ((cfg.get("notify") or {}).get("telegram") or {}).get("daily_actions", False):
        try:
            acts = plan.get("actions_text", {"sell": {}, "buy": {}})
            msg = build_coinbase_steps(acts)
            send_message(msg)
        except Exception as e:
            print("[telegram] no enviado:", e)
        
    print("OK ->", out_md)

if __name__ == "__main__":
    run()
