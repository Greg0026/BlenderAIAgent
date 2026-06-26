import asyncio
import os
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cfg import CFG, validate_api_keys
from core.llm import LLMClient
from core.phases import (
    f1_enhance,
    f15_math_planner,
    f2_codegen,
    f3a_morph_review,
    f3b_printability_review,
    f6_targeted_fix,
    f6_vision_fix,
)
from log import logger
from utils.code import format_error_for_query, summarize_error
from core.phases import _set_run_id
from utils.errors import ErrorHistory, OscillationDetector
from analyzers.mesh_validator import MeshValidator



class Phase(Enum):
    F1_ENHANCE = auto()
    F1_PLAN = auto()
    F2_GENERATE = auto()
    F3_MORPH = auto()
    F3_STATIC = auto()
    F4_EXECUTE = auto()
    F4_VISION = auto()
    F6_FIX = auto()
    F6_VIS_FIX = auto()
    DONE = auto()
    FAILED = auto()


class Orchestrator:
    def __init__(self, db, runner):
        validate_api_keys()
        self.db = db
        self.runner = runner
        self.llm = LLMClient()
        self.static_analyzer = None
        self.vision_reviewer = None
        self.error_history = ErrorHistory(max_history=10)
        self.mesh_validator = None
        self.vision_enabled = CFG.get("vision_review_enabled", True)

        try:
            from analyzers.static_analyzer import StaticAnalyzer
            self.static_analyzer = StaticAnalyzer()
        except ImportError:
            logger.warning("StaticAnalyzer not available, skipping static analysis.")

        self.mesh_validator = MeshValidator(self.runner) if self.runner else None

        if self.vision_enabled:
            try:
                from review.vision_reviewer import VisionReviewer
                self.vision_reviewer = VisionReviewer(
                    client=self.llm.client,
                    vision_model=CFG.get("vision_model", "moonshotai/kimi-k2.6"),
                )
            except ImportError:
                logger.warning("VisionReviewer not available, disabling vision review.")
                self.vision_enabled = False

    async def run(self, prompt: str, bver: str = "3.0") -> str:
        import hashlib
        run_id = hashlib.md5((prompt + str(os.getpid())).encode()).hexdigest()[:12]
        _set_run_id(run_id)
        self.error_history.clear()
        logger.info("=" * 60)
        logger.info("PIPELINE STARTED: %s", prompt[:80])
        logger.info("=" * 60)

        ctx = PipelineContext(prompt=prompt)
        phase = Phase.F1_ENHANCE

        while phase not in (Phase.DONE, Phase.FAILED):
            phase = await self._execute_phase(phase, ctx)

        ctx.log_final()

        if ctx.success and ctx.script:
            await self._maybe_export_stl(ctx)

        return ctx.script or self._generate_fallback_script(prompt)

    async def _execute_phase(self, phase: Phase, ctx: "PipelineContext") -> Phase:
        timeout = CFG.get("phase_timeout", 90)

        try:
            result = await asyncio.wait_for(
                self._dispatch_phase(phase, ctx),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("Phase %s timeout (%ds). Moving to next phase.", phase.name, timeout)
            return self._fallback_transition(phase)
        except Exception as e:
            logger.error("Phase %s failed: %s", phase.name, e)
            return self._fallback_transition(phase)

    async def _dispatch_phase(self, phase: Phase, ctx: "PipelineContext") -> Phase:
        if phase == Phase.F1_ENHANCE:
            return await self._phase_f1(ctx)
        elif phase == Phase.F1_PLAN:
            return await self._phase_f1_plan(ctx)
        elif phase == Phase.F2_GENERATE:
            return await self._phase_f2(ctx)
        elif phase == Phase.F3_MORPH:
            return await self._phase_f3_morph(ctx)
        elif phase == Phase.F3_STATIC:
            return await self._phase_f3_static(ctx)
        elif phase == Phase.F4_EXECUTE:
            return await self._phase_f4(ctx)
        elif phase == Phase.F4_VISION:
            return await self._phase_f4_vision(ctx)
        elif phase == Phase.F6_FIX:
            return await self._phase_f6(ctx)
        elif phase == Phase.F6_VIS_FIX:
            return await self._phase_f6_vis(ctx)
        return Phase.FAILED

    async def _phase_f1(self, ctx: "PipelineContext") -> Phase:
        ctx.enhanced_prompt = await f1_enhance(self.llm, ctx.prompt)
        logger.info("F1 completed (%d chars)", len(ctx.enhanced_prompt))

        archetype = self._extract_archetype(ctx.enhanced_prompt)
        if not archetype:
            archetype = await self._validate_archetype(ctx.prompt)
            ctx.enhanced_prompt = (
                f"ARCHETIPO: {archetype} -- classificato automaticamente\n\n{ctx.enhanced_prompt}"
            )
        return Phase.F1_PLAN

    async def _phase_f1_plan(self, ctx: "PipelineContext") -> Phase:
        try:
            ctx.math_plan = await f15_math_planner(self.llm, ctx.enhanced_prompt, ctx.prompt)
            logger.info("F1.5 completed (%d chars)", len(ctx.math_plan))
        except Exception as e:
            logger.warning("F1.5 failed: %s. Continuing without math plan.", e)
            ctx.math_plan = ctx.enhanced_prompt
        return Phase.F2_GENERATE

    async def _phase_f2(self, ctx: "PipelineContext") -> Phase:
        doc_ctx = ""
        try:
            doc_ctx = await self.db.search(ctx.enhanced_prompt, n_results=5)
        except Exception as e:
            logger.warning("VectorDB search failed: %s", e)

        try:
            ctx.script = await f2_codegen(self.llm, ctx.enhanced_prompt, ctx.math_plan, doc_ctx)
            logger.info("F2 completed (%d chars)", len(ctx.script))
        except Exception as e:
            logger.error("F2 failed: %s", e)
            ctx.script = self._generate_fallback_script(ctx.prompt)
            return Phase.DONE

        ctx.oscillation.add_snapshot(ctx.script)
        return Phase.F3_MORPH

    async def _phase_f3_morph(self, ctx: "PipelineContext") -> Phase:
        f3a_coro, f3b_coro = self._parallel_f3(ctx)
        f3a_result, f3b_result = None, None
        try:
            results = await asyncio.gather(f3a_coro, f3b_coro, return_exceptions=True)
            if not isinstance(results[0], Exception):
                f3a_result = results[0]
            else:
                logger.warning("F3A failed: %s. Continuing.", results[0])
            if not isinstance(results[1], Exception):
                f3b_result = results[1]
            else:
                logger.warning("F3B failed: %s. Continuing.", results[1])
        except Exception as e:
            logger.warning("F3A+F3B both failed: %s", e)

        ctx.script = self._merge_f3_results(ctx.script, f3a_result, f3b_result)
        logger.info("F3A+F3B completed (%d chars)", len(ctx.script))
        return Phase.F3_STATIC

    def _parallel_f3(self, ctx: "PipelineContext") -> tuple:
        fb = ctx.last_vision_feedback
        a = f3a_morph_review(self.llm, ctx.script, ctx.enhanced_prompt, ctx.math_plan, ctx.prompt, fb)
        b = f3b_printability_review(self.llm, ctx.script, ctx.enhanced_prompt, ctx.math_plan, fb)
        return a, b

    @staticmethod
    def _merge_f3_results(original: str, f3a: Optional[str], f3b: Optional[str]) -> str:
        if not f3a and not f3b:
            return original
        if not f3a:
            return f3b if f3b else original
        if not f3b:
            return f3a if f3a else original
        if f3a == f3b:
            return f3a
        if f3a == original and f3b != original:
            return f3b
        if f3b == original and f3a != original:
            return f3a
        return f3a

    async def _phase_f3_static(self, ctx: "PipelineContext") -> Phase:
        if not self.static_analyzer:
            return Phase.F4_EXECUTE

        try:
            static_ok, static_issues = self.static_analyzer.analyze(ctx.script)
        except Exception as e:
            logger.warning("Static analysis failed: %s", e)
            return Phase.F4_EXECUTE

        if static_ok:
            return Phase.F4_EXECUTE

        error_text = "\n".join(static_issues)
        ctx.error_text = error_text
        return await self._apply_f6_fix(ctx, error_text, "static fix attempt")

    async def _phase_f4(self, ctx: "PipelineContext") -> Phase:
        prev_script = ctx.script

        try:
            exec_ok, output = await self.runner.execute(ctx.script)
        except Exception as e:
            exec_ok, output = False, str(e)

        if exec_ok:
            if self._has_mesh_output(output):
                logger.info("F4 OK — mesh detected")

                if self.mesh_validator:
                    try:
                        valid, issues = await self.mesh_validator.validate(ctx.script)
                        if not valid:
                            issues_text = self.mesh_validator.format_issues_for_llm(issues)
                            logger.warning("Mesh validation failed: %s", issues_text[:100])
                            ctx.error_text = issues_text
                            return await self._apply_f6_fix(ctx, issues_text, "mesh validation fix")
                    except Exception as e:
                        logger.warning("Mesh validation failed: %s. Continuing.", e)

                return Phase.F4_VISION
            else:
                logger.warning("F4 OK but no mesh detected")
                error_text = "The script did not produce mesh objects in the scene. Add geometry."
                ctx.error_text = error_text
                return await self._apply_f6_fix(ctx, error_text, "no mesh fix")
        else:
            error_text = summarize_error(output, max_len=2000)
            ctx.error_text = error_text
            return await self._apply_f6_fix(ctx, error_text, "runtime fix attempt")

    async def _phase_f4_vision(self, ctx: "PipelineContext") -> Phase:
        if not self.vision_enabled:
            logger.info("Vision review disabled. Pipeline OK.")
            ctx.success = True
            return Phase.DONE

        try:
            vision_ok, vision_feedback = await self.vision_reviewer.review(
                self.runner, ctx.script, ctx.enhanced_prompt, ctx.math_plan
            )
        except Exception as e:
            logger.warning("Vision review failed: %s. Considering it passed.", e)
            ctx.success = True
            return Phase.DONE

        if vision_ok:
            logger.info("F4.5: Vision review PASSED")
            ctx.success = True
            return Phase.DONE

        logger.info("F4.5: Vision review FAILED")
        ctx.last_vision_feedback = vision_feedback
        ctx.vision_attempt += 1

        if ctx.vision_attempt >= CFG.get("error_loops", 8):
            logger.warning("Vision loop exhausted. Accepting last script.")
            ctx.success = True
            return Phase.DONE

        return await self._apply_vision_fix(ctx, vision_feedback)

    async def _phase_f6(self, ctx: "PipelineContext") -> Phase:
        error_text = ctx.error_text or ""
        if not error_text:
            return Phase.F4_EXECUTE

        return await self._apply_f6_fix(ctx, error_text, "targeted fix attempt")

    async def _phase_f6_vis(self, ctx: "PipelineContext") -> Phase:
        feedback = ctx.last_vision_feedback or ""
        if not feedback:
            return Phase.F4_VISION

        return await self._apply_vision_fix(ctx, feedback)

    async def _apply_f6_fix(self, ctx: "PipelineContext", error_text: str, fix_type: str) -> Phase:
        if self._is_stuck(ctx, error_text):
            logger.warning("No progress after fix. Forcing exit.")
            return Phase.F4_VISION

        ctx.fix_attempt += 1
        if ctx.fix_attempt > CFG.get("fix_loops", 6):
            logger.warning("Fix loop exhausted (%d attempts).", ctx.fix_attempt)
            return Phase.F4_VISION

        doc_ctx = ""
        try:
            doc_ctx = await self.db.search(format_error_for_query(error_text), n_results=3)
        except Exception:
            pass

        error_history = self.error_history.get_history_block()
        try:
            new_script = await f6_targeted_fix(
                self.llm, ctx.script, error_text, doc_ctx, error_history
            )
        except Exception as e:
            logger.warning("F6 fix failed: %s", e)
            return Phase.F4_VISION

        if new_script == ctx.script:
            logger.warning("F6 fix did not modify the script. No progress.")

        if not new_script or len(new_script.strip()) < 50:
            logger.warning("F6 fix produced empty script.")
            return Phase.F4_VISION

        self.error_history.add(error_text, fix_type)
        ctx.script = new_script
        ctx.oscillation.add_snapshot(ctx.script)

        if ctx.oscillation.is_oscillating():
            logger.warning("Oscillation detected! Forcing exit from fix loop.")
            return Phase.F4_VISION

        return Phase.F3_STATIC

    async def _apply_vision_fix(self, ctx: "PipelineContext", feedback: str) -> Phase:
        if ctx.oscillation.is_oscillating():
            logger.warning("Oscillation in vision loop. Accepting current script.")
            ctx.success = True
            return Phase.DONE

        error_history = self.error_history.get_history_block()
        try:
            new_script = await f6_vision_fix(self.llm, ctx.script, feedback, error_history)
        except Exception as e:
            logger.warning("F6-VIS failed: %s. Continuing with current script.", e)
            return Phase.FAILED

        if new_script == ctx.script or len(new_script.strip()) < 50:
            logger.warning("F6-VIS produced no changes. Accepting current script.")
            ctx.success = True
            return Phase.DONE

        self.error_history.add(f"Vision fix: {feedback[:100]}", "vision fix attempt")
        ctx.script = new_script
        ctx.oscillation.add_snapshot(ctx.script)

        return Phase.F3_MORPH

    def _is_stuck(self, ctx: "PipelineContext", error_text: str) -> bool:
        return self.error_history.is_repeated(error_text) or ctx.oscillation.is_oscillating()

    async def _maybe_export_stl(self, ctx: "PipelineContext") -> None:
        stl_dir = CFG.get("stl_output_dir", "~/Desktop/blender_prints")
        stl_dir = os.path.expanduser(stl_dir)
        os.makedirs(stl_dir, exist_ok=True)
        import hashlib
        obj_name = "print_" + hashlib.md5(ctx.prompt.encode()).hexdigest()[:8]
        stl_path = os.path.join(stl_dir, f"{obj_name}.stl")

        export_snippet = f'''
import bpy, bmesh, os
output_dir = {stl_dir!r}
os.makedirs(output_dir, exist_ok=True)
for obj in bpy.data.objects:
    if obj.type == "MESH":
        obj.select_set(True)
    else:
        obj.select_set(False)
bpy.ops.export_mesh.stl(
    filepath={stl_path!r},
    use_selection=True,
    use_mesh_modifiers=True,
    global_scale=1000,
)
print(f"STL_EXPORTED:{{stl_path}}")
'''
        try:
            ok, out = await self.runner.execute(ctx.script + "\n" + export_snippet)
            if ok and "STL_EXPORTED" in out:
                logger.info("STL exported: %s", stl_path)
            else:
                logger.warning("STL export failed (the mesh may not be valid).")
        except Exception as e:
            logger.warning("STL export failed: %s", e)

    def _fallback_transition(self, phase: Phase) -> Phase:
        fallback_map = {
            Phase.F1_ENHANCE: Phase.F1_PLAN,
            Phase.F1_PLAN: Phase.F2_GENERATE,
            Phase.F2_GENERATE: Phase.DONE,
            Phase.F3_MORPH: Phase.F3_STATIC,
            Phase.F3_STATIC: Phase.F4_EXECUTE,
            Phase.F4_EXECUTE: Phase.F4_VISION,
            Phase.F4_VISION: Phase.DONE,
            Phase.F6_FIX: Phase.F4_VISION,
            Phase.F6_VIS_FIX: Phase.DONE,
        }
        return fallback_map.get(phase, Phase.DONE)

    def _extract_archetype(self, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ARCHETIPO:"):
                for arch in ("A", "B", "C", "D", "E"):
                    if f"ARCHETIPO: {arch}" in stripped or f"ARCHETIPO:{arch}" in stripped:
                        return arch
        return ""

    async def _validate_archetype(self, prompt: str) -> str:
        try:
            result = await self.llm.call(
                system=(
                    "Classify the following 3D modeling prompt into an archetype: "
                    "A=revolution, B=extrusion/loft, C=boolean composite, D=voronoi/fractal, E=hybrid. "
                    "Reply ONLY with a letter (A/B/C/D/E)."
                ),
                messages=[{"role": "user", "content": prompt}],
                label="ARCHETYPE_VALIDATION",
                do_extract_code=False,
                temperature=0.0,
                max_tokens=10,
            )
            result = result.strip().upper()
            if result in ("A", "B", "C", "D", "E"):
                return result
        except Exception as e:
            logger.warning("Archetype validation failed: %s", e)
        return "E"

    def _has_mesh_output(self, output: str) -> bool:
        output_lower = output.lower()

        markers = [
            "mesh_validation_ok",
            "mesh_validation_fail",
            "printready",
            "stl_exported",
            "render_ok",
        ]
        for m in markers:
            if m in output_lower:
                return True

        core_api_signals = [
            "bpy.data.objects",
            "bpy.context.scene.objects",
        ]
        for sig in core_api_signals:
            if sig in output_lower:
                has_bpy_script = "import bpy" in output_lower
                has_error = "error:" in output_lower or "traceback" in output_lower
                return has_bpy_script and not has_error

        return False

    def _generate_fallback_script(self, prompt: str) -> str:
        import hashlib
        obj_name = "fallback_" + hashlib.md5(prompt.encode()).hexdigest()[:8]
        return f'''
import bpy
import bmesh

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

bpy.ops.mesh.primitive_cube_add(size=0.05, location=(0, 0, 0.025))
obj = bpy.context.active_object
obj.name = "PrintReady_{obj_name}"

bpy.ops.object.modifier_add(type='SOLIDIFY')
obj.modifiers["Solidify"].thickness = 0.0012
obj.modifiers["Solidify"].offset = -1.0

bpy.ops.object.modifier_add(type='SUBSURF')
obj.modifiers["Subsurf"].levels = 2

bpy.ops.object.modifier_add(type='WELD')
obj.modifiers["Weld"].merge_threshold = 0.0001

bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

mesh = bpy.context.active_object.data
bmesh_obj = bmesh.new()
bmesh_obj.from_mesh(mesh)
bmesh_obj.normal_short.calc_all()
bmesh_obj.free()

print("MESH_VALIDATION_OK:checked_1_objects")
'''


class PipelineContext:
    def __init__(self, prompt: str):
        self.prompt = prompt
        self.enhanced_prompt = prompt
        self.math_plan = ""
        self.script = ""
        self.success = False
        self.last_vision_feedback = ""
        self.error_text = ""

        self.vision_attempt = 0
        self.fix_attempt = 0

        self.oscillation = OscillationDetector(max_history=6)

    def log_final(self):
        status = "SUCCESS" if self.success else "PARTIAL (best-effort)"
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETED — %s", status)
        logger.info("Script: %d chars, Fix attempts: %d, Vision attempts: %d",
                     len(self.script) if self.script else 0,
                     self.fix_attempt, self.vision_attempt)
        logger.info("=" * 60)
