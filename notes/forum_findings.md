# Forum review — what was worth taking

Read: 737545 (seat-1 `step` bug), 736219 (rating mechanics, 65 votes, from a
former #1), 732450 (documentation vs engine, staff-confirmed).

## Already converted into measured gains

**Melon saturates at age 10, not 12** (732450, staff-confirmed). `max_yield` 6 is
reached on age 10 while `max_yield_day` is 12, so holding to the calendar wasted
two tile-days on every melon. Harvesting on saturation instead:

    melon_farm  +33,380 -> +35,056   and sd 1,381 -> 511
    wheat_farm  unchanged (wheat reaches 4 of a possible 6, so never saturates)

**Care bonus is +1, not +2** — our `GAME_SPEC.md` had the rulebook's wrong value.
Fixed. `ranch_farm` already modelled +1 because it was written from the engine.

**Strawberry yields exactly 4 times then dies** (ages 10/12/14/16) — 0.24
units/tile-day, not the "every other day" the table implies. That makes the
48,744 strawberry figure in `ranch_plan.md` unreachable: supplying the whole
drain would need ~57 tiles of a 50-tile farm. Built `berry_farm` to test it
anyway: **+22,660 against melon's +35,246.** Melon wins despite a market a
thirteenth the size, because it grows 2.3x faster per tile.

## Confirmed we are NOT exposed

**`obs["step"]` is never set for seat 1** (737545) — reportedly handicaps any
agent keying off `step` in half its games. We are immune: the key is *absent*
rather than present-and-None, so `Obs.__init__`'s
`obs.get("step", day * TURNS_PER_DAY + hour)` fallback resolves correctly.
Verified directly. The defensive wrapper paid for itself.

## Changes how we should read our own results

From 736219, by a former #1 who fitted his own rating curves:

- A new submission starts at **600** and is ~90% converged after **~60 games**.
  **Our 439 came after 3 games** — with K ~ 200 early, that is noise, not a
  verdict. Do not judge a submission before ~60 games / ~5 hours.
- Treat rating moves under ~50 points as noise even at 200 games.
- Only the **2 most recent submissions** are active and each restarts at 600;
  the final leaderboard is a Bradley-Terry fit over the ~2 weeks AFTER the
  deadline. Live rating history counts for nothing. Never resubmit an unchanged
  agent to re-roll.
- **"Most improvements that raise your average coin total but increase variance
  are rating-negative."** This is the sharpest one for us. `ranch_farm` has sd
  4,176 against `melon_farm`'s 511. Even at equal means, the high-variance agent
  rates worse. Our `score_lo` objective already points this way; `mean_margin`
  does not.
- Judge a change by win rate against *each* strong opponent, not the pooled
  mean — which is what our `by_opponent` breakdown is for.
- A commenter reports a measured **seat-0 win-rate edge**. We use `swap_seats`
  so our numbers are balanced, but it is worth measuring.

## Not yet acted on

- Diversifying across several premium crops at once. Melon and strawberry are
  opposite extremes (fast/tiny market vs slow/deep market); the scarcity model
  says holding both should beat either, but `_seed_targets` and `_crop_for`
  currently assume a single premium crop.
- Seat asymmetry measurement.
- Rebuilding the offline sparring pool weekly as the ladder meta drifts.
