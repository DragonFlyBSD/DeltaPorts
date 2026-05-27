"""Step 11d-1: schema + ReviewProvider Protocol + LocalPatchProvider
+ delivery.toml loader + bundle_review_requests queries.

These tests exercise the foundation; the higher-level Accept
integration is 11d-2 and lives in its own test file.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.db.schema import init_db
from dportsv3.delivery import (
    DeliveryConflictError,
    DeliveryConfigError,
    DeliveryError,
    ReviewProvider,
    ReviewRequestResult,
)
from dportsv3.delivery.config import DeliveryConfig, load_delivery_config
from dportsv3.delivery.local_patch import LocalPatchProvider
from dportsv3.tracker import agentic_queries as q


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
# Schema (11d-1.1)
# =====================================================================


def test_schema_migration_applies_cleanly(tmp_path):
    """Fresh DB initialized via init_db creates bundle_review_requests
    + its indexes. init_db is idempotent — second call must not error."""
    db = sqlite3.connect(str(tmp_path / "state.db"))
    init_db(db)
    init_db(db)  # idempotency

    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "bundle_review_requests" in tables

    indexes = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='bundle_review_requests'"
    ).fetchall()}
    assert "idx_brr_bundle" in indexes
    assert "uq_brr_open_signature" in indexes
    db.close()


def test_partial_unique_blocks_duplicate_open_delivery(tmp_path):
    """uq_brr_open_signature enforces "at most one open delivery
    per (provider, error_signature)". Two open rows with the same
    pair must raise sqlite3.IntegrityError."""
    db = sqlite3.connect(str(tmp_path / "state.db"))
    init_db(db)
    now = _now()
    db.execute(
        """INSERT INTO bundle_review_requests
           (bundle_id, provider, status, created_at, error_signature)
           VALUES (?, ?, 'created', ?, ?)""",
        ("b1", "github", now, "sig123"),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO bundle_review_requests
               (bundle_id, provider, status, created_at, error_signature)
               VALUES (?, ?, 'created', ?, ?)""",
            ("b2", "github", now, "sig123"),
        )
    db.close()


def test_partial_unique_allows_reopen_after_terminal(tmp_path):
    """Terminal statuses (closed/merged/create_failed) drop OUT of
    the partial-unique constraint, so a new open delivery for the
    same signature can land after the previous one is closed."""
    db = sqlite3.connect(str(tmp_path / "state.db"))
    init_db(db)
    now = _now()
    db.execute(
        """INSERT INTO bundle_review_requests
           (bundle_id, provider, status, created_at, error_signature)
           VALUES (?, ?, 'closed', ?, ?)""",
        ("b1", "github", now, "sig999"),
    )
    db.commit()
    # New 'created' row with the same signature is allowed.
    db.execute(
        """INSERT INTO bundle_review_requests
           (bundle_id, provider, status, created_at, error_signature)
           VALUES (?, ?, 'created', ?, ?)""",
        ("b2", "github", now, "sig999"),
    )
    db.commit()
    db.close()


# =====================================================================
# Protocol + dataclasses (11d-1.2)
# =====================================================================


