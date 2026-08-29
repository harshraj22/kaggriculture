# Public notebooks — is the field forking one agent?

Yes. Vote counts:

    840  Kaggriculture: Getting Started            (official starter)
    295  V16-RC5 | High-Score 8C/4S Premium Market Lead
    179  25/27 Strict-Future | v27 Midgame Meta Reset
    135  Adaptive Farming Strategy for Kaggriculture
    126  40/40 Early Floor | 39/46 Top-10 | v48 Fast Routes
    115  Kaggriculture: Findings from Zero to Top Meta
     99  Kaggriculture Rank Your Agent

**"8C/4S" is 8 Cows / 4 Sheep.** `truebelief`, the agent that beat us 86,511 to
21,620, ran 7 cows and 4 sheep. It was almost certainly a fork of that 295-vote
notebook. A session was spent reverse-engineering a 22 MB replay of a published
recipe.

The whole leaderboard tops out at 3,020 and the top twelve sit 2,796-3,020, so
"the 2000s" IS the frontier, and much of it is the same handful of agents.

## "Fast Routes" does NOT mean pathfinding

This was my misreading. In this competition "route" means a **strategic
build-order extracted from public episode replays**, not a walking path. The v48
notebook states its own composition as:

    v43/v44-compatible floor
      + first-shop fast-route continuations
      + one narrow BAKERY capital recovery
      + one child controller call per turn
    = v48 Fast-Climber Sparse Route Hybrid

Their stated key result: *"the gain comes from preserving options and selecting
compatible continuations - not declaring the newest route universally optimal."*
And: *"The best single current route managed only 29/46."*

## What this means for us

**Our architecture is already the frontier architecture.** A safe floor plus a
pool of alternatives plus one controller call per turn is exactly the
strategy/controller/arbiter design we built. The gap is CONTENT, not structure:
they hold a pool of routes mined from replays, we hold four strategies.

**Their branch triggers are shop-roll conditional** — "first YARN_STORE",
"first-shop". Shops are drawn with replacement, so which shops appear which
market is deep. Our `ThresholdController` has `shops_gte` (a COUNT) but no
predicate for shop IDENTITY. That is the single highest-value predicate we are
missing and it is a few lines.

**Their metric is win counts against named opponents** ("40/40 both seats",
"39/46 top-10 holdout"), with routes split chronologically into pool and
holdout. Same discipline as our protocols and `by_opponent` breakdown.

## A conclusion of mine that the evidence does NOT support

I twice asserted "fix routing first". Against that:

- The frontier notebook never mentions travel, movement, adjacency or
  pathfinding anywhere in 135k characters.
- Shrinking the farm to one quadrant (25 tiles, denser, less walking) COSTS
  1,300: **+33,953 against +35,246 for 50 tiles.**

So high movement share is not obviously the lever. It may simply be what a
tile-based farm costs. The routing question stays open; it should not be
prioritised on my say-so.
