"""Convert-mode renderer for the ``convert_to_dops`` intent (Step 25b).

Delegates to the existing deterministic translator in
``dportsv3.migration.convert.convert_record`` — that machinery
already knows how to read a Makefile.DragonFly, build the dops
plan, validate it, and (when not dry_run) write overlay.dops +
remove the legacy source.

This wrapper exists so the intent grammar has a single way to
spell "convert this port to dops" without duplicating the
substrate logic. Future intent types that need the same machinery
(e.g. a hypothetical "re-convert with operator overrides") would
build on top.
"""

from __future__ import annotations

from .grammar import ConvertToDops


def convert_to_dops(t, intent: ConvertToDops):
    """Run the deterministic compat→dops translator on t.port_dir.

    The migration.convert.convert_record API takes a "classified
    record" dict; we build the minimum it needs from the
    translator's (workspace, origin) pair.
    """
    from .translator import EditResult  # noqa: PLC0415

    # Late import: keeps the import graph clean for tests that
    # don't exercise convert.
    from dportsv3.migration.convert import convert_record  # noqa: PLC0415

    record = {
        "origin": t.origin,
        "bucket": "auto-safe",
    }
    result = convert_record(record, repo_root=t.workspace, dry_run=False)

    status = result.get("status")
    if status != "converted":
        errors = result.get("errors") or []
        return EditResult(
            ok=False, intent_type="convert_to_dops",
            error=(
                f"convert_record returned status={status!r}; "
                f"errors={errors}"
            ),
        )

    overlay = t.port_path("overlay.dops")
    mk = t.port_path("Makefile.DragonFly")
    paths = [str(p.relative_to(t.workspace))
             for p in (overlay, mk) if p.exists() or p == overlay]
    # Capture the diff over the whole port subtree so deletes (the
    # Makefile.DragonFly removal) land in substrate_diff too.
    return EditResult(
        ok=True, intent_type="convert_to_dops",
        paths_changed=paths,
        substrate_diff=t.git_diff(t.port_dir),
    )