def test_local_patch_satisfies_review_provider_protocol(tmp_path):
    """Structural check that LocalPatchProvider matches the
    ReviewProvider Protocol shape."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    p = LocalPatchProvider(outbox=outbox)

    def take_provider(prov: ReviewProvider) -> str:
        return prov.name

    assert take_provider(p) == "local-patch"


def test_review_request_result_is_immutable(tmp_path):
    """frozen=True on ReviewRequestResult — accidental mutation
    after construction must raise."""
    r = ReviewRequestResult(
        provider="local-patch",
        provider_pr_id="x.patch",
        url="/tmp/x.patch",
        branch="x",
        title="t",
        status="created",
    )
    with pytest.raises(Exception):
        r.url = "/somewhere/else"  # type: ignore[misc]


# =====================================================================
# delivery.toml loader (11d-1.3)
# =====================================================================


def _write_toml(path: Path, content: str) -> None:
    path.write_text(content)


def test_load_local_patch_no_token_needed(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    outbox = tmp_path / "outbox"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "local-patch"\n'
        f'outbox = "{outbox}"\n'
    ))
    cfg = load_delivery_config(cfg_path, env={})
    assert cfg.provider_type == "local-patch"
    assert cfg.token is None
    assert cfg.outbox == str(outbox)
    assert cfg.repo is None  # local-patch doesn't need a repo


def test_load_local_patch_missing_outbox_rejects(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, '[provider]\ntype = "local-patch"\n')
    with pytest.raises(DeliveryConfigError, match="provider.outbox"):
        load_delivery_config(cfg_path, env={})


def test_load_github_requires_token(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "DragonFlyBSD/DeltaPorts"\n'
        f'clone_dir = "{tmp_path}"\n'
    ))
    with pytest.raises(DeliveryConfigError, match="token"):
        load_delivery_config(cfg_path, env={})


def test_load_github_requires_clone_dir(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "DragonFlyBSD/DeltaPorts"\n'
    ))
    with pytest.raises(DeliveryConfigError, match="clone_dir"):
        load_delivery_config(cfg_path, env={
            "DPORTSV3_DELIVERY_TOKEN": "tok",
        })


def test_load_github_token_from_env(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "DragonFlyBSD/DeltaPorts"\n'
        f'clone_dir = "{tmp_path}"\n'
        'labels = ["agentic-fix"]\n'
    ))
    cfg = load_delivery_config(cfg_path, env={
        "DPORTSV3_DELIVERY_TOKEN": "ghp_token123",
    })
    assert cfg.provider_type == "github"
    assert cfg.token == "ghp_token123"
    assert cfg.repo == "DragonFlyBSD/DeltaPorts"
    assert cfg.clone_dir == str(tmp_path)
    assert cfg.labels == ("agentic-fix",)


def test_load_token_from_file_when_env_missing(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "x/y"\n'
        f'clone_dir = "{tmp_path}"\n'
    ))
    token_file = tmp_path / "delivery.token"
    token_file.write_text("ghp_from_file  \n")
    cfg = load_delivery_config(cfg_path, env={
        "DPORTSV3_CONFIG_DIR": str(tmp_path),
    })
    assert cfg.token == "ghp_from_file"


def test_token_repo_anchored_fallback(tmp_path, monkeypatch):
    """Mirror of the TOML repo-anchored fallback: when
    $DPORTSV3_CONFIG_DIR isn't set, _resolve_token should still
    find <repo-root>/config/delivery.token. Operators who drop
    both files into the repo's config/ directory (alongside
    agentic-policy.json) shouldn't have to set an env var just
    for the token lookup."""
    from dportsv3.delivery import config as cfg_mod
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "x/y"\n'
        f'clone_dir = "{tmp_path}"\n'
    ))
    # Stage a fake delivery.token in a fake "repo root" and point
    # _resolve_token's parents[4] computation at it.
    fake_repo = tmp_path / "fake-repo"
    (fake_repo / "config").mkdir(parents=True)
    (fake_repo / "config" / "delivery.token").write_text("ghp_repo_fallback")
    # config.py lives 4 parents under the repo root in production.
    # Recreate the same shape so parents[4] resolves.
    fake_config_py = (
        fake_repo / "scripts" / "generator" / "dportsv3"
        / "delivery" / "config.py"
    )
    fake_config_py.parent.mkdir(parents=True)
    fake_config_py.write_text("# stand-in")
    monkeypatch.setattr(cfg_mod, "__file__", str(fake_config_py))

    cfg = load_delivery_config(cfg_path, env={})  # no env vars
    assert cfg.token == "ghp_repo_fallback"


def test_env_token_takes_precedence_over_file(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "x/y"\n'
        f'clone_dir = "{tmp_path}"\n'
    ))
    (tmp_path / "delivery.token").write_text("ghp_from_file")
    cfg = load_delivery_config(cfg_path, env={
        "DPORTSV3_DELIVERY_TOKEN": "ghp_from_env",
        "DPORTSV3_CONFIG_DIR": str(tmp_path),
    })
    assert cfg.token == "ghp_from_env"


def test_unknown_provider_rejects(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, '[provider]\ntype = "bitbucket"\n')
    with pytest.raises(DeliveryConfigError, match="unknown provider"):
        load_delivery_config(cfg_path, env={})


def test_missing_provider_block_rejects(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, "# nothing here\n")
    with pytest.raises(DeliveryConfigError, match=r"\[provider\]"):
        load_delivery_config(cfg_path, env={})


def test_target_overrides_apply(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "DragonFlyBSD/DeltaPorts"\n'
        f'clone_dir = "{tmp_path}"\n'
        'base_branch = "master"\n'
        '\n'
        '[target."@2026Q2"]\n'
        'base_branch = "2026Q2"\n'
        'repo = "DragonFlyBSD/DeltaPorts-2026Q2"\n'
    ))
    cfg = load_delivery_config(
        cfg_path,
        target="@2026Q2",
        env={"DPORTSV3_DELIVERY_TOKEN": "tok"},
    )
    assert cfg.base_branch == "2026Q2"
    assert cfg.repo == "DragonFlyBSD/DeltaPorts-2026Q2"


def test_default_target_falls_back_when_target_section_absent(tmp_path):
    cfg_path = tmp_path / "delivery.toml"
    _write_toml(cfg_path, (
        '[provider]\n'
        'type = "github"\n'
        'repo = "x/y"\n'
        f'clone_dir = "{tmp_path}"\n'
        'base_branch = "master"\n'
    ))
    cfg = load_delivery_config(
        cfg_path, target="@unknown-target",
        env={"DPORTSV3_DELIVERY_TOKEN": "tok"},
    )
    assert cfg.base_branch == "master"


def test_missing_toml_file_rejects(tmp_path):
    with pytest.raises(DeliveryConfigError, match="not found"):
        load_delivery_config(tmp_path / "missing.toml", env={})


# =====================================================================
# LocalPatchProvider (11d-1.4)
# =====================================================================


_SAMPLE_DIFF = (
    "--- a/x\n"
    "+++ b/x\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _sample_args(branch: str = "agentic/devel-foo-20260527"):
    import hashlib
    sha = hashlib.sha256(_SAMPLE_DIFF.encode()).hexdigest()
    return {
        "clone_dir": Path("/unused"),
        "branch_name": branch,
        "base_branch": "master",
        "title": "devel/foo: fix dsynth build",
        "body": "Verified by verify-fix at 2026-05-27.\n",
        "labels": ["agentic-fix"],
        "diff_text": _SAMPLE_DIFF,
        "diff_sha256": sha,
        "draft": True,
    }


def test_local_patch_happy_path_writes_patch_and_metadata(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    provider = LocalPatchProvider(outbox=outbox)
    result = provider.create_review_request(**_sample_args())

    assert result.status == "created"
    assert result.provider == "local-patch"
    assert result.branch == "agentic/devel-foo-20260527"

    patch = outbox / "agentic/devel-foo-20260527.patch"
    meta = outbox / "agentic/devel-foo-20260527.metadata.json"
    assert patch.is_file()
    assert meta.is_file()
    assert patch.read_text() == _SAMPLE_DIFF
    metadata = json.loads(meta.read_text())
    assert metadata["branch"] == "agentic/devel-foo-20260527"
    assert metadata["base_branch"] == "master"
    assert metadata["labels"] == ["agentic-fix"]


def test_local_patch_idempotent_same_content(tmp_path):
    """Re-write with same diff SHA → status='updated', no error."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    provider = LocalPatchProvider(outbox=outbox)
    args = _sample_args()
    r1 = provider.create_review_request(**args)
    r2 = provider.create_review_request(**args)
    assert r1.status == "created"
    assert r2.status == "updated"


