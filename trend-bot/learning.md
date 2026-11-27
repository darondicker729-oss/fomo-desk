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

## 2026-11-27: new hypothesis 3, funding carry (delta-neutral)

**Why:** Perpetual futures pay "funding" between longs and shorts every 8 hours.
In crypto, leveraged longs usually outnumber shorts, so longs pay shorts. Holding
spot and shorting the same coin's perp cancels out price moves and collects that
payment. Who loses to it: leveraged speculators paying to hold their bets.

**Rules fixed before running (config-carry.yaml):**
- Per coin: enter when the 7-day average funding is above 10% a year, exit when it
  falls below 0. Decide at the daily close, act from the next day.
- Equal weight across active coins, max 25% of capital per coin.
- Capital per $1 hedged: $1 spot + $0.50 perp margin, so returns are on $1.50.
- Costs per side: spot 0.10% fee + 0.05% slippage; perp 0.05% fee + 0.05% slippage.
- Margin risk: if a coin rallies 40% above its level at the last weekly top-up, the
  short is assumed force-closed, with 1% extra slippage on top of normal costs.
- Grid: entry threshold [5%, 10%, 20% a year] x lookback [3, 7, 14 days].
- Same 9 checks and thresholds as before. Trials share the same all-time count.

**Not modelled (real risks):** the exchange failing or freezing withdrawals (as FTX
did in 2022), funding schedule changes, and borrowing or transfer delays.

**Kill criterion:** if it fails the research checks, graveyard. No tuning.

**Result (test `5cef38f8680c`): FAILED 1 of 9, the best result so far.** +6.7% a year
after costs, max drawdown -1.7%, beats 100% of random runs, 74% of walk-forward
quarters positive. Failed only the deflated Sharpe check (0.82 vs 0.90).

**Why it failed the luck check:** returns have very fat tails (excess kurtosis 32,
from forced margin closes and funding spikes), and the trial pool mixes trend and
carry variants. Counting only the 15 carry variants gives 1.00. The pre-registered
rule counts all trials, so the verdict stands: FAIL.

**The bigger problem, by year (real costs):** 2020 +12.5%, 2021 +21.2%, 2022 -1.1%,
2023 +3.1%, 2024 +5.0%, 2025 +0.4%. Nearly all the profit came from the 2020-21
mania. Since 2022 it has averaged about +1.9% a year, below US Treasury bill yields,
with exchange risk on top. At 2x costs, 2022 and 2025 lose money. US venues
(Coinbase, CME) likely pay less funding than Binance, so it would be worse there.

**Lesson:** carry is a real edge, but it pays mainly in speculative manias, and
lately it hasn't beaten a risk-free savings rate. Revisit only if funding stays
high for months (the signal itself would show this), and test on US venue data.

## 2026-11-27: new hypothesis 4, selling cash-secured S&P 500 puts

**Why:** Options buyers pay extra for crash insurance (the "volatility risk
premium"), so sellers of puts tend to be paid more than the losses they cover.
Who loses to it: hedgers buying protection. Documented in academic and Cboe research.

**Test (no settings chosen, so no tuning possible):** the Cboe PUT index, which sells
a one-month at-the-money S&P 500 put each month, backed by Treasury bills. Daily data
from 2007. Retail cost drag assumed 0.5% a year (bid-ask and commissions), and 1%
for the 2x test. ASSUMPTION, not data. Research period: 2007-01-01 to 2025-11-26.
Holdout: 2025-11-27 onwards, looked at once.

**Pass if all hold in the research period:**
1. Beats T-bills (cash) after costs, and at 2x costs.
2. Sharpe ratio at least as high as the S&P 500 (price index; true total return is
   higher by the dividend yield, noted in the result).
3. Smaller max drawdown than the S&P 500.
4. The last 5 research years (2021-2025) still beat T-bills after costs.
Then the holdout once: positive and ahead of T-bills after costs.

**Result: FAILED 1 of 4 research checks (no holdout run).** 2007-2025 after costs:
+6.4% a year, vol 14%, max drawdown -37% (S&P price index: +8.7%, vol 20%, max
drawdown -57%, before ~2% dividends). Beat cash in every test and 72% of months
were positive, but its Sharpe (0.41) was below the S&P's (0.45), even before the
S&P's dividends. Crash months were as bad as the S&P: Oct 2008 -17.7%, Mar 2020 -13.5%.

**Lesson:** selling puts behaves like owning about 70% of the S&P 500. The steady
"income" is stock-market risk paid out monthly. An index fund did as well or better
for the risk, with less work and simpler taxes. Graveyard.

## 2026-11-27: new hypothesis 5, Kalshi favourite-longshot bias

**Why:** In betting markets, long shots tend to be overpriced and favourites
underpriced, because people overpay for lottery-like payoffs. Who loses to it:
bettors buying cheap "yes" tickets on unlikely outcomes.

**Rules fixed before collecting data:**
- Sample: settled binary Kalshi markets (no multi-leg parlays), volume of at least
  1,000 contracts, sampled evenly by month.
- Price: last trade at least 3 hours before the market's close. Markets with no
  such trade are skipped.
- Bet: buy the favourite side (yes or no) when it is priced 80c or more.
- Costs: Kalshi taker fee 0.07 x P x (1-P) per contract (assumes 100-contract
  orders) plus 1c slippage. 2x test doubles both. ASSUMPTION on slippage.
- Research: markets closing 2025-06-01 to 2026-08-26. Holdout: closing
  2026-08-27 onwards, looked at once.

**Pass if:** at least 300 bets; average profit per $1 staked above 0 after costs,
with the 95% bootstrap interval (resampling whole events) above 0; still positive at
2x costs; positive in both halves of the research period. Then holdout once.

**Method change before seeing any results:** Kalshi's historical endpoint ignores
date filters, so sampling by month isn't possible. Instead: a fixed list of 20
long-running series chosen across categories (KXNFLGAME, KXNBAGAME, KXMLBGAME,
KXNHLGAME, KXNCAAFGAME, KXNCAAMBGAME, KXHIGHNY, KXHIGHCHI, KXHIGHMIA, KXHIGHLAX,
KXFED, KXFEDDECISION, KXCPI, KXCPIYOY, KXPAYROLLS, KXGDP, KXBTCD, KXINX, KXTSAW,
KXRT), up to 300 random eligible markets per series from up to 20 pages of
listings, closing from 2025-06-01. All other rules unchanged.
