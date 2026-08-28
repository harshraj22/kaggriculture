# Brainstorm

Working notes. Anything that becomes settled fact moves to `docs/`.

> **Status: most of this is unmeasured argument.** Everything below was reasoned
> from the spec tables before the evaluation harness existed. Where measurement
> has since contradicted it, that's marked inline. Treat unmarked claims as
> hypotheses, not findings.

## What the community has established (Aug 2026)

Sourced from the competition forum. Facts here are *other people's* measurements —
useful, and worth re-deriving before betting on.

**Reference scores.** Built-in `starter` ≈ 3,500. One competitor's public writeup
reports **15,394** for a wheat-bootstrap→melon agent, vs 2,687 for livestock-first
and **18** for all-melon. Our best is 3,889 — roughly `starter`, and ~25% of a
competent agent. That's the gap to close.

**The melon trap — my tile-day argument was right and incomplete.**
[Their table](https://www.kaggle.com/competitions/kaggriculture/discussion/737731)
puts melon at ~123 profit/tile-day vs wheat ~41 — the same ~3× ratio I derived.
But melon costs $80/tile and **pays nothing until day 10**: filling 25 tiles is
$2,000 of a $3,000 purse, you go broke around day 3, can no longer hire, one
farmer can't water 25 plants, and the crop dies before maturing. Final bank: 18.
**Tile-day value is necessary but not sufficient — cash flow and time-to-first-yield
gate it.** The working shape is wheat as bootstrap (pays from day 2), converting
tiles to melon out of realised profit.

**Optimise for dispersion, not just expectation.** The 1.32.7 change added ~nothing
at the median and a lot in the tail. A competitor argues the objective is
`Pr[win] = Φ(μ/σ)` — so **σ matters directly**, and a config with the same mean and
lower variance is strictly better. Our `evaluate.py` currently optimises
`mean_margin` alone, which ignores σ entirely. See "Framework consequence" below.

**Tomato looks under-adopted.** After 1.32.7, ladder-wide carrot planting jumped
6.3% → 44.2% of seat-games. Tomato — whose realised price ceiling moved *most*
(p99 128 → 786, max 1,803) — went 0.7% → 1.0%. Either everyone knows something, or
it's neglected. Worth measuring, cheaply.

**Engine facts that cost other people days.** All now in `docs/GAME_SPEC.md`,
all verified by me against 1.32.7 source: the shed is the four centre tiles (not a
findable tile, not "adjacent"); `SELL` only spends from the shed while `HARVEST`
fills the *unit's* inventory; `FEED` takes wheat from the acting unit's inventory,
not the shed.

**Turn budget.** 1s per turn, plus a 60s bank for the whole episode; only the
excess over 1s is deducted. So occasional slow turns are affordable — that's more
room for search than I assumed.

**Data available.** A public [kaggriculture-episodes dataset](https://www.kaggle.com/datasets/georgymamarin/kaggriculture-episodes)
of ~37k episodes with an `engine_version` column, plus a daily top-episodes dataset.
Free supervision for behaviour cloning and for checking what strong agents do.

## Measured so far (protocol v1, vs. `pass`, 60 paired episodes)

| config | mean score | note |
|---|---|---|
| `safe_only` | 3889 | SafeFarmer all season |
| `split_season` | 3623 | wheat_loop 10 days, then SafeFarmer |
| `baseline` | 3197 | wheat_loop whenever eligible |

From a 3000 stake, the best is +889 over 30 days. All three are far below what the
tile-day arithmetic below predicts, which means the bottleneck is execution, not
crop choice.

**`wheat_loop` is negative value.** It hires 6 hands/day and still plants ~15 seeds
per game. Action histogram over one episode: `HARVEST 2235, NORTH 1067, PASS 806,
WATER 45, PLANT 15`. Roughly 5,100 hand-actions bought, almost none of them useful.

## What the game actually rewards

Rating moves on **win/loss only** — margin is irrelevant. So the objective is
`P(beat a similarly-rated opponent)`, not expected profit. Consequences:

- Low-variance strategies that reliably clear a threshold beat high-variance moonshots.
- Once we're clearly ahead late in a game, dumping produce at bad prices is *free* — the
  only thing that matters is ending with more coins than them. Endgame liquidation should
  be aggressive and unconditional.
- We can see the opponent's money and farm every turn. That's a live scoreboard. Trailing
  late → take more risk. Leading late → mirror them and deny.

## The central tension: actions vs. land vs. market depth

Three separate budgets, and they bind at different times.

1. **Actions.** 24 turns/day/unit. Every plant needs 1 WATER + 1 HARVEST; every animal needs
   FEED + CARE + HARVEST + COLLECT_FERTILIZER. Movement between tiles is pure overhead.
2. **Land.** 25 tiles to start, 100 if you buy all three quadrants ($7k total).
3. **Market depth.** The real ceiling. Premium goods hit the $1 floor after a modest glut.

Early game action-bound → mid game land-bound → late game market-bound. The strategy has to
know which regime it's in.

## Labor is absurdly cheap

`HIRE` cost is fib(n) per day: 1, 1, 2, 3, 5, 8, 13, 21, 34, 55...

- 6 hands/day = **20 coins** for 144 extra actions.
- 10 hands/day = 143 coins for 240 extra actions.

One wheat harvest is worth ~25 coins. So hiring looks near-free, and I claimed this was
"almost certainly correct and probably the single biggest lever in the game."

**Measured: hiring alone is worthless, and can be worse than nothing.** `wheat_loop`
hires 6/day and loses to `safe_farmer`, which hires none. Buying actions is trivial;
*routing* them is the actual problem, and it's unsolved. Movement (`NORTH` 1067) and
no-op `HARVEST` (2235) dominate the action budget.

The open question was "what's the cap before marginal hands can't reach useful work" —
the real answer so far is that the cap is 0 until routing works, because idle hands
cost coins and produce nothing. Revisit once a strategy can actually direct them.

## Market: the numbers say a lot

From the price table (verified against our model in `tests/test_smoke.py`):

| Resource | Glut behaviour | Read |
|---|---|---|
| Wheat | P(I0+T)=$20, P(I0+2T)=$19 | **Nearly glut-proof.** Dump freely. |
| Egg | $40 / $39 | Same — `log` above-curve. |
| Carrot | $10 / $1 | Crashes on glut — but **scarcity now spikes** (`hinge`, 1.32.7). |
| Tomato | $24 / $9 | Crashes on glut — **largest scarcity upside** post-1.32.7. |
| Strawberry, Melon, Milk, Wool | $1 / $1 | **Floor after one field's output.** |

So melon at base $250 looks like the money crop, but T=300 with `sq` above-curve means
dumping a real harvest takes it to $1. Premium goods are **timing plays, not volume plays**:
sell a few units at a time, ideally when town demand has drained inventory below I0.

Conversely wheat/egg are **volume plays** — the price barely moves, so throughput is
everything. And wheat is dual-use: animal feed *and* a sellable product, and it's one of
only two things you can BUY_PRODUCT.

Hypothesis worth testing early: **wheat + geese, maximum hands, ignore premium crops
entirely.** Wheat feeds the geese, eggs are glut-proof at $40, geese produce daily from
day 4. That's a boring compounding engine with no price risk.

## Town demand is the hidden clock — WEAKENED by 1.32.6

Every 3 days a random shop unlocks, permanently. Shops drain market inventory → inventory
below I0 → prices *rise*. Wheat appears in 5 of 8 shops; strawberry in 4; milk in 3.

I argued late game has strictly more demand, because the town center ramped 1× → 2×
(day 10) → 4× (day 20), and concluded we should **hold premium goods and sell late**.

**1.32.6 deleted that ramp.** The town center now buys 1× of each product per tick,
flat, for the whole game. Demand still grows as shops unlock, but it no longer
accelerates, so the hold-for-late argument lost most of its force.

Worse for planning: shops are now drawn **with replacement**, so the demand mix is a
per-game roll — a game can have 3× Yarn Store and no Bakery. That argues for reading
`town.unlocked_shops` at runtime and adapting, rather than optimising against an
average game that never occurs.

## Fertilizer economics

- Buy at $100, or collect free from animals (1/animal/day after CARE).
- On a one-time crop it converts +1/watered-day into +2/watered-day over 3 days → +3 units.
- Melon at $250 × 3 = $750 for one FERTILIZE action. Wheat at $25 × 3 = $75.
- Free animal fertilizer is strictly better than selling it at $100 if we have anything
  premium to put it on.

## Endgame

Unsold inventory scores **zero**. There must be a hard liquidation phase:

- Compute, per product, how many turns of selling it takes to move the shed at acceptable prices.
- Stop planting anything that can't yield before day 30.
- Last 2–3 days: every market slot goes to SELL, spread across products to avoid stacking
  the same curve.
- The 10-orders/turn cap × 24 turns = 240 orders/day, so liquidation capacity is not the
  constraint — price impact is. Start earlier than feels right.

## Things to verify in code (not from the doc)

- [ ] Does `hands` in the action dict need to match the number of hired hands exactly?
- [ ] Can multiple units WATER the same tile in one turn (wasted) — does the env dedupe?
- [ ] Exact spawn ordering of hands around the shed (NWSE) — determines pathing plans.
- [ ] Does BUY_LAND cost scale with quadrants owned or purchases made?
- [ ] What does the built-in `"starter"` agent actually score? That's our real bar.
- [ ] Weed spawn 0.005/tile/day × 100 tiles × 30 days ≈ 15 weeds/game. Cheap to ignore?

## Framework consequence: the objective may be wrong

`tools/evaluate.py` sets `OBJECTIVE = "mean_margin"`. I chose it because win rate is
binary and noisy, and margin is a lower-variance correlated proxy. That reasoning holds
for *measurement*, but the community argument is that the thing being maximised is
`Pr[win] = Φ(μ/σ)` — in which **σ is not noise to be averaged away, it is part of the
objective**. Two configs with identical mean margin are not equally good; the tighter one
wins more often.

The 1.32.7 change makes this concrete: it added ~0 at the median and a lot in the tail,
i.e. it is a pure dispersion change. `mean_margin` is blind to it by construction.

We already record `stdev_margin`, so the fix is cheap — a `margin_z` objective of
`mean_margin / stdev_margin`. Worth doing before the first real sweep, since which
objective we optimise decides what the sweep finds. Two caveats: `stdev_margin` over 60
paired episodes is itself a noisy estimate, and a config that never loses gives σ→0 and
an unbounded score, so it needs a floor.

## Experiment queue

Run each with `python tools/evaluate.py --config configs/<x>.yaml`, then `compare.py`.

- [x] Establish the floor — `safe_only` 3889, `baseline` 3197 (protocol v1).
- [x] Labour lever in isolation — **negative**; see above.
- [ ] **Fix or delete `wheat_loop`.** Routing is the bottleneck: a unit that arrives
      at a tile and finds nothing to do should re-target, and no two units should
      claim the same tile. Until this works, nothing else is measurable.
- [ ] **Add `margin_z = mean_margin / stdev_margin` as an objective option** and decide
      which one the sweep uses. Cheap, and it changes what BO optimises for.
- [ ] **Wheat-bootstrap → melon**, the shape a competitor reports at 15,394. Our best is
      3,889, so this is the single biggest known gap.
- [ ] **Measure tomato.** Ladder adoption is 1% despite the largest post-1.32.7 ceiling
      move. Either neglected or a trap — one `--strategy` run answers it.
- [ ] **Read the episodes dataset** rather than guessing: what do high-bank agents plant,
      how many hands do they run, when do they sell?
- [ ] Land purchase timing (fixed order NE → SW → SE at $1k/$2k/$4k).
- [ ] Crop comparison — the tile-day argument above says melon ≫ wheat; untested.
- [ ] Endgame liquidation scheduler (unsold inventory scores zero).
- [ ] Market-aware sell throttling via `agentlib.market.dump_capacity`.
- [ ] Protocol v2 adding `starter` and self-play, once we beat `pass` convincingly.
