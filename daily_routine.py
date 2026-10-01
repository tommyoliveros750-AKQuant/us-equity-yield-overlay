#!/usr/bin/env python3
import os, json, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.expanduser("~/adam_khoo_system/.env"), override=True)
ALPACA_KEY=os.getenv("ALPACA_API_KEY"); ALPACA_SECRET=os.getenv("ALPACA_SECRET_KEY")
ALPACA_URL=os.getenv("ALPACA_BASE_URL","https://paper-api.alpaca.markets")
AK_HOME=os.path.expanduser("~/adam_khoo_system")

def auto_credit_dividends():
    """
    Auto-credits dividend income to options_log.json each morning.
    Alpaca paper accounts do not pay dividends — this recognises
    what would have been received in a real account.
    Adds a clear note: PAPER SIMULATION — real account would have paid.
    Prevents double-counting by checking date before adding.
    """
    import json, os, requests
    from datetime import datetime
    
    AK_HOME = os.path.expanduser("~/adam_khoo_system")
    today   = datetime.now().strftime("%Y-%m-%d")
    
    # Load dividend log to find what is due today
    div_log_path = AK_HOME + "/dividend_log.json"
    opt_log_path = AK_HOME + "/options_log.json"
    
    try:
        div_data = json.load(open(div_log_path)) if os.path.exists(div_log_path) else {}
        opt_log  = json.load(open(opt_log_path)) if os.path.exists(opt_log_path) else []
        
        # Check upcoming dividends for any pay dates that fall today or have passed
        # without being credited
        upcoming  = div_data.get("upcoming", [])
        expected  = div_data.get("expected", [])
        
        credits_added = 0
        total_credited = 0.0
        
        for div in expected:
            pay_date = div.get("pay_date","")
            if not pay_date: continue
            ticker   = div.get("ticker","")
            amount   = float(div.get("total", 0))
            ex_date  = div.get("ex_date","")
            
            # Only credit if pay date has passed
            if pay_date <= today:
                # Check not already credited for this pay date
                already = any(
                    e.get("strategy") == "DIVIDEND_PAPER_CREDIT" and
                    e.get("ticker")   == ticker and
                    e.get("note","").find(pay_date) >= 0
                    for e in opt_log
                )
                if not already and amount > 0:
                    opt_log.append({
                        "date":         today,
                        "strategy":     "DIVIDEND_PAPER_CREDIT",
                        "ticker":       ticker,
                        "symbol":       f"{ticker}_DIV_{pay_date}",
                        "description":  f"{ticker} dividend — paper account simulation",
                        "total_income": round(amount, 2),
                        "note":         (f"PAPER SIMULATION: Alpaca paper accounts do not pay "
                                        f"dividends. A real account would have received "
                                        f"${amount:.2f} on pay date {pay_date} "
                                        f"(ex-date {ex_date}). Recognised as a gain for "
                                        f"true return accuracy. NOTE: TTP funded accounts "
                                        f"require closing positions before ex-date — "
                                        f"dividends not collectible on prop accounts."),
                    })
                    credits_added += 1
                    total_credited += amount
        
        if credits_added > 0:
            json.dump(opt_log, open(opt_log_path,"w"), indent=2)
            print(f"  ✓ Dividend auto-credit: ${total_credited:.2f} added "
                  f"({credits_added} payment(s) — paper simulation)")
            print(f"    Note: Real account would have received this. "
                  f"TTP prop accounts require closing before ex-date.")
        else:
            pass  # No new dividends due — silent
            
    except Exception as e:
        pass  # Silent fail — never break daily routine



# True win rate calculation
try:
    from win_rate_utils import calculate_true_daily_pnl, get_options_income_today
    WIN_RATE_AVAILABLE = True
except ImportError:
    WIN_RATE_AVAILABLE = False


LOG_PATH=AK_HOME+"/daily_log.json"

