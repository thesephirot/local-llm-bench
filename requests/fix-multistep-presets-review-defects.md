Fix the 3 blocking defects found in the review of the multi-step prompt presets work.

Where: the full review with repros is adws/adw_data/sessions/dfd81b7b/context_handoff/review.md. The defects are in app/main.py (TTFT measurement), app/database.py (chain-run steps decode), static/app.js (steps editor state sync). The multi-step presets build is currently uncommitted in the working tree.

Done means:
1. TTFT is measured at first-token arrival again: a single-step run produces exactly the same result values as before the multi-step change, and tokens_per_second uses the correct generation time. (review defect #1, app/main.py ~line 513)
2. Chain-run retrieval (GET /api/chain-runs, db.get_chain_run) no longer raises TypeError when a stored result has steps — only json.loads when the steps value is a str. (review defect #2, app/database.py ~line 354)
3. The preset steps editor never loses typed text: textarea input syncs into the step state on edit, and saving reads the textareas by index so duplicate step texts don't collapse. (review defect #3, static/app.js ~lines 79-97)
4. New regression tests cover defects 1 and 2, and uv run pytest passes.

Out of scope: new features, Chain Configs model-chaining changes, and the benchmarks.db git-tracking/permissions question — that is handled separately; do not touch benchmarks.db, .claude/, or adws/.
