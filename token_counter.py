"""Token counter - light-weight LLM token estimation.

Why not just call tiktoken / anthropic tokenizer?
  1. Avoid adding a heavy dependency. Most users only need an
     approximate "did this save tokens?" number, not exact billing.
  2. Avoid version-pinning nightmare (tiktoken changes, vendor SDKs
     change, etc.).
  3. Works offline. No model download. No API key.

Approach:
  Two complementary estimators - caller picks one (or uses both and
  reports the average).

  - char_estimate(text): tokens ~ chars / 3.7. This is the empirical
    average for code (mostly ASCII identifiers + operators + sparse
    whitespace) under the cl100k / o200k tokenizers used by GPT-4
    and Claude 3.5. Accuracy: +/-15%.

  - word_estimate(text): split on whitespace + C punctuation, count
    pieces, then multiply by 1.30 (subword splitting on identifiers
    typically yields 1.2-1.5 tokens per word in code). Accuracy: +/-20%
    for code, +/-10% for natural language.

  - estimate(text) returns both; caller can decide. The default for
    the MCP banner is char_estimate because it correlates best with
    what the LLM actually pays for.

References (load-bearing numbers):
  - OpenAI cookbook: 1 token ~ 4 chars of English text
  - Empirical: code averages ~3.5-4.0 chars/token for cl100k
  - Anthropic Claude: similar ratios
  - Subword effect: 1 CamelCase identifier -> 1-3 tokens
"""
from __future__ import annotations

import re
from typing import Dict, Union


# Average character-per-token ratio for code under cl100k / o200k.
# 3.7 is a conservative mid-point; bump to 4.0 for whitespace-heavy
# files, drop to 3.3 for identifier-dense.
_CODE_CHARS_PER_TOKEN = 3.7

# Subword multiplier for word-based estimation.
# Empirical: 1 CamelCase word -> 1.2-1.5 tokens under BPE; 1 underscore
# word -> 1.5-2.0. Average ~ 1.3.
_CODE_WORDS_PER_TOKEN_INV = 1.0 / 1.30

# Whitespace + C punctuation that separates "words" in code.
# Kept as a compiled regex for speed; tokenizer-style alternatives
# (e.g. tree-sitter) are out of scope for this lightweight module.
_WORD_SPLIT_RE = re.compile(r"[\s\+\-\*/%=<>!&|^~(){}\[\];,.\?:'\"#]+")


def char_estimate(text: str) -> int:
    """Estimate token count from character count.

    Args:
        text: The string to estimate.

    Returns:
        Approximate token count (always >= 0).
    """
    if not text:
        return 0
    # Use max(1, ...) so non-empty strings always get at least one
    # token. A one-char string is a degenerate case but should not
    # report "0 tokens".
    return max(1, int(len(text) / _CODE_CHARS_PER_TOKEN))


def word_estimate(text: str) -> int:
    """Estimate token count from word count, with subword correction.

    Args:
        text: The string to estimate.

    Returns:
        Approximate token count.
    """
    if not text:
        return 0
    words = [w for w in _WORD_SPLIT_RE.split(text) if w]
    if not words:
        return 0
    return max(1, int(len(words) * _CODE_WORDS_PER_TOKEN_INV))


def estimate(text: str) -> Dict[str, Union[int, float]]:
    """Run both estimators. Useful when you want to compare them.

    Returns:
        Dict with keys: chars, words, char_estimate, word_estimate, avg.
    """
    chars = char_estimate(text)
    words = word_estimate(text)
    return {
        "chars": len(text),
        "words": len([w for w in _WORD_SPLIT_RE.split(text) if w]),
        "char_estimate": chars,
        "word_estimate": words,
        "avg": (chars + words) // 2,
    }


def estimate_pair(original: str, pruned: str) -> Dict[str, Union[int, float]]:
    """Compute token savings for a before/after pair.

    Returns:
        Dict with: original_tokens, pruned_tokens, saved_tokens,
        saved_pct. Uses char_estimate as the authoritative number.
    """
    o = char_estimate(original)
    p = char_estimate(pruned)
    saved = max(0, o - p)
    pct = round(saved / o * 100, 2) if o > 0 else 0.0
    return {
        "original_tokens": o,
        "pruned_tokens": p,
        "saved_tokens": saved,
        "saved_pct": pct,
    }
