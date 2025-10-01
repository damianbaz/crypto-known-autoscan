# -*- coding: utf-8 -*-
import os, json, yaml
from typing import Dict, Tuple

STATE_PATH = os.path.join(os.path.dirname(__file__), "out", "portfolio_state.json")

def load_cfg(path="portfolio.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def compute_portfolio_value(holdings: Dict[str, float], prices: Dict[str, float], cash_usd: float) -> float:
    v = float(cash_usd or 0)
    for sym, qty in (holdings or {}).items():
        p = prices.get(sym)
        if p: v += qty * p
    return v

def plan_rebalance(prices_map: Dict[str, float], targets: Dict[str, float], cfg: dict, state: dict) -> Tuple[dict, dict]:
    fees_bps = int((cfg.get("trading") or {}).get("fees_bps", 10))
    min_trade = float((cfg.get("trading") or {}).get("min_trade_usd", 5.0))

    # estado actual
    cash = float(state.get("cash_usd", cfg.get("portfolio", {}).get("initial_cash", 0.0)))
    holdings = dict(state.get("holdings", {}))   # {SYM: qty}
    # precios actuales por símbolo "name" de config (BTC/ETH, etc.)
    prices = {k: float(v) for k, v in prices_map.items() if v}

    # valor total actual
    total = compute_portfolio_value(holdings, prices, cash)
    if total <= 0:
        total = float(cfg.get("portfolio", {}).get("initial_cash", 0.0))

    desired_usd = {sym: total * w for sym, w in targets.items()}

    # construir órdenes buy/sell necesarias (en USD)
    orders = []  # lista de {symbol, side, usd, qty}
    new_holdings = holdings.copy()
    new_cash = cash

    for sym, tgt_usd in desired_usd.items():
        p = prices.get(sym)
        if not p or p <= 0:  # si no hay precio, saltar
            continue
        cur_qty = holdings.get(sym, 0.0)
        cur_usd = cur_qty * p
        delta = tgt_usd - cur_usd
        # ignorar microajustes
        if abs(delta) < min_trade:
            continue
        side = "BUY" if delta > 0 else "SELL"
        usd = abs(delta)
        # aplicar fee
        fee = usd * (fees_bps / 10000.0)
        # qty según side
        qty = (usd - fee) / p if side == "BUY" else (usd) / p
        # actualizar estado simulado
        if side == "BUY":
            if new_cash < usd:  # si falta cash, vende proporcionalmente otras (simple)
                pass  # MVP: asumimos cash suficiente por soft rebalance
            new_cash -= usd
            new_holdings[sym] = new_holdings.get(sym, 0.0) + qty
        else:
            # no vender más de lo que hay
            sell_qty = min(qty, new_holdings.get(sym, 0.0))
            usd_obtenido = sell_qty * p
            new_holdings[sym] = max(0.0, new_holdings.get(sym, 0.0) - sell_qty)
            new_cash += usd_obtenido

        orders.append({"symbol": sym, "side": side, "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})

    # recalcular total tras rebalance
    new_total = compute_portfolio_value(new_holdings, prices, new_cash)

    return {
        "orders": orders,
        "before": {"cash_usd": cash, "holdings": holdings, "total_usd": round(total,2)},
        "after": {"cash_usd": round(new_cash,2), "holdings": new_holdings, "total_usd": round(new_total,2)}
    }, prices
