"""
FreeBSD to DragonFly transformation functions.

Handles the automatic transformations needed when converting
FreeBSD ports to DragonFly BSD, such as:
- User/group ID mappings
- Path adjustments
- Platform-specific conditionals
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from dports.utils import get_logger


# User ID mappings: FreeBSD UID -> DragonFly UID
EXTRA_UIDS = {
    # Add mappings as needed
    # "freebsd_user": "dragonfly_user",
}

# Group ID mappings: FreeBSD GID -> DragonFly GID
EXTRA_GIDS = {
    # Add mappings as needed
    # "freebsd_group": "dragonfly_group",
}

# Directories to exclude from transformation
EXCLUDE_DIRS = {
    ".git",
    ".svn",
    "__pycache__",
    "work",
    "distfiles",
    "packages",
}

# File patterns to transform
TRANSFORM_PATTERNS = [
    "Makefile",
    "Makefile.*",
    "*.mk",
    "pkg-plist",
    "pkg-descr",
    "pkg-message",
]


def needs_transformation(path: Path) -> bool:
    """
    Check if a file or directory needs transformation.
    
    Args:
        path: Path to check
        
    Returns:
        True if transformation is needed
    """
    if path.is_dir():
        # Check if any files in directory need transformation
        for pattern in TRANSFORM_PATTERNS:
            if list(path.glob(pattern)):
                return True
        return False
    
    # Check file
    for pattern in TRANSFORM_PATTERNS:
        if path.match(pattern):
            return True
    return False


def transform_file(path: Path) -> bool:
    """
    Transform a single file from FreeBSD to DragonFly format.
    
    Args:
        path: Path to file
        
    Returns:
        True if file was modified
    """
    log = get_logger(__name__)
    
    if not path.exists() or not path.is_file():
        return False
    
    try:
        content = path.read_text()
    except UnicodeDecodeError:
        log.debug(f"Skipping binary file: {path}")
        return False
    
    original = content
    
    # Apply transformations
    content = transform_uids(content)
    content = transform_gids(content)
    content = transform_platform_conditionals(content)
    content = transform_paths(content)
    
    if content != original:
        path.write_text(content)
        log.debug(f"Transformed: {path}")
        return True
    
    return False


def transform_directory(path: Path) -> int:
    """
    Transform all applicable files in a directory.
    
    Args:
        path: Directory path
        
    Returns:
        Number of files transformed
    """
    log = get_logger(__name__)
    count = 0
    
    for item in path.rglob("*"):
        if item.is_dir():
            if item.name in EXCLUDE_DIRS:
                continue
        elif item.is_file():
            if needs_transformation(item):
                if transform_file(item):
                    count += 1
    
    if count > 0:
        log.info(f"Transformed {count} files in {path}")
    
    return count


def transform_uids(content: str) -> str:
    """
    Transform FreeBSD UIDs to DragonFly UIDs.
    
    Args:
        content: File content
        
    Returns:
        Transformed content
    """
    for fbsd_uid, dfly_uid in EXTRA_UIDS.items():
        # Transform USERS= assignments
        content = re.sub(
            rf'\bUSERS\s*[+=]\s*{re.escape(fbsd_uid)}\b',
            f'USERS+={dfly_uid}',
            content
        )
        # Transform RUN_AS_USER
        content = re.sub(
            rf'\bRUN_AS_USER\s*[?:]?=\s*{re.escape(fbsd_uid)}\b',
            f'RUN_AS_USER?={dfly_uid}',
            content
        )
    
    return content


def transform_gids(content: str) -> str:
    """
    Transform FreeBSD GIDs to DragonFly GIDs.
    
    Args:
        content: File content
        
    Returns:
        Transformed content
    """
    for fbsd_gid, dfly_gid in EXTRA_GIDS.items():
        # Transform GROUPS= assignments
        content = re.sub(
            rf'\bGROUPS\s*[+=]\s*{re.escape(fbsd_gid)}\b',
            f'GROUPS+={dfly_gid}',
            content
        )
    
    return content


def transform_platform_conditionals(content: str) -> str:
    """
    Transform platform-specific conditionals.
    
    Handles patterns like:
    - .if ${OPSYS} == FreeBSD
    - .ifdef FREEBSD
    
    Args:
        content: File content
        
    Returns:
        Transformed content
    """
    # This is a placeholder - actual transformations depend on
    # specific patterns found in the ports tree
    
    # Example: Add DragonFly to FreeBSD conditionals
    # .if ${OPSYS} == FreeBSD -> .if ${OPSYS} == FreeBSD || ${OPSYS} == DragonFly
    
    return content


def transform_paths(content: str) -> str:
    """
    Transform FreeBSD-specific paths to DragonFly equivalents.
    
    Args:
        content: File content
        
    Returns:
        Transformed content
    """
    # Path transformations
    replacements = {
        # Example: "/usr/local/etc/rc.d" might need adjustment
        # Add actual path mappings as needed
    }
    
    for old_path, new_path in replacements.items():
        content = content.replace(old_path, new_path)
    
    return content
