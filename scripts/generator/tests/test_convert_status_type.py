"""Q2: STATUS → overlay.dops ``type`` directive integration.

Two layers under test:

1. ``convert.read_status_port_type`` resolves STATUS's first-token
   to a normalized port type, or returns None for absent /
   unrecognized / empty files. The convert payload surfaces this
   to the agent as a non-negotiable header directive.

2. ``runner._check_status_dops_type_parity`` is the handler-side
   safety guard that fires before STATUS is deleted from
   ``files_removed``: it compares the STATUS-declared type to the
   ``type`` directive in the just-written overlay.dops and refuses
   to delete STATUS on mismatch. Without this guard, an agent that
   emits the wrong type would silently switch a MASK port to PORT
   (= start materializing the upstream we explicitly denied).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.convert import build_convert_payload, read_status_port_type
from dportsv3.agent.runner import (
    _check_status_dops_type_parity,
    _read_dops_port_type,
)


# --- read_status_port_type ---------------------------------------------------


def test_read_status_recognizes_port_default(tmp_path):
    (tmp_path / "STATUS").write_text(
        "PORT\nLast attempt: 1.0\nLast success: 1.0\n"
    )
    assert read_status_port_type(tmp_path) == "port"


def test_read_status_recognizes_mask(tmp_path):
    (tmp_path / "STATUS").write_text("MASK\n")
    assert read_status_port_type(tmp_path) == "mask"


def test_read_status_recognizes_dport(tmp_path):
    (tmp_path / "STATUS").write_text("DPORT\n")
    assert read_status_port_type(tmp_path) == "dport"


def test_read_status_recognizes_lock(tmp_path):
    (tmp_path / "STATUS").write_text("LOCK\nVersion: 1.0\n")
    assert read_status_port_type(tmp_path) == "lock"


def test_read_status_absent_file_returns_none(tmp_path):
    assert read_status_port_type(tmp_path) is None


def test_read_status_empty_file_returns_none(tmp_path):
    (tmp_path / "STATUS").write_text("")
    assert read_status_port_type(tmp_path) is None


def test_read_status_unknown_token_returns_none(tmp_path):
    """An unrecognized first token (e.g. junk file or future
    token) returns None so the handler can fall back to
    compat-default ``port`` rather than crashing."""
    (tmp_path / "STATUS").write_text("WIDGET\nstuff\n")
    assert read_status_port_type(tmp_path) is None


# --- build_convert_payload surfaces the expected type -----------------------


def _classified_record():
    return {"bucket": "compat_unrecognized", "classification_reasons": []}


def _det_result():
    return {"status": "needs_llm", "parse_ok": True,
            "check_ok": True, "plan_ok": True,
            "deterministic_ok": False, "errors": []}


def test_payload_surfaces_mask_type_as_mandatory(tmp_path):
    """For non-default types the prompt must instruct the agent to
    emit a matching ``type`` directive — otherwise the handler's
    safety guard will refuse to delete STATUS."""
    port_dir = tmp_path / "ports" / "devel" / "demo"
    port_dir.mkdir(parents=True)
    (port_dir / "STATUS").write_text("MASK\n")

    payload = build_convert_payload(
        origin="devel/demo", repo_root=tmp_path,
        classified_record=_classified_record(),
        deterministic_result=_det_result(),
        dops_quickref_text="",
    )
    assert "## Expected port type" in payload
    assert "MASK" in payload
    assert "type mask" in payload
    assert "MUST include a matching" in payload


def test_payload_notes_port_default_as_clarifying(tmp_path):
    """For PORT (the default), the prompt still mentions the type
    for clarity but doesn't impose the safety-guard language."""
    port_dir = tmp_path / "ports" / "devel" / "demo"
    port_dir.mkdir(parents=True)
    (port_dir / "STATUS").write_text("PORT\nLast attempt: 1.0\n")

    payload = build_convert_payload(
        origin="devel/demo", repo_root=tmp_path,
        classified_record=_classified_record(),
        deterministic_result=_det_result(),
        dops_quickref_text="",
    )
    assert "## Expected port type" in payload
    assert "type port" in payload
    # PORT case omits the safety-guard wording — the planner's
    # default is already port, so an absent directive is fine.
    assert "MUST include a matching" not in payload


