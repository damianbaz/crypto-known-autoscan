# en, p.ej., aggregator.py
import json, re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"

def _load_last_reports(n=14):
    files = sorted(DOCS_DIR.glob("report-*.json"))
    return files[-n:]

def _extract_date(name):
    m = re.search(r"report-(\d{4}-\d{2}-\d{2})\.json", name)
    return m.group(1) if m else None

def _norm(weights):
    s = sum(weights)
    return [w/s for w in weights] if s else weights

def make_weights(mode="exp", alpha=0.8, fixed=None, n=14):
    if mode == "fixed" and fixed:
        w = fixed[:n] + [0]*(n-len(fixed))
        return _norm(w)
    # exponencial por defecto
    w = [alpha**k for k in range(n)]  # k=0 hoy
    return _norm(w)

def build_weighted(n=14, weights=None):
    reps = _load_last_reports(n)
    if not reps:
        return {"window_days": n, "symbols": {}, "dates": []}

    # del más antiguo al más reciente para alinear k
    reps_sorted = sorted(reps, key=lambda p: _extract_date(p.name) or "")
    dates = [_extract_date(p.name) for p in reps_sorted]
    # k=0 será el último (hoy)
    weights = weights or make_weights(n=n)
    # reindexar pesos a fechas: map day_index -> weight
    # más antiguo -> índice grande; más reciente -> índice 0
    # invertimos para que weights[0]=hoy
    weights = list(reversed(weights))

    by_sym = defaultdict(lambda: {"name": None, "scores": [], "days_present": 0})
    for i, p in enumerate(reps_sorted):
        data = json.loads(p.read_text(encoding="utf-8"))
        for proj in data.get("projects", []):
            sym = proj.get("symbol")
            name = proj.get("name")
            score = (proj.get("score") or {}).get("total")
            if score is None:
                continue
            by_sym[sym]["name"] = name or by_sym[sym]["name"]
            by_sym[sym]["days_present"] += 1
            # peso del día i (i sube con el tiempo): el más reciente tiene mayor weight
            w = weights[i]
            by_sym[sym]["scores"].append((dates[i], score, w))

    out = {}
    for sym, info in by_sym.items():
        if not info["scores"]:
            continue
        # weighted sum
        num = sum(s*w for (_, s, w) in info["scores"])
        den = sum(w for (_, _, w) in info["scores"])
        wscore = num/den if den else None
        out[sym] = {
            "name": info["name"],
            "days_present": info["days_present"],
            "weighted_score_14d": round(wscore, 2) if wscore is not None else None,
            "last_date": dates[-1],
            "history": [{"day": d, "score": s, "weight": round(w,4)} for (d, s, w) in info["scores"]],
        }

    return {"window_days": n, "dates": dates, "symbols": out}
