# AK Equity Income System
## Public paper-trading record

A rules-based US equity process: quality equities plus a covered-call income overlay.

Published by **LURAVA LTD** (England & Wales, Company No. 16411816).

### Important — please read

This is a **paper-trading record**: simulated results in a broker paper account. It is published for information only. It is **not** an offer, invitation or recommendation to invest or to use any service. Nothing on this page is investment advice or a personal recommendation.

---

## Performance (paper account)

| Metric | Value |
|--------|--------|
| As of | 30 October 2026 |
| Track record start | 17 April 2026 |
| Starting capital | $100,000 |
| Current NAV | $113,355.42 |
| Return since start | +13.36% |
| Peak NAV | $114,341.69 |
| Maximum drawdown (daily closes, broker history) | 7.31% |
| Strategy | Quality equities + covered-call income |

### Track record phases

| Phase | Period | Max drawdown (daily closes) |
|-------|--------|-----------------------------|
| Phase 1 — development | Apr–Aug 2026 | 7.31% (guards still being completed) |
| Phase 2 — full guards | Sep–Oct 2026 | 3.42% |

Phase 2 is the period in which the full risk-guard and exit framework was active. The phases are shown separately because the system was still being built during Phase 1.

Illustrative annualised rate of the period: about 26% (simple annualisation of the period to date only — **not** a forecast).

---

## How the process works (high level)

- **Selection:** rules-based screen (growth, profitability, trend, market-cycle conditions). Exact thresholds are not published.
- **Income:** covered calls on eligible holdings under defined rules.
- **Risk:** rules-based exits and volatility guards. When an exit rule triggers, the position is exited.

This describes a published process. It is not a personal recommendation.

### Anonymised position example (optional)

| Position | Return | Status |
|----------|--------|--------|
| Stock 1–4 | positive (paper) | Active / income rules as applicable |
| Stock 5 | negative (paper) | Exited when multiple exit rules triggered |

Tickers are not published. Stock 5 illustrates process: exit rules applied as designed.

---

## Performance ledger

[strategy_performance_ledger.csv](./strategy_performance_ledger.csv)

- One row per trading day from the broker paper account history  
- No tickers, code or strategy parameters in the public file  
- Drawdown on daily closes only (not intraday)

### Limitations of paper-trading results

No real money was at risk. Live trading can differ on fills, costs, taxes, slippage, option assignment and market impact. Results do not reflect the pressure of trading real capital. **Past performance is not a reliable indicator of future results.**

---


- **Model:** data / signal licensing — not discretionary portfolio management and not a pooled fund  
- **Delivery:** restricted API into the subscriber’s **own** Alpaca or Tradier account  
- **Custody:** 100% with the subscriber; LURAVA does not hold client money  
- **Not provided:** investment advice; personal recommendations; discretionary management  

**Contact**  
- Email: ak.quantsystem@proton.me  


---

*Paper track record only. Not an offer or recommendation to invest. Not investment advice. Past performance is not a reliable guide to future results.*
