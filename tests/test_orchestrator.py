"""Test per core/orchestrator.py — metodi sincroni interni."""

import pytest
from unittest.mock import patch, MagicMock
from core.orchestrator import Orchestrator


@pytest.fixture
def orch():
    """Crea un'istanza Orchestrator con mock per LLMClient."""
    with patch("core.orchestrator.LLMClient") as MockLLM, \
         patch("core.orchestrator.validate_api_keys") as MockValidate:
        mock_llm = MagicMock()
        MockLLM.return_value = mock_llm
        MockValidate.return_value = None

        class FakeDB:
            async def search(self, *a, **kw):
                return ""

        class FakeRunner:
            async def execute(self, script):
                return True, "MESH_VALIDATION_OK"

        instance = Orchestrator(FakeDB(), FakeRunner())
        # patch llm on instance too because orchestator.__init__ sets self.llm
        instance.llm = mock_llm
        yield instance


class TestExtractArchetype:
    def test_extracts_A(self, orch):
        assert orch._extract_archetype("ARCHETIPO: A -- rivoluzione") == "A"

    def test_extracts_B(self, orch):
        assert orch._extract_archetype("ARCHETIPO: B") == "B"

    def test_extracts_C(self, orch):
        assert orch._extract_archetype("ARCHETIPO:C") == "C"

    def test_returns_empty_when_not_found(self, orch):
        assert orch._extract_archetype("nessun archetipo qui") == ""

    def test_case_insensitive(self, orch):
        # _extract_archetype upperca la riga, quindi case-insensitive
        assert orch._extract_archetype("archetipo: D") == "D"

    def test_case_insensitive_lowercase(self, orch):
        assert orch._extract_archetype("archetipo: d") == "D"

    def test_multiple_lines(self, orch):
        text = "prima riga\nARCHETIPO: E -- ibrido\naltra riga"
        assert orch._extract_archetype(text) == "E"


class TestHasMeshOutput:
    def test_detects_validation_ok(self, orch):
        assert orch._has_mesh_output("import bpy\nMESH_VALIDATION_OK")

    def test_detects_validation_fail(self, orch):
        assert orch._has_mesh_output("import bpy\nMESH_VALIDATION_FAIL")

    def test_detects_print_ready_marker(self, orch):
        assert orch._has_mesh_output("import bpy\nprintready")

    def test_detects_stl_exported(self, orch):
        assert orch._has_mesh_output("STL_EXPORTED:/path/to/file.stl")

    def test_detects_bpy_data_objects(self, orch):
        assert orch._has_mesh_output("import bpy\nbpy.data.objects")

    def test_returns_false_when_script_has_error(self, orch):
        assert not orch._has_mesh_output("import bpy\nError: name 'x' is not defined\n")

    def test_returns_false_when_no_bpy_import(self, orch):
        assert not orch._has_mesh_output("")

    def test_returns_false_for_output_without_script(self, orch):
        assert not orch._has_mesh_output("Blender started\nRendering done\n")

    def test_case_insensitive_markers(self, orch):
        assert orch._has_mesh_output("import bpy\nmesh_validation_ok")

    def test_no_false_positive_with_error_and_signal(self, orch):
        assert not orch._has_mesh_output("import bpy\nbpy.data.objects\nTraceback: error")

    def test_detects_bpy_context_scene_objects(self, orch):
        assert orch._has_mesh_output("import bpy\nbpy.context.scene.objects")

    def test_render_ok_marker(self, orch):
        assert orch._has_mesh_output("RENDER_OK:/tmp/view_front.png")


class TestGenerateFallbackScript:
    def test_returns_string(self, orch):
        script = orch._generate_fallback_script("test prompt")
        assert isinstance(script, str)
        assert len(script) > 50

    def test_contains_import_bpy(self, orch):
        script = orch._generate_fallback_script("test")
        assert "import bpy" in script

    def test_contains_solidify_subsurf_weld(self, orch):
        script = orch._generate_fallback_script("test")
        assert "SOLIDIFY" in script or "Solidify" in script
        assert "SUBSURF" in script or "Subsurf" in script
        assert "WELD" in script or "Weld" in script

    def test_contains_validation_marker(self, orch):
        script = orch._generate_fallback_script("test")
        assert "MESH_VALIDATION_OK" in script

    def test_includes_prompt_hash_in_name(self, orch):
        script = orch._generate_fallback_script("unique_test_prompt")
        assert "PrintReady_" in script


@pytest.mark.asyncio
class TestArchetypeValidation:
    async def test_default_E_on_failure(self, orch):
        orch.llm = None  # force failure
        result = await orch._validate_archetype("test")
        assert result == "E"


class TestIsStuck:
    def test_not_stuck_with_fresh_context(self, orch):
        from core.orchestrator import PipelineContext
        ctx = PipelineContext("test")
        assert not orch._is_stuck(ctx, "Error: fresh error")

    def test_stuck_on_repeated_error(self, orch):
        from core.orchestrator import PipelineContext
        ctx = PipelineContext("test")
        orch.error_history.add("Error: repeated", "fix")
        assert orch._is_stuck(ctx, "Error: repeated")

    def test_stuck_on_oscillation(self, orch):
        from core.orchestrator import PipelineContext
        ctx = PipelineContext("test")
        ctx.oscillation.add_snapshot("A")
        ctx.oscillation.add_snapshot("B")
        ctx.oscillation.add_snapshot("A")
        ctx.oscillation.add_snapshot("B")
        assert orch._is_stuck(ctx, "Error: fresh error")
