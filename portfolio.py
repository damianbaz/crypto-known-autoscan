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
    Rebalanceo "duro": SOLO rotación. Lo vendido = lo comprado (neto fees).
    Salida:
      plan = {
        "orders": [ {symbol, side, usd, qty, price, fee_usd} ... ],
        "actions_text": {"sell": {"BTC": 120.0, ...}, "buy": {"ETH": 170.0, ...}},
        "before": {...}, "after": {...}
      }
    """
    fees_bps = int((cfg.get("trading") or {}).get("fees_bps", 10))  # 0.10% por defecto
    min_trade = float((cfg.get("trading") or {}).get("min_trade_usd", 5.0))

    # Estado actual
    cash = float(state.get("cash_usd", cfg.get("portfolio", {}).get("initial_cash", 0.0)))
    holdings = dict(state.get("holdings", {}))   # {SYM: qty}
    prices = {k: float(v) for k, v in prices_map.items() if v}

    # --- override manual para re-sincronizar ---
    ov = (cfg.get("state_override") or {})
    if ov.get("enabled"):
        cash = float(ov.get("cash_usd", 0.0))
        mode = (ov.get("mode") or "quantities").lower()
        if mode == "quantities":
            holdings = {k: float(v) for k, v in (ov.get("holdings") or {}).items()}
        elif mode == "usd":
            # convertir USD declarados a cantidades usando precios actuales
            holdings = {}
            for sym, usd in (ov.get("holdings_usd") or {}).items():
                p = prices.get(sym)
                if p and p > 0:
                    holdings[sym] = float(usd) / p
        # Nota: dejalo en 'enabled: true' sólo el día del sync; luego volvé a false
    
    # Valor total actual
    total = compute_portfolio_value(holdings, prices, cash)
    if total <= 0:
        total = float(cfg.get("portfolio", {}).get("initial_cash", 0.0))

    # USD deseado por símbolo
    desired_usd = {sym: total * w for sym, w in (targets or {}).items()}

    # USD actual por símbolo
    current_usd = {}
    for sym, p in prices.items():
        qty = holdings.get(sym, 0.0)
        current_usd[sym] = qty * p

    # Excesos (vender) y déficits (comprar)
    sell_map, buy_map = {}, {}
    for sym, tgt_usd in desired_usd.items():
        p = prices.get(sym)
        if not p:  # sin precio no operamos
            continue
        cur = current_usd.get(sym, 0.0)
        delta = tgt_usd - cur
        if delta < -min_trade:
            sell_map[sym] = abs(delta)  # USD a vender
        elif delta > min_trade:
            buy_map[sym] = delta        # USD a comprar

    # Si no hay nada relevante que mover
    if not sell_map and not buy_map:
        return {
            "orders": [],
            "actions_text": {"sell": {}, "buy": {}},
            "before": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": round(total,2)},
            "after": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": round(total,2)}
        }, prices

    # Presupuesto de compras = ventas netas de fee de VENTA
    total_sell = sum(sell_map.values())
    sell_fee = total_sell * (fees_bps / 10000.0)
    buy_budget = max(0.0, total_sell - sell_fee)

    # Si el presupuesto no alcanza para todas las compras, las escalamos proporcionalmente
    total_buy_need = sum(buy_map.values()) or 1.0
    scale = min(1.0, buy_budget / total_buy_need)

    orders = []
    actions_text = {"sell": {}, "buy": {}}
    new_holdings = holdings.copy()
    new_cash = 0.0  # objetivo: terminar siempre sin cash adicional

    # 1) Generar órdenes de VENTA
    for sym, usd_to_sell in sell_map.items():
        p = prices[sym]
        usd = round(usd_to_sell, 2)
        if usd < min_trade: 
            continue
        qty = usd / p
        # no vender más de lo que hay
        qty = min(qty, new_holdings.get(sym, 0.0))
        usd = qty * p
        fee = usd * (fees_bps / 10000.0)
        new_holdings[sym] = max(0.0, new_holdings.get(sym, 0.0) - qty)
        new_cash += (usd - fee)
        orders.append({"symbol": sym, "side": "SELL", "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})
        actions_text["sell"][sym] = round(usd, 2)

    # 2) Generar órdenes de COMPRA con el cash disponible (tras vender)
    # repartimos según déficits escalados
    pending_budget = new_cash
    for sym, need_usd in buy_map.items():
        p = prices[sym]
        usd = round(need_usd * scale, 2)
        if usd < min_trade:
            continue
        usd = min(usd, pending_budget)
        if usd < min_trade:
            continue
        # fee en compra
        fee = usd * (fees_bps / 10000.0)
        usd_net = usd - fee
        qty = usd_net / p
        new_holdings[sym] = new_holdings.get(sym, 0.0) + qty
        pending_budget -= usd
        orders.append({"symbol": sym, "side": "BUY", "usd": round(usd,2), "qty": round(qty,8), "price": p, "fee_usd": round(fee,2)})
        actions_text["buy"][sym] = round(usd, 2)

        if pending_budget < min_trade:
            break

    new_cash = round(pending_budget, 2)  # puede quedar centavos por redondeo
    before_total = round(total, 2)
    after_total = round(compute_portfolio_value(new_holdings, prices, new_cash), 2)

    return {
        "orders": orders,
        "actions_text": actions_text,
        "before": {"cash_usd": round(cash,2), "holdings": holdings, "total_usd": before_total},
        "after": {"cash_usd": new_cash, "holdings": new_holdings, "total_usd": after_total}
    }, prices
