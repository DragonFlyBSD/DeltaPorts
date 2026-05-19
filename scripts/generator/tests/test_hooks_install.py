"""Tests for ``dportsv3 hooks install/uninstall/status``."""

from __future__ import annotations

import os
import stat
from argparse import Namespace
from pathlib import Path

import pytest

from dportsv3.commands.hooks import (
    CONF_TARGET,
    HOOK_SCRIPTS,
    cmd_hooks,
)


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """A fake hook source dir with all expected files."""
    src = tmp_path / "src"
    src.mkdir()
    for name in HOOK_SCRIPTS:
        (src / name).write_text(f"#!/bin/sh\n# {name}\n")
    (src / "dportsv3-hooks.conf.example").write_text(
        "# example config\nARTIFACT_STORE_URL=http://127.0.0.1:8788\n"
    )
    return src


def _args(**kw: object) -> Namespace:
    return Namespace(**kw)


def test_install_copies_all_hooks_and_sets_exec_bit(
    source_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dst = tmp_path / "etc-dsynth"
    rc = cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=False)
    )
    assert rc == 0
    for name in HOOK_SCRIPTS:
        assert (dst / name).is_file()
        mode = (dst / name).stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH
    assert (dst / CONF_TARGET).is_file()
    out = capsys.readouterr().out
    assert "Next steps:" in out


def test_install_preserves_existing_conf(
    source_dir: Path, tmp_path: Path
) -> None:
    dst = tmp_path / "etc-dsynth"
    dst.mkdir()
    (dst / CONF_TARGET).write_text("OPERATOR_VALUE=keep-me\n")

    rc = cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=False)
    )
    assert rc == 0
    assert (dst / CONF_TARGET).read_text() == "OPERATOR_VALUE=keep-me\n"


def test_install_force_overwrites_conf(
    source_dir: Path, tmp_path: Path
) -> None:
    dst = tmp_path / "etc-dsynth"
    dst.mkdir()
    (dst / CONF_TARGET).write_text("OPERATOR_VALUE=keep-me\n")

    rc = cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=True)
    )
    assert rc == 0
    assert "OPERATOR_VALUE=keep-me" not in (dst / CONF_TARGET).read_text()
    assert "ARTIFACT_STORE_URL" in (dst / CONF_TARGET).read_text()


def test_uninstall_removes_hooks_preserves_conf(
    source_dir: Path, tmp_path: Path
) -> None:
    dst = tmp_path / "etc-dsynth"
    cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=False)
    )

    rc = cmd_hooks(_args(hooks_action="uninstall", prefix=dst, purge=False))
    assert rc == 0
    for name in HOOK_SCRIPTS:
        assert not (dst / name).exists()
    assert (dst / CONF_TARGET).exists(), "uninstall should preserve operator config"


def test_uninstall_purge_removes_conf(
    source_dir: Path, tmp_path: Path
) -> None:
    dst = tmp_path / "etc-dsynth"
    cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=False)
    )

    rc = cmd_hooks(_args(hooks_action="uninstall", prefix=dst, purge=True))
    assert rc == 0
    assert not (dst / CONF_TARGET).exists()


def test_status_clean_install(
    source_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dst = tmp_path / "etc-dsynth"
    cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=False)
    )

    rc = cmd_hooks(_args(hooks_action="status", prefix=dst, source=source_dir))
    assert rc == 0
    out = capsys.readouterr().out
    assert f"{len(HOOK_SCRIPTS)} hook(s) installed" in out
    assert "0 missing" in out


def test_status_detects_stale(
    source_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dst = tmp_path / "etc-dsynth"
    cmd_hooks(
        _args(hooks_action="install", source=source_dir, prefix=dst, force=False)
    )

    # Bump source mtime so installed file looks stale.
    new_time = (source_dir / "hook_pkg_failure").stat().st_mtime + 60
    os.utime(source_dir / "hook_pkg_failure", (new_time, new_time))

    cmd_hooks(_args(hooks_action="status", prefix=dst, source=source_dir))
    out = capsys.readouterr().out
    assert "stale" in out


def test_status_missing_dir_returns_nonzero(
    tmp_path: Path, source_dir: Path
) -> None:
    dst = tmp_path / "nope"
    rc = cmd_hooks(_args(hooks_action="status", prefix=dst, source=source_dir))
    assert rc != 0
