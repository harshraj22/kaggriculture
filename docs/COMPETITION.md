# Competition logistics

<https://www.kaggle.com/competitions/kaggriculture>

## Timeline

| Date | Event |
|---|---|
| Jul 29, 2026 | Start |
| Sep 30, 2026 | **Final submission deadline** (11:59 PM UTC) |
| Oct 1 – ~Oct 15, 2026 | Games keep running until leaderboard converges; final Bradley-Terry tournament |

## Evaluation

- Up to **5 submissions/day**. Only the **latest 2** are tracked and used for final evaluation.
- Each upload runs a **validation episode** (agent vs. a copy of itself). Failure → `Error` status.
- Skill rating updates on **win/loss/tie only** — the coin margin does not matter.
  Beating a high-rated agent moves you more.
- Leaderboard shows your best bot; final ranking comes from a Bradley-Terry fit over all episodes.

Implication: optimize for **P(win)**, not expected profit. A strategy that wins 60% of games by
$10 beats one that wins 45% by $10,000.

## Submission

Requirement: **`main.py` at the archive root**, exposing an `agent` function.

```bash
# single file
kaggle competitions submit kaggriculture -f main.py -m "wheat loop v1"

# multi-file
tar -czf submission.tar.gz main.py agentlib/
kaggle competitions submit kaggriculture -f submission.tar.gz -m "v1"
```

`make bundle` / `make submit MSG="..."` wrap this.

## Auth setup (once, on your machine)

1. Get a token at <https://www.kaggle.com/settings/api> → "Generate New Token".
2. ```bash
   mkdir -p ~/.kaggle
   nano ~/.kaggle/access_token      # paste token string
   chmod 600 ~/.kaggle/access_token
   ```
   Alternatives: `kaggle auth login` (OAuth) or `export KAGGLE_API_TOKEN=...`
3. **Join the competition on the website** (accept rules) before submitting.
4. Verify: `kaggle competitions list --group entered`

## Useful CLI

```bash
kaggle competitions pages kaggriculture --content     # full rules/spec text
kaggle competitions download kaggriculture -p data/   # README.md + AGENTS.md
kaggle competitions submissions kaggriculture
kaggle competitions episodes <SUBMISSION_ID>
kaggle competitions replay <EPISODE_ID> -p replays/
kaggle competitions logs <EPISODE_ID> 0 -p logs/
kaggle competitions leaderboard kaggriculture -s
```

## Built-in baseline agents

`"pass"`, `"random"`, `"starter"` (deterministic baseline) — pass by name to `env.run`.
