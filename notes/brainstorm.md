# Brainstorm

Working notes. Anything that becomes settled fact moves to `docs/`.

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

One wheat harvest is worth ~25 coins. **Hiring 6+ hands every single day from day 1 is
almost certainly correct and probably the single biggest lever in the game.** First thing
to test: baseline vs. baseline+max-hire.

Open question: what's the real cap? Hands spawn adjacent to the shed and pathing overhead
grows, so there's a point where the marginal hand can't reach useful work. Measure it.

## Market: the numbers say a lot

From the price table (verified against our model in `tests/test_smoke.py`):

| Resource | Glut behaviour | Read |
|---|---|---|
| Wheat | P(I0+T)=$20, P(I0+2T)=$19 | **Nearly glut-proof.** Dump freely. |
| Egg | $40 / $39 | Same — `log` above-curve. |
| Carrot | $10 / $1 | Crashes hard. |
| Tomato | $24 / $9 | Crashes. |
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

## Town demand is the hidden clock

Every 3 days a random shop unlocks, permanently. Shops drain market inventory → inventory
below I0 → prices *rise*. Wheat appears in 5 of 8 shops; strawberry in 4; milk in 3.

Late game has strictly more demand than early game, and the town center goes 1× → 2× (day 10)
→ 4× (day 20). This argues for **holding premium goods and selling them late**, when
accumulated town consumption has pushed inventory down. Needs simulation to confirm the
magnitude — town drain is ~6/day/shop vs. I0 = 10,000, so it may be too small to matter
within 30 days. **Check this before building strategy on it.**

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

## Experiment queue

1. `wheat_loop` vs `random` and vs `starter` — establish the floor.
2. `wheat_loop` + hire 6 hands/day — measure the labor lever in isolation.
3. Add BUY_LAND timing sweep.
4. Wheat+geese engine vs. mixed-crop.
5. Endgame liquidation scheduler.
6. Market-aware sell throttling (use `agentlib.market.dump_capacity`).
