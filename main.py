"""Competition entrypoint. Must stay at the root of the submission archive.

Two hard constraints imposed by kaggle_environments' agent loader
(`agent.py::get_last_callable`), both of which cost a whole episode if broken:

1. The file is `exec`'d with an empty globals dict, so **`__file__` does not
   exist**. Anything resolving paths relative to this file raises NameError and
   the submission errors out. Not needed anyway: the loader appends this
   directory to `sys.path` before exec, so `import agentlib` just works.

2. The loader picks `[v for v in env.values() if callable(v)][-1]` — the *last*
   callable in module order. So `agent` must be defined last, and no import or
   definition introducing a callable may follow it.

Keep this file exactly this small.
"""

from agentlib.planner import decide


def agent(obs, config=None):
    return decide(obs, config)
