#!/usr/bin/env python
"""Play N games against a baseline and report win rate with a confidence interval.

Ratings on the leaderboard move on win/loss only, so win rate — not mean profit —
is the number that matters.

    python tools/arena.py --opponent random --games 20
    python tools/arena.py --opponent main.py --games 30 --steps 240   # self-play
"""

import argparse
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z**2 / n
    c = p + z**2 / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return ((c - s) / d, (c + s) / d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=os.path.join(ROOT, "main.py"))
    ap.add_argument("--opponent", default="random")
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--steps", type=int, default=720)
    ap.add_argument("--swap", action="store_true", help="alternate seats to cancel seat bias")
    args = ap.parse_args()

    from kaggle_environments import make

    wins = losses = ties = errors = 0
    margins: list[float] = []
    t0 = time.time()

    for g in range(args.games):
        env = make("kaggriculture", configuration={"episodeSteps": args.steps, "seed": g})
        seat = g % 2 if args.swap else 0
        players = [args.agent, args.opponent] if seat == 0 else [args.opponent, args.agent]
        env.run(players)

        final = env.steps[-1]
        if final[seat].get("status") != "DONE":
            errors += 1
            print(f"  game {g}: ERROR {final[seat].get('status')}")
            continue

        mine = final[seat].get("reward") or 0
        theirs = final[1 - seat].get("reward") or 0
        margins.append(mine - theirs)
        if mine > theirs:
            wins += 1
        elif mine < theirs:
            losses += 1
        else:
            ties += 1
        print(f"  game {g}: {mine:>10.0f} vs {theirs:>10.0f}  ({mine - theirs:+.0f})")

    n = wins + losses + ties
    lo, hi = wilson(wins, n)
    print("-" * 56)
    print(f"games={n} errors={errors}  wall={time.time() - t0:.0f}s")
    print(f"W/L/T = {wins}/{losses}/{ties}   winrate={wins / n if n else 0:.1%} "
          f"[{lo:.1%}, {hi:.1%}]")
    if margins:
        print(f"margin: median={statistics.median(margins):+.0f} mean={statistics.mean(margins):+.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
