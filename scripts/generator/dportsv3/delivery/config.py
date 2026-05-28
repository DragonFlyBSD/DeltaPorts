"""Load + resolve ``config/delivery.toml`` for Step 11d.

Schema (per the plan §11d):

    [provider]
    type = "github"        # "github" | "gitlab" | "gitea" | "local-patch"
    repo = "DragonFlyBSD/DeltaPorts"
    clone_dir = "/srv/dports-clone"   # required for non-local-patch
    base_branch = "master"
    draft = true
    labels = ["agentic-fix", "needs-review"]
    branch_template = "agentic/{origin_safe}-{bundle_short}"
    committer_name = "Fred [bot]"           # commit author/committer identity
    committer_email = "github@dragonflybsd.org"

    [target."@2026Q2"]     # optional per-target override section
    base_branch = "2026Q2"
    repo = "DragonFlyBSD/DeltaPorts-2026Q2"

The TOP-LEVEL ``[provider]`` block is the default. Per-target
sections override individual fields for a specific target value;
unspecified fields fall back to the top-level.

Token resolution (highest precedence first):
- ``$DPORTSV3_DELIVERY_TOKEN`` env var.
- ``$DPORTSV3_CONFIG_DIR/delivery.token`` file (must be 0400 or
  caller-readable only — we don't enforce the mode but document
  the expectation).
- None — only valid when ``provider.type == "local-patch"``.

Tokens are the ONLY env-var input — they're secrets and don't
belong in a committable file. Everything else (clone path,
outbox) lives in this TOML.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import DeliveryConfigError


__all__ = [
    "DeliveryConfig",
    "load_delivery_config",
]


_KNOWN_PROVIDERS = frozenset({"github", "gitlab", "gitea", "local-patch"})
_DEFAULT_BRANCH_TEMPLATE = "agentic/{origin_safe}-{bundle_short}"
_DEFAULT_COMMITTER_NAME = "Fred [bot]"
_DEFAULT_COMMITTER_EMAIL = "github@dragonflybsd.org"


@dataclass(frozen=True)
class DeliveryConfig:
    """Resolved per-target delivery configuration.

    ``token`` is the resolved secret (None for ``local-patch``).
    ``clone_dir`` is the operator's local DeltaPorts checkout —
    required for network providers, ignored for ``local-patch``.
    ``outbox`` is the local-patch destination directory —
    required for ``local-patch``, None otherwise.
    """
    provider_type: str
    repo: str | None
    base_branch: str
    draft: bool
    labels: tuple[str, ...]
    branch_template: str
    token: str | None
    clone_dir: str | None
    outbox: str | None
    committer_name: str = _DEFAULT_COMMITTER_NAME
    committer_email: str = _DEFAULT_COMMITTER_EMAIL
    extras: dict[str, object] = field(default_factory=dict)


def load_delivery_config(
    config_path: Path,
    *,
    target: str | None = None,
    env: dict[str, str] | None = None,
) -> DeliveryConfig:
    """Parse ``delivery.toml`` and resolve per-target overrides.

    Raises ``DeliveryConfigError`` on missing required fields,
    unknown provider types, or unreadable token files. Caller
    distinguishes by the message string; the exception type is
    intentionally flat for v1.
    """
    env = env if env is not None else dict(os.environ)
    if not config_path.is_file():
        raise DeliveryConfigError(
            f"delivery.toml not found at {config_path!s}"
        )
    try:
        raw = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise DeliveryConfigError(
            f"delivery.toml is not valid TOML: {exc}"
        ) from exc

    provider_block = raw.get("provider")
    if not isinstance(provider_block, dict):
        raise DeliveryConfigError(
            "delivery.toml: required [provider] block missing"
        )

    # Resolve per-target overrides. The target lookup is a nested
    # `[target."<name>"]` section. tomllib turns those into the
    # nested dict structure raw["target"][target].
    target_overrides: dict = {}
    if target:
        target_section = raw.get("target", {})
        if isinstance(target_section, dict):
            specific = target_section.get(target)
            if isinstance(specific, dict):
                target_overrides = specific

    def field_value(key: str, default=None):
        if key in target_overrides:
            return target_overrides[key]
        return provider_block.get(key, default)

    provider_type = field_value("type")
    if not provider_type or not isinstance(provider_type, str):
        raise DeliveryConfigError(
            "delivery.toml: required field provider.type missing "
            "(one of 'github', 'gitlab', 'gitea', 'local-patch')"
        )
    if provider_type not in _KNOWN_PROVIDERS:
        raise DeliveryConfigError(
            f"delivery.toml: unknown provider type {provider_type!r} "
            f"(known: {sorted(_KNOWN_PROVIDERS)!r})"
        )

    repo = field_value("repo")
    if provider_type != "local-patch" and not repo:
        raise DeliveryConfigError(
            f"delivery.toml: provider.repo is required for "
            f"type={provider_type!r}"
        )

    base_branch = field_value("base_branch") or "master"
    draft = bool(field_value("draft", True))
    labels_val = field_value("labels", [])
    if not isinstance(labels_val, list):
        raise DeliveryConfigError(
            "delivery.toml: provider.labels must be a list of strings"
        )
    labels = tuple(str(x) for x in labels_val)
    branch_template = field_value("branch_template", _DEFAULT_BRANCH_TEMPLATE)
    committer_name = str(
        field_value("committer_name", _DEFAULT_COMMITTER_NAME)
        or _DEFAULT_COMMITTER_NAME
    )
    committer_email = str(
        field_value("committer_email", _DEFAULT_COMMITTER_EMAIL)
        or _DEFAULT_COMMITTER_EMAIL
    )

    # Token: env var first, then file fallback. Local-patch never
    # needs one.
    token: str | None = None
    if provider_type != "local-patch":
        token = _resolve_token(env)
        if not token:
            raise DeliveryConfigError(
                f"delivery.toml: provider type {provider_type!r} "
                f"requires a token. Set $DPORTSV3_DELIVERY_TOKEN or "
                f"place it at $DPORTSV3_CONFIG_DIR/delivery.token."
            )

    clone_dir_val = field_value("clone_dir")
    if provider_type != "local-patch":
        if not clone_dir_val or not isinstance(clone_dir_val, str):
            raise DeliveryConfigError(
                f"delivery.toml: provider.clone_dir is required "
                f"for type={provider_type!r} (the local DeltaPorts "
                f"checkout the tracker pushes from)"
            )
        clone_dir: str | None = clone_dir_val
    else:
        clone_dir = None

    outbox_val = field_value("outbox")
    if provider_type == "local-patch":
        if not outbox_val or not isinstance(outbox_val, str):
            raise DeliveryConfigError(
                "delivery.toml: provider.outbox is required for "
                "type='local-patch' (directory where patches get "
                "written)"
            )
        outbox: str | None = outbox_val
    else:
        outbox = None

    # Preserve any extra top-level fields so providers can read
    # implementation-specific knobs (e.g. gitea host) without
    # extending this dataclass for every variant.
    _known = {"type", "repo", "base_branch", "draft", "labels",
              "branch_template", "clone_dir", "outbox",
              "committer_name", "committer_email"}
    extras = {
        k: v for k, v in provider_block.items() if k not in _known
    }
    for k, v in target_overrides.items():
        if k not in _known:
            extras[k] = v

    return DeliveryConfig(
        provider_type=str(provider_type),
        repo=str(repo) if repo else None,
        base_branch=str(base_branch),
        draft=draft,
        labels=labels,
        branch_template=str(branch_template),
        token=token,
        clone_dir=clone_dir,
        outbox=outbox,
        committer_name=committer_name,
        committer_email=committer_email,
        extras=extras,
    )


def _resolve_token(env: dict[str, str]) -> str | None:
    """Token from env var, then from file. Search order mirrors
    ``orchestrator.resolve_config``'s tier-3 fallback so an operator
    who drops ``delivery.toml`` + ``delivery.token`` into the repo's
    ``config/`` directory (next to ``agentic-policy.json``) doesn't
    also have to export ``$DPORTSV3_CONFIG_DIR`` just for the token
    lookup. The prior shape gated the file lookup entirely on
    ``$DPORTSV3_CONFIG_DIR`` and silently treated "env var unset"
    as "no token", producing ``DeliveryConfigError: requires a
    token`` even when ``config/delivery.token`` was sitting right
    next to the TOML the loader had just successfully read.

    Search order:
      1. ``$DPORTSV3_DELIVERY_TOKEN`` env var.
      2. ``$DPORTSV3_CONFIG_DIR/delivery.token`` when the env var
         is set.
      3. ``<repo-root>/config/delivery.token`` — repo-anchored
         default, same root computation as the TOML fallback.
    """
    direct = env.get("DPORTSV3_DELIVERY_TOKEN", "").strip()
    if direct:
        return direct
    candidates: list[Path] = []
    config_dir = env.get("DPORTSV3_CONFIG_DIR", "").strip()
    if config_dir:
        candidates.append(Path(config_dir) / "delivery.token")
    # Repo-anchored fallback. This file lives at scripts/generator/
    # dportsv3/delivery/config.py — parents[4] is the repo root.
    candidates.append(
        Path(__file__).resolve().parents[4] / "config" / "delivery.token"
    )
    for token_file in candidates:
        if not token_file.is_file():
            continue
        try:
            value = token_file.read_text().strip()
        except OSError as exc:
            raise DeliveryConfigError(
                f"delivery.token at {token_file!s} is unreadable: {exc}"
            ) from exc
        if value:
            return value
    return None
