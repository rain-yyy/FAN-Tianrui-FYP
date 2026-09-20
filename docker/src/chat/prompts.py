"""
Single system prompt for the chat agent.

Replaces the old `docker/src/agent/prompts.py`'s four prompts (planner,
tool-router, evaluator, synthesizer — ~540 lines) plus the hand-written JSON
tool-schema examples they embedded: tool schemas are now sent structurally
via `bind_tools()` (see `docker/src/chat/tools/`), so the model reads real
typed schemas instead of imitating prose JSON examples.
"""
from __future__ import annotations

import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.prompts import OUTPUT_LANGUAGE_EN

_SYSTEM_TEMPLATE = """You are a repository-understanding assistant for the repository at {repo_url}.

## When to use tools
Answer directly from your own knowledge or the conversation history when the question does not need
repository-specific facts: greetings, general programming concepts, opinions, or follow-ups you can
resolve from what was already said. Otherwise, use the available tools to gather grounded evidence
before answering.

## Tool preferences
- Prefer structural tools (`code_graph`, `file_read`, `grep_search`) over `rag_search` when you need an
  exact location, relationship, or line of code — they give precise, verifiable answers.
- Use `rag_search` for concept/documentation questions and when you need semantically relevant context
  rather than an exact match.
- Use `repo_map` first when you need repository-wide orientation (architecture, tech stack, entrypoints).
- Use `web_search` only for knowledge outside this repository (external package versions, CVEs, API docs).
- Call as many tools as you need, but stop calling tools once you are confident you can answer well —
  do not keep exploring past the point of diminishing returns.

## Answering
- Cite exact evidence: `file_path:line_start-line_end` for structural claims.
- Mark claims you cannot verify from tool results as "likely" or "unclear" rather than stating them as fact.
- If a diagram would clarify an architecture, mechanism, or call flow, include one directly in your
  answer as a fenced ```mermaid``` code block (e.g. `graph TD` or `sequenceDiagram`) — do not put it in
  a separate field.
- Never fabricate file paths, line numbers, or code you have not actually seen via a tool result.
""" + OUTPUT_LANGUAGE_EN + """

<REPO_FACTS>
{repo_facts}
</REPO_FACTS>
"""


def build_system_prompt(repo_url: str, repo_facts: str = "") -> str:
    return _SYSTEM_TEMPLATE.format(
        repo_url=repo_url,
        repo_facts=repo_facts or "No durable facts recorded for this repository yet.",
    )


def render_repo_facts(facts: dict) -> str:
    """
    Flatten a `repo_memory.facts` JSON blob (see `src/chat/repo_memory.py`)
    into the plain text `build_system_prompt`'s `repo_facts` arg expects.
    Empty/missing fields are omitted entirely rather than stubbed out, so an
    empty `facts` dict renders to "" and `build_system_prompt` falls back to
    its own placeholder string.
    """
    if not facts:
        return ""

    lines: list[str] = []

    tech_stack = facts.get("tech_stack") or {}
    if tech_stack:
        lines.append("Tech stack: " + ", ".join(f"{k}: {v}" for k, v in tech_stack.items()))

    entrypoints = facts.get("key_entrypoints") or []
    if entrypoints:
        lines.append("Key entrypoints: " + ", ".join(entrypoints))

    responsibilities = facts.get("module_responsibilities") or {}
    if responsibilities:
        lines.append("Module responsibilities:")
        lines.extend(f"  - {module}: {desc}" for module, desc in responsibilities.items())

    constraints = facts.get("core_constraints") or []
    if constraints:
        lines.append("Known constraints:")
        lines.extend(f"  - {c}" for c in constraints)

    failed = facts.get("failed_approaches") or []
    if failed:
        lines.append("Previously failed approaches (do not repeat):")
        lines.extend(f"  - {f}" for f in failed)

    return "\n".join(lines)


_SUMMARY_SYSTEM_PROMPT = """You compress conversation history into a concise running summary while preserving
critical context: the user's core goals, established facts about the codebase, and any user preferences
or constraints mentioned. Discard tool-call mechanics, verbose explanations, and dead-end exploration —
keep conclusions only. Write a short paragraph (plain text, no JSON, no markdown headers).""" + OUTPUT_LANGUAGE_EN


