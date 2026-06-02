"""Integration tests for compose's scratch+reconcile output path.

The behavior under test: full composes (non-dry-run, non-incremental)
write into a scratch tree, then reconcile onto the live output via
``dportsv3.fsutils.reconcile``. dsynth's port-change detector
(``subs.c::crcDirTree``) folds ``mtime + size + path`` per file into
a per-port CRC; an unconditional rewrite of bit-identical content
flips the CRC and force-rebuilds the port. With the scratch+reconcile
path in place, a no-op recompose is a true filesystem no-op and
dsynth sees an unchanged CRC.

These tests pin both directions:
- No-change recompose: content/size/mtime stable for every file.
- Real change: only the affected files shift; others stay put.

Plus the operational properties (scratch cleanup, incremental and
dry-run bypass, strict failure leaves live untouched).
"""

from __future__ import annotations

import binascii
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from dportsv3.cli import main


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_git(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], root)
    _run(["git", "config", "user.email", "test@test"], root)
    _run(["git", "config", "user.name", "test"], root)
    _run(["git", "checkout", "-b", "main"], root)
    _run(["git", "add", "."], root)
    _run(["git", "commit", "-q", "-m", "init"], root)


def _setup_two_ports(freebsd: Path, delta: Path) -> Path:
    """Two ports: devel/a has a dops overlay (Makefile rewritten on
    every compose), devel/b is pure pass-through (no overlay). The
    pass-through port covers the seed-stage mtime-preservation path;
    the overlay port covers the apply-pipeline + reconcile path."""
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / "devel" / "a" / "pkg-descr").write_text("a port\n")
    (freebsd / "devel" / "b").mkdir(parents=True)
    (freebsd / "devel" / "b" / "Makefile").write_text("VAR= b\n")
    (freebsd / "devel" / "b" / "pkg-descr").write_text("b port\n")
    (freebsd / "UPDATING").write_text("upstream\n")
    _init_git(freebsd)

    overlay = delta / "ports" / "devel" / "a" / "overlay.dops"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        'target @main\nport devel/a\ntype port\nmk set VAR "new"\n'
    )
    return overlay


def _compose(output: Path, delta: Path, freebsd: Path, *extra: str) -> tuple[int, dict]:
    code = main(
        [
            "compose",
            "--target", "@main",
            "--output", str(output),
            "--delta-root", str(delta),
            "--freebsd-root", str(freebsd),
            "--oracle-profile", "off",
            "--json",
            *extra,
        ]
    )
    return code


def _snapshot(root: Path) -> dict[str, tuple]:
    """Per-file fingerprint (content_hash, size, mtime_ns) for every
    regular file under root. Dotfiles + .core excluded (mirrors
    dsynth's crcDirTree filter)."""
    snap: dict[str, tuple] = {}
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        cur_p = Path(cur)
        rel_dir = cur_p.relative_to(root)
        for name in files:
            if name.startswith(".") or name.endswith(".core"):
                continue
            p = cur_p / name
            rel = str(rel_dir / name) if str(rel_dir) != "." else name
            st = p.lstat()
            if p.is_symlink():
                snap[rel] = ("symlink", os.readlink(p), st.st_size, st.st_mtime_ns)
            else:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                snap[rel] = ("file", h, st.st_size, st.st_mtime_ns)
    return snap


def _dsynth_crc(root: Path) -> int:
    """Replicate DragonFly dsynth's subs.c::crcDirTree formula —
    per-file CRC32(mtime) ^ CRC32(size) ^ CRC32(path), XORed
    tree-wide. Regular files and symlinks, dotfiles and .core
    excluded. The whole point of the integration is to keep this
    value stable across a no-op recompose."""
    tree_crc = 0
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        cur_p = Path(cur)
        for name in files:
            if name.startswith(".") or name.endswith(".core"):
                continue
            p = cur_p / name
            st = p.lstat()
            mtime_b = int(st.st_mtime).to_bytes(8, "little", signed=False)
            size_b = int(st.st_size).to_bytes(8, "little", signed=False)
            path_b = str(p).encode("utf-8", errors="replace")
            v = binascii.crc32(mtime_b) & 0xFFFFFFFF
            v = binascii.crc32(size_b, v) & 0xFFFFFFFF
            v = binascii.crc32(path_b, v) & 0xFFFFFFFF
            tree_crc ^= v
    return tree_crc


# --- The dsynth invariant: no-op recompose is a true no-op ---


