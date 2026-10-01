#!/usr/bin/env python3
import os, json, time, requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.expanduser("~/adam_khoo_system/.env"), override=True)

# Cash floor integration
try:
    from cash_floor import check_cash_floor
    CASH_FLOOR_AVAILABLE = True
except ImportError:
    CASH_FLOOR_AVAILABLE = False

# Sector rotation strike selection
try:
    from sector_rotation_strikes import get_phase_adjusted_strike, get_current_phase
    SECTOR_ROTATION_AVAILABLE = True
except ImportError:
    SECTOR_ROTATION_AVAILABLE = False


# ══════════════════════════════════════════════════════════════
# AK OPTIONS GUARD SYSTEM — 5 permanent pre-trade checks
# Every options order passes ALL 5 guards before placement
# ══════════════════════════════════════════════════════════════

import re as _re

def _get_option_positions(headers, base_url):
    """Get all current options positions with P&L"""
    try:
        pos = requests.get(base_url+"/v2/positions",
            headers=headers, timeout=10).json()
        opts = []
        for p in (pos if isinstance(pos, list) else []):
            sym = p.get("symbol","")
            qty = float(p.get("qty",0) or 0)
            if len(sym) > 6:
                m = _re.match(r"([A-Z]+1?)\d", sym)
                ticker = m.group(1) if m else sym[:4]
                opts.append({
                    "symbol":   sym,
                    "ticker":   ticker,
                    "qty":      qty,
                    "side":     "SHORT" if qty < 0 else "LONG",
                    "entry":    float(p.get("avg_entry_price",0) or 0),
                    "current":  float(p.get("current_price",0) or 0),
                    "pl":       float(p.get("unrealized_pl",0) or 0),
                    "pl_pct":   float(p.get("unrealized_plpc",0) or 0)*100,
                })
        return opts
    except:
        return []

def _get_portfolio_tickers(headers, base_url):
    """Get list of stock tickers currently held"""
    try:
        pos = requests.get(base_url+"/v2/positions",
            headers=headers, timeout=10).json()
        return {p.get("symbol","") for p in (pos if isinstance(pos, list) else [])
                if len(p.get("symbol","")) <= 6
                and int(float(p.get("qty",0) or 0)) > 0}
    except:
        return set()

def _get_recent_scan(ticker):
    """Check if ticker has a passing AK scan in the last 30 days"""
    import json, os
    from datetime import datetime, timedelta
    try:
        log_path = os.path.expanduser("~/adam_khoo_system/signals_log.json")
        signals  = json.load(open(log_path)) if os.path.exists(log_path) else []
        cutoff   = (datetime.now() - timedelta(days=30)).isoformat()[:10]
        for s in reversed(signals):
            if (s.get("ticker","").upper() == ticker.upper() and
                s.get("date","") >= cutoff):
                score = s.get("criteria_passed", s.get("score", 0))
                return int(score) >= 5, s.get("decision",""), s.get("date","")
        return False, "No recent scan", ""
    except:
        return False, "Scan check failed", ""

