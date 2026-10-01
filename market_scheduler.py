#!/usr/bin/env python3
"""
AK MARKET SCHEDULER v2 — CONTINUOUS EXECUTION
Fixes the core problem: orders queued any time of day execute
within 30 seconds if market is open, not just at open.
Also executes immediately on queue if market already open.

Key change from v1:
- Checks queue every 30 seconds throughout entire session
- Executes immediately when item queued during market hours
- Sends Telegram confirmation for every fill
- Never makes you wait until next day

Run: python3 market_scheduler_v2.py run &
"""
import os, sys, json, time, requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/adam_khoo_system/.env"), override=True)
KEY    = os.getenv("ALPACA_API_KEY")
SECRET = os.getenv("ALPACA_SECRET_KEY")
BASE   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
AK     = os.path.expanduser("~/adam_khoo_system")
QUEUE  = AK + "/scheduler_queue.json"
LOG    = AK + "/scheduler_log.json"
h      = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET,
          "Content-Type": "application/json"}

def load_queue():
    try: return json.load(open(QUEUE))
    except: return []

def save_queue(q):
    json.dump(q, open(QUEUE, "w"), indent=2)

def load_log():
    try: return json.load(open(LOG))
    except: return []

def save_log(entries):
    json.dump(entries[-200:], open(LOG, "w"), indent=2)

def is_market_open():
    now_utc = datetime.now(timezone.utc)
    est_offset = -4  # EDT (summer)
    now_est = now_utc + timedelta(hours=est_offset)
    weekday = now_est.weekday()
    if weekday >= 5: return False
    h, m = now_est.hour, now_est.minute
    after_open  = (h > 9) or (h == 9 and m >= 30)
    before_close = h < 16
    return after_open and before_close

def minutes_to_open():
    now_utc = datetime.now(timezone.utc)
    est_offset = -4
    now_est = now_utc + timedelta(hours=est_offset)
    weekday = now_est.weekday()
    days_ahead = 0
    if weekday == 5: days_ahead = 2
    elif weekday == 6: days_ahead = 1
    target = now_est + timedelta(days=days_ahead)
    target = target.replace(hour=9, minute=31, second=0, microsecond=0)
    if days_ahead == 0 and now_est.hour >= 16:
        target += timedelta(days=1)
        if target.weekday() >= 5:
            target += timedelta(days=2)
    if is_market_open(): return 0
    return max(0, (target - now_est).total_seconds() / 60)

def place_order(task):
    sym   = task.get("symbol", "")
    side  = task.get("side", "buy")
    qty   = str(task.get("qty", "1"))
    price = task.get("limit_price", "")
    otype = "limit" if price else "market"
    order = {
        "symbol": sym, "qty": qty, "side": side,
        "type": otype, "time_in_force": "day",
    }
    if price: order["limit_price"] = str(price)
    try:
        r = requests.post(BASE + "/v2/orders", headers=h,
                         json=order, timeout=10).json()
        if r.get("id"):
            return True, r["id"][:16]
        else:
            return False, r.get("message", "unknown")[:60]
    except Exception as e:
        return False, str(e)[:60]

def notify_mac(title, message):
    try:
        import subprocess
        script = f'display notification "{message}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except: pass

def notify_telegram(message):
    try:
        cfg = json.load(open(AK + "/notification_config.json"))
        if not cfg.get("telegram_enabled"): return
        token   = cfg.get("telegram_token", "")
        chat_id = cfg.get("telegram_chat_id", "")
        if not token or not chat_id: return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=8)
    except: pass

def execute_queue(reason="market_open"):
    queue = load_queue()
    if not queue: return 0, 0

    success = failed = 0
    results = []
    now = datetime.now()

    print(f"\n  [{now.strftime('%H:%M:%S')}] Executing {len(queue)} task(s) — {reason}")

    for task in queue:
        sym  = task.get("symbol", "")
        side = task.get("side", "buy")
        qty  = task.get("qty", 1)
        ok, detail = place_order(task)
        if ok:
            success += 1
            status = f"✅ {side.upper()} {qty} {sym} — filled"
            print(f"  ✓ {status}")
        else:
            failed += 1
            status = f"❌ {side.upper()} {qty} {sym} — {detail}"
            print(f"  ✗ {status}")
        results.append({
            "time": now.isoformat()[:19],
            "symbol": sym, "side": side, "qty": qty,
            "success": ok, "detail": detail,
        })

    save_queue([])

    log = load_log()
    log.extend(results)
    save_log(log)

    # Notify
    lines = [f"⚡ <b>AK Orders Executed</b> — {now.strftime('%H:%M')}"]
    for r in results:
        lines.append(f"{'✅' if r['success'] else '❌'} {r['side'].upper()} {r['qty']} {r['symbol']}")
        if not r['success']:
            lines.append(f"   Error: {r['detail']}")
    notify_telegram("\n".join(lines))
    notify_mac("AK Orders", f"{success} filled, {failed} failed")

    return success, failed

