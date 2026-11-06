"""Curio's reference domains, deliberately different from each other.

- `fashion`: H&M purchases, CLIP photo search, recommenders and an outfit planner.
- `cocktails`: an offline recipe catalog with pantry matching.
- `books`: goodbooks-10k with readers-also-liked and mood browse.
- `pairings`: book → drink pairing, the one domain built on two others.

Each domain keeps its catalog and recommenders beside its `agent` package (the toolkit the
engine runs, its tools, prompts, grounding check and session schemas). A domain imports
the engine and itself, never another domain; only `pairings` uses books and cocktails.
"""