def test_noop_recompose_preserves_every_files_mtime_content_and_size(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    _setup_two_ports(freebsd, delta)

    assert _compose(output, delta, freebsd) == 0
    capsys.readouterr()

    pre_a = _snapshot(output / "devel" / "a")
    pre_b = _snapshot(output / "devel" / "b")
    pre_crc_a = _dsynth_crc(output / "devel" / "a")
    pre_crc_b = _dsynth_crc(output / "devel" / "b")

    # Sleep > 1s so any fresh write would land in a different st_mtime
    # second (dsynth uses second-granularity time_t in its CRC).
    time.sleep(1.2)

    assert _compose(output, delta, freebsd) == 0
    capsys.readouterr()

    post_a = _snapshot(output / "devel" / "a")
    post_b = _snapshot(output / "devel" / "b")
    post_crc_a = _dsynth_crc(output / "devel" / "a")
    post_crc_b = _dsynth_crc(output / "devel" / "b")

    # Per-file: content hash, size, AND mtime ns must be identical.
    # The mtime check is the key one — content + size are usually
    # stable anyway, but mtime drift is what makes dsynth rebuild.
    assert post_a == pre_a, f"devel/a (dops overlay) drifted: {post_a} vs {pre_a}"
    assert post_b == pre_b, f"devel/b (pass-through) drifted: {post_b} vs {pre_b}"
    # And the dsynth-formula CRC must stay flat.
    assert post_crc_a == pre_crc_a
    assert post_crc_b == pre_crc_b


def test_overlay_change_propagates_to_live_with_fresh_mtime(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    overlay = _setup_two_ports(freebsd, delta)

    assert _compose(output, delta, freebsd) == 0
    capsys.readouterr()

    pre_a = _snapshot(output / "devel" / "a")
    pre_b = _snapshot(output / "devel" / "b")
    pre_crc_a = _dsynth_crc(output / "devel" / "a")
    pre_crc_b = _dsynth_crc(output / "devel" / "b")

    time.sleep(1.2)
    overlay.write_text(
        'target @main\nport devel/a\ntype port\nmk set VAR "different"\n'
    )

    assert _compose(output, delta, freebsd) == 0
    capsys.readouterr()

    post_a = _snapshot(output / "devel" / "a")
    post_b = _snapshot(output / "devel" / "b")
    post_crc_a = _dsynth_crc(output / "devel" / "a")
    post_crc_b = _dsynth_crc(output / "devel" / "b")

    # Makefile changed: content, size, and mtime must all differ —
    # otherwise dsynth would miss the rebuild.
    assert post_a["Makefile"] != pre_a["Makefile"]
    assert post_a["Makefile"][1] != pre_a["Makefile"][1]  # content hash
    assert post_a["Makefile"][2] != pre_a["Makefile"][2]  # size
    assert post_a["Makefile"][3] != pre_a["Makefile"][3]  # mtime
    assert (output / "devel" / "a" / "Makefile").read_text() == "VAR= different\n"

    # Other files in devel/a unchanged (dops only touched Makefile).
    for rel in pre_a:
        if rel == "Makefile":
            continue
        assert post_a[rel] == pre_a[rel], f"devel/a/{rel} drifted"
    # devel/b entirely unchanged (different port).
    assert post_b == pre_b

    # CRCs reflect this correctly: devel/a flips, devel/b stable.
    assert post_crc_a != pre_crc_a
    assert post_crc_b == pre_crc_b


def test_first_compose_into_fresh_output_succeeds_with_reconcile_stage(
    tmp_path, capsys
) -> None:
    # First compose has no existing output — the reconcile primitive
    # would have nothing on the dst side to compare against, so
    # `_reconcile_to_live` takes the copytree branch. Verify the
    # stage still records cleanly and the tree is materialized.
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    _setup_two_ports(freebsd, delta)

    assert _compose(output, delta, freebsd) == 0
    payload = json.loads(capsys.readouterr().out)

    stages = [s["name"] for s in payload["stages"]]
    assert "reconcile_output" in stages
    assert payload["stages"][-1]["name"] == "reconcile_output"
    assert payload["stages"][-1]["success"] is True
    assert (output / "devel" / "a" / "Makefile").read_text() == "VAR= new\n"
    assert (output / "devel" / "b" / "Makefile").read_text() == "VAR= b\n"


# --- Stages where scratch is bypassed (no reconcile_output) ---


def test_incremental_compose_bypasses_scratch_indirection(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    _setup_two_ports(freebsd, delta)

    # Seed the live tree via a full compose first.
    assert _compose(output, delta, freebsd) == 0
    capsys.readouterr()

    # Re-compose with --origin (incremental) — scratch must NOT be
    # used. Verifiable via the absence of `reconcile_output` from
    # the stage list. Incremental composes are operator-explicit
    # rebuild requests; the dsynth-stability win doesn't apply and
    # the scratch overhead is unnecessary.
    assert _compose(output, delta, freebsd, "--origin", "devel/a") == 0
    payload = json.loads(capsys.readouterr().out)

    stages = [s["name"] for s in payload["stages"]]
    assert "reconcile_output" not in stages


def test_dry_run_compose_bypasses_scratch_indirection(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    _setup_two_ports(freebsd, delta)

    assert _compose(output, delta, freebsd, "--dry-run") == 0
    payload = json.loads(capsys.readouterr().out)

    stages = [s["name"] for s in payload["stages"]]
    assert "reconcile_output" not in stages
    # Dry-run: live output never created.
    assert not output.exists()


# --- Scratch lifecycle ---


def test_scratch_directory_cleaned_up_after_successful_compose(
    tmp_path, capsys, monkeypatch
) -> None:
    """Scratch lives under the OS tmp dir (via tempfile.mkdtemp) and
    must be cleaned up regardless of compose outcome. Drive
    tempfile to a known path so we can verify it's gone."""
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    scratch_anchor = tmp_path / "scratch_anchor"
    scratch_anchor.mkdir()
    _setup_two_ports(freebsd, delta)

    monkeypatch.setenv("TMPDIR", str(scratch_anchor))

    assert _compose(output, delta, freebsd) == 0
    capsys.readouterr()

    # After compose, no `dportsv3-compose-*` dir should remain.
    leaked = list(scratch_anchor.glob("dportsv3-compose-*"))
    assert leaked == [], f"scratch dirs leaked: {leaked}"


def test_scratch_directory_cleaned_up_on_strict_failure(
    tmp_path, capsys, monkeypatch
) -> None:
    """Strict-mode early returns from `_run_stages` must still drop
    the scratch dir (the try/finally guarantee). Trigger a strict
    failure via a stale port classification."""
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    scratch_anchor = tmp_path / "scratch_anchor"
    scratch_anchor.mkdir()

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_git(freebsd)

    # Overlay for a port that doesn't exist in freebsd — preflight
    # surfaces this as a stale-overlay error, and in strict mode we
    # short-circuit before reconcile.
    (delta / "ports" / "devel" / "missing").mkdir(parents=True)
    (delta / "ports" / "devel" / "missing" / "overlay.dops").write_text(
        'target @main\nport devel/missing\ntype port\nmk set VAR "x"\n'
    )

    monkeypatch.setenv("TMPDIR", str(scratch_anchor))

    code = _compose(output, delta, freebsd, "--strict")
    capsys.readouterr()
    assert code != 0  # strict failure

    leaked = list(scratch_anchor.glob("dportsv3-compose-*"))
    assert leaked == [], f"scratch dirs leaked on strict failure: {leaked}"


def test_strict_failure_does_not_touch_live_output(
    tmp_path, capsys, monkeypatch
) -> None:
    """When strict mode bails before `reconcile_output` runs, the
    live tree must not receive any writes from this compose — even
    stages that ran successfully are scoped to scratch and discarded.
    Improvement over today's write-as-you-go pipeline."""
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_git(freebsd)

    # Pre-populate live output with a sentinel so we can detect any
    # accidental mutation. With scratch indirection it should
    # survive the strict-failure compose untouched.
    output.mkdir(parents=True)
    sentinel = output / "sentinel.txt"
    sentinel.write_text("operator-placed\n")
    sentinel_mtime = sentinel.stat().st_mtime_ns

    # Strict failure: stale overlay.
    (delta / "ports" / "devel" / "missing").mkdir(parents=True)
    (delta / "ports" / "devel" / "missing" / "overlay.dops").write_text(
        'target @main\nport devel/missing\ntype port\nmk set VAR "x"\n'
    )

    time.sleep(1.2)
    code = _compose(output, delta, freebsd, "--strict")
    capsys.readouterr()
    assert code != 0

    # Live sentinel intact AND mtime preserved.
    assert sentinel.exists()
    assert sentinel.read_text() == "operator-placed\n"
    assert sentinel.stat().st_mtime_ns == sentinel_mtime
