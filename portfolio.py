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
    Rotation-only mode:
      - If trading.rotation_only = true: BUY budget == SELL proceeds (net of sell fees). No new cash used.
      - Else: buy budget = cash + net sells.
    Returns plan + prices. Plan includes totals for messaging.
    """
    fees_bps = int((cfg.get("trading") or {}).get("fees_bps", 10))      # 0.10%
    min_trade = float((cfg.get("trading") or {}).get("min_trade_usd", 5.0))
    rotation_only = bool((cfg.get("trading") or {}).get("rotation_only", False))
    fee_rate = (fees_bps / 10000.0)

    # State
    init_cash = float((cfg.get("portfolio") or {}).get("initial_cash", 0.0))
    cash = float(state.get("cash_usd", init_cash))
    holdings = dict(state.get("holdings", {}))
    prices = {k: float(v) for k, v in (prices_map or {}).items() if v}

    # Current value
    total = compute_portfolio_value(holdings, prices, cash)
    if total <= 0:
        total = init_cash

    # Desired USD by targets
    targets = targets or {}
    desired_usd = {sym: total * w for sym, w in targets.items()}

    # Current USD by symbol
    current_usd = {sym: holdings.get(sym, 0.0) * prices.get(sym, 0.0) for sym in prices}

    # Excess/deficit
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
        # nothing to do
        return {
            "orders": [],
            "actions_text": {"sell": {}, "buy": {}},
            "totals": {"sold_usd": 0.0, "bought_usd": 0.0},
            "before": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": round(total,2)},
            "after": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": round(total,2)}
        }, prices

    # Budget
    total_sell_intent = sum(sell_map.values())
    sell_fee_on_intent = total_sell_intent * fee_rate
    if rotation_only:
        buy_budget = max(0.0, total_sell_intent - sell_fee_on_intent)
    else:
        buy_budget = cash + max(0.0, total_sell_intent - sell_fee_on_intent)

    # If budget < total buy need, scale buys proportionally
    total_buy_need = sum(buy_map.values()) or 1.0
    scale = min(1.0, buy_budget / total_buy_need)

    orders = []
    actions_text = {"sell": {}, "buy": {}}
    new_holdings = holdings.copy()

    # We keep track of cash separately; for rotation_only we try to end ~ same cash
    new_cash = cash
    sold_total, bought_total = 0.0, 0.0

    # 1) SELL ORDERS -> add to cash net of fee
    for sym, usd_to_sell in sell_map.items():
        p = prices[sym]
        usd = round(usd_to_sell, 2)
        if usd < min_trade:
            continue
        qty = usd / p
        qty = min(qty, new_holdings.get(sym, 0.0))   # don't sell more than we have
        usd = qty * p
        fee = usd * fee_rate
        new_holdings[sym] = max(0.0, new_holdings.get(sym, 0.0) - qty)
        new_cash += (usd - fee)
        sold_total += usd
        orders.append({"symbol": sym, "side": "SELL", "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})
        actions_text["sell"][sym] = round(usd, 2)

    # 2) BUY ORDERS using budget
    pending_budget = buy_budget if rotation_only else new_cash
    last_buy_idx = None
    for sym, need_usd in buy_map.items():
        p = prices[sym]
        usd = round(need_usd * scale, 2)
        usd = min(usd, pending_budget)
        if usd < min_trade:
            continue
        fee = usd * fee_rate
        usd_net = usd - fee
        qty = usd_net / p
        new_holdings[sym] = new_holdings.get(sym, 0.0) + qty
        pending_budget -= usd
        bought_total += usd
        orders.append({"symbol": sym, "side": "BUY", "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})
        actions_text["buy"][sym] = round(usd, 2)
        last_buy_idx = len(orders) - 1
        if pending_budget < min_trade:
            break

    # --- Force buys == budget (rotation_only) by topping up the last BUY if needed ---
    if rotation_only and last_buy_idx is not None and pending_budget >= 0.01:
        # allocate remaining pennies to the last buy
        o = orders[last_buy_idx]
        p = o["price"]
        old_usd = o["usd"]; old_fee = o["fee_usd"]
        new_usd = round(old_usd + pending_budget, 2)
        new_fee = new_usd * fee_rate
        delta_qty = ((new_usd - new_fee) - (old_usd - old_fee)) / p
        o["usd"] = round(new_usd, 2)
        o["fee_usd"] = round(new_fee, 2)
        o["qty"] = round(o["qty"] + max(0.0, delta_qty), 8)
        bought_total += round(pending_budget, 2)
        actions_text["buy"][o["symbol"]] = round(new_usd, 2)
        pending_budget = 0.0

    # Update cash:
    if rotation_only:
        # End with original cash (plus rounding crumbs if any)
        new_cash = round(cash, 2)
    else:
        new_cash = round(pending_budget, 2)

    before_total = round(total, 2)
    after_total = round(compute_portfolio_value(new_holdings, prices, new_cash), 2)

    plan = {
        "orders": orders,
        "actions_text": actions_text,
        "totals": {"sold_usd": round(sold_total,2), "bought_usd": round(bought_total,2)},
        "before": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": before_total},
        "after": {"cash_usd": new_cash, "holdings": new_holdings, "total_usd": after_total}
    }
    return plan, prices
