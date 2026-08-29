# Next strategy, derived from the engine — not from a replay

The replay told us our model was wrong. It should not tell us our numbers. Every
figure below comes from `CROPS` / `ANIMALS` / `MARKET_PARAMS` / `SHOPS` and from
`pass`-vs-`pass` measurement, none from an opponent's board.

## 1. Demand is a RATE, and it is the real ceiling

Shops consume every 4 turns (6x/day); a shop selling exactly ONE product
consumes at **2x**. The town centre takes 1 of everything per day. Measured
season drain, averaged over 10 train seeds in a game where nobody sells:

    product      season drain   base   sustainable revenue
    WOOL                  289    200            57,840
    STRAWBERRY            406    120            48,744
    MILK                  273    160            43,680
    WHEAT                 559     25            13,980
    EGG                   273     50            13,650
    TOMATO                199     60            11,952
    CARROT                284     35             9,933
    MELON                  30    250             7,500

**Melon absorbs 30 units a season. We sold 110.** That is why its price fell
266 -> 62: we produced nearly 4x what the world wanted. `melon_farm` is built on
the thinnest market on the board.

**WOOL is the single most valuable market**, despite having the FEWEST shops
(1.3 on average). `YARN_STORE` sells only wool, so it drains at double rate.
That is invisible from watching anyone play and falls straight out of the code.

Robustness: across 30 train seeds, MILK has shop demand in 29, EGG in 28, WOOL
in 21, and there is no seed where all three are absent. This is structural, not
a lucky shop roll.

## 2. Animals convert labour ~7x better than wheat

Verified against `_daily_refresh_animals`: production every `interval` days is
`1 + care bonus accrued since the last one`, bonus +1 per fed-and-cared day.
(`docs/GAME_SPEC.md` says +2 — that is a doc error, fix it.)

    animal   unit/day   coin/day   coin/turn
    COW          1.50        240         107
    SHEEP        1.17        234         104
    GOOSE        2.00        100          44
    wheat           -          -          14   <- what we do today

Cross-check: this model predicts 1.17 wool/sheep/day and 1.5 milk/cow/day.
Measured from episode 101897782: 1.17 and 1.29. Close enough to trust.

`BUILD_PASTURE` and `BUILD_COOP` have **no cost check** in the engine, so a
structure is one unit-turn and a tile. Only the animal costs coins.

## 3. The derived shape

Sized to *meet the drain*, not to fill the farm:

    11 SHEEP (5,500) + 8 COW (3,200) + 6 GOOSE (1,800) = 25 animals, 10,500 capital
    revenue at base prices if we supply the full drain: 115,130
    herd labour: 56 turns/day of ~216 available

Feed is the live constraint, and it is a genuine fork:

    grow it   550 wheat needs ~23 tiles -> 48 of 50 tiles committed, +28 turns/day
    buy it    ~550 wheat, but buying DRAINS market inventory and pushes the price
              up; season wheat drain is only 559, so we would roughly double it

Probably a mix, and exactly the kind of continuous trade-off to hand to BO
rather than to guess. Note this also stops us selling wheat, which is what
crushed our own wheat price to 20.4 (below its base of 25).

## 4. What this is NOT

Not truebelief's plan. They ran 7 cows and 4 sheep and earned most from milk;
the engine says **wool is worth more than milk**, and they under-weighted the
best market while over-weighting melon — a market that absorbs 30 units a
season. They were also mid-field, not the frontier. Copying their mix would
have inherited both errors.

## 5. Open risks

- **Labour.** 56 turns/day for the herd plus feed production, against ~216 with
  60% currently lost to travel. Routing may bind before the market does, exactly
  as it did at 75 tiles.
- **Market coupling.** If the field converges on animals, milk and wool glut and
  these numbers fall. The scarcity framing says the edge is in markets others
  are not in; that is a reason to keep strawberry (48,744, no capital, no feed)
  in view as a hedge.
- **v3 is a weak yardstick now.** It pins `melon_farm`, which we have just shown
  is built on the worst market on the board. Repoint it once this lands.
