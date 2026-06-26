import pytest
from utils.errors import ErrorHistory, OscillationDetector


class TestErrorHistory:
    def test_starts_empty(self):
        eh = ErrorHistory(max_history=10)
        assert eh._errors == []
        assert eh._fixes == []

    def test_add_records_error(self):
        eh = ErrorHistory()
        eh.add("Error: something went wrong", "tried fix A")
        assert len(eh._errors) == 1
        assert len(eh._fixes) == 1

    def test_is_repeated_detects_duplicate(self):
        eh = ErrorHistory()
        eh.add("Error: something went wrong")
        assert eh.is_repeated("Error: something went wrong")

    def test_is_repeated_returns_false_for_new_error(self):
        eh = ErrorHistory()
        eh.add("Error: first error")
        assert not eh.is_repeated("Error: second error")

    def test_get_history_block_returns_formatted_text(self):
        eh = ErrorHistory()
        assert eh.get_history_block() == "Nessun tentativo precedente."

        eh.add("Error: test error", "tried fix")
        block = eh.get_history_block()
        assert "TENTATIVI PRECEDENTI" in block
        assert "Error: test error" in block
        assert "tried fix" in block

    def test_clear_resets(self):
        eh = ErrorHistory()
        eh.add("Error: test")
        eh.clear()
        assert eh._errors == []
        assert eh._fixes == []

    def test_max_history_limit(self):
        eh = ErrorHistory(max_history=2)
        eh.add("Error: one")
        eh.add("Error: two")
        eh.add("Error: three")
        assert len(eh._errors) == 2
        assert "Error: one" not in eh._errors

    def test_signature_is_deterministic(self):
        sig1 = ErrorHistory._signature("Error: abc")
        sig2 = ErrorHistory._signature("Error: abc")
        assert sig1 == sig2

    def test_signature_extracts_error_line(self):
        sig = ErrorHistory._signature("line1\nError: division by zero\nline3")
        assert "Error: division by zero" in sig

    def test_signature_fallback_to_last_line(self):
        sig = ErrorHistory._signature("foo\nbar")
        assert sig == "bar"

    def test_same_error_not_duplicated(self):
        eh = ErrorHistory()
        eh.add("Error: dup")
        eh.add("Error: dup")
        assert len(eh._errors) == 1


class TestOscillationDetector:
    def test_starts_not_oscillating(self):
        od = OscillationDetector()
        assert not od.is_oscillating()

    def test_is_oscillating_detects_abab_pattern(self):
        od = OscillationDetector()
        od.add_snapshot("script A")
        od.add_snapshot("script B")
        od.add_snapshot("script A")
        od.add_snapshot("script B")
        assert od.is_oscillating()

    def test_not_oscillating_with_less_than_4_snapshots(self):
        od = OscillationDetector()
        od.add_snapshot("A")
        od.add_snapshot("B")
        od.add_snapshot("C")
        assert not od.is_oscillating()

    def test_not_oscillating_with_unique_scripts(self):
        od = OscillationDetector()
        od.add_snapshot("A")
        od.add_snapshot("B")
        od.add_snapshot("C")
        od.add_snapshot("D")
        assert not od.is_oscillating()

    def test_detect_cycle_three_period(self):
        od = OscillationDetector(max_history=10)
        od.add_snapshot("A")
        od.add_snapshot("B")
        od.add_snapshot("C")
        od.add_snapshot("A")
        od.add_snapshot("B")
        od.add_snapshot("C")
        assert od.is_oscillating()

    def test_add_snapshot_respects_max_history(self):
        od = OscillationDetector(max_history=3)
        od.add_snapshot("A")
        od.add_snapshot("B")
        od.add_snapshot("C")
        od.add_snapshot("D")
        assert len(od._snapshots) == 3
        assert "A" not in od._snapshots

    def test_signature_returns_md5(self):
        sig = OscillationDetector._signature("test script")
        assert len(sig) == 32
        assert all(c in "0123456789abcdef" for c in sig)

    def test_signature_empty_for_empty_script(self):
        assert OscillationDetector._signature("") == ""
