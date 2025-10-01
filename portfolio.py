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
    """
    - Aplica state_override una sola vez si enabled=true.
    - rotation_only=true: BUY budget == SELL neto (no usa cash nuevo).
    - Devuelve plan (con totales) + precios.
    """
    fees_bps = int((cfg.get("trading") or {}).get("fees_bps", 10))      # 0.10%
    min_trade = float((cfg.get("trading") or {}).get("min_trade_usd", 5.0))
    rotation_only = bool((cfg.get("trading") or {}).get("rotation_only", False))
    fee_rate = (fees_bps / 10000.0)

    init_cash = float((cfg.get("portfolio") or {}).get("initial_cash", 0.0))
    cash = float(state.get("cash_usd", init_cash))
    holdings = dict(state.get("holdings", {}))
    prices = {k: float(v) for k, v in (prices_map or {}).items() if v}

    # --- OVERRIDE: apply once ---
    ov = (cfg.get("state_override") or {})
    already = bool(state.get("_override_applied", False))
    override_used = False
    if ov.get("enabled") and not already:
        mode = (ov.get("mode") or "quantities").lower()
        cash = float(ov.get("cash_usd", 0.0))
        if mode == "quantities":
            holdings = {k: float(v) for k, v in (ov.get("holdings") or {}).items()}
        elif mode == "usd":
            holdings = {}
            for sym, usd in (ov.get("holdings_usd") or {}).items():
                p = prices.get(sym)
                if p and p > 0:
                    holdings[sym] = float(usd) / p
        override_used = True

    total = compute_portfolio_value(holdings, prices, cash)
    if total <= 0:
        # si no hay precios aún o todo 0, estimar con initial_cash (solo para targets)
        total = init_cash

    targets = targets or {}
    desired_usd = {sym: total * w for sym, w in targets.items()}
    current_usd = {sym: holdings.get(sym, 0.0) * prices.get(sym, 0.0) for sym in prices}

    sell_map, buy_map = {}, {}
    for sym, tgt_usd in desired_usd.items():
        p = prices.get(sym)
        if not p:
            continue
        cur = current_usd.get(sym, 0.0)
        delta = tgt_usd - cur
        if delta < -min_trade:
            sell_map[sym] = abs(delta)
        elif delta > min_trade:
            buy_map[sym] = delta

    if not sell_map and not buy_map and (rotation_only or cash < min_trade):
        plan = {
            "orders": [],
            "actions_text": {"sell": {}, "buy": {}},
            "totals": {"sold_usd": 0.0, "bought_usd": 0.0},
            "before": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": round(compute_portfolio_value(holdings, prices, cash),2)},
            "after": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": round(compute_portfolio_value(holdings, prices, cash),2)},
            "override_applied": override_used
        }
        return plan, prices

    total_sell_intent = sum(sell_map.values())
    sell_fee_on_intent = total_sell_intent * fee_rate
    buy_budget = max(0.0, total_sell_intent - sell_fee_on_intent) if rotation_only else cash + max(0.0, total_sell_intent - sell_fee_on_intent)

    total_buy_need = sum(buy_map.values()) or 1.0
    scale = min(1.0, buy_budget / total_buy_need)

    orders = []
    actions_text = {"sell": {}, "buy": {}}
    new_holdings = holdings.copy()
    new_cash = cash
    sold_total, bought_total = 0.0, 0.0

    # SELLs
    for sym, usd_to_sell in sell_map.items():
        p = prices[sym]
        usd = round(usd_to_sell, 2)
        if usd < min_trade: 
            continue
        qty = min(usd / p, new_holdings.get(sym, 0.0))
        usd = qty * p
        fee = usd * fee_rate
        new_holdings[sym] = max(0.0, new_holdings.get(sym, 0.0) - qty)
        new_cash += (usd - fee)
        sold_total += usd
        orders.append({"symbol": sym, "side": "SELL", "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})
        actions_text["sell"][sym] = round(usd, 2)

    # BUYs
    pending_budget = buy_budget if rotation_only else new_cash
    last_buy_idx = None
    for sym, need_usd in buy_map.items():
        p = prices[sym]
        usd = round(min(need_usd * scale, pending_budget), 2)
        if usd < min_trade:
            continue
        fee = usd * fee_rate
        qty = (usd - fee) / p
        new_holdings[sym] = new_holdings.get(sym, 0.0) + qty
        pending_budget -= usd
        bought_total += usd
        orders.append({"symbol": sym, "side": "BUY", "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})
        actions_text["buy"][sym] = round(usd, 2)
        last_buy_idx = len(orders) - 1
        if pending_budget < min_trade:
            break

    # Top-up final para que buys == sells (rotation_only)
    if rotation_only and last_buy_idx is not None and pending_budget >= 0.01:
        o = orders[last_buy_idx]
        p = o["price"]
        old_usd, old_fee = o["usd"], o["fee_usd"]
        new_usd = round(old_usd + pending_budget, 2)
        new_fee = new_usd * fee_rate
        delta_qty = ((new_usd - new_fee) - (old_usd - old_fee)) / p
        o["usd"] = round(new_usd, 2)
        o["fee_usd"] = round(new_fee, 2)
        o["qty"] = round(o["qty"] + max(0.0, delta_qty), 8)
        bought_total += round(pending_budget, 2)
        actions_text["buy"][o["symbol"]] = round(new_usd, 2)
        pending_budget = 0.0

    if rotation_only:
        new_cash = round(cash, 2)  # no gastamos cash adicional
    else:
        new_cash = round(pending_budget, 2)

    before_total = round(compute_portfolio_value(holdings, prices, cash), 2)
    after_total = round(compute_portfolio_value(new_holdings, prices, new_cash), 2)

    plan = {
        "orders": orders,
        "actions_text": actions_text,
        "totals": {"sold_usd": round(sold_total,2), "bought_usd": round(bought_total,2)},
        "before": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": before_total},
        "after": {"cash_usd": new_cash, "holdings": new_holdings, "total_usd": after_total},
        "override_applied": override_used
    }
    return plan, prices
