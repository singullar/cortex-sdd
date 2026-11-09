#!/usr/bin/env python3
"""Unit tests for scripts/cortex_sanitize.py — untrusted cycle text made safe.

Contract: terminal control sequences never survive (`\\n`/`\\t` do), likely
secrets are redacted, bounds are honored, and `clean_tree` reaches every
string of a JSON value (keys included) without touching non-strings.

Self-contained on purpose (stdlib unittest only): run with
    python3 test/test_cortex_sanitize.py
"""

from __future__ import annotations

import subprocess
import sys
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cortex_sanitize import REDACTED, clean, clean_tree, redact, strip_controls  # noqa: E402

CONTROL_CHARS = [chr(c) for c in range(0x00, 0x20) if chr(c) not in "\n\t"] + [chr(c) for c in range(0x7F, 0xA0)]


class StripControlsTests(unittest.TestCase):
    def assertNoControls(self, text: str) -> None:
        for ch in CONTROL_CHARS:
            self.assertNotIn(ch, text, f"control {ord(ch):#04x} survived in {text!r}")

    def test_ansi_csi_color_and_cursor_moves_removed(self):
        self.assertEqual(strip_controls("\x1b[31mred\x1b[0m \x1b[2J\x1b[10;5Hhere"), "red here")

    def test_osc_title_and_clipboard_writes_removed(self):
        self.assertEqual(strip_controls("\x1b]0;pwned title\x07ok"), "ok")
        self.assertEqual(strip_controls("\x1b]52;c;ZXZpbA==\x1b\\after"), "after")  # OSC 52 + ST

    def test_unterminated_osc_swallows_the_rest(self):
        self.assertEqual(strip_controls("before\x1b]0;never terminated"), "before")

    def test_c0_and_c1_removed_but_newline_and_tab_kept(self):
        self.assertEqual(strip_controls("a\x00b\x07c\rd\ne\tf\x7fg\x9bh"), "abcd\ne\tfgh")

    def test_hostile_mix_leaves_no_control_byte(self):
        hostile = "\x1b[?1049h\x1b]8;;http://evil\x1b\\link\x1b]8;;\x1b\\\x1bc\x1b7\x9d0;t\x9c\x08\x1b(B"
        out = strip_controls(hostile)
        self.assertNoControls(out)
        self.assertNotIn("\x1b", out)
        self.assertIn("link", out)

    def test_plain_text_untouched(self):
        text = "Phase 2 — Ship · org/repo#12 · ação ✓\n\tindented"
        self.assertEqual(strip_controls(text), text)


