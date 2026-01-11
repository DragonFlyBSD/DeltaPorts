#!/usr/bin/env python3
"""
snippet-extractor: Extract bounded source/log snippets for agent context.

Usage:
    snippet-extractor --bundle <path> [options]

Options:
    --bundle PATH           Evidence bundle directory (required)
    --round N               Snippet round number (default: 1)
    --distfiles-dir PATH    Override distfiles directory
    --buildbase-dir PATH    Override buildbase directory (for workdir scan)
    --max-per-snippet N     Max bytes per snippet (default: 51200)
    --max-total N           Max total bytes per round (default: 204800)
    --prefer-workdir        Prefer preserved workdir over distfiles
    --dry-run               Parse requests but don't extract
    --verbose               Verbose output

Exit codes:
    0 - Success, at least some snippets extracted
    1 - No snippet requests found
    2 - All requests failed (nothing extracted)
    3 - Configuration/usage error

The extractor reads snippet requests from analysis/triage.md or analysis/patch.md
and writes results to analysis/snippets/round_N/.
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------

@dataclass
class SnippetRequest:
    raw: str
    type: str  # source, buildsystem, configure, log
    path: Optional[str] = None
    line: Optional[int] = None
    context: Optional[int] = None  # ±N lines
    start_line: Optional[int] = None  # for log requests
    end_line: Optional[int] = None
    status: str = "pending"
    output: Optional[str] = None
    bytes: int = 0
    actual_lines: Optional[list] = None
    note: Optional[str] = None


@dataclass
class RoundResult:
    round: int
    source: str  # "workdir", "distfiles", "log"
    distfile: Optional[str] = None
    workdir_path: Optional[str] = None
    requests: list = field(default_factory=list)
    total_bytes: int = 0
    budget_remaining: int = 0
    truncated: bool = False


@dataclass
class Manifest:
    rounds: list = field(default_factory=list)
    total_rounds: int = 0
    total_bytes_all_rounds: int = 0


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

VERBOSE = False

def log(level: str, msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} {level:5} {msg}", file=sys.stderr)

def log_verbose(msg: str):
    if VERBOSE:
        log("DEBUG", msg)

def log_info(msg: str):
    log("INFO", msg)

def log_warn(msg: str):
    log("WARN", msg)

def log_error(msg: str):
    log("ERROR", msg)


# -----------------------------------------------------------------------------
# Request parsing
# -----------------------------------------------------------------------------

def parse_snippet_requests(content: str) -> list[SnippetRequest]:
    """Parse ## Snippet Requests section from markdown."""
    # Find the section
    match = re.search(
        r"^##\s*Snippet Requests\s*\n(.*?)(?=^##|\Z)",
        content,
        re.MULTILINE | re.DOTALL | re.IGNORECASE
    )
    if not match:
        return []
    
    section = match.group(1)
    requests = []
    
    for line in section.split("\n"):
        line = line.strip()
        if not line:
            continue
        
        # Parse: - `request:spec` — description
        m = re.match(r"-\s*`([^`]+)`", line)
        if not m:
            continue
        
        spec = m.group(1)
        req = parse_request_spec(spec)
        if req:
            requests.append(req)
    
    return requests


def parse_request_spec(spec: str) -> Optional[SnippetRequest]:
    """Parse a single request specification."""
    parts = spec.split(":")
    if len(parts) < 2:
        return None
    
    req_type = parts[0].lower()
    
    if req_type == "source":
        # source:path:line:context or source:path:all
        if len(parts) < 3:
            return None
        path = parts[1]
        if len(parts) >= 3 and parts[2].lower() == "all":
            return SnippetRequest(
                raw=spec, type="source", path=path,
                line=None, context=None
            )
        try:
            line = int(parts[2])
            context = 30  # default
            if len(parts) >= 4:
                ctx_str = parts[3].strip()
                if ctx_str.startswith("±") or ctx_str.startswith("+/-"):
                    ctx_str = ctx_str.lstrip("±+/-")
                context = int(ctx_str)
            return SnippetRequest(
                raw=spec, type="source", path=path,
                line=line, context=context
            )
        except ValueError:
            return None
    
    elif req_type in ("buildsystem", "configure"):
        # buildsystem:path or configure:path
        path = ":".join(parts[1:])  # rejoin in case path has colons
        return SnippetRequest(raw=spec, type=req_type, path=path)
    
    elif req_type == "log":
        # log:start:end
        if len(parts) < 3:
            return None
        try:
            start = int(parts[1])
            end = int(parts[2])
            return SnippetRequest(
                raw=spec, type="log",
                start_line=start, end_line=end
            )
        except ValueError:
            return None
    
    return None


