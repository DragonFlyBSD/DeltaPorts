"""Phase B (Step 48 cutover) — the deterministic bootstrap/abort routing
that replaces defer-to-convert at a build failure with no overlay.dops."""

from dportsv3.agent.overlay_state import OverlayFacts, bootstrap_decision


def _facts(**kw) -> OverlayFacts:
    return OverlayFacts(origin="category/name", port_exists=True, **kw)


def test_overlay_present_proceeds_to_patch():
    d = bootstrap_decision(_facts(overlay_dops=True), None)
    assert d.action == "proceed"


def test_new_port_bootstraps_type_port():
    d = bootstrap_decision(_facts(), None)
    assert d.action == "bootstrap"
    assert d.overlay_type == "port"
    assert d.remove_status is False


def test_pure_dport_bootstraps_type_dport_and_drops_status():
    d = bootstrap_decision(_facts(newport=True), "dport")
    assert d.action == "bootstrap"
    assert d.overlay_type == "dport"
    assert d.remove_status is True


def test_dport_with_makefile_dragonfly_aborts():
    # A dport that ALSO carries a Makefile.DragonFly has non-dport compat
    # to absorb — a bare type=dport header would drop it. Abort to human.
    d = bootstrap_decision(
        _facts(newport=True, makefile_dragonfly=("Makefile.DragonFly",)), "dport"
    )
    assert d.action == "abort"


def test_makefile_dragonfly_aborts():
    d = bootstrap_decision(_facts(makefile_dragonfly=("Makefile.DragonFly",)), None)
    assert d.action == "abort"


def test_diffs_abort():
    d = bootstrap_decision(_facts(diff_files=("diffs/Makefile.diff",)), None)
    assert d.action == "abort"


def test_dragonfly_files_abort():
    d = bootstrap_decision(_facts(dragonfly_files=("dragonfly/patch-x",)), None)
    assert d.action == "abort"


def test_lock_status_bootstraps_type_lock():
    d = bootstrap_decision(_facts(), "lock")
    assert d.action == "bootstrap"
    assert d.overlay_type == "lock"
    assert d.remove_status is True


def test_mask_status_aborts():
    # A masked port shouldn't reach a build failure; never auto-author a mask.
    d = bootstrap_decision(_facts(), "mask")
    assert d.action == "abort"
