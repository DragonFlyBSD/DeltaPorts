#!/usr/bin/env python3
"""
dports - DragonFly Ports generator tool

Merges FreeBSD Ports Collection with DeltaPorts overlays to produce DPorts.

Usage:
    dports <command> [options]

Commands:
    merge           Full merge of all ports (or specific ports)
    sync            Sync a single port to the potential tree
    prune           Remove obsolete ports from DPorts/DeltaPorts
    makefiles       Generate category Makefiles
    index           Generate INDEX file
    daemon          Run the background commit daemon
    bulk-list       Generate list for poudriere bulk builds
    stinkers        Find unbuilt ports with most dependents

Global Options:
    -c, --config PATH    Config file (default: /usr/local/etc/dports.conf)
    -v, --verbose        Verbose output
    -n, --dry-run        Show what would be done
    -q, --quiet          Minimal output
    -h, --help           Show help

Environment variables (override config file):
    DPORTS_FPORTS, DPORTS_MERGED, DPORTS_DPORTS, DPORTS_DELTA,
    DPORTS_POTENTIAL, DPORTS_INDEX, DPORTS_CONFIG
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple, Iterator


# =============================================================================
# Constants
# =============================================================================

VERSION = "2.0.0"
DEFAULT_CONFIG_PATH = "/usr/local/etc/dports.conf"
DEFAULT_LOG_DIR = os.path.expanduser("~/.dports/logs")

# Architecture transformations (FreeBSD amd64 -> DragonFly x86_64)
ARCH_TRANSFORMS = [
    (re.compile(r'OPTIONS_DEFAULT_amd64'), 'OPTIONS_DEFAULT_x86_64'),
    (re.compile(r'OPTIONS_DEFINE_amd64'), 'OPTIONS_DEFINE_x86_64'),
    (re.compile(r'BROKEN_amd64'), 'BROKEN_x86_64'),
    (re.compile(r'_ON_amd64'), '_ON_x86_64'),
    (re.compile(r'_OFF_amd64'), '_OFF_x86_64'),
    (re.compile(r'CFLAGS_amd64'), 'CFLAGS_x86_64'),
    (re.compile(r'\{ARCH:Mamd64\}'), '{ARCH:Mx86_64}'),
    (re.compile(r'_amd64='), '_x86_64='),
    # ARCH checks
    (re.compile(r'(\$\{ARCH\}[^}]*)(amd64|"amd64")'), 
     lambda m: m.group(1) + m.group(2).replace('amd64', 'x86_64')),
]

# Patterns to remove (libomp dependencies not needed on DragonFly)
LIBOMP_PATTERNS = [
    re.compile(r'libomp\.so:devel/openmp\b\s*'),
    re.compile(r'libomp\.so\.0:devel/openmp\b\s*'),
]

# Files that might need arch transformation
LEGACY_PATTERN = re.compile(r'amd64|libomp')

# Directories to exclude from port listings
EXCLUDE_DIRS = frozenset({'Templates', 'Tools', 'Mk', 'Keywords', '.git'})

# Language directories needing Makefile.inc
LANG_DIRS = [
    'arabic', 'chinese', 'french', 'german', 'hebrew', 'hungarian',
    'japanese', 'korean', 'polish', 'portuguese', 'russian',
    'ukrainian', 'vietnamese'
]

# Extra UID/GID entries for DragonFly
EXTRA_GIDS = ["avenger:*:60149:", "cbsd:*:60150:"]
EXTRA_UIDS = [
    "avenger:*:60149:60149::0:0:Mail Avenger:/var/spool/avenger:/usr/sbin/nologin",
    "cbsd:*:60150:60150::0:0:Cbsd user:/nonexistent:/bin/sh",
]


# =============================================================================
# Enums & Data Classes
# =============================================================================

class PortStatus(Enum):
    MASK = "MASK"    # Port excluded from DPorts
    PORT = "PORT"    # Port derived from FreeBSD
    DPORT = "DPORT"  # Port created from scratch
    LOCK = "LOCK"    # Locked - copy from DPorts as-is
    UNKNOWN = "UNKNOWN"


@dataclass
class Config:
    """Configuration for DPorts generation."""
    fports: Path
    merged: Path  
    dports: Path
    delta: Path
    potential: Optional[Path] = None
    index: Optional[Path] = None
    comqueue: Optional[Path] = None
    log_dir: Path = field(default_factory=lambda: Path(DEFAULT_LOG_DIR))
    verbose: bool = False
    dry_run: bool = False
    
    def validate(self) -> List[str]:
        errors = []
        for name in ['fports', 'merged', 'dports', 'delta']:
            path = getattr(self, name)
            if path and not path.exists():
                errors.append(f"{name} directory does not exist: {path}")
        return errors


@dataclass
class PortInfo:
    """Parsed STATUS file."""
    status: PortStatus
    last_attempt: str = ""
    last_success: str = ""
    comment: str = ""
    
    @classmethod
    def from_file(cls, path: Path) -> PortInfo:
        if not path.exists():
            return cls(status=PortStatus.UNKNOWN)
        try:
            lines = path.read_text().strip().split('\n')
            if not lines:
                return cls(status=PortStatus.UNKNOWN)
            
            status_str = lines[0].split()[0] if lines[0].split() else ""
            try:
                status = PortStatus(status_str)
            except ValueError:
                status = PortStatus.UNKNOWN
            
            info = cls(status=status)
            for line in lines[1:]:
                if line.startswith("Last attempt:"):
                    info.last_attempt = line.split(":", 1)[1].strip()
                elif line.startswith("Last success:"):
                    info.last_success = line.split(":", 1)[1].strip()
                elif line.startswith("#"):
                    info.comment = line[1:].strip()
            return info
        except Exception:
            return cls(status=PortStatus.UNKNOWN)


@dataclass
class MergeResult:
    port: str
    success: bool
    action: str  # merged, fast, skipped, masked, locked, dport, error
    message: str = ""
    patch_errors: List[str] = field(default_factory=list)


# =============================================================================
# Logging
# =============================================================================

class Logger:
    """Simple logger with file and console output."""
    
    def __init__(self, name: str, log_dir: Optional[Path] = None,
                 verbose: bool = False, quiet: bool = False):
        self.name = name
        self.verbose = verbose
        self.quiet = quiet
        self.log_file: Optional[Path] = None
        self._file = None
        
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_file = log_dir / f"{name}_{ts}.log"
            self._file = open(self.log_file, 'w')
    
    def close(self):
        if self._file:
            self._file.close()
    
    def _log(self, level: str, msg: str, console: bool = True):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {level}: {msg}"
        if self._file:
            self._file.write(line + '\n')
            self._file.flush()
        if console and not self.quiet:
            if level == "ERROR":
                print(f"ERROR: {msg}", file=sys.stderr)
            elif level == "WARN":
                print(f"WARN: {msg}", file=sys.stderr)
            elif level == "DEBUG":
                if self.verbose:
                    print(f"  {msg}")
            else:
                print(msg)
    
    def debug(self, msg: str): self._log("DEBUG", msg)
    def info(self, msg: str): self._log("INFO", msg)
    def warn(self, msg: str): self._log("WARN", msg)
    def error(self, msg: str): self._log("ERROR", msg)


# =============================================================================
# Configuration Loading
# =============================================================================

def load_config(config_path: Optional[str] = None) -> Config:
    """Load config from file with environment variable overrides."""
    path = config_path or os.environ.get('DPORTS_CONFIG', DEFAULT_CONFIG_PATH)
    
    # Parse simple KEY=VALUE format
    file_vals: Dict[str, str] = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                file_vals[k.strip().lower()] = v.strip()
    
    def get(name: str, default: str = "") -> str:
        env = os.environ.get(f'DPORTS_{name.upper()}')
        return env if env else file_vals.get(name.lower(), default)
    
    def get_path(name: str, default: str = "") -> Optional[Path]:
        v = get(name, default)
        return Path(v) if v else None
    
    return Config(
        fports=get_path('fports', '/usr/ports') or Path('/usr/ports'),
        merged=get_path('merged') or Path('.'),
        dports=get_path('dports') or Path('.'),
        delta=get_path('delta') or Path('.'),
        potential=get_path('potential'),
        index=get_path('index'),
        comqueue=get_path('comqueue'),
        log_dir=get_path('log_dir', DEFAULT_LOG_DIR) or Path(DEFAULT_LOG_DIR),
    )


# =============================================================================
# Transform Functions
# =============================================================================

def needs_transform(directory: Path) -> List[str]:
    """Return list of files in directory that need arch transformation."""
    result = []
    for pattern in ['Makefile*', '*.common']:
        for f in directory.glob(pattern):
            if f.is_file():
                try:
                    if LEGACY_PATTERN.search(f.read_text()):
                        result.append(f.name)
                except Exception:
                    pass
    return sorted(set(result))


def transform_content(content: str) -> str:
    """Apply arch transforms and remove libomp deps."""
    for pattern, repl in ARCH_TRANSFORMS:
        if callable(repl):
            content = pattern.sub(repl, content)
        else:
            content = pattern.sub(repl, content)
    for pattern in LIBOMP_PATTERNS:
        content = pattern.sub('', content)
    return content


def transform_file(filepath: Path, preserve_mtime: bool = True):
    """Transform file in place."""
    try:
        stat = filepath.stat() if preserve_mtime else None
        content = filepath.read_text()
        transformed = transform_content(content)
        if content != transformed:
            filepath.write_text(transformed)
            if stat:
                os.utime(filepath, (stat.st_atime, stat.st_mtime))
    except Exception:
        pass


def transform_dir(directory: Path, files: List[str]):
    """Transform specified files in directory."""
    for name in files:
        f = directory / name
        if f.exists():
            transform_file(f)


# =============================================================================
# Patch Operations
# =============================================================================

def apply_patch(workdir: Path, patch_file: Path) -> Tuple[bool, str]:
    """Apply patch, return (success, output)."""
    try:
        r = subprocess.run(
            ['patch', '--force', '--quiet', '-i', str(patch_file)],
            cwd=workdir, capture_output=True, text=True
        )
        output = (r.stdout + r.stderr).strip()
        return (r.returncode == 0 and not output), output
    except Exception as e:
        return False, str(e)


def cleanup_orig_files(directory: Path):
    """Remove .orig files, preserving timestamps on patched files."""
    for orig in directory.rglob('*.orig'):
        patched = orig.with_suffix('')
        if patched.exists():
            os.utime(patched, (orig.stat().st_atime, orig.stat().st_mtime))
        orig.unlink()


# =============================================================================
# Port Operations
# =============================================================================

def list_ports(directory: Path) -> List[str]:
    """List category/port paths in a ports tree."""
    ports = []
    if not directory.exists():
        return ports
    for cat in sorted(directory.iterdir()):
        if not cat.is_dir() or cat.name in EXCLUDE_DIRS or cat.name.startswith('.'):
            continue
        for port in sorted(cat.iterdir()):
            if port.is_dir() and not port.name.startswith('.'):
                ports.append(f"{cat.name}/{port.name}")
    return ports


def parse_index(index_path: Path) -> Dict[str, str]:
    """Parse INDEX file, return {origin: version}."""
    result = {}
    if not index_path.exists():
        return result
    for line in index_path.read_text().splitlines():
        fields = line.split('|')
        if len(fields) >= 2:
            pkg = fields[0]
            port_path = fields[1].rstrip('/')
            parts = port_path.split('/')
            if len(parts) >= 2:
                origin = f"{parts[-2]}/{parts[-1]}"
                version = pkg.rsplit('-', 1)[-1] if '-' in pkg else ""
                result[origin] = version
    return result


def cpdup(src: Path, dst: Path):
    """Copy directory tree preserving timestamps."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    os.utime(dst, (src.stat().st_atime, src.stat().st_mtime))


