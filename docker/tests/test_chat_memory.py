"""
Pure-function tests for `src/chat/memory.py`'s token-budget history
reconstruction — no Supabase/network I/O needed.
"""
from langchain_core.messages import AIMessage, HumanMessage

from src.chat.memory import count_message_tokens, row_to_message, truncate_to_budget


def _msg(role: str, n_chars: int):
    cls = HumanMessage if role == "user" else AIMessage
    return cls(content="x" * n_chars)


def test_truncate_to_budget_keeps_newest_first():
    messages = [_msg("user", 40), _msg("assistant", 40), _msg("user", 40), _msg("assistant", 40)]
    # each message is ~10 tokens (40 chars // 4); a 15-token budget fits only the newest one
    kept = truncate_to_budget(messages, budget_tokens=15)
    assert kept == [messages[-1]]


def test_truncate_to_budget_keeps_everything_under_budget():
    messages = [_msg("user", 40), _msg("assistant", 40)]
    kept = truncate_to_budget(messages, budget_tokens=1000)
    assert kept == messages


def test_truncate_to_budget_always_keeps_at_least_the_newest_message():
    messages = [_msg("user", 4000)]
    kept = truncate_to_budget(messages, budget_tokens=1)
    assert kept == messages


def test_row_to_message_maps_known_roles():
    user_msg = row_to_message({"role": "user", "content": "hi"})
    assistant_msg = row_to_message({"role": "assistant", "content": "hello"})
    assert isinstance(user_msg, HumanMessage) and user_msg.content == "hi"
    assert isinstance(assistant_msg, AIMessage) and assistant_msg.content == "hello"


def test_row_to_message_ignores_unknown_roles():
    assert row_to_message({"role": "system", "content": "x"}) is None


def test_count_message_tokens_is_length_based():
    assert count_message_tokens(_msg("user", 40)) == 10
