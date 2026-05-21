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
    """Helpers used by the lifecycle integration tests must be
    importable without firing up the LLM, the DB, or the queue.

    Phase 5 Step 4: _completion_events_for retired — event firing
    moved into each Step's StepOutcome. The wrapper helpers
    process_*_job + _finish_orchestrator_run live in their place.
    """
    from dportsv3.agent.runner import (
        _apply_transition,           # lifecycle.apply wrapper
        _finish_orchestrator_run,    # extract events + sibling fan-out
        _register_new_job,           # initial HOOK_ENQUEUED + jobs metadata
        claim_next_job_batch,        # filesystem queue claim
        parse_job_file,              # .job file → dict
        process_job,                 # the orchestration entrypoint
        process_triage_job,          # orchestrator wrapper for TriageStep
        process_patch_job,           # orchestrator wrapper for PatchAttemptStep
    )

    for fn in (_apply_transition, _finish_orchestrator_run,
               _register_new_job, claim_next_job_batch, parse_job_file,
               process_job, process_triage_job, process_patch_job):
        assert callable(fn)


def test_step_outcome_events_populated_by_steps():
    """The Phase 5 cutover moved event mapping inside the Step
    classes. Smoke-check that TriageStep + PatchAttemptStep import
    cleanly — Step-class-specific behavior is covered by
    test_patch_step.py and the e2e suite.
    """
    from dportsv3.agent.steps import TriageStep, PatchAttemptStep
    assert TriageStep().name == "triage"
    assert PatchAttemptStep().name == "patch"
