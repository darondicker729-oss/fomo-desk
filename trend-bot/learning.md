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
