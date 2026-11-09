#!/usr/bin/env python3
"""cortex_sanitize.py — make untrusted cycle text safe to display, copy or log.

Every string the status adapter, the decision handler and the event journal
emit passes through `clean()` (or `clean_tree()` for whole JSON values):

- terminal control sequences are removed — ANSI CSI (`ESC [ … final`), OSC
  (`ESC ] … BEL|ST`), other 2-byte ESC sequences, C1 controls and every C0
  control except `\\n` and `\\t`. A hostile EXP.md, receipt or transcript can
  therefore never move a cursor, set a title or write the clipboard;
- likely secrets are redacted to `[REDACTED]` — AWS access key ids, GitHub
  tokens (`ghp_`/`gho_`/`ghs_`/`ghu_`/`ghr_`/`github_pat_`), Slack tokens,
  `Bearer …` and `Authorization: Basic …` headers, URL userinfo
  (`scheme://user:pass@`), bare JWTs, `sk_live_`/`sk-ant-` keys, PEM
  private-key blocks and `key|token|secret|password` assignments. Best effort by pattern: it narrows exposure, it is not a
  guarantee — the adapter also never emits environment values or transcripts;
- an optional `limit` bounds the length (an ellipsis marks the cut).

Stdlib only, pure functions. The CLI is the same `clean()` as a filter, for a
skill writing tool output to a file (the review's `evidence/` files):

    <command> | cortex_sanitize.py [--limit N] > <file>
"""

from __future__ import annotations

import argparse
import base64
import binascii
import re
import sys

REDACTED = "[REDACTED]"

_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_ESC_RE = re.compile(r"\x1b[@-Z\\-_]?")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_SECRET_RES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{10,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),
)
# Kept around the marker: the scheme and host of a URL, the word `Basic` before its credential.
# Userinfo runs to the authority's last `@` (a password may hold a raw one); the user may be empty (`redis://:pw@h`).
# The password stops at whitespace, `/` or a quote, so one JSON line's next field is never swallowed. Stated
# residuals: a raw `/` in a password ends the match (that URL is not redacted); a bare `host:443?x=a@b` reads as
# userinfo and is over-redacted (fail-safe).
_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/?#@:\"']*:[^\s/\"']*@")
# A Basic credential after an Authorization header name, however short ("Basic YTpi").
_BASIC_HEADER_RE = re.compile(r"(?i)(authorization[\"'\\]*\s*[:=]\s*[\"'\\]*basic[ \t]+)[A-Za-z0-9+/=]{4,}")
# A Basic credential anywhere else, like Bearer (an assignment, escaped JSON), on the same line: a token that
# decodes as base64 to `user:pass` (`_basic_credential`), so prose ("basic security checks", "basic OAuth2 flow")
# is left alone.
_BASIC_RE = re.compile(r"(?i)\b(basic[ \t]+)([A-Za-z0-9+/]{4,}={0,2})(?![A-Za-z0-9+/=])")


def _basic_credential(token: str) -> bool:
    """A Basic token: base64 that decodes to bytes holding a `:` (user:pass) and no control byte but a tab — in any
    charset, so a Latin-1 password is caught too."""
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    return b":" in raw and all(byte >= 0x20 and byte != 0x7F or byte == 0x09 for byte in raw)
# A backslash belongs to the value as half of an escape pair (`\x`, `\\`) — so a secret holding one is redacted
# whole — but never before a quote: redacting an already JSON-quoted string (a resume text, a prompt line) must
# not swallow the escape of a quote and close the literal early. A value that is exactly `[REDACTED]` (followed by
# whatever ends a value, or by one of the escapes a JSON literal holds after it — `\n`, `\t`, `\"`, `\\`,
# `\u2028`/`\u2029`, so a second pass over a quoted literal never swallows the text that follows) is never matched
# again, so a second pass is a no-op (redaction is idempotent); one that merely starts with the marker is still a
# value and is redacted — except raw text that glues one of those escapes, as literal characters, right after the
# marker (the declared trade-off: raw text cannot be told apart from a literal here). A quoted value keeps its quotes
# around the marker, so text that followed the closing quote never becomes part of the value on the next pass.
_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:secret|token|passw(?:or)?d|api[_-]?key|access[_-]?key|private[_-]?key)"
    r"[A-Za-z0-9_.-]*)(\s*[:=]\s*)([\"']?)(?!\[REDACTED\](?:[\s\"',;]|\\[\\\"nt]|\\u202[89]|$))((?:[^\s\"',;\\]|\\[^\"\s]|\\(?=\s|$)){4,})\3"
)


def strip_controls(text: str) -> str:
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    return _CTRL_RE.sub("", text)


def redact(text: str) -> str:
    for pattern in _SECRET_RES:
        text = pattern.sub(REDACTED, text)
    text = _USERINFO_RE.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
    text = _BASIC_HEADER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _BASIC_RE.sub(lambda m: f"{m.group(1)}{REDACTED}" if _basic_credential(m.group(2)) else m.group(0), text)
    return _ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(3)}", text)


def clean(value, limit: int | None = None) -> str:
    """Controls stripped, secrets redacted, optionally bounded. Non-str → str."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = redact(strip_controls(text))
    if limit is not None and limit > 0 and len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def clean_tree(value, limit: int | None = None):
    """Apply `clean` to every string inside a JSON-shaped value (keys included)."""
    if isinstance(value, str):
        return clean(value, limit)
    if isinstance(value, dict):
        return {clean(k): clean_tree(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_tree(v, limit) for v in value]
    return value


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="stdin, cleaned (controls stripped, secrets redacted), to stdout")
    parser.add_argument("--limit", type=_positive, default=None)
    args = parser.parse_args(argv)
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")  # tool output is not always UTF-8
    sys.stdout.buffer.write(clean(text, args.limit).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
