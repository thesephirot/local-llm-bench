Extend prompt presets to support multiple ordered prompt steps, so one preset can chain one prompt after another.

Where: app/models.py (PromptPreset), app/main.py (preset CRUD + benchmark execution), app/database.py (preset persistence), static/index.html and static/app.js (preset modal + preset list UI), tests/ (suite: uv run pytest).

Done means:
- A preset stores an ordered list of prompt steps instead of only a single prompt.
- The preset modal lets you add, edit, reorder, and remove steps.
- Running a preset executes step 1, then step 2, etc., in order, as one continuous multi-turn conversation: each step's prompt and response stay in the message context sent to the model, and every step's response is recorded and visible in results/history.
- Existing single-prompt presets keep working unchanged (treated as one step).
- Tests cover multi-step preset execution and single-prompt backward compatibility; uv run pytest passes.

Out of scope: Chain Configs (model chaining) changes, branching/conditional logic between steps, per-step temperature or max_tokens overrides.