def test_payload_omits_section_when_status_absent(tmp_path):
    """No STATUS → no expected-type section. The agent can still
    emit ``type port`` per the dops quickref convention; no
    instruction is needed."""
    port_dir = tmp_path / "ports" / "devel" / "demo"
    port_dir.mkdir(parents=True)
    payload = build_convert_payload(
        origin="devel/demo", repo_root=tmp_path,
        classified_record=_classified_record(),
        deterministic_result=_det_result(),
        dops_quickref_text="",
    )
    assert "## Expected port type" not in payload


# --- _read_dops_port_type ---------------------------------------------------


def test_dops_reader_finds_type_directive(tmp_path):
    (tmp_path / "overlay.dops").write_text(
        "port devel/foo\ntype mask\nreason \"x\"\n"
    )
    assert _read_dops_port_type(tmp_path) == "mask"


def test_dops_reader_returns_none_when_directive_absent(tmp_path):
    (tmp_path / "overlay.dops").write_text(
        "port devel/foo\nreason \"x\"\n"
    )
    assert _read_dops_port_type(tmp_path) is None


def test_dops_reader_returns_none_when_file_absent(tmp_path):
    assert _read_dops_port_type(tmp_path) is None


def test_dops_reader_ignores_comments_after_directive(tmp_path):
    (tmp_path / "overlay.dops").write_text(
        "port devel/foo\ntype dport  # legacy DragonFly-only\n"
    )
    assert _read_dops_port_type(tmp_path) == "dport"


def test_dops_reader_rejects_unknown_type_token(tmp_path):
    """Whitelist enforced in the regex — ``type widget`` is not a
    valid plan_type, so the reader returns None (treated as default
    ``port`` by the caller)."""
    (tmp_path / "overlay.dops").write_text(
        "port devel/foo\ntype widget\n"
    )
    assert _read_dops_port_type(tmp_path) is None


# --- _check_status_dops_type_parity ----------------------------------------


def _setup_port(tmp_path, status_text: str | None,
                dops_text: str | None) -> Path:
    if status_text is not None:
        (tmp_path / "STATUS").write_text(status_text)
    if dops_text is not None:
        (tmp_path / "overlay.dops").write_text(dops_text)
    return tmp_path


def test_parity_check_passes_when_both_say_port(tmp_path):
    _setup_port(tmp_path, "PORT\n",
                "port x/y\ntype port\n")
    assert _check_status_dops_type_parity(tmp_path) is None


def test_parity_check_passes_when_both_say_mask(tmp_path):
    _setup_port(tmp_path, "MASK\n",
                "port x/y\ntype mask\n")
    assert _check_status_dops_type_parity(tmp_path) is None


def test_parity_check_passes_when_status_port_and_dops_omits_type(tmp_path):
    """Absent ``type`` directive defaults to ``port`` in the
    planner — matches STATUS=PORT. Safe to delete STATUS."""
    _setup_port(tmp_path, "PORT\n",
                "port x/y\nreason \"x\"\n")
    assert _check_status_dops_type_parity(tmp_path) is None


def test_parity_check_passes_when_no_status_and_no_dops_type(tmp_path):
    """Both defaults to port — no mismatch."""
    _setup_port(tmp_path, None,
                "port x/y\nreason \"x\"\n")
    assert _check_status_dops_type_parity(tmp_path) is None


def test_parity_check_fails_when_mask_vs_port(tmp_path):
    """The catastrophic case: STATUS=MASK, dops omits/wrong type.
    Deleting STATUS would silently start materializing the
    upstream we explicitly denied. Safety guard refuses."""
    _setup_port(tmp_path, "MASK\n",
                "port x/y\ntype port\n")
    reason = _check_status_dops_type_parity(tmp_path)
    assert reason is not None
    assert "status-type-mismatch" in reason
    assert "`mask`" in reason
    assert "`port`" in reason


