# Eval pipeline

Produces the numbers behind these resume claims:

1. "generates wikis for repos up to **NK LOC** in **X min** at **~$Y** per repo" (Stage 1)
2. "hybrid retrieval ... improving file-level **Recall@5** from **X%** (dense-only) to
   **Y%** on a **N-question benchmark over M repos**; agent answers carry citations,
   **Z%** of which resolve to valid code locations" (Stages 2 and 3)

None of this instrumentation exists in `src/` -- wiki generation has no stage
timing or cost tracking, retrieval has no recall benchmark, and citations are
inline prose with no structured field to check against ground truth. Rather
than touch production code, everything here is either (a) a read of data the
pipeline already produces (code graphs, `current_step` progress labels), or
(b) a runtime monkeypatch confined to this eval process (see
`common/llm_usage_tracker.py`).

## Before running anything

- **Budget ceiling**: every stage runner takes `--max-cost-usd` (default from
  `EVAL_MAX_COST_USD` env var, else $18) and stops starting new work once
  cumulative *tracked* cost crosses it. Tracked cost relies on OpenRouter's
  `cost` field (opt-in, enabled automatically) with a published-pricing
  fallback for embeddings -- treat it as a good estimate, not an invoice;
  cross-check against your OpenRouter dashboard after a pilot run.
- **`EVAL_USER_ID`**: set this env var (or `TEST_USER_ID`, same convention as
  `docker/tests/conftest.py`) to a real row id in `profiles`/`auth.users` --
  `tasks.user_id` has a foreign-key constraint, so a made-up UUID will fail
  every `/generate` call.
- **Isolation**: this runs against your real Supabase/Qdrant/disk, not a
  throwaway environment. `common/manifest.py` records every repo_url /
  task_id / chat_id created in `results/eval_manifest.json`; `common/cleanup.py`
  deletes exactly those rows/points/directories and nothing else. Stage 1
  also skips (never re-ingests) any repo already present in your
  `repositories` table.
- No separate server process is needed -- every stage drives the FastAPI
  `app` in-process (`common/api_client.py`), the same way
  `docker/tests/conftest.py`'s `api_client` fixture does. This is required
  for cost tracking to work at all: the monkeypatches only see LLM calls made
  in this process.

Run everything from `docker/eval/`.

## Stage 1 -- generation time / LOC / $ cost

```bash
python -m stage1_generation.run_generation_eval --pilot 3      # sanity check first
python -m stage1_generation.run_generation_eval                # full repos.json run
```

Edit `repos.json` first -- it's a candidate list stratified by a rough LOC
guess; review it, swap anything you don't want measured. Repos run
**sequentially** (no `/generate` concurrency guard exists, and parallel repos
would hit OpenRouter rate limits and make cost attribution ambiguous).
Appends to `results/generation_results.jsonl`.

Sanity-check the pilot's tracked cost against your OpenRouter activity
dashboard before trusting the numbers, then decide the real repo count/tiers
within your budget.

## Stage 2 -- retrieval Recall@5 (near-zero cost)

```bash
python -m stage2_retrieval.build_question_set --num-repos 8 --per-repo 6
python -m stage2_retrieval.run_retrieval_eval
```

Reuses Stage 1's code graphs (deterministic "where is X defined" questions,
free) and a cheap flash-tier model (semantic questions from sampled
functions/classes) -- no new ingestion, no chat-agent calls. Spot-check
~10% of `results/question_set.jsonl` by eye before trusting recall numbers.
`run_retrieval_eval.py` compares three configs (dense-only / hybrid without
HyDE+MMR / full production) called as direct Python functions, bypassing the
chat LLM entirely. Writes `results/retrieval_results.jsonl`.

## Stage 3 -- citation validity (real `/chat` calls, budget-aware)

```bash
python -m stage3_citation.run_citation_eval --pilot 5     # check real $/call first
python -m stage3_citation.run_citation_eval --num-questions 25
```

Runs the semantic subset of the Stage 2 question set through the actual
`chat_agent` model (`openai/gpt-5.1`, the most expensive stage per call).
Extracts `file_path:line_start-line_end`-style citations from the answer text
via regex (no such extractor exists anywhere else in the codebase) and checks
each one resolves to a real file / real line range in the Stage 1 checkout.
Writes `results/citation_results.jsonl`.

## Analysis

```bash
python -m analysis.summarize
```

Aggregates all three `results/*.jsonl` files into `results/summary.md` with
the exact numbers to drop into the resume bullets.

## Cleanup

```bash
python -m common.cleanup --dry-run   # see what would be removed
python -m common.cleanup             # actually remove it
```

Deletes every `tasks`/`repositories`/`chat_history`/`chat_messages` row,
Qdrant point, and on-disk directory recorded in `results/eval_manifest.json` --
nothing else. Safe to run any time, including partway through a run.