# =============================================================================
# Merge Logic
# =============================================================================

class Merger:
    """Handles port merging operations."""
    
    def __init__(self, config: Config, log: Logger):
        self.cfg = config
        self.log = log
        self.workdir: Optional[Path] = None
    
    def __enter__(self):
        self.workdir = Path(tempfile.mkdtemp(prefix='dports_'))
        return self
    
    def __exit__(self, *args):
        if self.workdir and self.workdir.exists():
            shutil.rmtree(self.workdir)
    
    def merge_port(self, origin: str, version: str = "") -> MergeResult:
        """Merge a single port."""
        delta_port = self.cfg.delta / "ports" / origin
        merged_port = self.cfg.merged / origin
        fports_port = self.cfg.fports / origin
        
        # No delta entry - fast copy
        if not delta_port.exists():
            return self._fast_merge(origin, fports_port, merged_port)
        
        info = PortInfo.from_file(delta_port / "STATUS")
        
        if info.status == PortStatus.MASK:
            if self.cfg.dry_run:
                return MergeResult(origin, True, 'masked', 'Would remove')
            if merged_port.exists():
                shutil.rmtree(merged_port)
            return MergeResult(origin, True, 'masked')
        
        if info.status == PortStatus.LOCK:
            if self.cfg.dry_run:
                return MergeResult(origin, True, 'locked', 'Would copy from DPorts')
            src = self.cfg.dports / origin
            if not src.exists():
                return MergeResult(origin, False, 'error', 'LOCK but not in DPorts')
            merged_port.parent.mkdir(parents=True, exist_ok=True)
            cpdup(src, merged_port)
            return MergeResult(origin, True, 'locked')
        
        if info.status == PortStatus.DPORT:
            newport = delta_port / "newport"
            if not newport.exists():
                return MergeResult(origin, False, 'error', 'DPORT missing newport/')
            if self.cfg.dry_run:
                return MergeResult(origin, True, 'dport', 'Would copy newport')
            cpdup(newport, merged_port)
            return MergeResult(origin, True, 'dport')
        
        # Check for skip (same version already merged)
        if merged_port.exists() and version and info.last_attempt == version:
            return MergeResult(origin, True, 'skipped', f'Already v{version}')
        
        # Check if we have any modifications
        has_mkdf = (delta_port / "Makefile.DragonFly").exists()
        has_dfly = (delta_port / "dragonfly").is_dir()
        has_diffs = (delta_port / "diffs").is_dir()
        
        if not (has_mkdf or has_dfly or has_diffs):
            return self._fast_merge(origin, fports_port, merged_port)
        
        return self._full_merge(origin, fports_port, delta_port, merged_port,
                                has_mkdf, has_dfly, has_diffs)
    
    def _fast_merge(self, origin: str, src: Path, dst: Path) -> MergeResult:
        """Fast merge - copy and transform."""
        if self.cfg.dry_run:
            return MergeResult(origin, True, 'fast', 'Would copy')
        
        if not src.exists():
            return MergeResult(origin, False, 'error', 'Source not found')
        
        legacy = needs_transform(src)
        if not legacy:
            dst.parent.mkdir(parents=True, exist_ok=True)
            cpdup(src, dst)
        else:
            assert self.workdir is not None
            work = self.workdir / origin.replace('/', '_')
            shutil.copytree(src, work)
            transform_dir(work, legacy)
            dst.parent.mkdir(parents=True, exist_ok=True)
            cpdup(work, dst)
        
        return MergeResult(origin, True, 'fast')
    
    def _full_merge(self, origin: str, fports: Path, delta: Path, dst: Path,
                    has_mkdf: bool, has_dfly: bool, has_diffs: bool) -> MergeResult:
        """Full merge with patches."""
        if self.cfg.dry_run:
            return MergeResult(origin, True, 'merged', 'Would merge with patches')
        
        assert self.workdir is not None
        work = self.workdir / origin.replace('/', '_')
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(fports, work)
        
        # Copy Makefile.DragonFly
        if has_mkdf:
            shutil.copy2(delta / "Makefile.DragonFly", work)
        
        # Copy dragonfly/
        if has_dfly:
            shutil.copytree(delta / "dragonfly", work / "dragonfly")
        
        # Apply diffs
        patch_errors = []
        if has_diffs:
            diffs = delta / "diffs"
            
            # Handle REMOVE file
            remove_file = diffs / "REMOVE"
            if remove_file.exists():
                for line in remove_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith('#'):
                        to_rm = work / line
                        if to_rm.exists():
                            to_rm.unlink()
            
            # Apply patches
            for diff in sorted(diffs.glob("*.diff")):
                ok, output = apply_patch(work, diff)
                if not ok:
                    self.log.error(f"{origin}: patch failed: {diff.name}")
                    if output:
                        self.log.error(f"  {output}")
                    patch_errors.append(f"{diff.name}: {output}")
            
            cleanup_orig_files(work)
        
        # Transform
        legacy = needs_transform(work)
        if legacy:
            transform_dir(work, legacy)
        
        # Copy to destination
        dst.parent.mkdir(parents=True, exist_ok=True)
        cpdup(work, dst)
        
        return MergeResult(
            origin, 
            success=len(patch_errors) == 0,
            action='merged',
            message=f"{len(patch_errors)} patch error(s)" if patch_errors else "",
            patch_errors=patch_errors
        )


