"""Error classification for MacroPruner-Ctx.

Why custom exceptions instead of plain raise?
  LLM clients vary in how they handle MCP protocol errors vs. tool
  return strings. Many clients (including some versions of
  Claude Desktop) swallow protocol errors silently and surface only
  the text content. So we return error INFORMATION as text, but
  prefix it with a severity tag the LLM can grep:

      [FATAL]   something the user must fix (bad path, missing dep)
      [ERROR]   unexpected internal failure (parser bug, IO error)
      [WARN]    something that didn't go as expected but
                the tool recovered (e.g. one dep file unreadable
                in read_c_with_deps, others still returned)

  The LLM should treat [FATAL] and [ERROR] as "this call did not
  succeed, retry with different args". [WARN] is "this call
  succeeded but with caveats; you may want to mention it".

Tool code in mcp_server.py catches all exceptions and re-emits
them through `format_error()`. The Tool's banner doesn't have to
care about the original exception type.
"""
from __future__ import annotations

import os
from typing import Optional


class MacroPrunerError(Exception):
    """Base for errors that should surface to the LLM with a tag."""

    def __init__(self, message: str, severity: str = "ERROR", hint: str = ""):
        super().__init__(message)
        self.severity = severity
        self.hint = hint

    def formatted(self) -> str:
        lines = [f"[{self.severity}] {self.args[0]}"]
        if self.hint:
            lines.append(f"  hint: {self.hint}")
        return "\n".join(lines)


class FatalError(MacroPrunerError):
    """The user must fix something. Call did not produce useful output."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message, severity="FATAL", hint=hint)


class TransientError(MacroPrunerError):
    """The call mostly worked but with caveats. Output may be partial."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message, severity="WARN", hint=hint)


def format_error(exc: BaseException) -> str:
    """Render any exception as a tagged string for tool return value.

    Known MacroPrunerError subclasses get their full formatted()
    treatment. Unknown exceptions get a generic [ERROR] prefix
    with type + message so the LLM has something to grep on.
    """
    if isinstance(exc, MacroPrunerError):
        return exc.formatted()
    if isinstance(exc, FileNotFoundError):
        return FatalError(
            str(exc),
            hint="check that the file exists and the path is correct",
        ).formatted()
    if isinstance(exc, (ValueError, TypeError)):
        return FatalError(
            str(exc),
            hint="this usually means invalid arguments",
        ).formatted()
    if isinstance(exc, PermissionError):
        return FatalError(
            str(exc),
            hint="the file is not writable; check permissions",
        ).formatted()
    # Unknown — keep the original message but tag it.
    return f"[ERROR] {type(exc).__name__}: {exc}"


def with_fallback(fn, *args, fallback_value=None, **kwargs):
    """Call fn(*args, **kwargs); on FatalError, return fallback_value.

    Used for read_c_with_deps's per-dep loop: if one dependency file
    errors, log a WARN-tagged message and keep going with the rest.

    Args:
        fn: callable to invoke
        fallback_value: what to return on error
    Returns:
        fn's return value, or fallback_value on error.
    """
    try:
        return fn(*args, **kwargs)
    except MacroPrunerError as e:
        # Re-raise fatal; just record transient.
        if isinstance(e, FatalError):
            raise
        return fallback_value
    except Exception as e:
        # Unknown error inside a per-dep loop: warn, don't kill the
        # whole call.
        return fallback_value
