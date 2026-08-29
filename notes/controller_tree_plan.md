# Controller tree & policy portfolio — approach and order of work

Design note. **Status: plan, unmeasured.** Everything below is argument from
this repo's own measurements plus the community numbers in
[brainstorm.md](brainstorm.md); nothing has been validated end-to-end. Where a
claim IS measured, the source is named. The repo's standing rules apply to every
step: paired seeds, protocol discipline (`compare.py`'s comparability guards),
search on `train` / confirm on `holdout`, meaning-in-code / numbers-in-config.

## 1. The idea, and the three corrections it needs

The proposal: many simple strategies as leaves; controllers select among them;
controllers above controllers; a tree. Optimise it the way one trains a deep
network — layer by layer — with Bayesian optimisation as the trainer.

The structure is sound and has a name: hierarchical RL's **options framework**
(leaves = primitive policies, controllers = policies over options). It fits this
game: 720 turns with distinct economic phases — bootstrap, steady farming,
conversion, endgame liquidation. But three corrections change how it should be
built:

**Correction 1 — there is no backprop here.** Deep nets work because gradients
assign credit through every layer jointly. BO sees one scalar per trial, treats
the tree as a black box, and its sample efficiency collapses past roughly 15–20
dimensions. Adding a layer multiplies parameters while the signal stays one
number, so "train the whole tree with BO" does not scale with depth. The analogy
that does hold is **greedy layer-wise training**: tune a level, freeze it, tune
the level above — each stage low-dimensional, which is where BO is strong. Its
known cost is that a leaf tuned solo can be mistuned for its role in the stack
(a `safe_farmer` tuned for its own margin is not the same as one tuned to be a
good endgame closer). Phase D below buys that back cheaply.

**Correction 2 — depth adds organisation, not expressiveness.** A tree of
threshold controllers all reading the same `Obs` collapses mathematically to one
flat rule list; nesting them cannot express anything a single bigger controller
cannot. Depth pays only through modularity — freezing and reusing a tuned
subtree — or when levels differ in *information basis* (one level reads slow
accumulated state such as a regime estimate, another reads fast state such as
day and money). So: depth is capped at 2, and a deep config must beat the best
flat one by more than `se_p` or it is dropped (§7).

**Correction 3 — the repo's own evidence says leaves come first.** Twice now a
single new leaf has moved the score more than everything else combined:
`wheat_farm` (~3.9k → 15.9k on v1/train) and then `melon_farm` (15.9k → 36.4k,
beating `wheat_farm` 60/60 head-to-head; README table). Controllers have
nothing to route between until several strong leaves exist whose best regimes
differ, and nothing to prove until the objective can see them. Hence the phase
order in §3.

## 2. Invariants (every phase, every claim)

1. **Contribution over quality.** A policy is admitted to the portfolio for
   what it adds to the best score achievable by a controller *over the
   portfolio*, never for its solo score. Solo score is a gate (§4 stage 0),
   not the decision.
2. **All deltas are paired.** Same seeds, judged on `se_p` from `compare.py`.
   Decision thresholds are stated in units of `se_p` (defaults in §5), never in
   raw coins — env upgrades re-scale coins silently (see the 1.32.7 variance
   note in the README).
3. **Meaning in code, numbers in config.** BO searches only continuous knobs
   (day boundaries, money thresholds). "Which strategy owns which slot" is
   decided by measurement (§4), never by BO over categoricals.
4. **Scanning is not finding.** Anything selected by scanning many
   (policy, window) cells is a multiple-testing suspect until it survives one
   confirmation on `holdout`.
5. **Never searchable:** eligibility rules, the `SafeFarmer` fallback, the
   strike policy. (Already the repo's rule; it applies at every tree level —
   BO would buy score by disabling the safety net.)
6. **Wealth means portfolio value, not banked coins** (definition §5). Windowed
   banked-coin deltas reward liquidating over growing — the same failure the
   README flags for per-step RL rewards.
7. **Diversity is mechanism, not constants.** Constant-variants of one strategy
   are ONE leaf plus BO's tuning job (rho ≈ 1 between them). A new leaf must
   differ in mechanism: crop mix, conversion timing, market behaviour, endgame
   handling.
8. **On contested protocols, dispersion counts.** Kaggle rates win/loss/tie, so
   the objective behaves like Pr[win] ≈ Φ(μ/σ) (brainstorm.md). Judge
   selection on `score_lo`; a specialist that raises mean AND variance can
   lower the thing the leaderboard pays for.

## 3. Order of work

Phases are sequential except F. Each has an exit criterion; do not start a
phase before the previous one's criterion is met.

**Phase A — restore objective gradient. STATUS: done, as a live benchmark.**
Instead of a new v4, v3 was redesigned in place: ONE pinned opponent — the
current best strategy — on v1's worlds, centred at 0.5 (a mirror); search with
`mean_margin` (continuous everywhere, including below the opponent), accept
with `score_lo` (the ladder's shape). The in-place edit was safe this once
because prior results were cleared; it stays safe only because
`protocol_hash`/`code_hash` guards refuse stale comparisons. The
new-file-new-`id` rule still binds any future change that must coexist with
retained results.
A live benchmark carries a standing maintenance duty: **when a new best lands,
repoint `opponents:` at it.** Due now: `melon_farm` beats the pinned
`wheat_farm` 60/60 (score_lo 0.940 — re-saturated, the same failure that
retired `starter`), so v3's opponent becomes `strategy:melon_farm` (README
flags the one-line change).
*Standing exit criterion (re-check after every repoint):* the current best
mirrors at ~0.5 and weaker configs spread below rather than piling at 0.

**Phase B — grow mechanism-diverse leaves.** Priority order, grounded in
brainstorm.md:

1. **Wheat→melon conversion — DONE: `melon_farm`** (36355 v1/train, 36245
   holdout; beats `wheat_farm` 60/60, +20492). Two of its measured lessons are
   now plan facts: the plot is capped by MARKET DEPTH (~150 sellable
   units/season → 10 tiles), not tile yield; and constants tuned against
   passive opponents overfit — vs `starter` the sweep picks 14 tiles, which
   LOSES round-robins to 10. Tune contested constants on v3.
2. **Anti-mirror** — promoted to the highest-value new leaf. `melon_farm`'s
   edge is market-coupled: ~36k passive vs ~26k in a mirror where two farms
   flood the same melon market, and the ladder will converge on melon. Prices
   are inventory-coupled (`market_price(item, inventory)`): read the glut the
   opponent creates and shift crop mix and sell metering against it.
3. **Endgame liquidator** — stop sowing any crop that cannot mature by day 29
   (wheat: last useful planting day 25; melon: day 19, first yield at age 10),
   drain the shed before the final turn, respecting the 100-unit shed cap and
   overflow-discard rule. (`melon_farm` already dumps all on the last day; the
   leaf generalises that.)
4. **Tomato probe** — post-1.32.7 its realised price ceiling moved most
   (p99 128 → 786) while adoption stayed ~1%; cheap to measure, possibly
   neglected.
5. **Shop arbitrage** — buy from town shops when rolls are rich, resell. Note
   `melon_farm`'s finding that no shop demands melon; arbitrage lives in the
   other goods.

Each leaf follows the `wheat_farm` documentation convention: docstring is the
specification, tuned constants named, measured-and-rejected variants recorded.
*Exit:* at least two leaves beyond the incumbent with per-seed rho below ~0.8
against the current best (`melon_farm`) AND at least one positive phase-swap
window each (measured in Phase C — B and C iterate as a pair).

**Phase C — portfolio selection.** The pipeline in §4. *Exit:* a portfolio of
at most 5 leaves and the three-number diagnostic (§4 stage 3) computed.
**Stop rule:** if the best fixed schedule ≈ best single leaf (within `se_p`),
there is no routable diversity — return to Phase B. Do not tune controllers
over a portfolio of clones.

**Phase D — flat controller over the portfolio.** One `threshold` or `schedule`
controller; BO over its boundaries/thresholds (`make sweep`, protocol v3,
`OBJ=score_lo`), at most ~8–10 dimensions. Leaves stay frozen EXCEPT the one or
two leaf constants most plausibly coupled to switching (e.g. `melon_farm`'s
`premium_tiles` cap and its melon sell-metering ratio, both market-coupled):
re-expose exactly those in the sweep.
This is the cheap repair for greedy layer-wise mistuning (Correction 1).
Confirm the winner once on `holdout`.
*Exit:* flat controller beats the best single leaf by more than `se_p` on v3.
If it does not, the portfolio is not paying — return to B/C, do not add depth.

**Phase E — depth decision.** Only now consider a composite (§6). Justify each
level by a different information basis: root routes on slow accumulated regime
state (realised shop richness, opponent money slope, market crop mix); subtrees
handle within-regime scheduling on fast state (day, money, tiles). Layer-wise:
freeze the tuned Phase-D subtree(s), tune only the root's knobs.
*Kill criterion:* depth-2 must beat the Phase-D flat controller by more than
`se_p` on v3 and survive `holdout`, else keep flat. Depth cap 2.

**Phase F — learned controller (parallel, opportunistic).** `PolicyController`
is blocked on action-space size; it unblocks when the portfolio reaches ≥ 4
leaves. `--trajectory` already records the eligibility mask per turn, which is
the part offline learning cannot reconstruct. This is the true "joint training"
analog of the neural-net intuition; the tree is the interim structure that
also generates its training data.

## 4. Phase C in detail — choosing policies for the portfolio

Two obvious selectors, and why each needs repair before use:

- **"Keep every policy above a score threshold."** A portfolio is not a
  leaderboard. This admits clones (ten `wheat_farm` variants all clear any bar
  and give the controller nothing to route between) and rejects specialists
  (an anti-mirror or endgame specialist can score well below `melon_farm` solo
  while being the best policy alive in its window or world-type — exactly what
  the tree exists to route to). Absolute score survives only as
  stage-0 gate below.
- **"Keep a policy if some N adjacent turns raise wealth more than a
  threshold."** Right instinct — local competence, not global — but as stated
  it measures the world, not the policy. Three repairs: (i) measure portfolio
  value, not banked coins (a `SELL` spikes money without creating value);
  (ii) subtract the paired baseline on the same seed and window (every policy's
  wealth rises on a rich shop roll); (iii) fix the window grid in advance and
  require consistency across seeds — "any window anywhere" is a max over many
  tests and admits luck. Even repaired it stays observational: the policy only
  visits states its own earlier actions created. So it is the cheap prefilter
  (stage 1), never the decision.

The decision-grade measurement is **interventional** and already exists in the
repo: a `ScheduleController` phase-swap.

**Stage 0 — gates (absolute, cheap).** Loads under strict mode; beats `pass`
on v1; eligibility honest (`is_eligible` reflects real applicability, since it
doubles as the mask for Phase F).

**Stage 1 — trajectory screening (observational prefilter).** Solo runs with
`--trajectory`. Per fixed window (grid in §5), compute the excess
portfolio-value rate against the backbone's run on the same seed and window.
Nominate (policy, window) pairs where the median excess across seeds is
positive AND positive in ≥ 60% of seeds.

**Stage 2 — phase-swap fingerprint (interventional).** For each nominated pair,
a schedule config: backbone (the current best — `melon_farm` today)
everywhere, candidate owns the window. Evaluate vs pure backbone, paired seeds, v1. Output: a matrix
policy × window → paired delta ± `se_p`. Every cell is one v1 run (~20 s), so
ten policies × six windows is lunch-break sized. The delta is the candidate's
contribution *starting from the backbone's real state, judged by final score* —
an audition for exactly the controller slot it would occupy.

**Stage 3 — three-number diagnostic.** From the matrix:
(a) best single leaf, solo;
(b) best fixed schedule assembled from the per-window winners — window deltas
do NOT compose additively (state carries across the boundary), so assemble it
as a real schedule config and evaluate it;
(c) per-seed hindsight oracle — best policy per window per seed, summed from
the matrix; an approximate upper bound, never a deployable config.
Read: **(b) − (a)** is the value of scheduling at all; **(c) − (b)** is the
ceiling for *adaptive* (state-reading) control — the only justification for
threshold controllers and for depth. If (b) ≈ (a): stop rule, back to Phase B.

**Stage 4 — greedy forward selection.** Start the portfolio at `{melon_farm}`.
`wheat_farm` is now an ordinary candidate: `melon_farm` subclasses it and
embeds its whole wheat engine, so expect the dominance test to retire it — but
let the test decide, not sentiment.
Repeatedly add the leaf that most improves (b), re-evaluating (b) each round;
stop when the gain drops below `se_p`; portfolio cap 5 (every member multiplies
the Phase-D search space). Along the way discard dominated clones: rho > 0.95
against a member and no window with positive paired delta.

**Stage 5 — confirmation.** Final portfolio + Phase-D controller once on
`holdout`, and on v3 judged by `score_lo` (invariant 8). Stage 1–2 scanned many
cells; this is where luck gets filtered out.

## 5. Definitions (so every number means one thing)

- **Portfolio value V(t):** money + realizable shed value, where realizable
  means what selling the shed NOW would actually fetch via
  `market_price(item, inventory)` — not last price × quantity, because sales
  move the price. Optional third term, standing crops at expected remaining
  yield × realizable unit value: start WITHOUT it; add it only if stage-1
  screening visibly misranks growers against sellers.
- **Window grid:** day-aligned, one wheat cycle wide — days 0–4, 5–9, 10–14,
  15–19, 20–24, 25–29 (24 turns/day; wheat is plant day D, harvest D+4).
  Windows shorter than a cycle measure the phase of the crop cycle, not the
  policy.
- **Excess rate (stage 1):** [V_p(end) − V_p(start)] − [V_b(end) − V_b(start)]
  on the same seed and window; b = current backbone (`melon_farm` today; the
  backbone is a named choice, changes only deliberately, and tracks the
  current best).
- **`se_p`, rho:** exactly as `compare.py` computes them (paired).
- **Default thresholds:** nominate at stage 1 per the 60% rule; keep at
  stage 2 if delta > 2·`se_p`; final keep/kill decisions at `se_p`. Any
  deviation is stated next to the claim it supports.

## 6. Composite controller — design, not code

Semantics for the (Phase E, only-if-earned) controller-of-controllers:

- A composite's spec contains **child controller specs** plus one routing rule;
  `from_spec` recurses. A child is a full controller; the composite delegates
  `select(obs, candidates)` to whichever child the routing rule picks.
- Eligibility filtering stays where it is (the arbiter) — candidates arrive
  already filtered at every level; no level re-implements it.
- `describe()` recurses so the config hash and the result row capture the whole
  tree. `diagnostics()` aggregates children under namespaced keys, so "this
  subtree never fired in 60 episodes" is visible in the result row — that is
  the branch-pruning signal (§7).
- Routing state (regime estimates accumulated across turns) lives in the
  composite and is cleared by `reset()` — one instance per episode per seat, the
  same two-seats-one-interpreter constraint the README documents for `planner`.
- Structure search is NOT BO's job. Hand-design at most 3–4 tree shapes; BO
  tunes numbers within each shape; shapes are compared paired like any two
  configs. (Searching structure automatically is evolutionary/GP territory and
  eats the eval budget combinatorially.)

## 7. Guardrails and kill criteria

- **Dead branches:** any rule/subtree that `diagnostics()` shows firing in
  < 5% of episodes is removed (or its window widened deliberately) before the
  next sweep — redundant parameterisations create plateaus that waste trials.
- **Switch thrash:** strategies stay warm (`observe`/`on_action` run every turn
  on every strategy), but a flapping controller still thrashes plans. If
  diagnostics show switching well above the designed rate, add ONE stickiness
  knob (minimum turns before re-switch) to the searchable space rather than
  hand-tuning rules.
- **No Optuna pruners** except at whole-seed-set boundaries (protects pairing —
  already the repo's rule; restated because trees make sweeps longer and
  pruning more tempting).
- **Depth cap 2**, and each added level must clear `se_p` on v3 + `holdout`
  over the level below, else the shallower config stands.
- **Every cross-config claim** passes `compare.py`'s comparability guards
  (`protocol_hash`, `code_hash`) — a fingerprint matrix mixing code versions is
  void, same as any other result.

## 8. Ideas worth a cheap measurement (none discussed above, all unmeasured)

1. **Mine the ladder's replays into leaves and opponents.** Two sources with
   different jobs:
   the community ~37k-episode dataset (brainstorm.md, `engine_version` column)
   for pre-aggregated, ladder-wide stats; and Kaggle's OFFICIAL daily replay
   datasets (`kaggle/kaggriculture-episodes-<date>`) — full JSON replays of
   completed episodes, selected daily ranked by average agent rating, capped at
   20 GiB/day. Verified for 2026-07-30: 864 episode JSONs of 5–23 MB each
   (~13.6 GB total) plus ONE ~70 KB index CSV (episode ids, timestamps,
   ratings/scores — 7 columns).
   **Working rule — never bulk-download.** Per day: fetch the index CSV only
   (KBs); pick at most ~20 top-rated episodes from it; fetch those JSONs
   (≲ 300 MB); reduce each to a compact per-episode feature row (land-purchase
   days, hires per day, crop mix by day, sell cadence, final banks, price
   paths); keep only the summaries (gitignored, like `results/`); delete the
   raw JSONs. The summaries are the durable asset.
   Uses, in plan order:
   (a) **Gap analysis for Phase B** — top agents' per-phase behaviour stats vs
   `wheat_farm`'s; every large difference is a concrete constant to re-tune or
   a missing mechanism to build, which turns Phase B from invention into
   targeted copying.
   (b) **Portfolio coverage** — label each top-agent turn with which of our
   leaves (if any) would have emitted that action; the unexplained fraction
   measures the leaf gap more directly than any score delta.
   (c) **Scripted mimic opponents for v3/v5** (feeds idea 5) — distilled
   archetypes, NOT verbatim "ghost" replays: the market is shared, so an
   action stream conditioned on prices both players moved is only
   approximately valid outside its original episode.
   (d) **Regime-feature estimation for idea 2** — empirical distributions of
   shop-roll richness and the world features where top agents' behaviour forks.
   (e) **Live top-of-ladder σ and score bars, daily** (invariant 8), plus
   meta-shift tracking (the carrot 6.3% → 44.2% jump in brainstorm.md is this
   signal, computed by someone else).
   Cautions: rating-ranked selection over-represents the top — never mix its
   adoption stats with the community dataset's ladder-wide ones unlabelled;
   always filter by engine version (1.32.7 changed the game materially);
   replays carry no eligibility masks, so they feed Phase B distillation and
   the coverage measure, not Phase F's off-policy learning directly.
2. **Regime features as first-class controller state.** Realised shop richness
   so far, opponent money slope, market inventory composition (visible via
   prices) — accumulated across turns in controller state. These differ in
   information basis from turn-number and from instantaneous money, which is
   precisely what earns a level above `schedule`/`threshold` (Correction 2).
3. **Turn-budget lookahead.** 1 s/turn plus a 60 s episode bank (brainstorm.md)
   means a handful of slow turns are free. A leaf that runs real forward
   simulation at 2–3 critical decisions per episode — land purchase timing,
   melon conversion timing — is affordable and unexplored.
4. **Variance shaping on v3.** Given invariant 8, consider an objective variant
   penalising σ (e.g. mean − λσ on margins) for SEARCH, while `score_lo`
   remains the judge — a config with equal mean and lower spread is strictly
   better on the ladder.
5. **Mirror-weighted evaluation.** As leaves diversify, rho falls and mirror
   games stop tying; opponents near the top of the ladder resemble our own
   agent more than they resemble `starter`. A v5 protocol weighting self-play
   higher may predict leaderboard movement better. Measure before adopting.