# =============================================================================
# Infrastructure Merge (Mk, Templates, Tools, etc.)
# =============================================================================

def merge_tools(cfg: Config, log: Logger):
    """Merge Tools/ with perl path fixes."""
    log.info("Merging Tools/...")
    if cfg.dry_run:
        return
    
    src = cfg.fports / "Tools"
    dst = cfg.merged / "Tools"
    
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    
    for f in src.rglob('*'):
        rel = f.relative_to(src)
        d = dst / rel
        if f.is_dir():
            d.mkdir(parents=True, exist_ok=True)
        else:
            content = f.read_text()
            content = content.replace('#!/usr/bin/perl', '#!/usr/local/bin/perl')
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_text(content)
            d.chmod(f.stat().st_mode)


def merge_keywords(cfg: Config, log: Logger):
    """Copy Keywords/."""
    log.info("Merging Keywords/...")
    if cfg.dry_run:
        return
    
    src = cfg.fports / "Keywords"
    dst = cfg.merged / "Keywords"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def merge_mk_templates(cfg: Config, log: Logger, workdir: Path):
    """Merge Mk/ and Templates/ with patches."""
    log.info("Merging Mk/ and Templates/...")
    if cfg.dry_run:
        return
    
    for dirname in ['Mk', 'Templates']:
        src = cfg.fports / dirname
        work = workdir / dirname
        dst = cfg.merged / dirname
        
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(src, work)
        
        # Remove bsd.gcc.mk
        if dirname == 'Mk':
            gcc = work / 'bsd.gcc.mk'
            if gcc.exists():
                gcc.unlink()
        
        # Apply patches
        diffs = cfg.delta / "special" / dirname / "diffs"
        if diffs.exists():
            for diff in sorted(diffs.glob("*.diff")):
                ok, out = apply_patch(work, diff)
                if not ok:
                    log.error(f"Patch failed: {diff}")
            cleanup_orig_files(work)
        
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(work, dst)
    
    # Replace linux.mk
    linux_src = cfg.delta / "special/Mk/replacements/Uses/linux.mk"
    if linux_src.exists():
        shutil.copy2(linux_src, cfg.merged / "Mk/Uses/linux.mk")


