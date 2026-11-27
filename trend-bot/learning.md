# Learning log

One change at a time. Every change is logged here before it is tested.

## 2026-11-27: change 1, wide emergency stops (stop_mult 3.0 -> 7.0)

**Why:** First research run (test `95df2b8bd997`) failed 5 of 9 checks. 1,200 of
1,531 exits were stop-outs, and turnover was 12.4x equity a year (about 7-8% a
year in costs at 0.31% per side). The 3-sigma stop kept shaking the bot out of
trends, then it re-bought the following week. Hypothesis: let the trend rule
handle normal exits and keep the stop only for crashes.

**Kill criterion:** if research still fails, this approach doesn't beat holding
BTC on this data, and we stop rather than keep tuning.

**Result (test `f8b36a768b71`): improved, still FAILED 5 of 9.** Return after costs
went from +5.1% to +11.3% a year, Sharpe from 0.42 to 0.77, and turnover halved
(12.4x to 5.9x). Still fails walk-forward (48% of quarters positive), deflated
Sharpe (0.74 with 28 variants tried), Monte Carlo drawdown (-41.9% vs -20% kill
switch), buy-and-hold BTC on return/drawdown, and random entries (beats only 46%).

**Lesson:** random entries with the same sizing and stops score about as well, so
the edge here comes from volatility sizing and risk control, not from the
trend signal. The sma=50 grid rows look better, but choosing one now would be
picking the best of 28 tries on the same data. Kill criterion met: stop tuning.

## 2026-11-27: new hypothesis 2, volatility-sized BTC + ETH, always held

**Why:** Runs 1-2 showed random entries with the same sizing did as well as the
trend signal, so the value seemed to come from volatility sizing and risk
control. Test that part alone: hold BTC and ETH all the time, sized so the
portfolio targets a fixed volatility, with 7-sigma emergency stops. Who loses
to it: holders who keep full size through high-volatility crashes.

**Settings fixed before running:** config-volhold.yaml, target_vol 0.15, grid
target_vol [0.10, 0.15, 0.20]. Same 9 checks, same thresholds.

**Kill criterion:** if it fails the research checks, it goes to the graveyard. No tuning.

**Result (test `91045f54338d`): FAILED 4 of 9, the closest so far.** +6.3% a year,
Sharpe 1.10, max drawdown -9.3%, Monte Carlo worst-5% -15%. Passed costs, 2x
costs, walk-forward (65%), neighbours and Monte Carlo. Failed: too few trades
(66, because it rarely trades), deflated Sharpe 0.88 vs 0.90, BTC return/drawdown
(0.68 vs 0.76), random entries (beats 82%, needs 90%).

**Lesson:** it only reached 5.7% volatility, not the 15% target, because the 1%
risk-per-trade rule with 7-sigma stops capped each coin at about 4%. Its
risk-adjusted result is about the same as just holding BTC, with a far smaller
drawdown. That's a risk-controlled way to hold crypto, not an edge. Per the kill
criterion: graveyard, no tuning.