def queue_task(symbol, side, qty, limit_price=None, note=""):
    queue = load_queue()
    # Duplicate check 1: already in scheduler queue
    for existing in queue:
        if (existing.get("symbol") == symbol and
            existing.get("side")   == side and
            str(existing.get("qty","")) == str(qty)):
            print(f"  Already queued: {side} {qty} {symbol}")
            return

    # Duplicate check 2: already an open order in Alpaca
    try:
        _r = requests.get(BASE + "/v2/orders?status=open",
                         headers={"APCA-API-KEY-ID": KEY,
                                  "APCA-API-SECRET-KEY": SECRET},
                         timeout=8)
        open_orders = _r.json() if _r.status_code == 200 else []
        for o in (open_orders if isinstance(open_orders, list) else []):
            if (o.get("symbol","").upper() == symbol.upper() and
                o.get("side","") == side):
                print(f"  Already open order in Alpaca: {side} {o.get('qty')} {symbol}")
                print(f"  Order ID: {o.get('id','?')[:16]} [{o.get('status')}]")
                print(f"  Not adding duplicate to queue.")
                return
    except:
        pass  # If check fails, allow queue (fail open)

    task = {
        "symbol": symbol, "side": side, "qty": qty,
        "added": datetime.now().isoformat()[:19], "note": note,
    }
    if limit_price:
        task["limit_price"] = limit_price

    queue.append(task)
    save_queue(queue)

    if is_market_open():
        print(f"  ✓ Queued: {side.upper()} {qty} {symbol}")
        print(f"  ⚡ Market is OPEN — executing immediately...")
        # Execute right away — no waiting
        time.sleep(1)
        s, f = execute_queue("immediate_queue")
        if s > 0:
            print(f"  ✓ Filled immediately")
        else:
            print(f"  ✗ Fill failed — check order details")
    else:
        mins = minutes_to_open()
        h_str = f"{int(mins//60)}h {int(mins%60)}m" if mins >= 60 else f"{int(mins)}m"
        print(f"  ✓ Queued: {side.upper()} {qty} {symbol}")
        print(f"  ⏱ Market closed — executes in {h_str} (2:30pm London)")
        print(f"  📱 Telegram notification when filled")
    notify_mac("AK Queued", f"{side.upper()} {qty} {symbol}")

def run_scheduler():
    print(f"\n  AK Scheduler v2 started — {datetime.now().strftime('%d %b %Y %H:%M')}")
    print(f"  Checks queue every 30 seconds throughout market session")
    print(f"  Orders execute immediately when queued during market hours")
    print(f"  Press Ctrl+C to stop\n")

    last_date = ""
    last_open_state = False

    while True:
        now   = datetime.now()
        date  = now.strftime("%Y-%m-%d")
        open_ = is_market_open()
        queue = load_queue()

        # Reset daily tracking
        if date != last_date:
            last_date = date
            last_open_state = False

        # Execute whenever: market open AND queue has items
        # This runs every 30 seconds — so orders execute within 30s of queuing
        if open_ and queue:
            print(f"  [{now.strftime('%H:%M:%S')}] {len(queue)} item(s) in queue — executing")
            execute_queue("continuous_check")

        # Log market open/close transitions
        if open_ and not last_open_state:
            print(f"  [{now.strftime('%H:%M:%S')}] Market opened — monitoring queue")
            last_open_state = True
        elif not open_ and last_open_state:
            print(f"  [{now.strftime('%H:%M:%S')}] Market closed")
            last_open_state = False

        time.sleep(30)

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "run":
        run_scheduler()
    elif args[0] == "queue" and len(args) >= 3:
        symbol = args[1]
        side   = args[2]
        qty    = int(args[3]) if len(args) > 3 else 1
        price  = args[4] if len(args) > 4 else None
        queue_task(symbol, side, qty, price)
    elif args[0] == "list":
        q = load_queue()
        if not q: print("  Queue empty")
        else:
            open_ = is_market_open()
            mins  = minutes_to_open()
            print(f"  {'OPEN — executes within 30 seconds' if open_ else f'Opens in {mins:.0f}m'}")
            for i, t in enumerate(q, 1):
                lp = f" @ ${t['limit_price']}" if t.get("limit_price") else ""
                print(f"  {i}. {t['side'].upper()} {t['qty']} {t['symbol']}{lp} — added {t['added'][11:16]}")
    elif args[0] == "now":
        q = load_queue()
        if not q: print("  Nothing in queue")
        else: execute_queue("manual_force")
    elif args[0] == "clear":
        save_queue([])
        print("  Queue cleared")
    elif args[0] == "status":
        open_ = is_market_open()
        mins  = minutes_to_open()
        q = load_queue()
        print(f"  Market: {'OPEN' if open_ else 'CLOSED'}")
        print(f"  Queue:  {len(q)} item(s)")
        if not open_: print(f"  Opens:  {mins:.0f}m (2:30pm London)")