def merge_treetop(cfg: Config, log: Logger):
    """Merge top-level files (UIDs, GIDs, MOVED)."""
    log.info("Merging top-level files...")
    if cfg.dry_run:
        return
    
    # GIDs
    gids = cfg.fports / "GIDs"
    if gids.exists():
        lines = gids.read_text().splitlines()
        result = []
        for line in lines:
            result.append(line)
            if 'nogroup:' in line:
                result.extend(EXTRA_GIDS)
        (cfg.merged / "GIDs").write_text('\n'.join(result) + '\n')
    
    # UIDs
    uids = cfg.fports / "UIDs"
    if uids.exists():
        lines = uids.read_text().splitlines()
        result = []
        for line in lines:
            result.append(line)
            if 'nobody:' in line:
                result.extend(EXTRA_UIDS)
        (cfg.merged / "UIDs").write_text('\n'.join(result) + '\n')
    
    # MOVED - filter old entries
    moved = cfg.fports / "MOVED"
    if moved.exists():
        lines = []
        for line in moved.read_text().splitlines():
            if line.startswith('#'):
                lines.append(line)
            else:
                parts = line.split('|')
                if len(parts) >= 3:
                    try:
                        year = int(parts[2].split('-')[0])
                        if year > 2012:
                            lines.append(line)
                    except:
                        lines.append(line)
        dst = cfg.merged / "MOVED"
        dst.write_text('\n'.join(lines) + '\n')
        shutil.copystat(moved, dst)
    
    # Treetop patches
    diffs = cfg.delta / "special/treetop/diffs"
    if diffs.exists():
        for diff in sorted(diffs.glob("*.diff")):
            apply_patch(cfg.merged, diff)
        for orig in cfg.merged.glob("*.orig"):
            orig.unlink()


