-- Chat memory schema changes for the agent-first chat redesign
-- (docker/src/chat/). Not applied automatically -- run this against your
-- Supabase project (SQL editor or `supabase db execute`) before deploying
-- the new chat backend, since docker/src/chat/memory.py depends on these
-- columns/table existing.

-- Rolling conversation summary, so history reconstruction (chat/memory.py)
-- doesn't have to re-read/re-summarize an entire long session every turn.
-- The cutoff is tracked by `created_at` timestamp, not message id: chat_messages.id
-- is a uuid (not sequential), so there is no meaningful "greater than" ordering on
-- it to build a numeric/FK cutoff from. Timestamps are what chat/memory.py already
-- orders history by, so reusing that column avoids a type mismatch entirely.
ALTER TABLE chat_history
  ADD COLUMN IF NOT EXISTS session_summary text,
  ADD COLUMN IF NOT EXISTS summary_up_to_created_at timestamptz;

-- Durable, cross-session repo facts. Realizes what the old agent's
-- `RepoFactsMemory` always claimed to be (long-term memory) but never was
-- (it was rebuilt fresh and discarded every request). The write path is a
-- stub for now (see docker/src/chat/memory.py) -- this table is created so
-- the phase-2 write path has somewhere to land without another migration.
CREATE TABLE IF NOT EXISTS repo_memory (
  repo_url text PRIMARY KEY,
  facts jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
