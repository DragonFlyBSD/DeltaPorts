"""Compose runtime policy constants."""

from __future__ import annotations

EXCLUDED_TOP_LEVEL = {
    ".git",
    "Mk",
    "Templates",
    "Tools",
    "Keywords",
    "distfiles",
}

PATCH_TIMEOUT_SECONDS = 30

SPECIAL_COMPONENTS = ("Mk", "Templates", "Tools", "Keywords", "treetop")

TREETOP_IDENTITY_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "GIDs": (
        "nogroup:",
        (
            "avenger:*:60149:",
            "cbsd:*:60150:",
        ),
    ),
    "UIDs": (
        "nobody:",
        (
            "avenger:*:60149:60149::0:0:Mail Avenger:/var/spool/avenger:/usr/sbin/nologin",
            "cbsd:*:60150:60150::0:0:Cbsd user:/nonexistent:/bin/sh",
        ),
    ),
}

MOVED_KEEP_AFTER_YEAR = 2012
UPDATING_ROLLING_WINDOW_DAYS = 10000