def merge_lang_makefiles(cfg: Config, log: Logger):
    """Copy Makefile.inc for language categories."""
    if cfg.dry_run:
        return
    for lang in LANG_DIRS:
        src = cfg.fports / lang / "Makefile.inc"
        dst = cfg.merged / lang / "Makefile.inc"
        if src.exists() and dst.parent.exists():
            shutil.copy2(src, dst)


# =============================================================================
# Commands
# =============================================================================

def cmd_merge(args, cfg: Config, log: Logger) -> int:
    """Full merge command."""
    if cfg.dry_run:
        log.info("DRY RUN - no changes will be made")
    
    # Parse INDEX
    port_versions = {}
    if cfg.index and cfg.index.exists():
        log.info(f"Parsing INDEX: {cfg.index}")
        port_versions = parse_index(cfg.index)
        log.info(f"  Found {len(port_versions)} ports")
    
    # Build port list
    if args.ports:
        ports = args.ports
    else:
        ports = set(port_versions.keys())
        # Add custom DPORTs
        delta_ports = cfg.delta / "ports"
        if delta_ports.exists():
            for origin in list_ports(delta_ports):
                info = PortInfo.from_file(delta_ports / origin / "STATUS")
                if info.status in (PortStatus.DPORT, PortStatus.LOCK):
                    ports.add(origin)
        ports = sorted(ports)
    
    log.info(f"Merging {len(ports)} ports...")
    
    workdir = Path(tempfile.mkdtemp(prefix='dports_'))
    try:
        # Infrastructure
        if not args.no_tools:
            merge_tools(cfg, log)
            merge_keywords(cfg, log)
        if not args.no_mk:
            merge_mk_templates(cfg, log, workdir)
        merge_treetop(cfg, log)
        
        # Ports
        stats = {'fast': 0, 'merged': 0, 'dport': 0, 'locked': 0, 
                 'masked': 0, 'skipped': 0, 'error': 0}
        failed = []
        
        with Merger(cfg, log) as merger:
            for i, origin in enumerate(ports, 1):
                if cfg.verbose:
                    log.debug(f"[{i}/{len(ports)}] {origin}")
                
                result = merger.merge_port(origin, port_versions.get(origin, ""))
                stats[result.action] = stats.get(result.action, 0) + 1
                
                if not result.success:
                    failed.append(result)
                    if args.fail_fast:
                        log.error("Stopping (--fail-fast)")
                        break
        
        merge_lang_makefiles(cfg, log)
        
        # Summary
        log.info("")
        log.info("=" * 50)
        log.info(f"Fast:    {stats['fast']}")
        log.info(f"Merged:  {stats['merged']}")
        log.info(f"DPORTs:  {stats['dport']}")
        log.info(f"Locked:  {stats['locked']}")
        log.info(f"Masked:  {stats['masked']}")
        log.info(f"Skipped: {stats['skipped']}")
        log.info(f"Errors:  {stats['error']}")
        
        if failed:
            log.info("")
            log.info("Failed ports:")
            for r in failed:
                log.error(f"  {r.port}: {r.message}")
        
        return 0 if not failed else 1
        
    finally:
        shutil.rmtree(workdir)


def cmd_sync(args, cfg: Config, log: Logger) -> int:
    """Sync single port to potential tree."""
    if not cfg.potential:
        log.error("POTENTIAL not configured")
        return 1
    
    if not args.port:
        log.error("Usage: dports sync <category/port>")
        return 1
    
    origin = args.port
    log.info(f"Syncing {origin}...")
    
    # Use potential as target instead of merged
    orig_merged = cfg.merged
    cfg.merged = cfg.potential
    
    with Merger(cfg, log) as merger:
        result = merger.merge_port(origin)
    
    cfg.merged = orig_merged
    
    # Also update merged if it exists
    merged_port = orig_merged / origin
    if merged_port.exists() and not cfg.dry_run:
        potential_port = cfg.potential / origin
        if potential_port.exists():
            cpdup(potential_port, merged_port)
    
    if result.success:
        log.info(f"  {result.action}: {result.message or 'OK'}")
        return 0
    else:
        log.error(f"  Failed: {result.message}")
        return 1


def cmd_prune(args, cfg: Config, log: Logger) -> int:
    """Prune obsolete ports."""
    if not args.confirm:
        log.info("SCAN MODE - use --confirm to actually prune")
    
    # Get merged ports (what should exist)
    merged_ports = set(list_ports(cfg.merged))
    
    # Get DPorts ports (what does exist)
    dports_ports = set(list_ports(cfg.dports))
    
    # Find obsolete in DPorts
    to_prune = dports_ports - merged_ports
    
    if not to_prune:
        log.info("No ports to prune from DPorts")
    else:
        log.info(f"Pruning {len(to_prune)} ports from DPorts:")
        for origin in sorted(to_prune):
            log.info(f"  {origin}")
            if args.confirm and not cfg.dry_run:
                port_dir = cfg.dports / origin
                if port_dir.exists():
                    shutil.rmtree(port_dir)
    
    # Check DeltaPorts for obsolete entries
    fports_ports = set(list_ports(cfg.fports))
    delta_ports_dir = cfg.delta / "ports"
    delta_ports = set(list_ports(delta_ports_dir)) if delta_ports_dir.exists() else set()
    
    delta_to_prune = []
    for origin in delta_ports:
        if origin not in fports_ports:
            info = PortInfo.from_file(delta_ports_dir / origin / "STATUS")
            # Only prune MASK or PORT (not DPORT - those are custom)
            if info.status in (PortStatus.MASK, PortStatus.PORT):
                delta_to_prune.append(origin)
    
    if not delta_to_prune:
        log.info("No ports to prune from DeltaPorts")
    else:
        log.info(f"Pruning {len(delta_to_prune)} ports from DeltaPorts:")
        for origin in sorted(delta_to_prune):
            log.info(f"  {origin}")
            if args.confirm and not cfg.dry_run:
                port_dir = delta_ports_dir / origin
                if port_dir.exists():
                    shutil.rmtree(port_dir)
    
    return 0


