"""Pin the alignment between hook record-result terms and tracker API.

Smoke surfaced a 422 from /api/builds/<run>/results because
hook_pkg_success was calling ``tracker_record_result pass`` while the
API's ``BuildResultLiteral`` enum accepts only ``success`` /
``failure`` / ``skipped`` / ``ignored``. Same bug on hook_pkg_failure
(``fail`` vs ``failure``).

This test pins both sides so any future drift fails fast.
"""

from __future__ import annotations

import re
from pathlib import Path


_REPO = Path(__file__).resolve().parents[3]
_HOOKS_DIR = _REPO / "scripts" / "dsynth-hooks"
_MODELS = _REPO / "scripts" / "generator" / "dportsv3" / "tracker" / "models.py"


def _hook_record_calls(name: str) -> list[str]:
    """Return every ``tracker_record_result <token>`` argument in the
    given hook file."""
    body = (_HOOKS_DIR / name).read_text()
    return re.findall(r"^tracker_record_result\s+(\S+)\s*$",
                      body, re.MULTILINE)


def _api_enum_values() -> set[str]:
    body = _MODELS.read_text()
    m = re.search(
        r"BuildResultLiteral\s*=\s*Literal\[(.+?)\]",
        body, re.DOTALL,
    )
    assert m, "couldn't locate BuildResultLiteral in models.py"
    # Extract quoted strings.
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_every_hook_record_call_uses_an_api_enum_value():
    """If a hook calls tracker_record_result with a string that the
    API's BuildResultLiteral doesn't accept, every build of that
    type fails with a generic 422 in production — and there's no
    test layer above that catches it. Hard-pin here."""
    expected = _api_enum_values()
    assert expected == {"success", "failure", "skipped", "ignored"}, (
        "Test out of date with BuildResultLiteral; update the test "
        "expectation if you intentionally added/removed enum values."
    )
    for hook in ("hook_pkg_success", "hook_pkg_failure",
                 "hook_pkg_skipped", "hook_pkg_ignored"):
        calls = _hook_record_calls(hook)
        assert calls, f"{hook} doesn't call tracker_record_result"
        for token in calls:
            assert token in expected, (
                f"{hook} calls tracker_record_result {token!r} but the "
                f"API's BuildResultLiteral accepts only {sorted(expected)!r}. "
                f"Fix the hook (preferred) or extend the enum."
            )


def test_record_result_cli_rejects_legacy_tokens():
    """The CLI's --result argparse now constrains to the same enum,
    so even if a future hook regression slips through, the CLI fails
    fast with a clear argparse error instead of a generic API 422."""
    import subprocess, sys, importlib.util

    # Drive the parser directly rather than spawning the whole CLI;
    # cli.build_parser exposes the configured ArgumentParser.
    spec = importlib.util.find_spec("dportsv3.cli")
    assert spec is not None
    from dportsv3 import cli as dp_cli  # noqa: PLC0415
    parser = dp_cli.create_parser()

    import argparse
    # 'pass' should now fail with SystemExit (argparse choices error).
    try:
        parser.parse_args(["tracker", "record-result",
                           "--run", "1", "--origin", "x/y",
                           "--version", "1.0", "--result", "pass"])
    except SystemExit as exc:
        # argparse exits with code 2 on choice error.
        assert exc.code == 2
    else:
        raise AssertionError(
            "--result=pass should be rejected by argparse choices"
        )

    # 'success' should parse cleanly.
    args = parser.parse_args(["tracker", "record-result",
                              "--run", "1", "--origin", "x/y",
                              "--version", "1.0", "--result", "success"])
    assert args.result == "success"
