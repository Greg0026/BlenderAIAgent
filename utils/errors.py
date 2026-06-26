"""Error tracking and oscillation detection for the pipeline.

Provides two fundamental classes for pipeline stability:

ErrorHistory:
  Maintains the history of errors encountered in F6 and F6-VIS phases.
  Each error is registered with a "signature" that identifies
  the error type (based on the message, not the entire stack trace).
  The history is passed to the LLM in subsequent calls to prevent
  repeating already attempted fixes that were unsuccessful.

OscillationDetector:
  Detects oscillation patterns in pipeline iterations.
  Compares MD5 fingerprints of scripts produced at each iteration.
  If the script returns to a previously seen state (pattern A→B→A→B), detects
  the oscillation and allows the orchestrator to break the cycle.
"""

import hashlib
import re
from typing import List, Optional


class ErrorHistory:
    """Error history with repetition detection.

    Useful to prevent the pipeline from repeating the same fix
    indefinitely without success. Each error is converted to a "signature" extracted
    from the main message (Error: or Exception: line), ignoring
    variable parts such as memory addresses or timestamps.

    Args:
        max_history: Maximum number of errors to keep in history.
    """

    def __init__(self, max_history: int = 10):
        """Initializes an empty history with maximum capacity.

        Args:
            max_history: Maximum number of errors to track (FIFO).
        """
        self._errors: List[str] = []
        self._fixes: List[str] = []
        self._max = max_history

    def add(self, error: str, fix_approach: str = ""):
        """Registers a new error and the attempted fix.

        If the same error (same signature) is already present, it is not
        duplicated. The fix_approach is an optional textual description
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
        """Checks if an error has already been seen before.

        Args:
            error: Full error text to check.

        Returns:
            True if the same error (same signature) is already in the history.
        """
        return self._signature(error) in self._errors

    def get_history_block(self) -> str:
        """Returns a formatted text block for the LLM prompt.

        The block lists previous errors and attempted fixes, with
        the instruction "DO NOT REPEAT" to prevent infinite loops.

        Returns:
            Formatted text with error history, or "No previous attempts."
        """
        if not self._errors:
            return "No previous attempts."
        lines = ["PREVIOUS ATTEMPTS (DO NOT REPEAT):"]
        for i, (err, fix) in enumerate(zip(self._errors, self._fixes), 1):
            lines.append(f"  {i}. Error: {err[:120]}")
            if fix:
                lines.append(f"     Fix attempted: {fix[:120]}")
        return "\n".join(lines)

    def clear(self):
        """Resets the history by clearing all registered errors."""
        self._errors.clear()
        self._fixes.clear()

    @staticmethod
    def _signature(error: str) -> str:
        """Extracts a unique signature from an error message.

        The signature is the first line containing "Error:" or "Exception:"
        (truncated to 150 characters). If no line matches, uses
        the last non-empty line. This allows grouping similar errors
        even if memory addresses or file paths vary.

        Args:
            error: Full error text.

        Returns:
            Signature string (max 150 characters).
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
    of the vision loop. If the script returns to a previously seen state
    (cyclic pattern A→B→A→B or A→B→C→A→B→C), reports the oscillation.

    Args:
        max_history: Maximum number of snapshots to keep in memory.
    """

    def __init__(self, max_history: int = 5):
        """Initializes the detector with maximum capacity.

        Args:
            max_history: Number of recent snapshots to retain.
        """
        self._snapshots: List[str] = []
        self._max = max_history

    def add_snapshot(self, script: str):
        """Adds the current snapshot (MD5 fingerprint of the script).

        Args:
            script: Full script text to track.
        """
        sig = self._signature(script)
        if not sig:
            return
        self._snapshots.append(sig)
        if len(self._snapshots) > self._max:
            self._snapshots.pop(0)

    def is_oscillating(self) -> bool:
        """Checks if an oscillation is in progress (A→B→A→B pattern or longer cycles).

        Checks period-2 patterns (A→B→A→B) with at least 4 snapshots,
        and period-3 patterns (A→B→C→A→B→C) with at least 6 snapshots.

        Returns:
            True if an oscillatory pattern has been detected.
        """
        if len(self._snapshots) < 4:
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
        """Calculates a normalized MD5 fingerprint for robust comparison.

        Removes comments, redundant spaces, and empty lines before
        computing the hash, so that cosmetic variations (comments,
        whitespace) do not prevent oscillation detection.

        Args:
            script: Script text.

        Returns:
            Hexadecimal MD5 fingerprint, or empty string if script is empty.
        """
        if not script:
            return ""
        norm = re.sub(r'#.*', '', script)
        norm = re.sub(r'\s+', ' ', norm).strip()
        return hashlib.md5(norm.encode()).hexdigest()
