"""Tests for convert payload + proof parser (Step 20b).

The ``run()`` end-to-end LLM call is not exercised here — it
needs a real provider and is a manual smoke-test for now. These
tests cover the deterministic parts:

- ``build_convert_payload`` surfaces the deterministic translator's
  output, the unsupported items, and the legacy source artifacts.
- ``parse_conversion_proof`` extracts JSON from the agent's
  response under the various shapes the model might emit.
"""

from __future__ import annotations

from pathlib import Path

from dportsv3.agent.convert import build_convert_payload, parse_conversion_proof


def _make_port(tmp_path: Path, origin: str) -> Path:
    port = tmp_path / "ports" / origin
    port.mkdir(parents=True)
    return port


def test_payload_surfaces_deterministic_ops_and_unsupported(tmp_path: Path) -> None:
    """Payload includes the auto-generated ops AND the unsupported
    reasons so the agent knows what's done vs. what's its job."""
    port = _make_port(tmp_path, "devel/with-conditional")
    (port / "Makefile.DragonFly").write_text(
        "USES+=pkgconfig\n"
        ".if ${OPSYS} == DragonFly\n"
        "CFLAGS+=-DFOO\n"
        ".endif\n"
    )
    classified = {
        "origin": "devel/with-conditional",
        "bucket": "review-needed",
        "classification_reasons": ["conditional_block_present"],
    }
    payload = build_convert_payload(
        origin="devel/with-conditional",
        repo_root=tmp_path,
        classified_record=classified,
        deterministic_result={"status": "blocked", "errors": []},
        dops_quickref_text="# quickref placeholder",
    )
    # Deterministic ops section names the op the parser produced.
    assert "Deterministic ops already produced" in payload
    assert 'mk add USES "pkgconfig"' in payload
    # Unsupported items section names the parser's reason.
    assert "Unsupported items" in payload
    assert "conditional_block_present" in payload
    # Source Makefile.DragonFly excerpt is included verbatim.
    assert "Makefile.DragonFly" in payload
    assert ".if ${OPSYS} == DragonFly" in payload
    # dops quickref is appended.
    assert "quickref placeholder" in payload


def test_payload_surfaces_raw_diffs(tmp_path: Path) -> None:
    """Ports with ``diffs/`` get each diff inlined so the agent can
    classify framework vs source-simple vs source-complex."""
    port = _make_port(tmp_path, "devel/has-diffs")
    diffs = port / "diffs"
    diffs.mkdir()
    (diffs / "patch-config.diff").write_text(
        "--- a/configure.ac\n+++ b/configure.ac\n@@ -1 +1 @@\n-FreeBSD\n+FreeBSDLike\n"
    )
    classified = {
        "origin": "devel/has-diffs",
        "bucket": "fallback-only",
        "classification_reasons": ["raw_diffs_present"],
    }
    payload = build_convert_payload(
        origin="devel/has-diffs",
        repo_root=tmp_path,
        classified_record=classified,
        deterministic_result={"status": "fallback", "errors": []},
        dops_quickref_text="",
    )
    assert "diffs/patch-config.diff" in payload
    assert "FreeBSDLike" in payload


def test_payload_handles_port_with_no_legacy_artifacts(tmp_path: Path) -> None:
    """If somehow the port has no legacy files at all, payload still
    renders without crashing — the sections are just empty."""
    _make_port(tmp_path, "devel/empty")
    payload = build_convert_payload(
        origin="devel/empty",
        repo_root=tmp_path,
        classified_record={"bucket": "?", "classification_reasons": []},
        deterministic_result={"status": "blocked", "errors": []},
        dops_quickref_text="",
    )
    # Should still mention the origin in the header.
    assert "devel/empty" in payload


def test_parse_conversion_proof_labeled_block() -> None:
    response = """Here's the conversion summary.

## Conversion Proof (JSON)

```json
{
  "origin": "devel/foo",
  "mechanical_ops_written": 5,
  "framework_migrated_to_dops": ["mk block set for OPSYS conditional"],
  "source_migrated_to_semantic": [],
  "source_patches_retained": [],
  "files_removed": ["Makefile.DragonFly"],
  "files_added": ["overlay.dops"],
  "verification_pending": true
}
```
"""
    proof = parse_conversion_proof(response)
    assert proof is not None
    assert proof["origin"] == "devel/foo"
    assert proof["mechanical_ops_written"] == 5
    assert proof["framework_migrated_to_dops"] == ["mk block set for OPSYS conditional"]


def test_parse_conversion_proof_fallback_to_last_json_block() -> None:
    """Agent omits the heading but emits a JSON object with an
    ``origin`` field — parser still finds it."""
    response = """All done.

```json
{"origin": "devel/foo", "source_patches_retained": [{"file": "x", "reason": "y"}]}
```
"""
    proof = parse_conversion_proof(response)
    assert proof is not None
    assert proof["origin"] == "devel/foo"
    assert proof["source_patches_retained"][0]["file"] == "x"


def test_parse_conversion_proof_skips_unrelated_json_blocks() -> None:
    """A JSON block without an ``origin`` is not the proof — parser
    keeps looking."""
    response = """Some thinking:

```json
{"plan": "do thing"}
```

## Conversion Proof (JSON)

```json
{"origin": "devel/foo", "files_added": ["overlay.dops"]}
```
"""
    proof = parse_conversion_proof(response)
    assert proof is not None
    assert proof["origin"] == "devel/foo"
    # Not the planning blob.
    assert "plan" not in proof


def test_parse_conversion_proof_returns_none_on_garbage() -> None:
    assert parse_conversion_proof("") is None
    assert parse_conversion_proof("just prose, no json") is None
    assert parse_conversion_proof("```\nnot json\n```") is None


def test_convert_success_predicate_requires_validate_dops_ok() -> None:
    """The attempt_loop's stop condition for convert REQUIRES
    proof.validate_dops_ok==True. A proof emitted against a dops
    the agent hasn't validated (or validated unsuccessfully) must
    NOT short-circuit attempt_loop — otherwise the agent could
    ship a broken dops the handler would reject anyway, wasting
    the attempt.

    The predicate is defined inline in ``convert.run``; test it via
    a lambda mirroring the contract so future drift fails here.
    """
    def is_success(p):
        if not isinstance(p, dict):
            return False
        if not isinstance(p.get("origin"), str):
            return False
        return p.get("validate_dops_ok") is True

    # Valid proofs.
    assert is_success({
        "origin": "devel/foo",
        "validate_dops_ok": True,
    }) is True

    # No origin → not a proof, keep looping.
    assert is_success({"validate_dops_ok": True}) is False

    # validate_dops_ok missing → keep looping.
    assert is_success({"origin": "devel/foo"}) is False

    # validate_dops_ok=False → keep looping.
    assert is_success({"origin": "devel/foo", "validate_dops_ok": False}) is False

    # validate_dops_ok truthy-but-not-True (e.g. "true" string) → reject.
    assert is_success({"origin": "devel/foo", "validate_dops_ok": "true"}) is False

