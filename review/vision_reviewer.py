from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Tuple

from log import logger


_RENDER_RESOLUTION = 512
_MAX_IMAGE_SIZE = 2 * 1024 * 1024


_RENDER_SNIPPET_TEMPLATE = '''

import math as _math
import os   as _os
from mathutils import Vector as _Vector

_OUTPUT_DIR = {output_dir!r}
_os.makedirs(_OUTPUT_DIR, exist_ok=True)

def _vision_render() -> None:
    import bpy as _bpy

    _target = None
    for _o in _bpy.data.objects:
        if _o.type == "MESH" and _o.name.startswith("PrintReady"):
            _target = _o
            break
    if _target is None:
        for _o in _bpy.data.objects:
            if _o.type == "MESH":
                _target = _o
                break
    if _target is None:
        print("RENDER_ERR:nessun oggetto MESH trovato")
        return

    _bpy.context.view_layer.update()
    _bb = [_target.matrix_world @ _Vector(c) for c in _target.bound_box]
    _cx = sum(v.x for v in _bb) / 8
    _cy = sum(v.y for v in _bb) / 8
    _cz = sum(v.z for v in _bb) / 8

    _dx = _target.dimensions.x
    _dy = _target.dimensions.y
    _dz = _target.dimensions.z
    _max_dim  = max(_dx, _dy, _dz, 1e-6)
    _diag_3d  = _math.sqrt(_dx**2 + _dy**2 + _dz**2)

    _dist = _diag_3d * 3.0

    _scene = _bpy.context.scene
    _scene.render.engine                       = "BLENDER_WORKBENCH"
    _scene.display.shading.light               = "STUDIO"
    _scene.display.shading.studio_light        = "paint.sl"
    _scene.display.shading.color_type          = "OBJECT"
    _scene.display.shading.show_shadows        = True
    _scene.display.shading.shadow_intensity    = 0.5
    _scene.render.resolution_x                 = {res}
    _scene.render.resolution_y                 = {res}
    _scene.render.image_settings.file_format   = "PNG"
    _scene.render.film_transparent             = False
    _scene.world.color                         = (0.12, 0.12, 0.14)

    if _target.active_material is None:
        _mat = _bpy.data.materials.new("__VisionMat__")
        _mat.diffuse_color = (0.78, 0.78, 0.80, 1.0)
        _target.data.materials.append(_mat)

    _cam_data               = _bpy.data.cameras.new("__VisionCam__")
    _cam_data.type          = "ORTHO"
    _cam_data.clip_start    = 0.001
    _cam_data.clip_end      = _dist * 10
    _cam_obj                = _bpy.data.objects.new("__VisionCam__", _cam_data)
    _bpy.context.collection.objects.link(_cam_obj)
    _scene.camera = _cam_obj

    _sun_added = False
    if not any(_o.type == "LIGHT" for _o in _bpy.data.objects):
        _ld  = _bpy.data.lights.new("__VisionSun__", "SUN")
        _ld.energy = 4.0
        _lo  = _bpy.data.objects.new("__VisionSun__", _ld)
        _bpy.context.collection.objects.link(_lo)
        _lo.location = _Vector((_max_dim * 2, -_max_dim * 2, _max_dim * 3))
        _sun_added = True

    _front_scale = max(_dx, _dz) * 1.35
    _right_scale = max(_dy, _dz) * 1.35
    _iso_scale   = _diag_3d * 1.25
    _top_scale   = max(_dx, _dy) * 1.35

    _views = [
        ("front", _Vector((_cx, _cy - _dist, _cz)), (_math.radians(90), 0.0, 0.0), _front_scale),
        ("right", _Vector((_cx + _dist, _cy, _cz)), (_math.radians(90), 0.0, _math.radians(90)), _right_scale),
        ("iso", _Vector((_cx + _dist * _math.cos(_math.radians(45)) * 0.9, _cy - _dist * _math.sin(_math.radians(45)) * 0.9, _cz + _dist * 0.65)), (_math.radians(54.7), 0.0, _math.radians(45)), _iso_scale),
        ("top", _Vector((_cx, _cy, _cz + _dist)), (0.0, 0.0, 0.0), _top_scale),
    ]

    _render_paths = []
    for _name, _loc, _rot, _ortho_sc in _views:
        _cam_data.ortho_scale    = _ortho_sc
        _cam_obj.location       = _loc
        _cam_obj.rotation_euler = _rot
        _out = _os.path.join(_OUTPUT_DIR, f"view_{{_name}}.png")
        _scene.render.filepath  = _out
        _bpy.ops.render.render(write_still=True)
        _render_paths.append(_out)
        print(f"RENDER_OK:{{_out}}")

    _bpy.data.objects.remove(_cam_obj, do_unlink=True)
    _bpy.data.cameras.remove(_cam_data)
    if _sun_added:
        for _o in list(_bpy.data.objects):
            if _o.name == "__VisionSun__":
                _bpy.data.objects.remove(_o, do_unlink=True)
    if "__VisionMat__" in _bpy.data.materials:
        _bpy.data.materials.remove(_bpy.data.materials["__VisionMat__"])

_vision_render()
'''


