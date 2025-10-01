# -*- coding: utf-8 -*-
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT_DIR, exist_ok=True)




def load_config(path: str) -> dict:
with open(path, "r", encoding="utf-8") as f:
return yaml.safe_load(f)




def run(path_cfg: str = "config.yaml"):
cfg = load_config(path_cfg)
wl = cfg.get("watchlist", [])


ids = [w["id"] for w in wl]
markets = fetch_markets(ids)


rows = []
for w in wl:
cid = w["id"]
name = w.get("name", cid)
mkt = markets.get(cid, {})


tvl = {"tvl": None, "tvl_chg_7d": None, "tvl_chg_30d": None}
if w.get("defillama_slug"):
try:
tvl = fetch_tvl_deltas(w["defillama_slug"])
except Exception as e:
print("[defillama] fallo", w["defillama_slug"], e)


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
md = render_markdown(now, rows, cfg.get("run", {}).get("min_rows_in_report", 10))


# guardar salida
day = datetime.utcnow().strftime("%Y-%m-%d")
out_md = os.path.join(OUT_DIR, f"report-{day}.md")
with open(out_md, "w", encoding="utf-8") as f:
f.write(md)


out_json = os.path.join(OUT_DIR, f"report-{day}.json")
with open(out_json, "w", encoding="utf-8") as f:
json.dump(rows, f, ensure_ascii=False, indent=2)


# alerta si hay score alto
thr = cfg.get("run", {}).get("alert_threshold_score", 70)
top = sorted(rows, key=lambda r: r["score"], reverse=True)[:3]
if top and top[0]["score"] >= thr:
msg = "\n".join([
"*Daily Crypto Brief — Conocidos*",
f"Fecha: {now}",
f"Top: {top[0]['name']} (score {top[0]['score']})",
f"2do: {top[1]['name']} (score {top[1]['score']})" if len(top) > 1 else "",
f"3ro: {top[2]['name']} (score {top[2]['score']})" if len(top) > 2 else "",
])
send_message(msg)


print("OK ->", out_md)




if __name__ == "__main__":
run()