def build_summary_messages(history_text: str, prior_summary: str, question: str) -> list[BaseMessage]:
    """Messages for the `chat_session_compressor` role — plain-text summary, not JSON."""
    human = (
        "Update the running summary of this conversation.\n\n"
        f"<PRIOR_SUMMARY>\n{prior_summary or '(none yet)'}\n</PRIOR_SUMMARY>\n\n"
        f"<NEW_MESSAGES>\n{history_text}\n</NEW_MESSAGES>\n\n"
        f"<LATEST_QUESTION>\n{question}\n</LATEST_QUESTION>\n\n"
        "Return only the updated summary paragraph."
    )
    return [SystemMessage(content=_SUMMARY_SYSTEM_PROMPT), HumanMessage(content=human)]


_REPO_MEMORY_EXTRACTION_SYSTEM_PROMPT = """You maintain a small, durable JSON fact sheet about this repository, carried across
chat sessions (this is long-term memory, not this conversation's context). Given the prior fact sheet
and a transcript of this turn's tool-assisted investigation, return an UPDATED fact sheet with the
same shape.

Rules:
- Only add or change a field when you are confident from evidence actually seen in this transcript
  (real tool results, not guesses or restating the question).
- Leave a field unchanged if nothing new was learned about it this turn.
- Keep lists short: at most 10 entries in `key_entrypoints`, `core_constraints`, and
  `failed_approaches` — drop the least useful old entry before adding a new one past that limit.
- Return ONLY a JSON object with exactly these keys, no others: `module_responsibilities` (object of
  path -> one-sentence string), `key_entrypoints` (array of strings), `tech_stack` (object of
  string -> string), `core_constraints` (array of strings), `failed_approaches` (array of strings).
  No prose, no markdown code fences, just the JSON object."""


def build_repo_memory_extraction_messages(prior_facts: dict, transcript_text: str) -> list[BaseMessage]:
    """Messages for the `chat_repo_memory_extractor` role — see `src/chat/repo_memory.py`."""
    human = (
        "Prior fact sheet (JSON):\n"
        f"{json.dumps(prior_facts, ensure_ascii=False)}\n\n"
        "This turn's transcript (tool calls, tool results, final answer):\n"
        f"{transcript_text}\n\n"
        "Return the updated fact sheet JSON now."
    )
    return [SystemMessage(content=_REPO_MEMORY_EXTRACTION_SYSTEM_PROMPT), HumanMessage(content=human)]


_PLAN_SYSTEM_PROMPT = """Before the agent starts gathering evidence, produce a short internal plan for how to
answer the user's question. This is scratch thinking the agent will see as context for its own tool
calls — not the final answer, and not shown to the user as-is.

Write 2-4 plain-text sentences covering: what is actually being asked, what kind of evidence would
answer it, and which tool(s) to reach for first. Be concrete about tool choice when the answer is
obvious from the question (e.g. "use code_graph to find callers of X"), but don't force a rigid list of
steps if the question is simple or exploratory. If the question needs no tools at all (a greeting, a
general concept, a follow-up answerable from conversation history), say so directly instead of
inventing a plan.

Do not answer the question itself here — only plan the approach."""


def build_plan_messages(system_prompt: str, question: str) -> list[BaseMessage]:
    """Messages for the `chat_planner` role — see the `plan` node in `src/chat/graph.py`."""
    return [
        SystemMessage(content=system_prompt),
        SystemMessage(content=_PLAN_SYSTEM_PROMPT),
        HumanMessage(content=question),
    ]


_VERIFY_SYSTEM_PROMPT = """You are checking a draft answer against the evidence actually gathered for it, before it
is shown to the user. You will see the full conversation, including every tool call and tool result the
agent made, followed by its draft final answer.

Check narrowly: does the evidence in the transcript actually support the draft answer's claims? Look
specifically for a claim the tool results don't back up, a cited file/line that doesn't appear in any
tool result, or a distinct part of the question the draft never actually addresses. Do not nitpick
style, completeness beyond the question asked, or claims correctly marked as "likely"/"unclear".

Respond with exactly one line:
- `PASS` if the draft is adequately supported, or
- `FAIL: <one sentence describing the specific gap>` if it is not.

Never respond with anything else."""


def build_verify_messages(transcript_messages: list[BaseMessage], draft_answer: str) -> list[BaseMessage]:
    """Messages for the `chat_verifier` role — see the `verify` node in `src/chat/graph.py`."""
    return (
        [SystemMessage(content=_VERIFY_SYSTEM_PROMPT)]
        + list(transcript_messages)
        + [HumanMessage(content=f"Draft final answer:\n{draft_answer}\n\nPASS or FAIL?")]
    )


__all__ = [
    "build_system_prompt",
    "render_repo_facts",
    "build_summary_messages",
    "build_repo_memory_extraction_messages",
    "build_plan_messages",
    "build_verify_messages",
]
