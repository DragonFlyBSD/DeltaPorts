"""Smoke test for the runner module's import surface.

Phase 1 Step 3.5: ``scripts/agent-queue-runner`` moved into
``dportsv3.agent.runner`` as a proper module so tests can import
internals directly. Lock that in with a tiny import test.
"""

from __future__ import annotations


def test_runner_module_imports():
    """The package import works at all (catches missing deps + syntax)."""
    from dportsv3.agent import runner  # noqa: F401


def test_runner_main_is_callable():
    """main() is the entrypoint; tests + the CLI subcommand both call it."""
    from dportsv3.agent.runner import main

    assert callable(main)


def test_runner_orchestration_helpers_importable():
    """Helpers used by the lifecycle integration test in Step 4 must be
    importable without firing up the LLM, the DB, or the queue."""
    from dportsv3.agent.runner import (
        _apply_transition,           # lifecycle.apply wrapper
        _completion_events_for,      # outcome → events mapping
        _register_new_job,           # initial HOOK_ENQUEUED + jobs metadata
        claim_next_job_batch,        # filesystem queue claim
        parse_job_file,              # .job file → dict
        process_job,                 # the orchestration entrypoint
    )

    # Sanity: the symbols are actually callable.
    for fn in (_apply_transition, _completion_events_for, _register_new_job,
               claim_next_job_batch, parse_job_file, process_job):
        assert callable(fn)


def test_completion_events_mapping_basic():
    """Quick sanity on the (job_type, success, status) → events mapping
    so a refactor doesn't silently break it. Full coverage lives in
    the Step 4 integration test."""
    from dportsv3.agent.lifecycle import JobEvent
    from dportsv3.agent.runner import _completion_events_for

    assert _completion_events_for("triage", True, "done") == [JobEvent.TRIAGE_OK]
    assert _completion_events_for("triage", True, "manual_tier") == [
        JobEvent.TRIAGE_OK, JobEvent.ESCALATE_MANUAL,
    ]
    assert _completion_events_for("triage", False, "boom") == [JobEvent.TRIAGE_FAIL]
    assert _completion_events_for("patch", True, "done") == [
        JobEvent.PATCH_OK, JobEvent.VERIFY_OK,
    ]
    assert _completion_events_for("patch", True, "budget-exhausted") == [
        JobEvent.PATCH_BUDGET_OUT,
    ]
    assert _completion_events_for("patch", True, "gave-up") == [
        JobEvent.PATCH_GAVE_UP,
    ]
    assert _completion_events_for("patch", False, "anything") == [
        JobEvent.PATCH_GAVE_UP,
    ]