def cmd_makefiles(args, cfg: Config, log: Logger) -> int:
    """Generate category Makefiles."""
    log.info("Generating Makefiles...")
    
    if cfg.dry_run:
        log.info("Would generate Makefiles")
        return 0
    
    categories = []
    for d in sorted(cfg.merged.iterdir()):
        if d.is_dir() and d.name not in EXCLUDE_DIRS and not d.name.startswith('.'):
            categories.append(d.name)
    
    # Top-level Makefile
    with open(cfg.merged / "Makefile", 'w') as f:
        for cat in categories:
            f.write(f"SUBDIR += {cat}\n")
    
    # Category Makefiles
    for cat in categories:
        cat_dir = cfg.merged / cat
        ports = []
        for p in sorted(cat_dir.iterdir()):
            if p.is_dir() and not p.name.startswith('.'):
                ports.append(p.name)
        
        with open(cat_dir / "Makefile", 'w') as f:
            for port in ports:
                f.write(f"SUBDIR += {port}\n")
    
    log.info(f"Generated Makefiles for {len(categories)} categories")
    return 0


def cmd_bulk_list(args, cfg: Config, log: Logger) -> int:
    """Generate bulk build list."""
    ports = list_ports(cfg.dports)
    for p in ports:
        print(p)
    log.info(f"Listed {len(ports)} ports")
    return 0


def cmd_daemon(args, cfg: Config, log: Logger) -> int:
    """Run commit daemon (simplified version)."""
    if not cfg.comqueue:
        log.error("COMQUEUE not configured")
        return 1
    
    log.info(f"Starting daemon, queue: {cfg.comqueue}")
    log.info("Press Ctrl+C to stop")
    
    counter = 0
    try:
        while True:
            counter += 1
            
            # Process delta commits
            for item in cfg.comqueue.glob("delta.*"):
                try:
                    content = item.read_text().split()
                    if len(content) >= 3:
                        origin, action, version = content[0], content[1], content[2]
                        msg = f"{action}: {origin} v{version}"
                        
                        status_file = cfg.delta / "ports" / origin / "STATUS"
                        if status_file.exists():
                            subprocess.run(
                                ['git', 'add', str(status_file)],
                                cwd=cfg.delta, capture_output=True
                            )
                            subprocess.run(
                                ['git', 'commit', '-q', '-m', msg, str(status_file)],
                                cwd=cfg.delta, capture_output=True
                            )
                    item.unlink()
                except Exception as e:
                    log.error(f"Error processing {item}: {e}")
            
            # Process dport commits
            for item in cfg.comqueue.glob("dport.*"):
                try:
                    content = item.read_text().split()
                    if len(content) >= 3:
                        origin, action, version = content[0], content[1], content[2]
                        msg = f"{action} {origin} version {version}"
                        
                        port_dir = cfg.dports / origin
                        if port_dir.exists():
                            subprocess.run(
                                ['git', 'add', '--all', str(port_dir)],
                                cwd=cfg.dports, capture_output=True
                            )
                            subprocess.run(
                                ['git', 'commit', '-q', '-m', msg, str(port_dir)],
                                cwd=cfg.dports, capture_output=True
                            )
                    item.unlink()
                except Exception as e:
                    log.error(f"Error processing {item}: {e}")
            
            # Sync with remote periodically
            if counter >= 30:
                for repo in [cfg.delta, cfg.dports]:
                    subprocess.run(['git', 'pull', '-q', '--no-edit'],
                                   cwd=repo, capture_output=True)
                    subprocess.run(['git', 'push', '-q'],
                                   cwd=repo, capture_output=True)
                counter = 0
            
            time.sleep(30)
            
    except KeyboardInterrupt:
        log.info("Daemon stopped")
    
    return 0