def test_parity_check_fails_when_mask_status_but_dops_omits_type(tmp_path):
    """Most likely real-world failure: agent forgot the ``type``
    directive entirely. Default is port, STATUS says mask, refuse."""
    _setup_port(tmp_path, "MASK\n",
                "port x/y\nreason \"x\"\n")
    reason = _check_status_dops_type_parity(tmp_path)
    assert reason is not None
    assert "mask" in reason


def test_parity_check_fails_when_dport_vs_lock(tmp_path):
    _setup_port(tmp_path, "DPORT\n",
                "port x/y\ntype lock\n")
    reason = _check_status_dops_type_parity(tmp_path)
    assert reason is not None
    assert "dport" in reason and "lock" in reason


# --- _apply_files_removed integration with STATUS guard --------------------


def test_apply_files_removed_deletes_status_when_dops_carries_matching_type(
    tmp_path, monkeypatch,
):
    """Happy path: STATUS=MASK, overlay.dops has ``type mask``. The
    safety guard sees parity → STATUS is deleted with the rest."""
    from dportsv3.agent import worker
    from dportsv3.agent import runner as runner_mod
    from dportsv3.agent.runner import _apply_files_removed

    port_dir = (
        tmp_path / "writable" / "work" / "DeltaPorts" / "ports" / "x" / "y"
    )
    port_dir.mkdir(parents=True)
    (port_dir / "STATUS").write_text("MASK\n")
    (port_dir / "Makefile.DragonFly").write_text("legacy\n")
    (port_dir / "overlay.dops").write_text(
        "port x/y\ntype mask\nreason \"x\"\n"
    )

    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: worker.EnvPaths(
            env_dir=tmp_path / "writable",
            writable=tmp_path / "writable",
        ),
    )
    activity_rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "extra": kw.get("extra", {})}
        ),
    )

    _apply_files_removed(
        queue_root=tmp_path / "queue", env="test-env", origin="x/y",
        proof={"files_removed": ["STATUS", "Makefile.DragonFly"]},
    )

    assert not (port_dir / "STATUS").exists()
    assert not (port_dir / "Makefile.DragonFly").exists()
    extra = activity_rows[0]["extra"]
    assert "STATUS" in extra["removed"]
    assert "Makefile.DragonFly" in extra["removed"]
    assert all(
        s["path"] != "STATUS" for s in extra["skipped"]
    )


def test_apply_files_removed_refuses_status_when_dops_type_mismatches(
    tmp_path, monkeypatch,
):
    """Catastrophic case caught: STATUS=MASK, overlay.dops carries
    ``type port`` (or omits type). Deleting STATUS would silently
    start materializing the upstream we explicitly denied. Guard
    refuses; Makefile.DragonFly still gets removed (only STATUS
    is gated)."""
    from dportsv3.agent import worker
    from dportsv3.agent import runner as runner_mod
    from dportsv3.agent.runner import _apply_files_removed

    port_dir = (
        tmp_path / "writable" / "work" / "DeltaPorts" / "ports" / "x" / "y"
    )
    port_dir.mkdir(parents=True)
    (port_dir / "STATUS").write_text("MASK\n")
    (port_dir / "Makefile.DragonFly").write_text("legacy\n")
    (port_dir / "overlay.dops").write_text(
        "port x/y\ntype port\nreason \"agent forgot mask\"\n"
    )

    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: worker.EnvPaths(
            env_dir=tmp_path / "writable",
            writable=tmp_path / "writable",
        ),
    )
    activity_rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "extra": kw.get("extra", {})}
        ),
    )

    _apply_files_removed(
        queue_root=tmp_path / "queue", env="test-env", origin="x/y",
        proof={"files_removed": ["STATUS", "Makefile.DragonFly"]},
    )

    # STATUS survives — the safety guard kicked in.
    assert (port_dir / "STATUS").exists()
    # Makefile.DragonFly is unaffected by the STATUS guard.
    assert not (port_dir / "Makefile.DragonFly").exists()
    extra = activity_rows[0]["extra"]
    assert "STATUS" not in extra["removed"]
    assert "Makefile.DragonFly" in extra["removed"]
    status_skip = next(
        (s for s in extra["skipped"] if s["path"] == "STATUS"), None,
    )
    assert status_skip is not None
    assert "status-type-mismatch" in status_skip["reason"]
