"""
Quarterly branch management for DPorts v2.

Handles quarterly naming conventions, validation, and path resolution
for multi-quarterly FreeBSD support.

Quarterly format: YYYYQN (e.g., 2025Q1, 2025Q2)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dports.config import Config

from dports.utils import DPortsError


# Pattern for quarterly identifiers: 2025Q1, 2024Q4, etc.
QUARTERLY_PATTERN = re.compile(r"^(\d{4})Q([1-4])$")


class QuarterlyError(DPortsError):
    """Error related to quarterly operations."""
    pass


@dataclass(frozen=True, order=True)
class Quarterly:
    """
    Represents a quarterly release branch.
    
    Immutable and sortable (by year, then quarter).
    """
    
    year: int
    quarter: int

    def __post_init__(self):
        if not (2020 <= self.year <= 2100):
            raise QuarterlyError(f"Invalid year: {self.year}")
        if not (1 <= self.quarter <= 4):
            raise QuarterlyError(f"Invalid quarter: {self.quarter}")

    def __str__(self) -> str:
        return f"{self.year}Q{self.quarter}"

    def __repr__(self) -> str:
        return f"Quarterly({self.year}, {self.quarter})"

    @classmethod
    def parse(cls, value: str) -> Quarterly:
        """
        Parse a quarterly string.
        
        Args:
            value: String like "2025Q1"
            
        Returns:
            Quarterly object
            
        Raises:
            QuarterlyError: If format is invalid
        """
        match = QUARTERLY_PATTERN.match(value.strip().upper())
        if not match:
            raise QuarterlyError(
                f"Invalid quarterly format: {value!r} (expected YYYYQN like 2025Q1)"
            )
        return cls(year=int(match.group(1)), quarter=int(match.group(2)))

    @classmethod
    def current(cls) -> Quarterly:
        """Get the current quarterly based on today's date."""
        today = date.today()
        quarter = (today.month - 1) // 3 + 1
        return cls(year=today.year, quarter=quarter)

    @classmethod
    def from_date(cls, d: date) -> Quarterly:
        """Get the quarterly for a specific date."""
        quarter = (d.month - 1) // 3 + 1
        return cls(year=d.year, quarter=quarter)

    def next(self) -> Quarterly:
        """Get the next quarterly."""
        if self.quarter == 4:
            return Quarterly(self.year + 1, 1)
        return Quarterly(self.year, self.quarter + 1)

    def previous(self) -> Quarterly:
        """Get the previous quarterly."""
        if self.quarter == 1:
            return Quarterly(self.year - 1, 4)
        return Quarterly(self.year, self.quarter - 1)

    def start_date(self) -> date:
        """Get the start date of this quarterly."""
        month = (self.quarter - 1) * 3 + 1
        return date(self.year, month, 1)

    def end_date(self) -> date:
        """Get the end date of this quarterly."""
        return self.next().start_date()


def validate_quarterly(value: str) -> Quarterly:
    """
    Validate and parse a quarterly string.
    
    Convenience function that wraps Quarterly.parse().
    
    Args:
        value: String to validate
        
    Returns:
        Quarterly object
        
    Raises:
        QuarterlyError: If invalid
    """
    return Quarterly.parse(value)


def is_valid_quarterly(value: str) -> bool:
    """Check if a string is a valid quarterly identifier."""
    return QUARTERLY_PATTERN.match(value.strip().upper()) is not None


def get_quarterly_diffs_dir(overlay_path: Path, quarterly: str) -> Path | None:
    """
    Get the quarterly-specific diffs directory if it exists.
    
    Args:
        overlay_path: Path to the port's overlay
        quarterly: Quarterly identifier
        
    Returns:
        Path to @QUARTER directory, or None if it doesn't exist
    """
    q_dir = overlay_path / "diffs" / f"@{quarterly}"
    return q_dir if q_dir.exists() else None


def list_quarterly_overrides(overlay_path: Path) -> list[str]:
    """
    List all quarterly overrides defined for a port.
    
    Args:
        overlay_path: Path to the port's overlay
        
    Returns:
        List of quarterly identifiers that have overrides
    """
    diffs_dir = overlay_path / "diffs"
    if not diffs_dir.exists():
        return []
    
    overrides = []
    for item in diffs_dir.iterdir():
        if item.is_dir() and item.name.startswith("@"):
            q_name = item.name[1:]  # Remove @ prefix
            if is_valid_quarterly(q_name):
                overrides.append(q_name)
    
    return sorted(overrides)
