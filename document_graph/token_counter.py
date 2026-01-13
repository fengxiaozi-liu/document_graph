from __future__ import annotations


def approx_tokens(text: str) -> int:
    # MVP: provider-agnostic approximation; refine later with real tokenizer.
    # Empirically, English ~4 chars/token; Chinese often closer to 1-2 chars/token.
    # We choose a conservative bound.
    n = len(text)
    return max(1, n // 3)


def approx_message_tokens(role: str, content: str) -> int:
    # Add small overhead for role/formatting.
    return approx_tokens(content) + 8