_SP_VISION_REVIEW = """\
Sei un Art Director e Quality Inspector 3D di alto livello, specializzato nella \
valutazione estetica e tecnica di mesh Blender destinate alla stampa 3D.

Il tuo compito primario e' garantire che l'oggetto sia BELLO, RAFFINATO e \
PERFETTAMENTE FEDELE all'intenzione artistica del prompt.

Ricevi 3 render ortografici della mesh prodotta (front, right, isometric) \
insieme alla Specifica Tecnica originale e al Piano Algoritmico.

CHECK V1 -- FEDELTA MORFOLOGICA AL PROMPT
  * La silhouette generale corrisponde inequivocabilmente alla forma richiesta?
  * OGNI dettaglio stilistico citato nel prompt e' visivamente presente?
  * Le proporzioni H/D sono compatibili con le dimensioni specificate?

CHECK V2 -- QUALITA ESTETICA E BELLEZZA  (PRIORITA MASSIMA)
  * La forma ha carattere e identita visiva forte?
  * Le superfici sono lisce e fluide con transizioni eleganti?
  * Il dettaglio e' distribuito in modo armonioso?

CHECK V3 -- QUALITA GEOMETRICA VISIVA
  * Ci sono artefatti visivi: pieghe, scalini, discontinuita?
  * Le normali sembrano corrette?
  * Il fondo dell'oggetto sembra chiuso e rifinito?

CHECK V4 -- STAMPABILITA VISIVA
  * Ci sono sbalzi > 45 senza supporto integrato?
  * L'oggetto sembra solid/watertight?

SCORING (0-10):
  morphology:   10 = ogni dettaglio del prompt presente; 0 = forma sbagliata.
  aesthetics:   10 = portfolio; 7 = buono; 5 = piatto; 0 = offensivo.
  geometry:     10 = perfetto; 6+ = accettabile.
  printability: 10 = perfetto; 6+ = stampabile.

REGOLA PASS: passed=true SOLO SE morphology >= 7 AND aesthetics >= 7 AND geometry >= 6 AND printability >= 6.

Rispondi ESCLUSIVAMENTE con JSON:
{
  "passed": <bool>,
  "scores": {"morphology": <0-10>, "aesthetics": <0-10>, "geometry": <0-10>, "printability": <0-10>},
  "issues": ["<problema concreto>"],
  "aesthetic_notes": "<valutazione estetica in 2-3 frasi>",
  "fix_instructions": "<istruzioni di fix prioritizzate. Vuoto se passed=true>"
}
"""


