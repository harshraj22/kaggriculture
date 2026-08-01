#!/usr/bin/env python
"""Run one local game and report the result.

    python tools/run_match.py --opponent random
    python tools/run_match.py --opponent starter --steps 240 --replay replays/x.json
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=os.path.join(ROOT, "main.py"))
    ap.add_argument("--opponent", default="random", help="random | pass | starter | path/to.py")
    ap.add_argument("--steps", type=int, default=720)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--replay", default=None, help="write replay JSON here")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    from kaggle_environments import make

    config = {"episodeSteps": args.steps}
    if args.seed is not None:
        config["seed"] = args.seed

    env = make("kaggriculture", configuration=config, debug=args.debug)
    t0 = time.time()
    env.run([args.agent, args.opponent])
    elapsed = time.time() - t0

    final = env.steps[-1]
    rewards = [s.get("reward") for s in final]
    statuses = [s.get("status") for s in final]

    print(f"steps={len(env.steps)}  wall={elapsed:.1f}s")
    for i, (r, st) in enumerate(zip(rewards, statuses)):
        who = "us" if i == 0 else "opp"
        print(f"  player {i} ({who}): reward={r} status={st}")

    if rewards[0] is not None and rewards[1] is not None:
        outcome = "WIN" if rewards[0] > rewards[1] else "LOSS" if rewards[0] < rewards[1] else "TIE"
        print(f"  -> {outcome} (margin {rewards[0] - rewards[1]:+})")

    if args.replay:
        os.makedirs(os.path.dirname(args.replay) or ".", exist_ok=True)
        with open(args.replay, "w") as f:
            json.dump(env.toJSON(), f)
        print(f"  replay -> {args.replay}")

    return 0 if statuses[0] == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
