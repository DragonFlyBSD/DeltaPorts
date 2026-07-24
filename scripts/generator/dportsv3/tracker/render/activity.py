"""Group a job's activity_log rows into attempt blocks for the job-detail
timeline (Phase 6 redesign).

A patch job's activity is a flat firehose — every llm_turn, tool call, and
decision in one long list, spanning multiple retry attempts. The runner
brackets each attempt with ``attempt_start`` / ``attempt_end`` rows, so we can
fold the firehose into one collapsible block per attempt with its outcome and
cost in the header — the CI-step / agent-trace pattern ("Attempt 2 — rebuild
failed"). Rows before the first attempt (triage, decision) form a leading
setup group.

Pure and unit-tested; the terminal-job view renders these, the live/active
view keeps the flat stream.
"""

from __future__ import annotations

from typing import Any


def group_activity_by_attempt(
    activity: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fold activity rows into ordered attempt groups.

    ``activity`` may be in any order (the detail route passes newest-first);
    grouping is done chronologically by ``id``. Each returned group is::

        {kind, label, attempt, outcome, outcome_cls, tokens, n_tools,
         rows, open}

    - ``kind``   — "attempt" or "setup".
    - ``outcome``/``outcome_cls`` — from the attempt_end ``rebuild_ok``
      (passed→built, failed→failed); None when the attempt has no end.
    - ``tokens`` — sum of llm_turn total_tokens in the group.
    - ``open``   — the last group defaults open (the most recent / relevant);
      every header shows its outcome so the operator can open the others.
    """
    rows = sorted(activity, key=lambda a: (a.get("id") or 0))
    groups: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    def _new(kind: str, label: str, attempt: int | None = None) -> dict[str, Any]:
        g = {
            "kind": kind, "label": label, "attempt": attempt,
            "outcome": None, "outcome_cls": "total",
            "tokens": 0, "n_tools": 0, "rows": [], "open": False,
        }
        groups.append(g)
        return g

    for a in rows:
        stage = a.get("stage") or ""
        extra = a.get("extra") if isinstance(a.get("extra"), dict) else {}
        if stage == "attempt_start":
            n = extra.get("attempt")
            cur = _new("attempt", f"Attempt {n}" if n else "Attempt", n)
        elif cur is None:
            cur = _new("setup", "Triage / setup")

        cur["rows"].append(a)
        if stage == "llm_turn":
            cur["tokens"] += extra.get("total_tokens") or 0
        elif stage.startswith("tool:"):
            cur["n_tools"] += 1
        elif stage == "attempt_end":
            ok = extra.get("rebuild_ok")
            if ok is True:
                cur["outcome"], cur["outcome_cls"] = "rebuild passed", "built"
            elif ok is False:
                cur["outcome"], cur["outcome_cls"] = "rebuild failed", "failed"

    if groups:
        groups[-1]["open"] = True
    return groups
