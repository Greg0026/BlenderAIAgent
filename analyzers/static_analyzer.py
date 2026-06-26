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
                f"ERR_LEN: Code too long ({len(code)} chars). "
                "Possible LLM hallucination."
            )

        code_lower = code.lower()
        if "import bpy" not in code_lower and "from bpy" not in code_lower:
            issues.append("ERR_IMPORT: Base module 'import bpy' missing.")

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            issues.append(f"ERR_SYNTAX [line {e.lineno}]: {e.msg}")
            return False, issues

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if mod in self.DANGEROUS_MODULES:
                        issues.append(
                            f"ERR_UNSAFE: Forbidden import of module '{alias.name}'."
                        )

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod = node.module.split(".")[0]
                    if mod in self.DANGEROUS_MODULES:
                        issues.append(
                            f"ERR_UNSAFE: Forbidden import from '{node.module}'."
                        )

            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.DANGEROUS_BUILTINS:
                        issues.append(
                            f"ERR_UNSAFE: Blocked built-in function '{node.func.id}'."
                        )

                if isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                        and node.func.attr in self.DANGEROUS_OS_CALLS
                    ):
                        issues.append(
                            f"ERR_UNSAFE: Dangerous call 'os.{node.func.attr}' forbidden."
                        )

        if not self._has_scene_reset(tree):
            issues.append(
                "WARN_SCENE: No scene reset (select_all + delete). "
                "Residual geometry from the default scene will cause issues."
            )

        code_upper = code.upper()
        if "BOOLEAN" in code_upper:
            if "transform_apply" not in code_lower:
                issues.append(
                    "WARN_BOOL: Missing 'transform_apply' before Boolean operation. "
                    "Will cause incorrect results."
                )
            if "solver" not in code_lower:
                issues.append(
                    "WARN_BOOL: Boolean without explicit solver='EXACT'. "
                    "Using FAST solver may generate non-manifold meshes."
                )

        has_solidify = "SOLIDIFY" in code_upper
        has_weld = "WELD" in code_upper or "remove_doubles" in code_lower
        has_normals = "normals_make_consistent" in code_lower or "recalc_outside" in code_lower
        has_stl_export = "export_mesh.stl" in code_lower or "export_scene.obj" in code_lower

        if not has_weld:
            issues.append(
                "WARN_3DP: No vertex welding (Weld/remove_doubles). "
                "Duplicate vertices cause slicer failure."
            )

        if not has_normals:
            issues.append(
                "WARN_3DP: No normal recalculation (normals_make_consistent). "
                "Inverted normals cause print inversion."
            )

        if not has_stl_export:
            issues.append(
                "INFO_EXPORT: No STL export in script (expected: "
                "export is handled by the external runner)."
            )

        errors = [i for i in issues if i.startswith("ERR")]
        return len(errors) == 0, issues
