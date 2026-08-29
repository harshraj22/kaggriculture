# Third-party opponent agents

**Nothing in this directory is ours.** These are public competitor agents,
committed so that `eval/protocols/v3.yaml` is reproducible from a fresh clone and
so the benchmark survives the original notebook being edited or withdrawn.

They are never imported by `agentlib/` and never ship in a submission. The only
thing that touches them is `evaluate.py`, which passes a file path to
`env.run()` via the `file:<path>` opponent form.

## v48

    source   https://www.kaggle.com/code/kaitofukami/40-40-early-floor-39-46-top-10-v48-fast-routes
    author   Kaito Fukami
    agent    main.py, 107,008 bytes
    sha256   dadee25a9840313218384208c53b2c4752f82c3209cc654632e0b96c65e2664a

The checksum is the author's own, published in the notebook. `main.py` was
extracted from the committed `.ipynb` and verified against it, so the file here
is provably the exact artifact they released — not something we reassembled.

Reproduce:

    kaggle kernels pull kaitofukami/40-40-early-floor-39-46-top-10-v48-fast-routes \
        -p opponents/v48
    python tools/extract_agent.py opponents/v48/*.ipynb --expect dadee25a9840313218384208c53b2c4752f82c3209cc654632e0b96c65e2664a

## On redistribution

Kaggle notebooks carry a licence set by their author, and we have **not verified
what licence this one uses**. Committing it to a public repository redistributes
someone else's work. If that matters — this repo has a public GitHub remote — the
alternatives are to keep only the `.ipynb` (still redistribution), or to keep
neither and re-fetch, which is what the two commands above are for.

Attribution above is deliberate and should stay with the file.