# -----------------------------------------------------------------------------
# Meta parsing
# -----------------------------------------------------------------------------

def parse_meta_file(path: Path) -> dict:
    """Parse key=value meta file."""
    data = {}
    if not path.exists():
        return data
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip()
    return data


def parse_distinfo(path: Path) -> list[tuple[str, str, int]]:
    """Parse distinfo file, return list of (filename, hash, size)."""
    results = []
    if not path.exists():
        return results
    
    # Format: SHA256 (filename) = hash
    #         SIZE (filename) = size
    sha_pattern = re.compile(r"SHA256\s+\(([^)]+)\)\s*=\s*(\w+)")
    size_pattern = re.compile(r"SIZE\s+\(([^)]+)\)\s*=\s*(\d+)")
    
    sha_map = {}
    size_map = {}
    
    with open(path) as f:
        for line in f:
            m = sha_pattern.match(line)
            if m:
                sha_map[m.group(1)] = m.group(2)
                continue
            m = size_pattern.match(line)
            if m:
                size_map[m.group(1)] = int(m.group(2))
    
    for fname, sha in sha_map.items():
        size = size_map.get(fname, 0)
        results.append((fname, sha, size))
    
    return results


# -----------------------------------------------------------------------------
# Workdir detection
# -----------------------------------------------------------------------------

def find_workdir(buildbase: Path, origin: str) -> Optional[Path]:
    """Scan SL* directories for preserved workdir matching origin."""
    cat, port = origin.split("/", 1) if "/" in origin else (origin, "")
    
    for slot_dir in sorted(buildbase.glob("SL*")):
        # Look for construction/<cat>/<port>/work
        work_path = slot_dir / "construction" / cat / port / "work"
        if work_path.is_dir():
            log_info(f"Found preserved workdir: {work_path}")
            return work_path
    
    return None


# -----------------------------------------------------------------------------
# Distfile extraction
# -----------------------------------------------------------------------------

def find_distfile(distfiles_dir: Path, distinfo_entries: list) -> Optional[Path]:
    """Find the primary distfile in the distfiles directory."""
    for fname, sha, size in distinfo_entries:
        # Try common locations
        candidates = [
            distfiles_dir / fname,
            distfiles_dir / fname.split("/")[-1],  # basename only
        ]
        for candidate in candidates:
            if candidate.exists():
                log_verbose(f"Found distfile: {candidate}")
                return candidate
    
    return None


def extract_distfile_to_workdir(distfile: Path, workdir: Path) -> bool:
    """Extract distfile to temporary workdir."""
    workdir.mkdir(parents=True, exist_ok=True)
    
    name = distfile.name.lower()
    
    try:
        if name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(distfile, "r:gz") as tf:
                tf.extractall(workdir, filter="data")
            return True
        elif name.endswith((".tar.xz", ".txz")):
            with tarfile.open(distfile, "r:xz") as tf:
                tf.extractall(workdir, filter="data")
            return True
        elif name.endswith((".tar.bz2", ".tbz2", ".tbz")):
            with tarfile.open(distfile, "r:bz2") as tf:
                tf.extractall(workdir, filter="data")
            return True
        elif name.endswith(".tar"):
            with tarfile.open(distfile, "r:") as tf:
                tf.extractall(workdir, filter="data")
            return True
        elif name.endswith(".zip"):
            with zipfile.ZipFile(distfile, "r") as zf:
                zf.extractall(workdir)
            return True
        else:
            log_warn(f"Unknown archive format: {name}")
            return False
    except Exception as e:
        log_error(f"Failed to extract {distfile}: {e}")
        return False


