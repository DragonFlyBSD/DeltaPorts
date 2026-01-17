"""
FreeBSD to DragonFly transformation functions.

Handles the automatic transformations needed when converting
FreeBSD ports to DragonFly BSD:
- Architecture: amd64 -> x86_64
- Remove libomp dependencies (not needed on DragonFly)
- Extra UID/GID entries for DragonFly-specific users/groups
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Tuple, Callable, Union

from dports.utils import get_logger


# =============================================================================
# Architecture Transformations (amd64 -> x86_64)
# =============================================================================

# Compiled regex patterns with their replacements
# Each tuple is (pattern, replacement) where replacement can be str or callable
ARCH_TRANSFORMS: List[Tuple[re.Pattern, Union[str, Callable]]] = [
    (re.compile(r'OPTIONS_DEFAULT_amd64'), 'OPTIONS_DEFAULT_x86_64'),
    (re.compile(r'OPTIONS_DEFINE_amd64'), 'OPTIONS_DEFINE_x86_64'),
    (re.compile(r'BROKEN_amd64'), 'BROKEN_x86_64'),
    (re.compile(r'_ON_amd64'), '_ON_x86_64'),
    (re.compile(r'_OFF_amd64'), '_OFF_x86_64'),
    (re.compile(r'CFLAGS_amd64'), 'CFLAGS_x86_64'),
    (re.compile(r'\{ARCH:Mamd64\}'), '{ARCH:Mx86_64}'),
    (re.compile(r'_amd64='), '_x86_64='),
    # Complex pattern for ${ARCH} checks - uses lambda for replacement
    (re.compile(r'(\$\{ARCH\}[^}]*)(amd64|"amd64")'),
     lambda m: m.group(1) + m.group(2).replace('amd64', 'x86_64')),
]

# =============================================================================
# OpenMP Removal (libomp not needed on DragonFly)
# =============================================================================

LIBOMP_PATTERNS: List[re.Pattern] = [
    re.compile(r'libomp\.so:devel/openmp\b\s*'),
    re.compile(r'libomp\.so\.0:devel/openmp\b\s*'),
]

# =============================================================================
# Detection Pattern
# =============================================================================

# Pattern to quickly check if a file might need transformation
LEGACY_PATTERN = re.compile(r'amd64|libomp')

# =============================================================================
# Extra UID/GID Entries for DragonFly
# =============================================================================

# These get inserted into the GIDs file after 'nogroup:' line
EXTRA_GIDS: List[str] = [
    "avenger:*:60149:",
    "cbsd:*:60150:",
]

# These get inserted into the UIDs file after 'nobody:' line
# Format: name:*:uid:gid:class:change:expire:gecos:home:shell
EXTRA_UIDS: List[str] = [
    "avenger:*:60149:60149::0:0:Mail Avenger:/var/spool/avenger:/usr/sbin/nologin",
    "cbsd:*:60150:60150::0:0:Cbsd user:/nonexistent:/bin/sh",
]

# =============================================================================
# Directories/Files to Exclude
# =============================================================================

EXCLUDE_DIRS = frozenset({
    '.git', '.svn', '__pycache__', 'work', 'distfiles', 'packages'
})

# =============================================================================
# Transform Functions
# =============================================================================

def needs_transform(directory: Path) -> List[str]:
    """
    Check which files in a directory need arch transformation.
    
    Scans Makefile* and *.common files for patterns that indicate
    FreeBSD-specific content (amd64, libomp).
    
    Args:
        directory: Port directory to scan
        
    Returns:
        List of filenames that need transformation
    """
    result = []
    for pattern in ['Makefile*', '*.common']:
        for f in directory.glob(pattern):
            if f.is_file():
                try:
                    content = f.read_text()
                    if LEGACY_PATTERN.search(content):
                        result.append(f.name)
                except (OSError, UnicodeDecodeError):
                    pass
    return sorted(set(result))


def transform_content(content: str) -> str:
    """
    Apply all transformations to file content.
    
    Transforms:
    - amd64 references to x86_64
    - Removes libomp dependencies
    
    Args:
        content: Original file content
        
    Returns:
        Transformed content
    """
    # Apply architecture transforms
    for pattern, repl in ARCH_TRANSFORMS:
        if callable(repl):
            content = pattern.sub(repl, content)
        else:
            content = pattern.sub(repl, content)
    
    # Remove libomp dependencies
    for pattern in LIBOMP_PATTERNS:
        content = pattern.sub('', content)
    
    return content


def transform_file(filepath: Path, preserve_mtime: bool = True) -> bool:
    """
    Transform a file in place.
    
    Reads the file, applies transformations, and writes back if changed.
    Optionally preserves the original modification time.
    
    Args:
        filepath: Path to file to transform
        preserve_mtime: If True, preserve original mtime after transform
        
    Returns:
        True if file was modified, False otherwise
    """
    log = get_logger(__name__)
    
    try:
        stat_info = filepath.stat() if preserve_mtime else None
        content = filepath.read_text()
        transformed = transform_content(content)
        
        if content != transformed:
            filepath.write_text(transformed)
            if stat_info:
                os.utime(filepath, (stat_info.st_atime, stat_info.st_mtime))
            log.debug(f"Transformed: {filepath}")
            return True
        return False
        
    except (OSError, UnicodeDecodeError) as e:
        log.debug(f"Could not transform {filepath}: {e}")
        return False


def transform_directory(directory: Path, files: List[str]) -> int:
    """
    Transform specified files in a directory.
    
    Args:
        directory: Directory containing files
        files: List of filenames to transform
        
    Returns:
        Number of files actually modified
    """
    count = 0
    for name in files:
        f = directory / name
        if f.exists() and transform_file(f):
            count += 1
    return count


# =============================================================================
# UID/GID File Transformation (for merge_treetop)
# =============================================================================

def transform_gids_file(content: str) -> str:
    """
    Transform GIDs file content by inserting DragonFly-specific groups.
    
    Inserts EXTRA_GIDS entries after the 'nogroup:' line.
    
    Args:
        content: Original GIDs file content
        
    Returns:
        Transformed content with extra groups
    """
    lines = content.splitlines()
    result = []
    
    for line in lines:
        result.append(line)
        if 'nogroup:' in line:
            result.extend(EXTRA_GIDS)
    
    return '\n'.join(result) + '\n'


def transform_uids_file(content: str) -> str:
    """
    Transform UIDs file content by inserting DragonFly-specific users.
    
    Inserts EXTRA_UIDS entries after the 'nobody:' line.
    
    Args:
        content: Original UIDs file content
        
    Returns:
        Transformed content with extra users
    """
    lines = content.splitlines()
    result = []
    
    for line in lines:
        result.append(line)
        if 'nobody:' in line:
            result.extend(EXTRA_UIDS)
    
    return '\n'.join(result) + '\n'


def transform_moved_file(content: str, cutoff_year: int = 2012) -> str:
    """
    Transform MOVED file by filtering out old entries.
    
    Keeps comment lines and entries from after the cutoff year.
    
    Args:
        content: Original MOVED file content
        cutoff_year: Keep entries after this year (default: 2012)
        
    Returns:
        Filtered content
    """
    lines = []
    
    for line in content.splitlines():
        if line.startswith('#'):
            lines.append(line)
        else:
            parts = line.split('|')
            if len(parts) >= 3:
                try:
                    year = int(parts[2].split('-')[0])
                    if year > cutoff_year:
                        lines.append(line)
                except (ValueError, IndexError):
                    # Keep lines we can't parse
                    lines.append(line)
            else:
                lines.append(line)
    
    return '\n'.join(lines) + '\n'


def transform_tools_file(content: str) -> str:
    """
    Transform Tools/ files by fixing perl shebang.
    
    FreeBSD uses /usr/bin/perl, DragonFly uses /usr/local/bin/perl.
    
    Args:
        content: Original file content
        
    Returns:
        Content with fixed shebang
    """
    return content.replace('#!/usr/bin/perl', '#!/usr/local/bin/perl')
