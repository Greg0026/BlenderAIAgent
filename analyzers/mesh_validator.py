"""Validazione geometrica della mesh tramite bmesh snippet iniettato.

Usato nella fase F3.5 della pipeline (dopo StaticAnalyzer). Inietta
un blocco di codice bmesh alla fine dello script generato, lo esegue
in Blender, e analizza l'output per verificare la qualità della mesh.

Controlli effettuati dallo snippet iniettato:
  - Non-manifold edges (mesh non watertight → slicer la rifiuta)
  - Loose vertices (vertici liberi, residui Boolean)
  - Degenerate faces (facce con area ≈ 0)
  - Zero volume (mesh piana o vuota)
  - Nessun oggetto mesh nella scena
"""

from __future__ import annotations
from typing import Tuple, List


_VALIDATION_SNIPPET = '''

import bmesh as _bmesh

_val_issues: list = []
_mesh_objs = [_o for _o in bpy.data.objects if _o.type == "MESH"]

if not _mesh_objs:
    _val_issues.append("NO_MESH_OBJECTS")
else:
    _depsgraph = bpy.context.evaluated_depsgraph_get()

    for _obj in _mesh_objs:
        _eval = _obj.evaluated_get(_depsgraph)
        _mesh_eval = _eval.to_mesh()

        _bm = _bmesh.new()
        _bm.from_mesh(_mesh_eval)
        _bm.verts.ensure_lookup_table()
        _bm.edges.ensure_lookup_table()
        _bm.faces.ensure_lookup_table()

        _nm = [_e for _e in _bm.edges if not _e.is_manifold]
        if _nm:
            _val_issues.append(f"NON_MANIFOLD_EDGES:{_obj.name}:{len(_nm)}")

        _lv = [_v for _v in _bm.verts if not _v.link_edges]
        if _lv:
            _val_issues.append(f"LOOSE_VERTS:{_obj.name}:{len(_lv)}")

        _zf = [_f for _f in _bm.faces if _f.calc_area() < 1e-10]
        if _zf:
            _val_issues.append(f"DEGENERATE_FACES:{_obj.name}:{len(_zf)}")

        _vol = _bm.calc_volume()
        if _vol < 1e-12:
            _val_issues.append(f"ZERO_VOLUME:{_obj.name}")

        _bm.free()
        _eval.to_mesh_clear()

if _val_issues:
    print("MESH_VALIDATION_FAIL:" + "|".join(_val_issues))
else:
    print(f"MESH_VALIDATION_OK:checked_{len(_mesh_objs)}_objects")
'''


class MeshValidator:
    """Valida la mesh generata da uno script Blender.

    Inietta uno snippet bmesh che controlla manifoldness, vertici
    liberi, facce degeneri, volume e presenza di oggetti mesh.
    Il risultato è usato dall'orchestratore per decidere se passare
    alla fase successiva o iterare il fix.

    Args:
        runner: Istanza di BlenderRunner per l'esecuzione.
    """

    def __init__(self, runner) -> None:
        self.runner = runner

    async def validate(self, script: str) -> Tuple[bool, List[str]]:
        """Inietta lo snippet, esegue in Blender, analizza output.

        Args:
            script: Script Blender da validare (senza snippet).

        Returns:
            (True, []) se la mesh è valida.
            (False, [issues...]) se ci sono problemi geometrici.
        """
        combined = script + _VALIDATION_SNIPPET

        success, output = await self.runner.execute(combined)

        if not success:
            last_line = output.splitlines()[-1] if output else "output vuoto"
            return False, [f"EXEC_FAILED_DURING_VALIDATION:{last_line}"]

        for line in output.splitlines():
            line = line.strip()
            if line.startswith("MESH_VALIDATION_OK"):
                return True, []
            if line.startswith("MESH_VALIDATION_FAIL:"):
                raw = line.replace("MESH_VALIDATION_FAIL:", "")
                return False, [i for i in raw.split("|") if i]

        return False, ["VALIDATION_MARKERS_NOT_FOUND_IN_OUTPUT"]

    @staticmethod
    def format_issues_for_llm(issues: List[str]) -> str:
        """Converte le issues di validazione in un report leggibile dall'LLM.

        Args:
            issues: Lista di stringhe con i codici issue (es. NON_MANIFOLD_EDGES:obj:5).

        Returns:
            Testo formattato con descrizioni dettagliate per ogni issue.
        """
        lines = ["RAPPORTO VALIDAZIONE MESH (problemi che impediscono la stampa 3D):"]
        for issue in issues:
            if issue.startswith("NON_MANIFOLD_EDGES"):
                _, obj, count = issue.split(":")
                lines.append(
                    f"  NON-MANIFOLD: '{obj}' ha {count} edge aperti. "
                    "La mesh non è watertight -> slicer la rifiuterà o produrrà stampe fallate."
                )
            elif issue.startswith("LOOSE_VERTS"):
                _, obj, count = issue.split(":")
                lines.append(
                    f"  VERTICI LIBERI: '{obj}' ha {count} vertici non connessi. "
                    "Residui di operazioni booleane. Fix: bmesh.ops.delete + dissolve."
                )
            elif issue.startswith("DEGENERATE_FACES"):
                _, obj, count = issue.split(":")
                lines.append(
                    f"  FACCE DEGENERI: '{obj}' ha {count} facce con area ~= 0. "
                    "Fix: Weld modifier o bmesh.ops.remove_doubles."
                )
            elif issue.startswith("ZERO_VOLUME"):
                parts = issue.split(":", 1)
                obj = parts[1] if len(parts) > 1 else ""
                lines.append(
                    f"  VOLUME ZERO: '{obj}' non ha volume reale. "
                    "Mesh probabilmente piana o vuota."
                )
            elif issue == "NO_MESH_OBJECTS":
                lines.append(
                    "  NESSUN OGGETTO MESH nella scena. "
                    "Lo script è terminato senza creare geometria."
                )
            elif issue.startswith("EXEC_FAILED"):
                lines.append(f"  CRASH DURANTE VALIDAZIONE: {issue}")
            else:
                lines.append(f"  {issue}")
        return "\n".join(lines)