class RedactTests(unittest.TestCase):
    def test_aws_access_key_ids(self):
        self.assertEqual(redact("key AKIAABCDEFGHIJKLMNOP end"), f"key {REDACTED} end")
        self.assertEqual(redact("ASIAABCDEFGHIJKLMNOP"), REDACTED)

    def test_github_tokens(self):
        self.assertEqual(redact("ghp_" + "a" * 36), REDACTED)
        self.assertEqual(redact("x gho_" + "B" * 30 + " y"), f"x {REDACTED} y")
        self.assertEqual(redact("github_pat_" + "A1_" * 10), REDACTED)

    def test_slack_and_bearer(self):
        self.assertEqual(redact("xoxb-1234567890-abcdef"), REDACTED)
        self.assertEqual(redact("Authorization: Bearer abc.def-ghi_jkl012"), f"Authorization: {REDACTED}")

    def test_pem_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(redact(f"before\n{pem}\nafter"), f"before\n{REDACTED}\nafter")
        self.assertEqual(redact("-----BEGIN PRIVATE KEY-----\ntruncated"), REDACTED)  # unterminated → to the end

    def test_key_value_assignments(self):
        self.assertEqual(redact("password=hunter22"), f"password={REDACTED}")
        self.assertEqual(redact('API_KEY: "abcd1234"'), f'API_KEY: "{REDACTED}"')  # the quotes stay: a second pass is a no-op
        self.assertEqual(redact("db_secret = 's3cr3t!'"), f"db_secret = '{REDACTED}'")
        self.assertEqual(redact("GITHUB_TOKEN=xyz12345 next"), f"GITHUB_TOKEN={REDACTED} next")

    def test_a_quoted_literal_keeps_its_escapes(self):
        # Redacting an already JSON-quoted string must never swallow the backslash of an escaped quote.
        self.assertEqual(redact('"Which token=abcd\\" x"'), f'"Which token={REDACTED}\\" x"')
        self.assertEqual(redact(f'"token={REDACTED}\\""'), f'"token={REDACTED}\\""')

    def test_a_secret_holding_a_backslash_is_redacted_whole(self):
        for text in ("token=ab\\cdef", "password=abc\\", "token=ab\\\\cd", 'api_key: "sk\\abcdef"'):
            self.assertEqual(redact(text).split(REDACTED)[-1].strip('"'), "", text)
        self.assertEqual(redact("token=abc\\ next"), f"token={REDACTED} next")  # a trailing backslash mid-text

    def test_redaction_is_idempotent(self):
        for text in ("secret:'abcd'one", "secret:'abcd'two", "password: hunter22 and token=ab\\cdef",
                     "-----BEGIN RSA PRIVATE KEY-----\nabc", 'x "secret: [REDACTED]X"'):
            once = redact(text)
            self.assertEqual(redact(once), once, text)
        self.assertNotEqual(redact("secret:'abcd'one"), redact("secret:'abcd'two"))  # distinct values stay distinct

    def test_a_value_that_only_starts_with_the_marker_is_still_redacted(self):
        for text in ("password=[REDACTED]hunter2hunter2", "api_key: [REDACTED]AKIDsecretvalue123",
                     "token='[REDACTED]realtoken'"):
            self.assertNotIn("hunter2", redact(text), text)
            self.assertNotIn("AKIDsecret", redact(text), text)
            self.assertNotIn("realtoken", redact(text), text)
        for text in ("token=[REDACTED]", 'token="[REDACTED]" next', "x token=[REDACTED], y"):
            self.assertEqual(redact(text), text)  # exactly the marker: never matched again

    def test_a_second_pass_over_a_quoted_literal_keeps_the_text_after_the_marker(self):
        for text in ("Rotate token=abcd1234\nshould we deploy-now or wait?",
                     "Deploy with api_key=AbCd1234\tStaging-or-production first?",
                     'say api_key=abcd1234\\" then more'):
            literal = json.dumps(redact(text), ensure_ascii=False)
            self.assertEqual(redact(literal), literal, text)
        for text in ("Rotate token=abcd1234\u2028should we deploy-now", "Rotate token=abcd1234\u2029then"):
            literal = json.dumps(redact(text), ensure_ascii=False).replace("\u2028", "\\u2028").replace(
                "\u2029", "\\u2029")  # as quote() writes them
            self.assertEqual(redact(literal), literal, text)

    def test_the_declared_trade_off_after_the_marker(self):
        self.assertEqual(redact("password=[REDACTED]\\thunter2"), "password=[REDACTED]\\thunter2")  # kept
        for text in ("password=[REDACTED]/hunter2", "password=[REDACTED]\\fhunter2"):
            self.assertEqual(redact(text), "password=[REDACTED]", text)  # not an escape quote() writes

    def test_ordinary_prose_is_not_redacted(self):
        for text in ("token budget is 5", "the secret: ok", "passwords must rotate", "keyboard=us",
                     "basic configuration options", "https://github.com/acme/widget/pull/7", "ssh://git@host/repo",
                     "http://host:8080/path", "see https://a.b/c and mail bob@x.y", "a Basic Understanding",
                     "grants basic read/write access", "a basic PostgreSQL index", "the Basic JavaScript course",
                     "Basic auth/login flow", "is basic\nDeployment2 runs",
                     "basic security checks", "a basic workflow", "Basic Postgres setup", "basic OAuth2 flow",
                     "Basic S3 bucket", "basic IPv4 routing", "basic 101"):
            self.assertEqual(redact(text), text)

    def test_url_userinfo_basic_auth_jwt_and_api_keys(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        cases = {
            "git clone https://bob:hunter2@github.com/o/r": f"git clone https://{REDACTED}@github.com/o/r",
            "Authorization: Basic dXNlcjpwYXNzd29yZA==": f"Authorization: Basic {REDACTED}",
            '{"Authorization": "Basic dXNlcjpwYXNz"}': f'{{"Authorization": "Basic {REDACTED}"}}',
            "{'Authorization': 'Basic dXNlcjpwYXNz'}": f"{{'Authorization': 'Basic {REDACTED}'}}",
            "redis://:hunter2@cache:6379/0": f"redis://{REDACTED}@cache:6379/0",
            "postgres://u:p#ss@db/app": f"postgres://{REDACTED}@db/app",
            "redis://:a?b@cache": f"redis://{REDACTED}@cache",
            "https://host:8080/p?e=a@b": "https://host:8080/p?e=a@b",
            "https://bob:p@ss@github.com/o/r?x=1": f"https://{REDACTED}@github.com/o/r?x=1",
            '{"url":"https://a:b@h","x":"y@z"}': f'{{"url":"https://{REDACTED}@h","x":"y@z"}}',  # no field swallowed
            'auth = "Basic dXNlcjpwYXNz"': f'auth = "Basic {REDACTED}"',
            "Authorization: Basic YTpi": f"Authorization: Basic {REDACTED}",
            "basic YWRtaW46cORzc3f2cmQ=": f"basic {REDACTED}",
            "basic dTpwCXc=": f"basic {REDACTED}",
            "Authorization: Basic ABCDEFGHIJKL": f"Authorization: Basic {REDACTED}",
            '{\\"Authorization\\": \\"Basic dXNlcjpwYXNz\\"}': f'{{\\"Authorization\\": \\"Basic {REDACTED}\\"}}',
            f"cookie {jwt} end": f"cookie {REDACTED} end",
            "charge with sk_live_4eC39HqLyjWDarjtT1zdp7dc now": f"charge with {REDACTED} now",
            "ANTHROPIC sk-ant-api03-AbCdEfGhIjKlMnOp": f"ANTHROPIC {REDACTED}",
        }
        for text, want in cases.items():
            with self.subTest(text):
                self.assertEqual(redact(text), want)
                self.assertEqual(redact(want), want)  # idempotent


class CleanTests(unittest.TestCase):
    def test_controls_stripped_before_redaction(self):
        # an ANSI reset spliced into a key id must not shield it from redaction
        self.assertEqual(clean("AKIA\x1b[0mABCDEFGHIJKLMNOP"), REDACTED)

    def test_bounds(self):
        self.assertEqual(clean("abcdef", 4), "abc…")
        self.assertEqual(clean("abc", 3), "abc")
        self.assertEqual(clean("abcdef"), "abcdef")  # no limit → untouched
        self.assertEqual(clean("abcdef", 0), "abcdef")  # non-positive limit → no bound

    def test_non_strings_become_strings(self):
        self.assertEqual(clean(None), "")
        self.assertEqual(clean(5), "5")

    def test_clean_tree_reaches_every_string_and_keeps_types(self):
        tree = {
            "a\x1b[1m": ["x\x07", {"k": "password=hunter22"}, 3, None, True, 1.5],
            "n": 7,
            "t": ("tu\x00ple",),
        }
        self.assertEqual(clean_tree(tree), {
            "a": ["x", {"k": f"password={REDACTED}"}, 3, None, True, 1.5],
            "n": 7,
            "t": ["tuple"],
        })

    def test_clean_tree_limit_applies_to_strings_only(self):
        self.assertEqual(clean_tree({"k": "abcdef", "n": 123456}, limit=4), {"k": "abc…", "n": 123456})


class CliTests(unittest.TestCase):
    """The filter the review's evidence files go through: a gate's last lines, a PR body."""

    def run_cli(self, data: bytes, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "cortex_sanitize.py"), *args],
                              input=data, capture_output=True, timeout=30)

    def test_stdin_is_cleaned_to_stdout_lines_kept(self):
        token = "ghp_" + "a" * 36
        out = self.run_cli(f"\x1b[31mFAIL\x1b[0m test_x\nGITHUB_TOKEN={token}\n\t2 failed, 9 passed\n".encode())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.decode(), f"FAIL test_x\nGITHUB_TOKEN={REDACTED}\n\t2 failed, 9 passed\n")

    def test_invalid_utf8_is_replaced_never_a_crash(self):
        out = self.run_cli(b"ok \xff\xfe tail\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.decode("utf-8"), "ok \ufffd\ufffd tail\n")

    def test_empty_stdin(self):
        out = self.run_cli(b"")
        self.assertEqual((out.returncode, out.stdout), (0, b""))

    def test_limit(self):
        out = self.run_cli(b"abcdef", "--limit", "4")
        self.assertEqual((out.returncode, out.stdout.decode()), (0, "abc…"))
        for bad in ("0", "-3", "x"):
            with self.subTest(bad):
                self.assertEqual(self.run_cli(b"abcdef", "--limit", bad).returncode, 2)


if __name__ == "__main__":
    unittest.main()
