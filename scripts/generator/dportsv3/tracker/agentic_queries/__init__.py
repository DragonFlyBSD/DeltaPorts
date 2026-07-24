"""SQL helpers for the tracker's agentic-read endpoints.

Package facade: functions live in focused submodules; this re-exports
them so ``from dportsv3.tracker.agentic_queries import <fn>`` is unchanged.
"""

from dportsv3.tracker.agentic_queries._util import (
    _row_dict,
    _maybe,
    _decode_extra_json,
)
from dportsv3.tracker.agentic_queries.overview import (
    agentic_status,
    runner_status,
    distinct_targets,
)
from dportsv3.tracker.agentic_queries.activity import (
    recent_activity_for_bundle,
    recent_activity,
    activity_for_job,
    events_since,
)
from dportsv3.tracker.agentic_queries.env import (
    get_active_env,
    set_active_env,
    env_health_statuses,
)
from dportsv3.tracker.agentic_queries.runs import (
    list_runs,
    get_run,
)
from dportsv3.tracker.agentic_queries.jobs import (
    list_jobs,
    get_job,
    list_jobs_for_bundle,
    active_job_for_port,
    token_usage_for_job,
    token_usage_for_port,
    job_events_for_job,
    port_attempt_summary,
)
from dportsv3.tracker.agentic_queries.bundles import (
    list_bundles,
    get_bundle,
    get_artifact_ref,
    list_port_bundles,
    bundles_for_run,
)
from dportsv3.tracker.agentic_queries.manual import (
    list_manual_requests,
    get_manual_request,
    discard_manual_request,
    upsert_user_context_text,
    list_user_context_history,
)
from dportsv3.tracker.agentic_queries.skip import (
    is_origin_skipped,
    set_origin_skip,
    clear_origin_skip,
)
from dportsv3.tracker.agentic_queries.review import (
    insert_review_request,
    latest_review_request_for_bundle,
    find_open_review_request,
    update_review_request_status,
)

__all__ = [
    "_row_dict",
    "_maybe",
    "_decode_extra_json",
    "agentic_status",
    "runner_status",
    "distinct_targets",
    "recent_activity_for_bundle",
    "recent_activity",
    "activity_for_job",
    "events_since",
    "get_active_env",
    "set_active_env",
    "env_health_statuses",
    "list_runs",
    "get_run",
    "list_jobs",
    "get_job",
    "list_jobs_for_bundle",
    "active_job_for_port",
    "token_usage_for_job",
    "token_usage_for_port",
    "job_events_for_job",
    "port_attempt_summary",
    "list_bundles",
    "get_bundle",
    "get_artifact_ref",
    "list_port_bundles",
    "bundles_for_run",
    "list_manual_requests",
    "get_manual_request",
    "discard_manual_request",
    "upsert_user_context_text",
    "list_user_context_history",
    "is_origin_skipped",
    "set_origin_skip",
    "clear_origin_skip",
    "insert_review_request",
    "latest_review_request_for_bundle",
    "find_open_review_request",
    "update_review_request_status",
]