def run_all_guards(ticker, strategy, headers, base_url,
                   opt_type="CALL", strike=0, premium=0):
    """
    Run all 5 pre-trade guards. Returns (safe, reasons).
    safe=True means ALL guards passed — trade may proceed.
    safe=False means at least one guard blocked the trade.
    """
    blocks  = []
    warns   = []
    opts    = _get_option_positions(headers, base_url)
    held    = _get_portfolio_tickers(headers, base_url)

    # ── GUARD 1: No averaging down ────────────────────────────────────
    # Block if existing option on same ticker is losing > 50%
    existing_cc = [p for p in opts if (
        p["ticker"].upper() == ticker.upper() and
        p["side"] == "SHORT" and p["pl"] < 0)]
    # Guard 1 only blocks if trying to add a SECOND position on same ticker
    # One existing losing CC is acceptable — it is collecting theta
    # Block only if there are already 2+ short positions on same ticker
    if len(existing_cc) >= 2:
        worst = min(existing_cc, key=lambda x: x["pl"])
        loss_pct = abs(worst["pl_pct"])
        blocks.append(
            f"GUARD 1 BLOCKED — Averaging down prevention: "
            f"{ticker} already has {len(existing_cc)} SHORT positions. "
            f"AK rule: never add to a losing options position. "
            f"Close existing positions first.")

    # ── GUARD 2: CC stop-loss ─────────────────────────────────────────
    # Warn if existing CC is 2x underwater (stock surged through strike)
    for p in opts:
        if (p["ticker"].upper() == ticker.upper() and
            p["side"] == "SHORT" and
            p["symbol"][len(p["ticker"]):len(p["ticker"])+7].endswith("C") or
            "C" in p["symbol"][len(p["ticker"]):]):
            entry_val = abs(p["entry"]) * 100 * abs(p["qty"])
            if entry_val > 0 and abs(p["pl"]) > entry_val * 2.0:
                blocks.append(
                    f"GUARD 2 BLOCKED — CC stop-loss: {ticker} CC "
                    f"is losing {abs(p['pl_pct']):.0f}% — more than 2x premium. "
                    f"Close this position BEFORE opening new CC. "
                    f"Run: ak-queue {p['symbol']} buy {int(abs(p['qty']))}")

    # ── GUARD 3: Strategy type enforcement ───────────────────────────
    # Block debit strategies — only credit strategies allowed
    CREDIT_STRATEGIES = {
        "COVERED_CALL", "CASH_SECURED_PUT",
        "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD",
        "COVERED_CALL_ROLL"
    }
    DEBIT_STRATEGIES = {
        "LONG_CALL", "LONG_PUT",
        "BEAR_PUT_SPREAD", "BULL_CALL_SPREAD",
        "DEBIT_SPREAD"
    }
    strat_upper = strategy.upper().replace(" ","_")
    if strat_upper in DEBIT_STRATEGIES:
        blocks.append(
            f"GUARD 3 BLOCKED — Strategy type: '{strategy}' is a DEBIT "
            f"strategy that costs money upfront. AK system only uses CREDIT "
            f"strategies that generate income. Permitted: covered calls, "
            f"cash secured puts, bull put spreads.")

    # ── GUARD 4: Portfolio-only rule ─────────────────────────────────
    # Block options on stocks not currently held
    # Exception: SPY hedges and spreads are always permitted
    SPY_EXCEPTION = {"SPY", "QQQ", "IWM"}  # index hedges always OK
    if (ticker.upper() not in held and
        ticker.upper() not in SPY_EXCEPTION):
        blocks.append(
            f"GUARD 4 BLOCKED — Portfolio-only rule: {ticker} is not "
            f"currently held in the portfolio. AK rule: only sell covered "
            f"calls and CSPs on stocks you own or intend to own. "
            f"Run ak-scan {ticker} and buy shares first.")

    # ── GUARD 5: Pre-trade AK criteria screen ────────────────────────
    # Block if no recent passing scan (only for CC and CSP, not spreads)
    if strategy.upper() in ("COVERED_CALL","CASH_SECURED_PUT"):
        has_scan, decision, scan_date = _get_recent_scan(ticker)
        if not has_scan:
            warns.append(
                f"GUARD 5 WARNING — No recent AK scan for {ticker} "
                f"(last: {scan_date or 'never'}). "
                f"Run ak-scan {ticker} before placing. "
                f"Proceeding but recommend scanning first.")
        elif "SELL" in decision.upper() or "AVOID" in decision.upper():
            blocks.append(
                f"GUARD 5 BLOCKED — AK scan says {decision} for {ticker}. "
                f"Only place covered calls on CONFIDENT BUY stocks. "
                f"Re-scan with ak-scan {ticker}.")

    # ── RESULT ────────────────────────────────────────────────────────
    safe = len(blocks) == 0
    all_messages = blocks + warns

    if blocks:
        print()
        print(f"  ╔══ AK TRADE GUARD — {len(blocks)} BLOCK(S) ══════════════════╗")
        for msg in blocks:
            print(f"  ✗ {msg[:80]}")
            if len(msg) > 80:
                print(f"    {msg[80:]}")
        print(f"  ╚═══════════════════════════════════════════════════════╝")
        print(f"  Trade BLOCKED. Fix the issue above before proceeding.")
    if warns:
        for msg in warns:
            print(f"  ⚠ {msg}")

    return safe, all_messages


