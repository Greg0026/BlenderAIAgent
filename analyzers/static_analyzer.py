import ast
from typing import List, Set, Tuple

from cfg import CFG


class StaticAnalyzer:
    DANGEROUS_MODULES = {"subprocess", "socket", "urllib", "requests", "shutil"}

    DANGEROUS_OS_CALLS: Set[str] = {
        "system", "popen", "execv", "execl", "execle", "execlp", "execlpe",
        "execvp", "execvpe", "spawnl", "spawnv", "spawnle", "spawnve",
        "popen2", "popen3", "popen4", "startfile",
    }

    DANGEROUS_BUILTINS: Set[str] = {"eval", "exec", "__import__", "compile"}

    MIN_WALL_THICKNESS_M = 0.0012

    def __init__(self):
        self.MAX_LEN = CFG.get("static_max_len", 40000)

    @staticmethod
    def _has_scene_reset(tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                chain = []
                while isinstance(func, ast.Attribute):
                    chain.append(func.attr)
                    func = func.value
                if isinstance(func, ast.Name):
                    chain.append(func.id)
                if len(chain) >= 4 and chain[-1] == "bpy" and chain[-2] == "ops":
                    if chain[0] in ("select_all", "delete"):
                        return True
        return False

    def analyze(self, code: str) -> Tuple[bool, List[str]]:
        issues: List[str] = []

        if len(code) > self.MAX_LEN:
            issues.append(
                f"ERR_LEN: Codice troppo lungo ({len(code)} chars). "
                "Possibile allucinazione LLM."
            )

        code_lower = code.lower()
        if "import bpy" not in code_lower and "from bpy" not in code_lower:
            issues.append("ERR_IMPORT: Modulo base 'import bpy' mancante.")

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            issues.append(f"ERR_SYNTAX [riga {e.lineno}]: {e.msg}")
            return False, issues

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if mod in self.DANGEROUS_MODULES:
                        issues.append(
                            f"ERR_UNSAFE: Importazione vietata del modulo '{alias.name}'."
                        )

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod = node.module.split(".")[0]
                    if mod in self.DANGEROUS_MODULES:
                        issues.append(
                            f"ERR_UNSAFE: Import vietato da '{node.module}'."
                        )

            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.DANGEROUS_BUILTINS:
                        issues.append(
                            f"ERR_UNSAFE: Funzione nativa bloccata '{node.func.id}'."
                        )

                if isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                        and node.func.attr in self.DANGEROUS_OS_CALLS
                    ):
                        issues.append(
                            f"ERR_UNSAFE: Chiamata pericolosa 'os.{node.func.attr}' vietata."
                        )

        if not self._has_scene_reset(tree):
            issues.append(
                "WARN_SCENE: Nessun reset scena (select_all + delete). "
                "Geometrie residue dalla scena di default causeranno problemi."
            )

        code_upper = code.upper()
        if "BOOLEAN" in code_upper:
            if "transform_apply" not in code_lower:
                issues.append(
                    "WARN_BOOL: Mancata 'transform_apply' prima di operazione Booleana. "
                    "Causerà risultati errati."
                )
            if "solver" not in code_lower:
                issues.append(
                    "WARN_BOOL: Boolean senza solver='EXACT' esplicito. "
                    "Usare solver FAST può generare mesh non-manifold."
                )

        has_solidify = "SOLIDIFY" in code_upper
        has_weld = "WELD" in code_upper or "remove_doubles" in code_lower
        has_normals = "normals_make_consistent" in code_lower or "recalc_outside" in code_lower
        has_stl_export = "export_mesh.stl" in code_lower or "export_scene.obj" in code_lower

        if not has_weld:
            issues.append(
                "WARN_3DP: Nessuna saldatura vertici (Weld/remove_doubles). "
                "Vertici duplicati causano fallimento slicer."
            )

        if not has_normals:
            issues.append(
                "WARN_3DP: Nessun ricalcolo normali (normals_make_consistent). "
                "Normali invertite causano inversione stampa."
            )

        if not has_stl_export:
            issues.append(
                "INFO_EXPORT: Nessun export STL nello script (atteso: "
                "l'esportazione è gestita dal runner esterno)."
            )

        errors = [i for i in issues if i.startswith("ERR")]
        return len(errors) == 0, issues
