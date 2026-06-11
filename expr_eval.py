"""
ExpressionEvaluator — recursive-descent evaluator for C preprocessor
expressions. Replaces the tiny `evaluate_condition` inside pruner_core
and extends it to cover the patterns real embedded codebases actually use.

Supported grammar (subset of C11 6.10.1, plus a few Linux-kernel
extensions handled as macro-expansion whitelist):

    expression  := or_expr
    or_expr     := and_expr ('||' and_expr)*
    and_expr    := unary    ('&&' unary)*
    unary       := '!' unary | primary
    primary     := '(' expression ')' | comparison
    comparison  := additive (('==' | '!=' | '<' | '>' | '<=' | '>=') additive)?
    additive    := multiplicative (('+' | '-') multiplicative)*
    multiplicative := unary (('*' | '/' | '%') unary)*
    atom        := number | defined_call | identifier | macro_call

`defined_call` is `defined ( IDENT )` or `defined IDENT`.

`macro_call` is `IDENT ( args... )` — looked up in
`_MACRO_EXPANSION_WHITELIST` (e.g. IS_ENABLED, IS_BUILTIN). Anything
else evaluates as `defined(IDENT)`.

All identifiers are matched case-insensitively. The active-macros dict
maps lower-cased names to their string values (or None if defined-but-
no-value, e.g. plain `-DFOO`).

Examples
--------
>>> ev = ExpressionEvaluator({'product_type': '3'})
>>> ev.evaluate("PRODUCT_TYPE == 3")
True
>>> ev.evaluate("PRODUCT_TYPE == 5")
False
>>> ev.evaluate("defined(A) && defined(B)").evaluate(...)  # etc
False

>>> ev = ExpressionEvaluator({'config_foo': None, 'arch_x86': None})
>>> ev.evaluate("IS_ENABLED(CONFIG_FOO)")
True
>>> ev.evaluate("defined(ARCH_X86) || defined(ARCH_ARM)")
True
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple


# Macros that look like function calls but are pure compile-time tests.
# We expand them to defined() checks (or value checks, depending on the
# macro's kernel-style definition). Substring match is enough — we look
# for the macro name followed by '(' and replace the whole call.
_MACRO_EXPANSION_WHITELIST = {
    # Linux kernel style
    "is_enabled": "defined",
    "is_builtin": "defined",
    # Some BSD / RTOS variants
    "isc_enabled": "defined",
}


def _normalize_macro_dict(
    macros: Dict[str, Optional[str]],
) -> Dict[str, Optional[str]]:
    """Lower-case all keys. Values are kept as-is (numeric strings stay
    numeric; identifier-like values lower-cased). Numeric strings are
    accepted as int with base=0 (auto-detects 0x/0o/0b prefixes)."""
    out: Dict[str, Optional[str]] = {}
    for k, v in macros.items():
        nk = k.lower()
        if v is None:
            out[nk] = None
        else:
            try:
                int(v, 0)
                out[nk] = v
            except ValueError:
                try:
                    float(v)
                    out[nk] = v
                except ValueError:
                    out[nk] = v.lower()
    return out


class ExpressionEvaluator:
    """Evaluate C preprocessor expressions against an active-macro dict.

    A single instance is bound to one macro set; create a new instance per
    evaluation context to avoid cross-contamination.
    """

    # Token patterns. Order matters: longer first.
    # `defined` is treated as a soft keyword — its own kind, distinct
    # from generic identifiers, so the parser can route it specially.
    _TOKEN_RE = re.compile(
        r"""
        (?P<WS>\s+)                 |
        (?P<NUM>0[xX][0-9a-fA-F]+|  # hex
             \d+\.\d*|\.\d+|        # float
             \d+)                   # int
        |
        (?P<DEF>\bdefined\b)        |
        (?P<ID>[A-Za-z_][A-Za-z0-9_]*)
        |
        (?P<OP>==|!=|<=|>=|&&|\|\||<<|>>|[<>=+\-*/%!()\[\],])
        """,
        re.VERBOSE,
    )

    def __init__(self, active_macros: Dict[str, Optional[str]]):
        self._macros = _normalize_macro_dict(active_macros)

    # ── Public ──────────────────────────────────────────────────────

    def evaluate(self, condition: str) -> bool:
        """Evaluate a preprocessor condition. Returns True/False.

        On parse failure (unbalanced parens, trailing tokens, unknown
        operators), raises ValueError with the offending text. We do NOT
        silently fall back to False — that would cause the pruner to drop
        a code block the user thought was active.
        """
        condition = self._expand_whitelist_macros(condition)
        tokens = self._tokenize(condition)
        if not tokens:
            raise ValueError("empty condition")
        parser = _Parser(tokens, self._macros)
        result = parser.parse_expression()
        if parser.pos != len(tokens):
            raise ValueError(
                f"trailing tokens in condition: {condition!r} "
                f"(parsed {parser.pos} of {len(tokens)})"
            )
        return bool(result)

    # ── Tokenizer ───────────────────────────────────────────────────

    def _tokenize(self, text: str) -> list:
        out = []
        for m in self._TOKEN_RE.finditer(text):
            kind = m.lastgroup
            if kind == "WS":
                continue
            out.append((kind, m.group()))
        return out

    # ── Macro whitelist expansion ──────────────────────────────────

    def _expand_whitelist_macros(self, condition: str) -> str:
        """Replace IS_ENABLED(X) → defined(X), IS_BUILTIN(X) → defined(X).

        Done as a textual pass before tokenization. Robust against
        nested whitespace; we only replace when the function-call form
        is well-formed (balanced parens, no commas inside the
        argument — `IS_ENABLED(MACRO_FLAG)` is the only realistic shape).
        """
        for macro, replacement in _MACRO_EXPANSION_WHITELIST.items():
            # Match `MACRO_NAME(` then find the matching `)` and replace
            # the whole call. Case-insensitive on the macro name.
            pattern = re.compile(
                r"\b" + re.escape(macro) + r"\s*\(",
                re.IGNORECASE,
            )
            while True:
                m = pattern.search(condition)
                if not m:
                    break
                # Find matching close paren.
                depth = 1
                i = m.end()
                while i < len(condition) and depth > 0:
                    ch = condition[i]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    i += 1
                if depth != 0:
                    # Malformed — leave the text alone; the parser will
                    # either succeed (unlikely) or raise.
                    break
                arg = condition[m.end():i - 1].strip()
                if not arg.isidentifier() and not re.match(
                    r"^[A-Za-z_][A-Za-z0-9_]*$", arg
                ):
                    # Multi-arg or non-identifier — leave it; the parser
                    # will see the raw tokens and likely raise.
                    break
                condition = (
                    condition[:m.start()]
                    + replacement
                    + "("
                    + arg
                    + ")"
                    + condition[i:]
                )
        return condition


class _Parser:
    """Recursive-descent parser for preprocessor expressions."""

    def __init__(self, tokens: list, macros: Dict[str, Optional[str]]):
        self.tokens = tokens
        self.macros = macros
        self.pos = 0

    # Entry points
    def parse_expression(self):
        return self._parse_or()

    # or_expr := and_expr ('||' and_expr)*
    def _parse_or(self):
        left = self._parse_and()
        while self._peek_op("||"):
            self.pos += 1
            right = self._parse_and()
            left = (left or right)
        return left

    # and_expr := unary ('&&' unary)*
    def _parse_and(self):
        left = self._parse_unary()
        while self._peek_op("&&"):
            self.pos += 1
            right = self._parse_unary()
            left = (left and right)
        return left

    # unary := '!' unary | primary
    def _parse_unary(self):
        if self._peek_op("!"):
            self.pos += 1
            return not self._parse_unary()
        return self._parse_primary()

    # primary := '(' expression ')' | comparison
    def _parse_primary(self):
        if self._peek_op("("):
            self.pos += 1
            inner = self.parse_expression()
            if not self._peek_op(")"):
                raise ValueError("missing ')'")
            self.pos += 1
            return inner
        return self._parse_comparison()

    # comparison := additive (CMP additive)?
    def _parse_comparison(self):
        left = self._parse_additive()
        op = self._peek_op()
        if op in ("==", "!=", "<", ">", "<=", ">="):
            self.pos += 1
            right = self._parse_additive()
            return self._apply_cmp(op, left, right)
        return left

    # additive := multiplicative (('+' | '-') multiplicative)*
    def _parse_additive(self):
        left = self._parse_multiplicative()
        while self._peek_op() in ("+", "-"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            right = self._parse_multiplicative()
            if op == "+":
                left = (left or 0) + (right or 0)
            else:
                left = (left or 0) - (right or 0)
        return left

    # multiplicative := unary (('*' | '/' | '%') unary)*
    def _parse_multiplicative(self):
        left = self._parse_unary_atom()
        while self._peek_op() in ("*", "/", "%"):
            op = self.tokens[self.pos][1]
            self.pos += 1
            right = self._parse_unary_atom()
            try:
                if op == "*":
                    left = (left or 0) * (right or 0)
                elif op == "/":
                    left = (left or 0) // (right or 1)
                else:
                    left = (left or 0) % (right or 1)
            except ZeroDivisionError:
                left = 0
        return left

    # unary_atom := number | defined_call | identifier | macro_call
    def _parse_unary_atom(self):
        kind, val = self.tokens[self.pos]

        if kind == "NUM":
            self.pos += 1
            return self._coerce_number(val)

        # `defined ( IDENT )` or `defined IDENT` — both forms.
        if kind == "DEF":
            return self._parse_defined()

        if kind == "ID":
            return self._parse_identifier_or_call(val)

        if kind == "OP" and val == "(":
            # Should be handled in _parse_primary, but allow a fallback.
            return self._parse_primary()

        raise ValueError(f"unexpected token: {val!r}")

    # `defined ( IDENT )` or `defined IDENT`
    def _parse_defined(self):
        self.pos += 1  # consume 'defined'
        # Skip whitespace tokens (already filtered in tokenize, but be
        # safe in case of re-entry).
        # Optional `(`.
        if self._peek_op("("):
            self.pos += 1
            if self.pos >= len(self.tokens) or self.tokens[self.pos][0] != "ID":
                raise ValueError("expected identifier after 'defined('")
            name = self.tokens[self.pos][1].lower()
            self.pos += 1
            if not self._peek_op(")"):
                raise ValueError("expected ')' after defined(...)")
            self.pos += 1
            return name in self.macros
        # Bare form: `defined IDENT`
        if self.pos >= len(self.tokens) or self.tokens[self.pos][0] != "ID":
            raise ValueError("expected identifier after 'defined'")
        name = self.tokens[self.pos][1].lower()
        self.pos += 1
        return name in self.macros

    # Bare identifier → numeric value following C preprocessor semantics:
    #   defined with numeric value  → that value (int or float)
    #   defined with non-numeric    → 1 (truthy in `#if`)
    #   defined with no value (-D X) → 1
    #   not defined                  → 0
    # Function-call identifier → handled by whitelist (already expanded in
    # the pre-pass) or, for unknown calls, fall back to `defined(IDENT)`.
    def _parse_identifier_or_call(self, ident: str):
        ident_lc = ident.lower()
        # Function-call form?
        if (
            self.pos + 1 < len(self.tokens)
            and self.tokens[self.pos + 1] == ("OP", "(")
        ):
            # Whitelist should have been expanded in the pre-pass; be
            # defensive in case the pre-pass didn't match (e.g. multi-arg
            # call). Skip to matching ')' and return defined(IDENT).
            self.pos += 2  # consume IDENT and '('
            depth = 1
            while self.pos < len(self.tokens) and depth > 0:
                tok = self.tokens[self.pos]
                if tok == ("OP", "("):
                    depth += 1
                elif tok == ("OP", ")"):
                    depth -= 1
                self.pos += 1
            return 1 if ident_lc in self.macros else 0

        self.pos += 1
        if ident_lc not in self.macros:
            return 0
        raw = self.macros[ident_lc]
        if raw is None:
            return 1
        try:
            return int(raw, 0)  # base 0 = auto-detect 0x/0o prefixes
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                # String value (e.g. -DNAME=Foo) — use 1 (presence check).
                return 1

    # Helpers
    def _peek_op(self, op: Optional[str] = None):
        if self.pos >= len(self.tokens):
            return None
        kind, val = self.tokens[self.pos]
        if kind != "OP":
            return None
        if op is not None and val != op:
            return None
        return val

    @staticmethod
    def _coerce_number(text: str):
        text = text.lower()
        if text.startswith("0x"):
            return int(text, 16)
        if "." in text:
            return float(text)
        return int(text)

    @staticmethod
    def _apply_cmp(op: str, left, right) -> bool:
        # For comparisons involving undefined identifiers, treat them
        # as 0 (C preprocessor convention).
        if left is None:
            left = 0
        if right is None:
            right = 0
        try:
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == "<":
                return left < right
            if op == ">":
                return left > right
            if op == "<=":
                return left <= right
            if op == ">=":
                return left >= right
        except TypeError:
            # Comparing string to int — treat as not-equal.
            return op == "!="
        return False