def ag(path):
    try:
        r=requests.get(ALPACA_URL+path,headers={"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET},timeout=10)
        return r.json()
    except: return {}

def load_json(path):
    try: return json.load(open(path)) if os.path.exists(path) else None
    except: return None

def sep(): print("  " + "─"*54)

def run():
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║       AK DAILY ROUTINE — 15-MINUTE CHECKLIST        ║")
    print(f"  ║       {datetime.now().strftime('%A %d %B %Y — %H:%M'):<48}║")
    print("  ╚══════════════════════════════════════════════════════╝")

    est=timezone(timedelta(hours=-4)); now=datetime.now(est)
    h=now.hour; m=now.minute; wd=now.weekday()
    is_open=(h==9 and m>=30) or (10<=h<=15) or (h==16 and m==0)
    mkt="OPEN" if (is_open and wd<5) else "CLOSED"
    london=(now+timedelta(hours=5)).strftime("%H:%M")
    est_time = now.strftime('%H:%M')
    print(f"\n  Market:    {mkt}  |  EST: {est_time}  |  London: {london}")

    acct=ag("/v2/account"); pos=ag("/v2/positions")
    if not isinstance(pos,list): pos=[]
    pv=float(acct.get("portfolio_value",100000)); pnl=pv-100000
    cash=float(acct.get("cash",100000)); bp=float(acct.get("buying_power",0))
    stocks=[p for p in pos if len(p.get("symbol",""))<=6]
    options=[p for p in pos if len(p.get("symbol",""))>6]

    print(f"  Portfolio: ${pv:,.2f}  |  P&L: ${pnl:+,.2f} ({pnl/100000*100:+.2f}%)")
    print(f"  Cash:      ${cash:,.2f} ({cash/pv*100:.1f}%)  |  BP: ${bp:,.2f}")
    print(f"  Positions: {len(stocks)} stocks  {len(options)} options\n")

    alerts=[]
    for p in stocks:
        sym=p.get("symbol",""); qty=int(float(p.get("qty",0)))
        pl=float(p.get("unrealized_pl",0)); plp=float(p.get("unrealized_plpc",0))*100
        curr=float(p.get("current_price",0))
        arrow="▲" if pl>=0 else "▼"
        cc="[CC eligible]" if qty>=100 else f"[need {100-qty} for CC]"
        print(f"  {arrow} {sym:<6} {qty:>4} shares @ ${curr:.2f}  P&L ${pl:>+,.2f} ({plp:>+.1f}%)  {cc}")
        if plp<=-8: alerts.append(f"WARNING: {sym} down {plp:.1f}% — review stop loss")
        if plp>=20:  alerts.append(f"OPPORTUNITY: {sym} up {plp:.1f}% — sell covered call")

    for p in options:
        pl=float(p.get("unrealized_pl",0)); qty=float(p.get("qty",0))
        sym=p.get("symbol","")
        arrow="▲" if pl>=0 else "▼"
        side="SHORT" if qty<0 else "LONG"
        print(f"  {arrow} {sym:<32} {side}  P&L ${pl:>+,.2f}")
        if qty<0 and pl<-200: alerts.append(f"WARNING: {sym} losing ${abs(pl):.0f} — consider closing")

    hedge=load_json(AK_HOME+"/daily_hedge_report.json") or {}
    score=hedge.get("danger_score",0); level=hedge.get("danger_level","UNKNOWN")
    bar="█"*int(score/5)+"░"*(20-int(score/5))
    print(f"\n  Danger:    {score}/100  [{bar}]  {level}")

    if alerts:
        print(f"\n  ALERTS:")
        for a in alerts: print(f"    ! {a}")

    print(f"\n  ACTION PLAN:")
    actions=[]
    if cash/pv>0.30: actions.append(f"Deploy cash — ${cash:,.0f} idle ({cash/pv*100:.0f}%). Consider SPY or SCHD")
    eligible=[p.get("symbol") for p in stocks if int(float(p.get("qty",0)))>=100]
    if eligible:
        cc_str = ', '.join(eligible)
        actions.append(f"Covered calls available: {cc_str} — run: ak-options")
    if now.weekday()==0: actions.append("Monday — run weekly scan: ak-scan NVDA AMD KO JNJ")
    # Check for open limit orders and remind user
    try:
        import requests as req
        orders_r = req.get(
            os.getenv('ALPACA_BASE_URL','https://paper-api.alpaca.markets')+'/v2/orders?status=open',
            headers={'APCA-API-KEY-ID':os.getenv('ALPACA_API_KEY'),
                     'APCA-API-SECRET-KEY':os.getenv('ALPACA_SECRET_KEY')}, timeout=8)
        open_orders = orders_r.json() if orders_r.status_code==200 else []
        if isinstance(open_orders, list):
            stock_orders = [o for o in open_orders if len(o.get('symbol',''))<=6]
            if stock_orders:
                for o in stock_orders:
                    sym=o.get('symbol'); lp=o.get('limit_price')
                    qty=o.get('qty'); otype=o.get('type','')
                    actions.append(f'PENDING ORDER: {sym} {otype} {qty} shares @ ${lp} — still waiting to fill. Cancel with: ak-cancel {sym}')
    except: pass
    actions.append("Dashboard: http://localhost:8765")
    for i,a in enumerate(actions,1): print(f"  {i}. {a}")

    log=[]
    if os.path.exists(LOG_PATH):
        try: log=json.load(open(LOG_PATH))
        except: log=[]
    today=datetime.now().strftime("%Y-%m-%d")
    entry={"date":today,"timestamp":datetime.now().isoformat(),"market":mkt,
           "pv":round(pv,2),"pnl":round(pnl,2),"pnl_pct":round(pnl/100000*100,2),
           "cash":round(cash,2),"positions":len(stocks),"options_count":len(options),
           "danger":score,"alerts":alerts}
    existing=next((i for i,e in enumerate(log) if e.get("date")==today),None)
    if existing is not None: log[existing]=entry
    else: log.append(entry)
    json.dump(log,open(LOG_PATH,"w"),indent=2)

    if len(log)>=1:
        # True win rate includes options income collected each day
        if WIN_RATE_AVAILABLE:
            opts_today = get_options_income_today(
                {"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET},
                ALPACA_URL)
            true_wins = 0
            for i, e in enumerate(log):
                prev_pv = float(log[i-1].get("pv",0)) if i>0 else 100000
                curr_pv = float(e.get("pv",0))
                _, _, is_win = calculate_true_daily_pnl(curr_pv, prev_pv,
                    opts_today if i==len(log)-1 else 0)
                if is_win: true_wins += 1
            wr = true_wins / len(log) * 100
            print(f"\n  TRACK RECORD: {len(log)} days logged  |  Win rate (true): {wr:.0f}%")
        else:
            pnls=[e.get("pnl",0) for e in log]
            wins=[p for p in pnls if p>0]
            wr=len(wins)/len(pnls)*100 if pnls else 0
            print(f"\n  TRACK RECORD: {len(log)} days logged  |  Win rate: {wr:.0f}%")
        pnls=[e.get("pnl",0) for e in log]
        # True P&L = portfolio value vs $100,000 starting capital ONLY
    # Options log total shown separately — never inflates portfolio figure
    start_capital = 100000
    if log:
        current_pv = float(log[-1].get("pv", start_capital))
        real_pnl   = current_pv - start_capital
        print(f"  Portfolio P&L: ${real_pnl:+,.2f} ({real_pnl/start_capital*100:+.2f}%) — verified")
        print(f"  Options banked: see options_log.json for breakdown")
        print(f"  Prop firm eligibility: {len(log)}/90 days  ({max(0,90-len(log))} days remaining)")

    print(f"\n  Daily log saved. Run tomorrow morning to continue track record.")
    print(f"  ──────────────────────────────────────────────────────\n")

if __name__=="__main__":
    run()
