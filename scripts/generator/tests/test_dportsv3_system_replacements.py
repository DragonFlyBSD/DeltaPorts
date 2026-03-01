from __future__ import annotations

from pathlib import Path

from dportsv3.system_replacements import apply_system_replacements_to_port


def test_system_replacements_apply_and_report_hits(tmp_path: Path) -> None:
    port = tmp_path / "devel" / "a"
    port.mkdir(parents=True)
    makefile = port / "Makefile"
    makefile.write_text(
        "OPTIONS_DEFAULT_amd64= yes\n"
        '.if ${ARCH} == "amd64"\n'
        "BROKEN_amd64= old\n"
        ".endif\n"
        "LIB_DEPENDS= libomp.so:devel/openmp libomp.so.0:devel/openmp\n"
    )

    stats = apply_system_replacements_to_port(port, dry_run=False)

    assert stats.files_scanned == 1
    assert stats.files_changed == 1
    text = makefile.read_text()
    assert "OPTIONS_DEFAULT_x86_64= yes" in text
    assert "BROKEN_x86_64= old" in text
    assert '"amd64"' not in text
    assert "libomp.so:devel/openmp" not in text
    assert "libomp.so.0:devel/openmp" not in text
    assert stats.rule_hits.get("options-default-amd64", 0) >= 1
    assert stats.rule_hits.get("arch-guarded-amd64", 0) >= 1
    assert stats.rule_hits.get("libomp-dep", 0) >= 1


def test_system_replacements_are_idempotent(tmp_path: Path) -> None:
    port = tmp_path / "devel" / "b"
    port.mkdir(parents=True)
    makefile = port / "Makefile"
    makefile.write_text("OPTIONS_DEFINE_amd64= yes\n")

    first = apply_system_replacements_to_port(port, dry_run=False)
    second = apply_system_replacements_to_port(port, dry_run=False)

    assert first.files_changed == 1
    assert second.files_changed == 0
    assert makefile.read_text() == "OPTIONS_DEFINE_x86_64= yes\n"
