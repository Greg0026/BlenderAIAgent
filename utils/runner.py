import asyncio
import os
import tempfile
from typing import Optional, Tuple

from utils.code import extract_error_section


class BlenderRunner:
    def __init__(self, blender_executable: Optional[str] = None):
        self.blender_exe = blender_executable or os.environ.get("BLENDER_PATH", "blender")

    async def execute(self, script: str, timeout: float = None) -> Tuple[bool, str]:
        from cfg import CFG

        _timeout = timeout or CFG.get("blender_timeout", 120)

        fd, temp_path = tempfile.mkstemp(suffix=".py", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(script)

            process = await asyncio.create_subprocess_exec(
                self.blender_exe,
                "--background",
                "--factory-startup",
                "--python",
                temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return False, (
                    f"TIMEOUT: Blender did not finish within {_timeout}s. "
                    "Script likely in infinite loop or with overly expensive operations."
                )

            out_str = stdout.decode("utf-8", errors="replace")
            err_str = stderr.decode("utf-8", errors="replace")
            full_output = f"{out_str}\n{err_str}"

            is_hard_crash = process.returncode not in (0, 1)
            if is_hard_crash:
                return False, (
                    f"BLENDER_CRASH: returncode={process.returncode}. "
                    "Blender crashed (segfault, GPU error, or out-of-memory). "
                    "Ultime righe output:\n" + "\n".join(full_output.splitlines()[-20:])
                )

            has_traceback = (
                "Python: Traceback" in full_output
                or "Traceback (most recent call last):" in full_output
                or "Error: Python:" in full_output
                or "Error: line " in full_output
            )
            is_blender_script_fail = (
                "Error: Python script failed" in full_output
                or "Error: EXR_ERR" in full_output
            )

            if has_traceback or is_blender_script_fail:
                return False, extract_error_section(full_output, max_lines=50)

            return True, full_output

        except FileNotFoundError:
            return False, (
                f"Blender executable not found: '{self.blender_exe}'. "
                "Set BLENDER_PATH in .env"
            )
        except Exception as e:
            return False, f"Internal subprocess runner error: {str(e)}"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