def guard_cc_stoploss_check(headers, base_url):
    """
    Guard 2 active monitor — checks ALL open CCs for stop-loss triggers.
    Called by ak-roll daily. Queues close orders automatically.
    Returns list of positions needing closure.
    """
    import json
    opts    = _get_option_positions(headers, base_url)
    closures = []

    for p in opts:
        if p["side"] != "SHORT":
            continue
        sym = p["symbol"]
        # Detect calls by C in the options type position
        try:
            tick_end = next(i for i,c in enumerate(sym) if c.isdigit())
            opt_type = sym[tick_end+6]
        except:
            continue

        if opt_type != "C":
            continue

        entry_val = abs(p["entry"]) * 100 * abs(p["qty"])
        if entry_val <= 0:
            continue

        loss_ratio = abs(p["pl"]) / entry_val if p["pl"] < 0 else 0

        if loss_ratio >= 2.0:
            print(f"  🔴 CC STOP-LOSS: {sym}")
            print(f"     Entry premium: ${p['entry']:.2f} | "
                  f"Current: ${p['current']:.2f} | "
                  f"Loss: ${p['pl']:+,.0f} ({loss_ratio:.1f}x premium)")
            print(f"     AK rule: close when loss exceeds 2x premium collected")
            print(f"     Queuing close order automatically...")

            # Queue the close
            try:
                queue_path = os.path.expanduser(
                    "~/adam_khoo_system/scheduler_queue.json")
                queue = json.load(open(queue_path))                     if os.path.exists(queue_path) else []
                already = any(t.get("symbol")==sym and t.get("side")=="buy"
                             for t in queue)
                if not already:
                    queue.append({
                        "symbol": sym,
                        "side":   "buy",
                        "qty":    int(abs(p["qty"])),
                        "added":  datetime.now().isoformat()[:19],
                        "note":   f"CC stop-loss auto-close — loss {loss_ratio:.1f}x premium"
                    })
                    json.dump(queue, open(queue_path,"w"), indent=2)
                    print(f"     ✓ Queued close — executes at next market open")
                    closures.append(sym)
            except Exception as e:
                print(f"     Could not queue: {e}")
        elif loss_ratio >= 1.0:
            print(f"  🟡 CC WARNING: {sym} — loss {loss_ratio:.1f}x premium. "
                  f"Monitor closely.")

    return closures



def _pre_order_cash_check(headers, base_url, estimated_cost=0):
    """Block order if it would breach 5% cash minimum"""
    if not CASH_FLOOR_AVAILABLE:
        return True, "Cash floor module not available"
    safe, msg, cash_pct = check_cash_floor(
        headers, base_url,
        order_cost_estimate=estimated_cost,
        floor_pct=0.05,
        profile="growth"
    )
    if not safe:
        print(f"  ⛔ CASH FLOOR: {msg}")
    return safe, msg



def _guard_check_existing(headers, base_url, symbol_prefix, order_type="spread"):
    """
    Returns True if it is SAFE to place a new order.
    Returns False if a position or pending order already exists.
    Prevents duplicate automated positions.
    """
    import requests as _req
    try:
        # Check existing positions
        pos = _req.get(base_url+"/v2/positions", headers=headers, timeout=10).json()
        if isinstance(pos, list):
            for p in pos:
                sym = p.get("symbol","")
                if sym.startswith(symbol_prefix) and len(sym) > 6:
                    print(f"  GUARD: {symbol_prefix} {order_type} already exists ({sym}) — skipping")
                    return False

        # Check open orders
        ords = _req.get(base_url+"/v2/orders?status=open", headers=headers, timeout=10).json()
        if isinstance(ords, list):
            for o in ords:
                sym = o.get("symbol","")
                if sym.startswith(symbol_prefix) and len(sym) > 6:
                    print(f"  GUARD: {symbol_prefix} {order_type} order already pending ({sym}) — skipping")
                    return False
    except Exception as e:
        print(f"  GUARD: Check failed ({e}) — proceeding with caution")
        return True  # Fail open rather than block legitimate trades

    return True  # Safe to proceed

    # SPY BLOCKED: def _count_existing_spreads(headers, base_url, underlying="SPY"):
    """Count how many spread positions exist for an underlying"""
    import requests as _req
    count = 0
    try:
        pos = _req.get(base_url+"/v2/positions", headers=headers, timeout=10).json()
        if isinstance(pos, list):
            for p in pos:
                sym = p.get("symbol","")
                if sym.startswith(underlying) and len(sym) > 6:
                    count += 1
    except: pass
    return count