def test_local_patch_different_content_conflicts(tmp_path):
    """Same branch_name with different content → DeliveryConflictError.
    Overwriting silently would clobber operator-intermediate state."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    provider = LocalPatchProvider(outbox=outbox)
    args1 = _sample_args()
    provider.create_review_request(**args1)

    args2 = dict(args1)
    args2["diff_text"] = "different content\n"
    args2["diff_sha256"] = "0" * 64
    with pytest.raises(DeliveryConflictError, match="different content"):
        provider.create_review_request(**args2)


def test_local_patch_missing_outbox_errors(tmp_path):
    provider = LocalPatchProvider(outbox=tmp_path / "doesnt-exist")
    with pytest.raises(DeliveryConfigError, match="doesn't"):
        provider.create_review_request(**_sample_args())


def test_local_patch_refuses_unsafe_branch_name(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    provider = LocalPatchProvider(outbox=outbox)
    for bad in ("../escape", "/abs/path", "foo/../escape"):
        args = _sample_args(branch=bad)
        with pytest.raises(DeliveryError, match="unsafe"):
            provider.create_review_request(**args)


def test_local_patch_creates_nested_branch_subdirs(tmp_path):
    """Branch names with `/` (e.g. "agentic/devel-foo-xyz") nest in
    the outbox naturally — mkdir parents=True handles it."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    provider = LocalPatchProvider(outbox=outbox)
    args = _sample_args(branch="agentic/devel-foo-xyz")
    result = provider.create_review_request(**args)
    assert result.status == "created"
    assert (outbox / "agentic" / "devel-foo-xyz.patch").is_file()