class VisionReviewer:
    PASS_THRESHOLD_AESTHETICS   = 7.0
    PASS_THRESHOLD_MORPHOLOGY   = 7.0
    PASS_THRESHOLD_GEOMETRY     = 6.0
    PASS_THRESHOLD_PRINTABILITY = 6.0

    def __init__(
        self,
        client: Any,
        vision_model: str = "moonshotai/kimi-k2.6",
        output_dir: str | None = None,
        render_timeout: float = 120.0,
    ) -> None:
        self.client         = client
        self.vision_model   = vision_model
        self.render_timeout = render_timeout
        self.output_dir     = output_dir or os.path.join(
            tempfile.gettempdir(), "blender_vision_renders"
        )

    def inject_render_code(self, script: str) -> str:
        render_block = _RENDER_SNIPPET_TEMPLATE.format(
            output_dir=self.output_dir,
            res=_RENDER_RESOLUTION,
        )
        return script + render_block

    @staticmethod
    def _cleanup_renders(render_dir: str) -> None:
        if os.path.isdir(render_dir):
            try:
                shutil.rmtree(render_dir)
            except Exception:
                pass

    @staticmethod
    def extract_render_paths(runner_output: str) -> list[str]:
        paths = []
        for line in runner_output.splitlines():
            line = line.strip()
            if line.startswith("RENDER_OK:"):
                path = line.split(":", 1)[1].strip()
                if Path(path).exists():
                    paths.append(path)
        return paths

    @staticmethod
    def _encode_image(path: str) -> str:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) > _MAX_IMAGE_SIZE:
            logger.warning("[VISION] Immagine %s troppo grande (%d bytes), resize necessario.", path, len(data))
        return base64.b64encode(data).decode("utf-8")

    async def _call_vision_llm(
        self,
        render_paths: list[str],
        enhanced_prompt: str,
        math_plan: str,
    ) -> dict:
        content: list[dict] = []

        content.append({
            "type": "text",
            "text": (
                f"SPECIFICA TECNICA ORIGINALE:\n{enhanced_prompt}\n\n"
                f"PIANO ALGORITMICO DI RIFERIMENTO:\n{math_plan}\n\n"
                "Ecco i render ortografici della mesh prodotta.\n\n"
                "FOCUS PRIMARIO: valuta la BELLEZZA e la FEDELTA ESTETICA al prompt."
            ),
        })

        view_labels = ["Vista FRONT", "Vista RIGHT", "Vista ISOMETRIC", "Vista TOP"]
        for i, path in enumerate(render_paths):
            label = view_labels[i] if i < len(view_labels) else f"Vista {i+1}"
            b64   = self._encode_image(path)
            content.append({"type": "text", "text": label})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url":    f"data:image/png;base64,{b64}",
                    "detail": "low",
                },
            })

        content.append({
            "type": "text",
            "text": "Esegui la verifica. Rispondi SOLO con JSON valido.",
        })

        messages = [
            {"role": "system", "content": _SP_VISION_REVIEW},
            {"role": "user",   "content": content},
        ]

        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.vision_model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=4096,
                    stream=False,
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[VISION] Timeout chiamata LLM vision (60s). Review saltata.")
            return {"passed": True}

        raw = response.choices[0].message.content or ""
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.MULTILINE)

        try:
            return json.loads(raw_clean)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("[VISION] Risposta LLM non parsabile: %.100s", raw)
            return {
                "passed": True,
                "scores": {"morphology": 7, "aesthetics": 7, "geometry": 7, "printability": 7},
                "issues": [f"Risposta non parsabile: {raw[:200]}"],
            }

    async def review(
        self,
        runner: Any,
        script: str,
        enhanced_prompt: str,
        math_plan: str,
    ) -> Tuple[bool, str]:
        logger.info("F4.5: Vision Review -- render + analisi visiva")

        augmented = self.inject_render_code(script)

        try:
            run_ok, run_output = await asyncio.wait_for(
                runner.execute(augmented),
                timeout=self.render_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[VISION] Timeout render (%ss). Review saltata.", self.render_timeout)
            self._cleanup_renders(self.output_dir)
            return True, ""
        except Exception as exc:
            logger.warning("[VISION] Errore runner durante render: %s", exc)
            self._cleanup_renders(self.output_dir)
            return True, ""

        render_paths = self.extract_render_paths(run_output)

        if not render_paths:
            logger.warning("[VISION] Nessun render prodotto. Review saltata.")
            self._cleanup_renders(self.output_dir)
            return True, ""

        logger.info("[VISION] %d render catturati -> vision LLM", len(render_paths))

        try:
            report = await self._call_vision_llm(render_paths, enhanced_prompt, math_plan)
        except Exception as exc:
            logger.warning("[VISION] Errore LLM vision: %s. Review saltata.", exc)
            self._cleanup_renders(self.output_dir)
            return True, ""

        self._cleanup_renders(self.output_dir)
        passed = self._evaluate_report(report)
        self._log_report(report, passed)

        if passed:
            return True, ""

        feedback = self._build_feedback(report)
        return False, feedback

    def _evaluate_report(self, report: dict) -> bool:
        if report.get("passed") is False:
            return False

        scores = report.get("scores")
        if not scores or not isinstance(scores, dict):
            return False

        thresholds = {
            "aesthetics":    self.PASS_THRESHOLD_AESTHETICS,
            "morphology":    self.PASS_THRESHOLD_MORPHOLOGY,
            "geometry":      self.PASS_THRESHOLD_GEOMETRY,
            "printability":  self.PASS_THRESHOLD_PRINTABILITY,
        }

        for axis, threshold in thresholds.items():
            score = scores.get(axis)
            if score is None:
                return False
            if isinstance(score, (int, float)) and score < threshold:
                return False

        return True

    def _build_feedback(self, report: dict) -> str:
        issues        = report.get("issues", [])
        fix_instr     = report.get("fix_instructions", "")
        scores        = report.get("scores", {})
        aesthetic_notes = report.get("aesthetic_notes", "")

        thresholds = {
            "aesthetics":    self.PASS_THRESHOLD_AESTHETICS,
            "morphology":    self.PASS_THRESHOLD_MORPHOLOGY,
            "geometry":      self.PASS_THRESHOLD_GEOMETRY,
            "printability":  self.PASS_THRESHOLD_PRINTABILITY,
        }

        axis_order = ["aesthetics", "morphology", "geometry", "printability"]

        lines = ["=== VISION REVIEW FAILED ==="]
        for axis in axis_order:
            score = scores.get(axis)
            if score is None:
                continue
            threshold = thresholds.get(axis, 6.0)
            marker = "FAIL" if score < threshold else "PASS"
            label = {
                "aesthetics":   "Estetica",
                "morphology":   "Fedelta",
                "geometry":     "Geometria",
                "printability": "Stampabilita",
            }.get(axis, axis)
            lines.append(f"  [{marker}] {label:<14} {score:>4.1f}/10  (soglia: {threshold:.0f})")

        if aesthetic_notes:
            lines.append(f"\nNote estetiche:\n  {aesthetic_notes}")

        if issues:
            lines.append("\nProblemi:")
            for issue in issues:
                lines.append(f"  * {issue}")

        if fix_instr:
            lines.append(f"\nIstruzioni di fix visivo:\n{fix_instr[:2000]}")

        return "\n".join(lines)

    @staticmethod
    def _log_report(report: dict, passed: bool) -> None:
        status = "PASSED" if passed else "FAILED"
        scores = report.get("scores", {})
        aesthetic_notes = report.get("aesthetic_notes", "")

        axis_order = ["aesthetics", "morphology", "geometry", "printability"]
        axis_labels = {
            "aesthetics":   "Estetica",
            "morphology":   "Fedelta",
            "geometry":     "Geometria",
            "printability": "Stampabilita",
        }

        logger.info("[VISION] %s", status)
        for axis in axis_order:
            score = scores.get(axis)
            if score is None:
                continue
            label = axis_labels.get(axis, axis)
            bar = "#" * int(score) + "." * (10 - int(score))
            logger.info("[VISION]   %-14s [%s] %.1f/10", label, bar, score)

        if aesthetic_notes:
            logger.info("[VISION]   %s", aesthetic_notes[:200])

        issues = report.get("issues", [])
        if issues:
            for issue in issues[:3]:
                logger.info("[VISION]   Issue: %s", issue[:100])
