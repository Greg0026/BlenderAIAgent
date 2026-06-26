import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from log import logger
from vectordb.vectordb import VectorDB
from utils.runner import BlenderRunner
from core.orchestrator import Orchestrator


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BlenderAIAgent — Transform textual descriptions into 3D print-ready Blender scripts",
        epilog="Example: python main.py --prompt \"create a flower vase\" --output vase.py",
    )
    parser.add_argument(
        "-p", "--prompt",
        default=os.environ.get("PROMPT", "create a desk pen holder with 3 compartments, modern design. 3D print it."),
        help="Textual description of the 3D object to generate",
    )
    parser.add_argument(
        "-o", "--output",
        default=os.environ.get("OUTPUT_FILE", "3dtest.py"),
        help="Path of the output Blender Python file",
    )
    return parser.parse_args()


async def main():
    args = _parse_args()
    prompt = args.prompt
    output_file = args.output
    BLENDER_PATH = os.environ.get("BLENDER_PATH", "blender")

    runner = BlenderRunner(blender_executable=BLENDER_PATH)
    db = VectorDB()
    try:
        await db.build()
    except Exception as e:
        logger.warning("VectorDB build failed (%s). Continuing without.", e)

    try:
        orch = Orchestrator(db, runner)
    except EnvironmentError as e:
        logger.critical("Config error: %s", e)
        return

    logger.info("=" * 60)
    logger.info("BlenderAIAgent v5.0 — 3D Print-Ready Pipeline")
    logger.info("Prompt: %s", prompt[:120])
    logger.info("Output: %s", output_file)
    logger.info("=" * 60)

    try:
        final = await orch.run(prompt, bver="3.0")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final)
        logger.info("Script saved to: %s", os.path.abspath(output_file))
        logger.info("Run: blender --background --python %s", os.path.abspath(output_file))

    except Exception as e:
        logger.critical("Critical error: %s", e)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