def find_file_in_tree(root: Path, target_path: str) -> Optional[Path]:
    """Find a file in extracted tree, handling top-level directory variations."""
    # Direct path
    direct = root / target_path
    if direct.exists():
        return direct
    
    # Try under first subdirectory (common for tarballs)
    subdirs = [d for d in root.iterdir() if d.is_dir()]
    for subdir in subdirs:
        candidate = subdir / target_path
        if candidate.exists():
            return candidate
    
    # Try basename search as last resort
    basename = Path(target_path).name
    for found in root.rglob(basename):
        if found.is_file():
            return found
    
    return None


# -----------------------------------------------------------------------------
# Log extraction
# -----------------------------------------------------------------------------

def extract_log_lines(log_gz_path: Path, start: int, end: int) -> tuple[str, list]:
    """Extract lines from gzipped log file."""
    if not log_gz_path.exists():
        return "", []
    
    lines = []
    try:
        with gzip.open(log_gz_path, "rt", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if i >= start and i <= end:
                    lines.append(line.rstrip("\n"))
                if i > end:
                    break
    except Exception as e:
        log_error(f"Failed to read log: {e}")
        return "", []
    
    content = "\n".join(lines)
    actual_lines = [start, min(start + len(lines) - 1, end)] if lines else []
    return content, actual_lines


# -----------------------------------------------------------------------------
# Source extraction
# -----------------------------------------------------------------------------

def extract_source_snippet(
    file_path: Path,
    line: Optional[int],
    context: Optional[int],
    max_bytes: int
) -> tuple[str, list]:
    """Extract snippet from source file."""
    if not file_path.exists():
        return "", []
    
    try:
        with open(file_path, "r", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        log_error(f"Failed to read {file_path}: {e}")
        return "", []
    
    if line is None:
        # Extract entire file
        content = "".join(all_lines)
        if len(content) > max_bytes:
            content = content[:max_bytes] + "\n[...truncated...]\n"
        return content, [1, len(all_lines)]
    
    # Extract lines around target
    start_idx = max(0, line - context - 1)
    end_idx = min(len(all_lines), line + context)
    
    selected = all_lines[start_idx:end_idx]
    content = "".join(selected)
    
    if len(content) > max_bytes:
        content = content[:max_bytes] + "\n[...truncated...]\n"
    
    actual_lines = [start_idx + 1, start_idx + len(selected)]
    return content, actual_lines


# -----------------------------------------------------------------------------
# Safe filename generation
# -----------------------------------------------------------------------------

def safe_filename(path: str) -> str:
    """Convert path to safe filename."""
    # Replace path separators and special chars
    safe = path.replace("/", "_").replace("\\", "_")
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", safe)
    # Truncate if too long
    if len(safe) > 200:
        safe = safe[:200]
    return safe


# -----------------------------------------------------------------------------
# Main extraction logic
# -----------------------------------------------------------------------------

def process_requests(
    requests: list[SnippetRequest],
    bundle_dir: Path,
    output_dir: Path,
    source_root: Path,
    source_type: str,
    distfile_name: Optional[str],
    log_gz_path: Path,
    max_per_snippet: int,
    max_total: int
) -> RoundResult:
    """Process all snippet requests."""
    result = RoundResult(
        round=0,  # Will be set by caller
        source=source_type,
        distfile=distfile_name,
        workdir_path=str(source_root) if source_type == "workdir" else None,
        budget_remaining=max_total
    )
    
    total_bytes = 0
    
    for req in requests:
        if total_bytes >= max_total:
            req.status = "budget_exceeded"
            req.note = "Budget exhausted"
            result.truncated = True
            result.requests.append(asdict(req))
            continue
        
        remaining_budget = min(max_per_snippet, max_total - total_bytes)
        
        if req.type == "log":
            # Log extraction
            content, actual_lines = extract_log_lines(
                log_gz_path, req.start_line, req.end_line
            )
            if content:
                out_name = f"lines_{req.start_line}-{req.end_line}.txt"
                out_path = output_dir / "log" / out_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                
                if len(content) > remaining_budget:
                    content = content[:remaining_budget] + "\n[...truncated...]\n"
                
                out_path.write_text(content)
                req.status = "ok"
                req.output = f"log/{out_name}"
                req.bytes = len(content)
                req.actual_lines = actual_lines
                total_bytes += len(content)
            else:
                req.status = "not_found"
                req.note = "Could not extract log lines"
        
        elif req.type in ("source", "buildsystem", "configure"):
            # Source/build file extraction
            found_path = find_file_in_tree(source_root, req.path)
            
            if found_path:
                content, actual_lines = extract_source_snippet(
                    found_path, req.line, req.context, remaining_budget
                )
                if content:
                    subdir = req.type if req.type != "source" else "source"
                    out_name = safe_filename(req.path) + ".txt"
                    out_path = output_dir / subdir / out_name
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    out_path.write_text(content)
                    req.status = "ok"
                    req.output = f"{subdir}/{out_name}"
                    req.bytes = len(content)
                    req.actual_lines = actual_lines
                    total_bytes += len(content)
                else:
                    req.status = "empty"
                    req.note = "File exists but is empty or unreadable"
            else:
                req.status = "not_found"
                req.note = f"File not found in {source_type}"
        
        else:
            req.status = "unknown_type"
            req.note = f"Unknown request type: {req.type}"
        
        result.requests.append(asdict(req))
    
    result.total_bytes = total_bytes
    result.budget_remaining = max_total - total_bytes
    return result


def load_existing_manifest(bundle_dir: Path) -> Manifest:
    """Load existing manifest if present."""
    manifest_path = bundle_dir / "analysis" / "snippets" / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                data = json.load(f)
            return Manifest(
                rounds=data.get("rounds", []),
                total_rounds=data.get("total_rounds", 0),
                total_bytes_all_rounds=data.get("total_bytes_all_rounds", 0)
            )
        except Exception:
            pass
    return Manifest()


def save_manifest(manifest: Manifest, bundle_dir: Path):
    """Save manifest to bundle."""
    manifest_path = bundle_dir / "analysis" / "snippets" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(asdict(manifest), f, indent=2)


def save_round_manifest(result: RoundResult, output_dir: Path):
    """Save per-round manifest."""
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(asdict(result), f, indent=2)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    global VERBOSE
    
    parser = argparse.ArgumentParser(
        description="Extract bounded source/log snippets for agent context"
    )
    parser.add_argument("--bundle", required=True, help="Evidence bundle directory")
    parser.add_argument("--round", type=int, default=1, help="Snippet round number")
    parser.add_argument("--distfiles-dir", help="Override distfiles directory")
    parser.add_argument("--buildbase-dir", help="Override buildbase directory")
    parser.add_argument("--max-per-snippet", type=int, default=51200,
                        help="Max bytes per snippet (default: 51200)")
    parser.add_argument("--max-total", type=int, default=204800,
                        help="Max total bytes per round (default: 204800)")
    parser.add_argument("--prefer-workdir", action="store_true",
                        help="Prefer preserved workdir over distfiles")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse requests but don't extract")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    VERBOSE = args.verbose
    
    bundle_dir = Path(args.bundle)
    if not bundle_dir.exists():
        log_error(f"Bundle directory does not exist: {bundle_dir}")
        sys.exit(3)
    
    # Parse meta.txt for paths
    meta = parse_meta_file(bundle_dir / "meta.txt")
    origin = meta.get("origin", meta.get("origin_base", ""))
    if not origin:
        log_error("Could not determine origin from meta.txt")
        sys.exit(3)
    
    distfiles_dir = Path(args.distfiles_dir) if args.distfiles_dir else Path(meta.get("dir_distfiles", "/build/synth/distfiles"))
    buildbase_dir = Path(args.buildbase_dir) if args.buildbase_dir else Path(meta.get("dir_buildbase", "/build/synth"))
    
    log_info(f"Bundle: {bundle_dir}")
    log_info(f"Origin: {origin}")
    log_info(f"Round: {args.round}")
    log_verbose(f"Distfiles dir: {distfiles_dir}")
    log_verbose(f"Buildbase dir: {buildbase_dir}")
    
    # Find snippet requests in triage.md or patch.md
    requests = []
    for source_file in ["triage.md", "patch.md"]:
        source_path = bundle_dir / "analysis" / source_file
        if source_path.exists():
            content = source_path.read_text()
            requests = parse_snippet_requests(content)
            if requests:
                log_info(f"Found {len(requests)} requests in {source_file}")
                break
    
    if not requests:
        log_info("No snippet requests found")
        sys.exit(1)
    
    if args.dry_run:
        log_info("Dry run - parsed requests:")
        for req in requests:
            print(f"  {req.type}: {req.raw}")
        sys.exit(0)
    
    # Determine source: workdir or distfiles
    source_root = None
    source_type = None
    distfile_name = None
    tmp_workdir = None
    
    if args.prefer_workdir:
        source_root = find_workdir(buildbase_dir, origin)
        if source_root:
            source_type = "workdir"
    
    if not source_root:
        # Try distfiles
        distinfo_path = bundle_dir / "port" / "distinfo"
        distinfo_entries = parse_distinfo(distinfo_path)
        
        if distinfo_entries:
            distfile = find_distfile(distfiles_dir, distinfo_entries)
            if distfile:
                # Extract to temporary workdir
                tmp_workdir = bundle_dir / "analysis" / "snippets" / ".workdir"
                if tmp_workdir.exists():
                    # Reuse existing extraction
                    source_root = tmp_workdir
                    source_type = "distfiles"
                    distfile_name = distfile.name
                    log_info(f"Reusing extracted workdir: {tmp_workdir}")
                else:
                    log_info(f"Extracting distfile: {distfile}")
                    if extract_distfile_to_workdir(distfile, tmp_workdir):
                        source_root = tmp_workdir
                        source_type = "distfiles"
                        distfile_name = distfile.name
    
    if not source_root and not args.prefer_workdir:
        # Fallback: try workdir
        source_root = find_workdir(buildbase_dir, origin)
        if source_root:
            source_type = "workdir"
    
    if not source_root:
        log_warn("No source tree available (no workdir, no distfiles)")
        source_root = Path("/nonexistent")  # Will fail gracefully
        source_type = "none"
    
    log_info(f"Source type: {source_type}")
    
    # Output directory for this round
    output_dir = bundle_dir / "analysis" / "snippets" / f"round_{args.round}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Log path
    log_gz_path = bundle_dir / "logs" / "full.log.gz"
    
    # Process requests
    result = process_requests(
        requests,
        bundle_dir,
        output_dir,
        source_root,
        source_type,
        distfile_name,
        log_gz_path,
        args.max_per_snippet,
        args.max_total
    )
    result.round = args.round
    
    # Save round manifest
    save_round_manifest(result, output_dir)
    
    # Update cumulative manifest
    manifest = load_existing_manifest(bundle_dir)
    manifest.rounds.append(asdict(result))
    manifest.total_rounds = len(manifest.rounds)
    manifest.total_bytes_all_rounds = sum(r.get("total_bytes", 0) for r in manifest.rounds)
    save_manifest(manifest, bundle_dir)
    
    # Report results
    success_count = sum(1 for r in result.requests if r.get("status") == "ok")
    fail_count = len(result.requests) - success_count
    
    log_info(f"Extracted {success_count}/{len(result.requests)} snippets ({result.total_bytes} bytes)")
    
    if success_count == 0:
        log_error("All requests failed")
        sys.exit(2)
    
    if fail_count > 0:
        log_warn(f"{fail_count} requests failed")
    
    sys.exit(0)


if __name__ == "__main__":
    main()