def cmd_stinkers(args, cfg: Config, log: Logger) -> int:
    """Find unbuilt ports with most dependents."""
    if not cfg.index or not cfg.index.exists():
        log.error("INDEX file not found")
        return 1
    
    # Parse index
    index_data = {}  # pkg_name -> (origin, deps, build_deps)
    for line in cfg.index.read_text().splitlines():
        fields = line.split('|')
        if len(fields) >= 9:
            pkg = fields[0]
            path = fields[1]
            parts = path.rstrip('/').split('/')
            if len(parts) >= 2:
                origin = f"{parts[-2]}/{parts[-1]}"
                deps = fields[7].split() if len(fields) > 7 else []
                bdeps = fields[8].split() if len(fields) > 8 else []
                index_data[pkg] = (origin, deps, bdeps)
    
    # Find unbuilt ports
    dports_set = set(list_ports(cfg.dports))
    unbuilt = []
    
    for pkg, (origin, deps, bdeps) in index_data.items():
        if origin not in dports_set:
            info = PortInfo.from_file(cfg.delta / "ports" / origin / "STATUS")
            if info.status != PortStatus.MASK:
                unbuilt.append((pkg, origin))
    
    # Count dependents for each unbuilt port
    results = []
    for pkg, origin in unbuilt:
        count = 0
        for other_pkg, (_, deps, bdeps) in index_data.items():
            if pkg in deps or pkg in bdeps:
                count += 1
        if count > 0:
            results.append((count, origin))
    
    # Print sorted by count
    for count, origin in sorted(results, reverse=True):
        print(f"{count:4d}: {origin}")
    
    return 0