# =====================================================================
# Queries (11d-1.5)
# =====================================================================


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def test_insert_and_latest_review_request_round_trip(db):
    rid = q.insert_review_request(
        db, bundle_id="b1", provider="local-patch",
        provider_pr_id="x.patch", url="/tmp/x.patch",
        branch="x", title="fix",
        operator="alice", error_signature="sig-1",
    )
    db.commit()
    assert rid > 0
    latest = q.latest_review_request_for_bundle(db, "b1")
    assert latest is not None
    assert latest["bundle_id"] == "b1"
    assert latest["url"] == "/tmp/x.patch"
    assert latest["status"] == "created"


def test_latest_returns_most_recent(db):
    q.insert_review_request(
        db, bundle_id="b1", provider="local-patch",
        provider_pr_id="x1.patch", branch="x1",
        error_signature="sig-1",
    )
    rid2 = q.insert_review_request(
        db, bundle_id="b1", provider="github",
        provider_pr_id="42", url="https://github.com/x/y/pull/42",
        branch="x2", error_signature="sig-1",
    )
    db.commit()
    latest = q.latest_review_request_for_bundle(db, "b1")
    assert latest["id"] == rid2
    assert latest["provider"] == "github"


def test_find_open_review_request_matches(db):
    rid = q.insert_review_request(
        db, bundle_id="b1", provider="github",
        error_signature="sig-A",
    )
    db.commit()
    found = q.find_open_review_request(
        db, provider="github", error_signature="sig-A",
    )
    assert found is not None
    assert found["id"] == rid


def test_find_open_skips_terminal_statuses(db):
    """find_open ignores rows in closed/merged/create_failed —
    those are the same statuses the partial-unique drops."""
    q.insert_review_request(
        db, bundle_id="b1", provider="github",
        status="closed", error_signature="sig-A",
    )
    q.insert_review_request(
        db, bundle_id="b2", provider="github",
        status="create_failed", error_signature="sig-A",
    )
    db.commit()
    assert q.find_open_review_request(
        db, provider="github", error_signature="sig-A",
    ) is None


def test_update_status_attaches_provider_data(db):
    rid = q.insert_review_request(
        db, bundle_id="b1", provider="github",
        error_signature="sig-A",
    )
    db.commit()
    updated = q.update_review_request_status(
        db, request_id=rid, status="created",
        provider_pr_id="42", url="https://github.com/x/y/pull/42",
    )
    db.commit()
    assert updated is True
    row = q.latest_review_request_for_bundle(db, "b1")
    assert row["provider_pr_id"] == "42"
    assert row["url"] == "https://github.com/x/y/pull/42"
    assert row["last_synced_at"] is not None


def test_update_status_returns_false_on_unknown_id(db):
    assert q.update_review_request_status(
        db, request_id=999999, status="closed",
    ) is False
