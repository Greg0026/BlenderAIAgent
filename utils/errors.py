"""Error tracking and oscillation detection for the pipeline.

Provides two fundamental classes for pipeline stability:

ErrorHistory:
  Maintains the history of errors encountered in the F6 and F6-VIS phases.
  Each error is recorded with a "signature" that identifies
  the error type (based on the message, not the entire stack trace).
  The history is passed to the LLM in subsequent calls to prevent
  repeating already attempted fixes without success.

OscillationDetector:
  Detects oscillation patterns in pipeline iterations.
  Compares MD5 fingerprints of scripts produced at each iteration.
  If the script returns to an already seen state (A→B→A→B pattern), detects
  the oscillation and allows the orchestrator to break the cycle.
"""

import hashlib
import re
from typing import List, Optional


class ErrorHistory:
    """Error history with repetition detection.

    Useful to prevent the pipeline from repeating the same fix endlessly
    without success. Each error is converted to a "signature" extracted
    from the main message (Error: or Exception: line), ignoring
    variable parts like memory addresses or timestamps.

    Args:
        max_history: Maximum number of errors to keep in history.
    """

    def __init__(self, max_history: int = 10):
        """Initialize the empty history with maximum capacity.

        Args:
            max_history: Maximum number of errors to track (FIFO).
        """
        self._errors: List[str] = []
        self._fixes: List[str] = []
        self._max = max_history

    def add(self, error: str, fix_approach: str = ""):
        """Record a new error and the attempted fix.

        If the same error (same signature) is already present, it is not
        duplicated. fix_approach is an optional textual description
        of what was attempted (useful for the LLM prompt).

        Args:
            error: Full error text.
            fix_approach: Description of the attempted fix (e.g. "static fix attempt").
        """
        error_sig = self._signature(error)
        if error_sig not in self._errors:
            self._errors.append(error_sig)
            self._fixes.append(fix_approach)
        if len(self._errors) > self._max:
            self._errors.pop(0)
            self._fixes.pop(0)

    def is_repeated(self, error: str) -> bool:
        """Check if an error has already been seen before.

        Requires at least 2 occurrences to declare a repeat (avoids
        false positives from a single unsuccessful fix attempt).

        Args:
            error: Full error text to check.

        Returns:
            True if the same error (same signature) is already in history.
        """
        sig = self._signature(error)
        return sum(1 for e in self._errors if e == sig) >= 2

    def get_history_block(self) -> str:
        """Return a formatted text block for the LLM prompt.

        The block lists previous errors and attempted fixes, with
        the instruction "DO NOT REPEAT" to prevent infinite cycles.

        Returns:
            Formatted text with error history, or "No previous attempts."
        """
        if not self._errors:
            return "No previous attempts."
        lines = ["PREVIOUS ATTEMPTS (DO NOT REPEAT):"]
        for i, (err, fix) in enumerate(zip(self._errors, self._fixes), 1):
            lines.append(f"  {i}. Error: {err[:120]}")
            if fix:
                lines.append(f"     Attempted fix: {fix[:120]}")
        return "\n".join(lines)

    def clear(self):
        """Reset the history by deleting all recorded errors."""
        self._errors.clear()
        self._fixes.clear()

    @staticmethod
    def _signature(error: str) -> str:
        """Extract a unique signature from an error message.

        The signature is the first line containing "Error:" or "Exception:"
        (truncated to 150 characters). If no line matches, uses
        the last non-empty line. This allows grouping similar errors
        even if memory addresses or file paths vary.

        Args:
            error: Full error text.

        Returns:
            Signature string (max 150 chars).
        """
        lines = error.strip().splitlines()
        for line in lines:
            if "Error:" in line:
                return line.strip()[:150]
        for line in lines:
            if "Exception:" in line:
                return line.strip()[:150]
        for line in lines:
            if "line " in line and ("Error" in line or "invalid" in line or "cannot" in line):
                return line.strip()[:150]
        return lines[-1][:150] if lines else error[:150]


class OscillationDetector:
    """Detects oscillation patterns in pipeline iterations.

    Compares MD5 fingerprints of scripts produced at each iteration
    of the vision loop. If the script returns to an already seen state
    (cyclic pattern A→B→A→B or A→B→C→A→B→C), signals the oscillation.

    Args:
        max_history: Maximum number of snapshots to keep in memory.
    """

    def __init__(self, max_history: int = 5):
        """Initialize the detector with maximum capacity.

        Args:
            max_history: Number of recent snapshots to keep.
        """
        self._snapshots: List[str] = []
        self._max = max_history

    def add_snapshot(self, script: str):
        """Add the current snapshot (MD5 fingerprint of the script).

        Args:
            script: Full text of the script to track.
        """
        sig = self._signature(script)
        if not sig:
            return
        self._snapshots.append(sig)
        if len(self._snapshots) > self._max:
            self._snapshots.pop(0)

    def is_oscillating(self) -> bool:
        """Check if an oscillation is in progress (A→B→A→B or longer cycles).

        Checks period-2 patterns (A→B→A→B) with at least 4 snapshots,
        and period-3 patterns (A→B→C→A→B→C) with at least 6 snapshots.
        Excludes the case of stable convergence (A→A→A→A) which is not oscillation.

        Returns:
            True if an oscillation pattern has been detected.
        """
        if len(self._snapshots) < 4:
            return False
        if len(set(self._snapshots)) == 1:
            return False
        for period in range(2, 4):
            if len(self._snapshots) >= period * 2:
                pattern = self._snapshots[-period:]
                prev = self._snapshots[-(period * 2):-period]
                if pattern == prev:
                    return True
        return False

    @staticmethod
    def _signature(script: str) -> str:
        """Calculate the normalized MD5 fingerprint for robust comparison.

        Removes comments (only lines starting with #), redundant spaces
        and empty lines before computing the hash, so that cosmetic
        variations (comments, whitespace) do not prevent oscillation
        detection.

        Args:
            script: Script text.

        Returns:
            Hexadecimal MD5 fingerprint, or empty string if script is empty.
        """
        if not script:
            return ""
        lines = script.splitlines()
        cleaned = [l for l in lines if not l.strip().startswith("#")]
        norm = re.sub(r'\s+', ' ', " ".join(cleaned)).strip()
        return hashlib.md5(norm.encode()).hexdigest()