def cmd_index(args, cfg: Config, log: Logger) -> int:
    """Generate INDEX file."""
    merged = args.portsdir or cfg.merged
    dports = args.dports_dir or cfg.dports
    output = args.output or (cfg.fports / "INDEX-3") if cfg.fports else Path("/tmp/INDEX")
    
    log.info(f"Generating INDEX from {merged}...")
    
    if cfg.dry_run:
        log.info(f"Would write to {output}")
        return 0
    
    failed = []
    lines = []
    
    # Find all ports
    ports = list_ports(merged)
    log.info(f"Processing {len(ports)} ports...")
    
    for i, origin in enumerate(ports, 1):
        if cfg.verbose and i % 100 == 0:
            log.debug(f"  {i}/{len(ports)}...")
        
        port_dir = merged / origin
        try:
            result = subprocess.run(
                ['make', f'PORTSDIR={dports}', 'PORT_DBDIR=/tmp', 'describe'],
                cwd=port_dir, capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0 and result.stdout.strip():
                lines.append(result.stdout.strip())
            else:
                failed.append(origin)
        except Exception as e:
            failed.append(origin)
            log.debug(f"  Failed: {origin}: {e}")
    
    # Write INDEX
    output.write_text('\n'.join(lines) + '\n')
    log.info(f"Wrote {len(lines)} entries to {output}")
    
    if failed:
        log.warn(f"{len(failed)} ports failed:")
        for p in failed[:20]:  # Show first 20
            log.warn(f"  {p}")
        if len(failed) > 20:
            log.warn(f"  ... and {len(failed) - 20} more")
    
    return 0 if not failed else 1


def cmd_updating(args, cfg: Config, log: Logger) -> int:
    """Generate rolling UPDATING file (last year only)."""
    log.info("Generating rolling UPDATING...")
    
    if cfg.dry_run:
        log.info("Would filter UPDATING to last year")
        return 0
    
    src = cfg.fports / "UPDATING"
    dst = cfg.merged / "UPDATING"
    
    if not src.exists():
        log.error(f"UPDATING not found: {src}")
        return 1
    
    today = datetime.now()
    cutoff = today.year - 1  # Keep entries from last year
    
    lines = []
    keep = True
    for line in src.read_text().splitlines():
        # Check for date header (YYYYMMDD:)
        if re.match(r'^20\d{6}:', line):
            try:
                year = int(line[:4])
                keep = year >= cutoff
            except ValueError:
                keep = True
        if keep:
            lines.append(line)
    
    dst.write_text('\n'.join(lines) + '\n')
    log.info(f"Wrote {len(lines)} lines to {dst}")
    return 0


def cmd_quicksync(args, cfg: Config, log: Logger) -> int:
    """Sync multiple ports from a file."""
    syncfile = Path(args.file) if args.file else Path("/tmp/syncme")
    
    if not syncfile.exists():
        log.error(f"Sync file not found: {syncfile}")
        return 1
    
    origins = [l.strip() for l in syncfile.read_text().splitlines() if l.strip()]
    log.info(f"Syncing {len(origins)} ports from {syncfile}...")
    
    failures = 0
    for origin in origins:
        # Reuse sync logic
        class FakeArgs:
            port = origin
        
        result = cmd_sync(FakeArgs(), cfg, log)
        if result != 0:
            failures += 1
    
    log.info(f"Done. {len(origins) - failures} succeeded, {failures} failed.")
    return 0 if failures == 0 else 1


def cmd_identify_nobody(args, cfg: Config, log: Logger) -> int:
    """Find customized ports with nobody@ maintainer (easy to upstream)."""
    log.info("Finding customized ports with nobody maintainer...")
    
    # Find all ports with customizations
    delta_ports = cfg.delta / "ports"
    customized = set()
    
    for origin in list_ports(delta_ports):
        port_dir = delta_ports / origin
        has_custom = (
            (port_dir / "diffs").is_dir() or
            (port_dir / "dragonfly").is_dir() or
            (port_dir / "Makefile.DragonFly").exists()
        )
        if has_custom:
            customized.add(origin)
    
    # Check which have nobody maintainer
    nobody_pattern = re.compile(r'^MAINTAINER=.*ports@FreeBSD\.org', re.MULTILINE)
    results = []
    
    for origin in sorted(customized):
        fports_dir = cfg.fports / origin
        if fports_dir.exists():
            for makefile in fports_dir.glob("Makefile*"):
                try:
                    if nobody_pattern.search(makefile.read_text()):
                        results.append(origin)
                        break
                except Exception:
                    pass
        else:
            log.warn(f"Port no longer exists in FreeBSD: {origin}")
    
    log.info(f"Found {len(results)} customized ports with nobody maintainer:")
    for origin in results:
        print(origin)
    
    return 0


def cmd_deps(args, cfg: Config, log: Logger) -> int:
    """List ports depending on a given port (for bulk rebuilds)."""
    if not args.port:
        log.error("Usage: dports deps <category/port>")
        return 1
    
    if not cfg.index or not cfg.index.exists():
        log.error("INDEX file not found")
        return 1
    
    origin = args.port
    
    # Find package name for this origin
    target_pkg = None
    for line in cfg.index.read_text().splitlines():
        fields = line.split('|')
        if len(fields) >= 2:
            path = fields[1].rstrip('/')
            if path.endswith(f"/{origin.split('/')[-1]}") and origin.split('/')[0] in path:
                target_pkg = fields[0]
                break
    
    if not target_pkg:
        log.error(f"Port not found in INDEX: {origin}")
        return 1
    
    # Find all ports that depend on this package
    dependents = []
    for line in cfg.index.read_text().splitlines():
        fields = line.split('|')
        if len(fields) >= 9:
            path = fields[1].rstrip('/')
            parts = path.split('/')
            if len(parts) >= 2:
                dep_origin = f"{parts[-2]}/{parts[-1]}"
                deps = (fields[7] + ' ' + fields[8]).split()
                if target_pkg in deps:
                    dependents.append(dep_origin)
    
    # Filter to ports that exist in merged/dports
    merged_ports = set(list_ports(cfg.merged))
    result = [d for d in dependents if d in merged_ports]
    
    log.info(f"Found {len(result)} ports depending on {origin}:")
    for p in sorted(result):
        print(p)
    
    return 0


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="DragonFly Ports generator",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-c', '--config', help='Config file path')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-n', '--dry-run', action='store_true')
    parser.add_argument('-q', '--quiet', action='store_true')
    parser.add_argument('--version', action='version', version=f'dports {VERSION}')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # merge
    p = subparsers.add_parser('merge', help='Full merge of ports')
    p.add_argument('ports', nargs='*', help='Specific ports (default: all)')
    p.add_argument('--no-mk', action='store_true', help='Skip Mk/Templates')
    p.add_argument('--no-tools', action='store_true', help='Skip Tools/Keywords')
    p.add_argument('--fail-fast', action='store_true', help='Stop on first error')
    
    # sync
    p = subparsers.add_parser('sync', help='Sync single port')
    p.add_argument('port', help='category/port')
    
    # prune
    p = subparsers.add_parser('prune', help='Prune obsolete ports')
    p.add_argument('--confirm', action='store_true', help='Actually prune')
    
    # makefiles
    subparsers.add_parser('makefiles', help='Generate category Makefiles')
    
    # bulk-list
    subparsers.add_parser('bulk-list', help='List ports for bulk build')
    
    # daemon
    subparsers.add_parser('daemon', help='Run commit daemon')
    
    # stinkers
    subparsers.add_parser('stinkers', help='Find unbuilt ports with dependents')
    
    # index
    p = subparsers.add_parser('index', help='Generate INDEX file')
    p.add_argument('--portsdir', help='Ports directory (default: MERGED)')
    p.add_argument('--dports-dir', help='DPorts for PORTSDIR (default: DPORTS)')
    p.add_argument('-o', '--output', help='Output file')
    
    # updating
    subparsers.add_parser('updating', help='Generate rolling UPDATING file')
    
    # quicksync
    p = subparsers.add_parser('quicksync', help='Sync ports from file')
    p.add_argument('-f', '--file', help='File with port list (default: /tmp/syncme)')
    
    # identify-nobody
    subparsers.add_parser('identify-nobody', 
                          help='Find customized ports with nobody maintainer')
    
    # deps
    p = subparsers.add_parser('deps', help='List ports depending on a port')
    p.add_argument('port', help='category/port')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Load config
    try:
        cfg = load_config(args.config)
        cfg.verbose = args.verbose
        cfg.dry_run = args.dry_run
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1
    
    # Validate
    errors = cfg.validate()
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    
    # Setup logging
    log = Logger(
        args.command,
        log_dir=cfg.log_dir if not args.quiet else None,
        verbose=cfg.verbose,
        quiet=args.quiet
    )
    
    if log.log_file:
        log.info(f"Log: {log.log_file}")
    
    # Dispatch
    commands = {
        'merge': cmd_merge,
        'sync': cmd_sync,
        'prune': cmd_prune,
        'makefiles': cmd_makefiles,
        'bulk-list': cmd_bulk_list,
        'daemon': cmd_daemon,
        'stinkers': cmd_stinkers,
        'index': cmd_index,
        'updating': cmd_updating,
        'quicksync': cmd_quicksync,
        'identify-nobody': cmd_identify_nobody,
        'deps': cmd_deps,
    }
    
    try:
        return commands[args.command](args, cfg, log)
    finally:
        log.close()


if __name__ == '__main__':
    sys.exit(main())