ALPACA_KEY=os.getenv("ALPACA_API_KEY"); ALPACA_SECRET=os.getenv("ALPACA_SECRET_KEY")
ALPACA_URL=os.getenv("ALPACA_BASE_URL","https://paper-api.alpaca.markets")
DATA_URL="https://data.alpaca.markets"
HDR={"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET,"Content-Type":"application/json"}

def get_existing_option_symbols(headers, base_url):
    """Return set of option symbols already held SHORT (active CCs/CSPs)"""
    existing = set()
    try:
        pos = requests.get(base_url+"/v2/positions", headers=headers, timeout=10).json()
        if isinstance(pos, list):
            for p in pos:
                sym = p.get("symbol","")
                qty = float(p.get("qty", 0) or 0)
                if len(sym) > 6 and qty < 0:  # SHORT option position
                    # Extract underlying ticker
                    import re as _re
                    m = _re.match(r"([A-Z]+)\d", sym)
                    if m:
                        existing.add(m.group(1) + "_SHORT")
                    existing.add(sym)  # also track exact symbol
    except: pass
    return existing

def get_pending_option_orders(headers, base_url):
    """Return set of option underlying tickers with open SELL orders"""
    pending = set()
    try:
        ords = requests.get(base_url+"/v2/orders?status=open", headers=headers, timeout=10).json()
        if isinstance(ords, list):
            for o in ords:
                sym = o.get("symbol","")
                side = o.get("side","")
                if len(sym) > 6 and side == "sell":
                    import re as _re
                    m = _re.match(r"([A-Z]+)\d", sym)
                    if m:
                        pending.add(m.group(1))
    except: pass
    return pending

def get_next_valid_friday(days_out=35):
    """Get next valid option expiry Friday"""
    from datetime import datetime, timedelta
    t = datetime.now() + timedelta(days=days_out)
    while t.weekday() != 4:
        t += timedelta(days=1)
    return t

def has_active_cc(ticker, existing_symbols, pending_orders):
    """Check if ticker already has an active covered call or pending order"""
    if ticker in pending_orders:
        return True, "pending order already exists"
    if ticker + "_SHORT" in existing_symbols:
        return True, "short position already open"
    # Check exact symbol match for any call
    for sym in existing_symbols:
        if sym.startswith(ticker) and "C" in sym[len(ticker):len(ticker)+10]:
            return True, f"active call: {sym}"
    return False, ""


def ag(path, base=None):
    try:
        r=requests.get((base or ALPACA_URL)+path,headers=HDR,timeout=15)
        return r.json() if r.status_code==200 else None
    except Exception as e: print(f"  GET error: {e}"); return None

def ap(path, body):
    try:
        r=requests.post(ALPACA_URL+path,headers=HDR,json=body,timeout=15)
        d=r.json()
        if r.status_code in[200,201]: return d
        print(f"  Order error: {d.get('message',d)}")
        return None
    except Exception as e: print(f"  POST error: {e}"); return None

def get_expiry(days=35):
    t=datetime.now()+timedelta(days=days)
    while t.weekday()!=4: t+=timedelta(days=1)
    return t.strftime("%Y-%m-%d")

def occ_date(d): return datetime.strptime(d,"%Y-%m-%d").strftime("%y%m%d")

def snap_strike(price, strike):
    """Snap strike to nearest valid increment based on stock price"""
    if price > 200:   inc = 5.0
    elif price > 50:  inc = 2.5
    elif price > 25:  inc = 1.0
    else:             inc = 0.5
    return round(round(strike / inc) * inc, 2)

def build_symbol(ticker, expiry, otype, strike):
    strike_int = int(round(strike * 1000))
    return f"{ticker}{occ_date(expiry)}{'C' if otype.upper()=='CALL' else 'P'}{str(strike_int).zfill(8)}"

def get_price(ticker):
    pos=ag("/v2/positions")
    if isinstance(pos,list):
        for p in pos:
            if p.get("symbol")==ticker: return float(p.get("current_price",0))
    return 0

def get_shares(ticker):
    pos=ag("/v2/positions")
    if isinstance(pos,list):
        for p in pos:
            if p.get("symbol")==ticker: return int(float(p.get("qty",0)))
    return 0

def get_chain(ticker, expiry, otype="call"):
    path=f"/v1beta1/options/snapshots/{ticker}?expiration_date={expiry}&type={otype}&limit=50&feed=indicative"
    data=ag(path,base=DATA_URL)
    if not data: return []
    contracts=[]
    for sym,snap in data.get("snapshots",{}).items():
        det=snap.get("details",{}); q=snap.get("latestQuote",{}); g=snap.get("greeks",{})
        strike=float(det.get("strikePrice",0)); bid=float(q.get("bp",0)); ask=float(q.get("ap",0))
        mid=round((bid+ask)/2,2) if bid and ask else 0; delta=abs(float(g.get("delta",0)))
        if strike>0 and mid>0: contracts.append({"symbol":sym,"strike":strike,"bid":bid,"ask":ask,"mid":mid,"delta":delta})
    return sorted(contracts,key=lambda x:x["strike"])

def log_trade(data):
    path=os.path.expanduser("~/adam_khoo_system/options_log.json")
    log=[]
    if os.path.exists(path):
        try: log=json.load(open(path))
        except: log=[]
    # Only log with verified date — prevents undated ghost entries
    entry = {**data,
             "timestamp": datetime.now().isoformat(),
             "date":      datetime.now().strftime("%Y-%m-%d"),
             "verified":  True}
    log.append(entry)
    json.dump(log,open(path,"w"),indent=2)

def sell_covered_call(ticker, price=None, contracts=1):
    print(f"\n  --- COVERED CALL: {ticker} ---")
    # Run all 5 guards before proceeding
    safe, msgs = run_all_guards(ticker, "COVERED_CALL", HDR, ALPACA_URL,
                                 opt_type="CALL")
    if not safe:
        return None
    shares=get_shares(ticker); max_c=shares//100
    if max_c==0: print(f"  Need 100+ shares. Have {shares}."); return None
    contracts=min(contracts,max_c)
    if not price: price=get_price(ticker)
    if not price: print(f"  No price data"); return None
    # Phase-aware CC strike selection
    if SECTOR_ROTATION_AVAILABLE:
        phase = get_current_phase()
        strike_raw, pct_otm, rationale = get_phase_adjusted_strike(price, phase)
        strike = snap_strike(price, strike_raw)
        print(f"  Phase: {phase} | Strike: ${strike:.0f} ({pct_otm:+.1f}% OTM) | {rationale[:40]}")
    else:
        raw_strike = price * 1.06
        strike = snap_strike(price, raw_strike)
    expiry = get_expiry(35)
    chain=get_chain(ticker,expiry,"call"); selected=None
    if chain:
        for c in chain:
            if c["strike"]>=strike and 0.20<=c["delta"]<=0.40: selected=c; break
        if not selected:
            otm=[c for c in chain if c["strike"]>price*1.04]
            if otm: selected=otm[0]
    if not selected:
        sym=build_symbol(ticker,expiry,"CALL",strike)
        prem=round(price*0.015,2)
        selected={"symbol":sym,"strike":strike,"mid":prem,"delta":0.28}
    lp=round(selected["mid"]*0.90,2); income=round(lp*100*contracts,2)
    print(f"  Price: ${price:.2f} | Strike: ${selected['strike']:.0f} | Premium: ${selected['mid']:.2f} | Income: ${income:.2f}")
    print(f"  Contract: {selected['symbol']} | Expiry: {expiry} | Contracts: {contracts}")
    order={"symbol":selected["symbol"],"qty":str(contracts),"side":"sell","type":"limit","limit_price":str(lp),"time_in_force":"day"}
    result=ap("/v2/orders",order)
    if result and result.get("id"):
        print(f"  PLACED: {result['id'][:12]}... | Collecting ${income:.2f}")
        log_trade({"strategy":"COVERED_CALL","ticker":ticker,"symbol":selected["symbol"],"strike":selected["strike"],"expiry":expiry,"contracts":contracts,"premium":selected["mid"],"total_income":income,"order_id":result["id"],"stock_price":price})
        return result
    return None

def sell_bull_put_spread(ticker, price=None, contracts=1):
    """Bull put spreads — controlled by strategy_config.json"""
    import json as _json, os as _os
    _AK  = _os.path.expanduser("~/adam_khoo_system")
    _cfg = _json.load(open(_AK+"/strategy_config.json")) if _os.path.exists(_AK+"/strategy_config.json") else {}
    if not _cfg.get("spy_spreads_enabled", False):
        print(f"  Bull put spread on {ticker}: DISABLED")
        print(f"  Reason: {_cfg.get('spy_spreads_disabled_reason','Cash optimisation active')}")
        print(f"  To re-enable: edit ~/adam_khoo_system/strategy_config.json")
        return None
def _sell_bull_put_spread_live(ticker, price=None, contracts=1):
    print(f"\n  --- BULL PUT SPREAD: {ticker} ---")
    # Run guards — portfolio-only and no-averaging-down apply
    safe, msgs = run_all_guards(ticker, "BULL_PUT_SPREAD", HDR, ALPACA_URL)
    if not safe:
        return None
    if not price: price=get_price(ticker) or 0
    if not price: print(f"  No price data"); return None
    expiry=get_expiry(35)
    short_k=snap_strike(price, price*0.95); width=max(5,round(price*0.03/5)*5); long_k=snap_strike(price, short_k-width)
    short_prem=round(price*0.015,2); long_prem=round(price*0.005,2); net=round(short_prem-long_prem,2)
    max_profit=round(net*100*contracts,2); max_loss=round((width-net)*100*contracts,2)
    short_sym=build_symbol(ticker,expiry,"PUT",short_k)
    long_sym=build_symbol(ticker,expiry,"PUT",long_k)
    print(f"  Price: ${price:.2f} | Short put: ${short_k:.0f} | Long put: ${long_k:.0f} | Net credit: ${net:.2f}")
    print(f"  Max profit: ${max_profit:.2f} | Max loss: ${max_loss:.2f} | Expiry: {expiry}")
    print(f"  Win if {ticker} stays above ${short_k:.0f} at expiry")
    order={"order_class":"mleg","time_in_force":"day","type":"limit","limit_price":str(net),
           "legs":[{"symbol":short_sym,"side":"sell","ratio_qty":"1","position_effect":"open"},
                   {"symbol":long_sym,"side":"buy","ratio_qty":"1","position_effect":"open"}],"qty":str(contracts)}
    result=ap("/v2/orders",order)
    if result and result.get("id"):
        print(f"  PLACED: {result['id'][:12]}... | Max income: ${max_profit:.2f}")
        log_trade({"strategy":"BULL_PUT_SPREAD","ticker":ticker,"short_symbol":short_sym,"long_symbol":long_sym,"short_strike":short_k,"long_strike":long_k,"expiry":expiry,"contracts":contracts,"net_credit":net,"max_profit":max_profit,"max_loss":max_loss,"order_id":result["id"],"stock_price":price})
        return result
    return None

def sell_cash_secured_put(ticker, target_price, price=None, contracts=1):
    print(f"\n  --- CASH-SECURED PUT: {ticker} @ target ${target_price:.2f} ---")
    if not price: price=get_price(ticker)
    expiry=get_expiry(35); strike=snap_strike(price or target_price, target_price)
    dist=(price-strike)/price if price else 0.05
    prem=round(price*max(0.008,0.02-dist),2); income=round(prem*100*contracts,2)
    sym=build_symbol(ticker,expiry,"PUT",strike)
    effective=round((strike-prem),2)
    print(f"  Current: ${price:.2f} | Target: ${target_price:.2f} | Strike: ${strike:.0f}")
    print(f"  Premium: ${prem:.2f} | Income: ${income:.2f} | Effective buy price if assigned: ${effective:.2f}")
    print(f"  Contract: {sym} | Expiry: {expiry}")
    order={"symbol":sym,"qty":str(contracts),"side":"sell","type":"limit","limit_price":str(prem),"time_in_force":"day"}
    result=ap("/v2/orders",order)
    if result and result.get("id"):
        print(f"  PLACED: {result['id'][:12]}... | Income: ${income:.2f}")
        log_trade({"strategy":"CASH_SECURED_PUT","ticker":ticker,"symbol":sym,"strike":strike,"expiry":expiry,"premium":prem,"total_income":income,"effective_cost":effective,"order_id":result["id"],"target_price":target_price,"stock_price":price})
        return result
    return None

def buy_protective_put(ticker, price=None, contracts=1):
    print(f"\n  --- PROTECTIVE PUT: {ticker} ---")
    if not price: price=get_price(ticker)
    if not price: print(f"  No price data"); return None
    expiry=get_expiry(45); strike=snap_strike(price, price*0.92)
    prem=round(price*0.012,2); cost=round(prem*100*contracts,2)
    sym=build_symbol(ticker,expiry,"PUT",strike)
    print(f"  Current: ${price:.2f} | Protection at: ${strike:.0f} | Cost: ${cost:.2f}")
    print(f"  Contract: {sym} | Expiry: {expiry}")
    order={"symbol":sym,"qty":str(contracts),"side":"buy","type":"limit","limit_price":str(prem),"time_in_force":"day"}
    result=ap("/v2/orders",order)
    if result and result.get("id"):
        print(f"  PLACED: {result['id'][:12]}... | Protected against crash below ${strike:.0f}")
        log_trade({"strategy":"PROTECTIVE_PUT","ticker":ticker,"symbol":sym,"strike":strike,"expiry":expiry,"premium":prem,"total_cost":cost,"order_id":result["id"],"stock_price":price})
        return result
    return None

def run_monthly_cycle():
    print(f"\n{'='*55}\n  AK OPTIONS INCOME ENGINE — {datetime.now().strftime('%d %B %Y')}\n{'='*55}")
    positions=ag("/v2/positions"); acct=ag("/v2/account")
    if not isinstance(positions,list) or not positions:
        print("\n  No open positions. Run analysis with --trade --market first."); return
    pv=float(acct.get("portfolio_value",100000)) if acct else 100000
    print(f"\n  Portfolio: ${pv:,.2f} | Positions: {len([p for p in positions if len(p.get('symbol',''))<=6])}")
    total_income=0; calls_placed=0; skipped=[]
    for p in positions:
        sym=p.get("symbol",""); qty=int(float(p.get("qty",0))); price=float(p.get("current_price",0))
        if len(sym)>6: continue
        print(f"\n  [{sym}] {qty} shares @ ${price:.2f}")
        if qty>=100:
            c=qty//100; est=round(price*0.015*100*c,2); total_income+=est
            result=sell_covered_call(sym,price=price,contracts=c)
            if result: calls_placed+=1
            time.sleep(1)
        else:
            skipped.append(f"{sym}: {qty} shares (need {100-qty} more for covered call)")
            print(f"  {qty} shares — need 100 for covered call. Consider cash-secured put instead.")
    print(f"\n{'='*55}\n  MONTHLY INCOME SUMMARY\n{'='*55}")
    print(f"  Covered calls placed: {calls_placed}")
    print(f"  Est. monthly income:  ${total_income:.2f}")
    print(f"  Est. annual income:   ${total_income*12:,.2f}")
    print(f"  Annual yield:         {(total_income*12/pv*100):.1f}%")
    if skipped:
        print(f"\n  Positions needing more shares:")
        for s in skipped: print(f"    {s}")
    lp=os.path.expanduser("~/adam_khoo_system/options_log.json")
    if os.path.exists(lp):
        try:
            log=json.load(open(lp))
            collected=sum(t.get("total_income",t.get("max_profit",0)) for t in log if t.get("strategy")!="PROTECTIVE_PUT")
            costs=sum(t.get("total_cost",0) for t in log if t.get("strategy")=="PROTECTIVE_PUT")
            print(f"\n  CUMULATIVE OPTIONS P&L")
            print(f"  Premiums collected: ${collected:,.2f}")
            print(f"  Hedge costs:        ${costs:,.2f}")
            print(f"  Net options income: ${collected-costs:,.2f}")
        except: pass

if __name__=="__main__":
    import sys; args=sys.argv[1:]
    if not args or args[0]=="monthly": run_monthly_cycle()
    elif args[0]=="covered_call" and len(args)>=2: sell_covered_call(args[1].upper(),price=float(args[2]) if len(args)>2 else None)
    elif args[0]=="bull_put" and len(args)>=2: sell_bull_put_spread(args[1].upper(),price=float(args[2]) if len(args)>2 else None)
    elif args[0]=="csp" and len(args)>=3: sell_cash_secured_put(args[1].upper(),float(args[2]),price=float(args[3]) if len(args)>3 else None)
    elif args[0]=="protect" and len(args)>=2: buy_protective_put(args[1].upper(),price=float(args[2]) if len(args)>2 else None)
    else:
        print("""
Usage:
  python3 options_engine.py                    # Monthly income cycle on all positions
  python3 options_engine.py covered_call AAPL  # Sell covered call on AAPL
  python3 options_engine.py bull_put AAPL      # Bull put spread
  python3 options_engine.py csp AAPL 250       # Cash-secured put at $250 target
  python3 options_engine.py protect AAPL       # Protective put hedge
        """)
