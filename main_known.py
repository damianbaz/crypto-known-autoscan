# -*- coding: utf-8 -*-
import os, yaml, json
from datetime import datetime
from fetch_coingecko import fetch_markets
from fetch_defillama import fetch_tvl_deltas
from score_known import score_entry
from report_known import render_markdown

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

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    md = render_markdown(now, rows, (cfg.get("run", {}) or {}).get("min_rows_in_report", 10))

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

    print("OK ->", out_md)


if __name__ == "__main__":
    run()
