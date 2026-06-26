# BlenderAIAgent

**Autonomous pipeline that transforms natural language descriptions into production-ready Blender Python scripts and STL files for 3D printing.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Blender 3.0+](https://img.shields.io/badge/Blender-3.0+-orange.svg)](https://www.blender.org/)

```
"create a flower vase with hexagonal base, 2mm walls, 15cm height"
    → bpy Python script → validated manifold mesh → STL
```

---

## Features

- **Natural language → 3D model**: Describe what you want in plain English, get a ready-to-print STL
- **Multi-stage LLM pipeline**: 10 specialized phases with dedicated system prompts
- **Self-correcting**: Runtime error recovery, oscillation detection, and fallback cascade
- **Visual quality control**: LLM-powered vision review of 4 orthographic renders
- **Batch mode**: Autonomous headless generation of 40+ object categories
- **RAG-enhanced**: VectorDB with semantic search for Blender API documentation

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure your API keys
cp .env.example .env
# Edit .env with your keys and Blender path

# 3. Generate your first 3D model
python main.py -p "create a cylindrical vase, height 12cm, diameter 8cm, walls 2mm"

# 4. Run the generated script in Blender
blender --background --python 3dtest.py
```

---

## Architecture

### Pipeline Overview

```
Prompt → [F1] Enhancement → [F1.5] Math Planner → [F2] Code Generation
    ┌───────────────── VISION LOOP (up to 8 iterations) ─────────────────┐
    │  [F3A] Morphological Review → [F3B] Printability Review             │
    │      ┌─────────── FIX LOOP (up to 6 iterations) ──────────┐         │
    │      │  [F3.5] Static Analysis → [F6] Targeted Fix          │         │
    │      │  [F4]   Blender Execution → [F6] Targeted Fix         │         │
    │      └──────────────────────────────────────────────────────┘         │
    │  [F4.5] Vision Review (LLM vision) → [F6-VIS] Visual Fix              │
    └───────────────────────────────────────────────────────────────────────┘
    → Final Blender Python Script → [Optional] STL Export
```

### Phases

| Phase | Input | Output | Purpose |
|-------|-------|--------|---------|
| **F1** | User prompt | Technical specification | Translates request into numerical parameters and selects archetype |
| **F1.5** | Specification | Mathematical plan | Produces parametric formulas (e.g., r(z) for revolutions) |
| **F2** | Spec + Plan + Docs | `bpy` script | Generates the initial Blender script using RAG |
| **F3A** | Script + Spec | Corrected script | Verifies morphological fidelity to the original prompt |
| **F3B** | Script + Spec | Corrected script | Verifies 3D printability (manifold, thickness, modifier order) |
| **F3.5** | Script | Errors / Warnings | AST linter: syntax, dangerous modules, 3D-print heuristics |
| **F4** | Script | Blender output + validation | Headless execution with timeout; mesh geometry validation |
| **F4.5** | Script + Spec | Vision report | Renders 4 orthographic views; LLM evaluates aesthetics |
| **F6** | Script + Error | Corrected script | Runtime/static error correction with VectorDB retrieval |
| **F6-VIS** | Script + Report | Corrected script | Fixes visual and aesthetic issues |

### Geometric Archetypes

Every object is classified into one of five geometric archetypes for specialized code generation:

| Code | Type | Examples | Generation Method |
|------|------|----------|-------------------|
| **A** | Revolution | Cups, vases, bowls | `r(z)` parametric, `N_SEGS >= 128` |
| **B** | Extrusion / Loft | Handles, profiles | NURBS / Bezier curves + sweep |
| **C** | Boolean Composite | Boxes, housings | Primitives + Boolean DIFFERENCE / UNION |
| **D** | Voronoi / Fractal | Lampshades, sculptures | `bmesh` + noise + subdivision |
| **E** | Hybrid | Complex objects | Mix of multiple approaches |

---

## Project Structure

```
3dideaexp/
├── main.py                  # Entry point: single prompt → STL
├── afk.py                   # Autonomous batch runner (40+ categories)
├── cfg.py                   # Centralized configuration (reads .env)
├── log.py                   # Structured logger
├── requirements.txt         # Python dependencies
├── .env.example             # Configuration template
├── .gitignore
├── LICENSE
├── README.md
├── GUIDE.txt                # Full user guide
│
├── prompts/                 # LLM system prompts (editable plain text)
│   ├── f1_enhance.txt
│   ├── f15_math_planner.txt
│   ├── f2_codegen.txt
│   ├── f3a_morph.txt
│   ├── f3b_printability.txt
│   ├── f6_fix.txt
│   ├── f6_vis_fix.txt
│   ├── definition_of_done.txt
│   ├── shape_archetype_guide.txt
│   └── common_pitfalls.txt
│
├── core/                    # Pipeline core
│   ├── orchestrator.py      # Phase orchestrator with nested loops
│   ├── phases.py            # Phase implementations (F1–F6-VIS)
│   └── llm.py               # LLM wrapper with fallback cascade
│
├── utils/                   # Utilities
│   ├── runner.py            # Blender subprocess executor (asyncio)
│   ├── code.py              # Code extraction and error formatting
│   └── errors.py            # ErrorHistory + OscillationDetector
│
├── analyzers/               # Pre/post execution analyzers
│   ├── static_analyzer.py   # AST linter
│   └── mesh_validator.py    # bmesh mesh validation
│
├── review/
│   └── vision_reviewer.py   # Blender render + LLM vision review
│
├── vectordb/
│   └── vectordb.py          # ChromaDB + sentence-transformers
│
├── corpus/                  # Corpus builder (standalone)
│   └── corpus_builder.py    # Snippet collection from 6 sources
│
└── tests/                   # Automated tests (pytest)
    ├── test_cfg.py
    ├── test_code.py
    ├── test_errors.py
    └── test_orchestrator.py
```

---

## Installation

### Prerequisites

- **Python 3.9+**
- **Blender 3.0+** ([Download](https://www.blender.org/download/))
- **Internet connection** (for LLM API calls)
- Minimum **4 GB RAM** (GPU recommended for Vision Review)

### Step 1: Install Python Dependencies

```bash
pip install -r requirements.txt
```

Extra dependencies for `corpus_builder.py`:
```bash
pip install requests beautifulsoup4 PyGitHub
```

### Step 2: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

| Variable | Required | Description |
|----------|----------|-------------|
| `NVIDIA_API_KEY` | At least one | NVIDIA NIM API key |
| `OPENROUTER_API_KEY` | API key | OpenRouter API key |
| `OPENCODE_ZEN_API_KEY` | | OpenCode Zen API key |
| `BLENDER_PATH` | Yes | Absolute path to Blender executable |
| `LLM_MODEL_ID` | No | Primary model (default: `z-ai/glm-5.1`) |
| `LLM_VISION_MODEL` | No | Vision review model |
| `LLM_FALLBACK_MODELS` | No | Comma-separated fallback models |
| `GITHUB_TOKEN` | No | For `corpus_builder.py` (5000 req/h vs 60 anonymous) |

### Step 3: Verify Blender

```bash
blender --version
```

If Blender is not in your PATH, set `BLENDER_PATH` in `.env`:
```
BLENDER_PATH=/usr/local/blender/blender          # Linux / macOS
BLENDER_PATH=C:\Program Files\Blender Foundation\Blender 4.0\blender.exe   # Windows
```

---

## Usage

### Single Prompt

Generate a 3D model from a text description:

```bash
# Using the default prompt
python main.py

# Custom prompt via command line
python main.py -p "create a flower vase with hexagonal base, height 15cm"
python main.py --prompt "create a desk pen holder with 3 compartments" -o output.py

# Custom prompt via environment variable
PROMPT="create a modern table lamp with voronoi pattern, height 25cm" python main.py
```

The output is a Blender Python script (default: `3dtest.py`). Run it:

```bash
blender --background --python 3dtest.py
```

### Batch Mode (AFK)

Continuously generate 3D models across 40+ categories:

```bash
python afk.py
```

Configuration via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `BATCH_MAX_JOBS` | (unlimited) | Maximum number of jobs |
| `BATCH_PAUSE` | 10 | Seconds between jobs |
| `BATCH_OUTPUT_DIR` | `output_scripts/` | Output directory |

### Build the VectorDB Corpus

Collect `bpy` snippets from 6 sources to improve RAG quality:

```bash
python corpus_builder.py -v          # Full collection
python corpus_builder.py --index-only    # Indexing only
python corpus_builder.py --load-only     # Reuse existing corpus.jsonl
```

### Speed Test

Benchmark API response times across configured models:

```bash
python -m tests.speed_test
python -m tests.speed_test --model deepseek-ai/deepseek-v4-flash
python -m tests.speed_test --timeout 180
```

---

## How the Pipeline Works

1. **F1 — Prompt Enhancement**: The user's natural language request is transformed into a structured technical specification with exact numerical parameters, selected archetype (A–E), and printability constraints.

2. **F1.5 — Mathematical Planning**: The specification is converted into explicit parametric formulas — for example, `r(z) = 20 + 5*sin(z*π/H)` for a revolution archetype.

3. **F2 — Code Generation**: The LLM generates a complete Blender Python script based on the specification, mathematical plan, and VectorDB documentation (RAG).

4. **F3A + F3B — Dual Review**: Two specialized LLM reviewers examine the script. F3A checks morphological fidelity; F3B verifies printability and API safety. If both pass, the script proceeds unchanged.

5. **F3.5 — Static Analysis**: An AST-based linter checks syntax validity, dangerous module usage, and 3D-printing heuristics (modifier order, weld operations, normal orientation).

6. **F4 — Execution**: The script runs in headless Blender with a configurable timeout. Mesh validation checks for non-manifold geometry, zero volume, and degenerate faces.

7. **F4.5 — Vision Review**: Four orthographic renders are generated and analyzed by an LLM vision model for aesthetic quality and fidelity to the original prompt.

8. **F6 — Targeted Fix**: Runtime errors and static warnings are corrected with VectorDB-assisted retrieval and error history tracking to prevent repeated failures.

### Stability Mechanisms

| Mechanism | Description |
|-----------|-------------|
| **OscillationDetector** | Detects cyclic fix patterns (A → B → A → B) and forces termination |
| **ErrorHistory** | Tracks last 10 errors; passes "DO NOT REPEAT" context to the LLM |
| **Fallback script** | If the pipeline produces no result, generates a base cube with Solidify + Weld |
| **Model cascade** | Tries up to 6 fallback models if the primary model fails |
| **Weak model filter** | Automatically excludes models below 7B parameters from the cascade |
| **VectorDB RAG** | Semantic retrieval of `bpy` documentation snippets for context-aware generation |

---

## Configuration Reference

All configuration lives in `.env`. See `.env.example` for the complete template.

### Pipeline Control

| Variable | Default | Description |
|----------|---------|-------------|
| `ERROR_LOOPS` | 8 | Maximum vision loop iterations |
| `FIX_LOOPS` | 6 | Maximum fix loop iterations |
| `PHASE_TIMEOUT` | 900 | Overall timeout per pipeline phase (seconds) |
| `FIRST_CHUNK_TIMEOUT` | 120 | Timeout for first LLM token (seconds) |
| `MIN_CODE_LENGTH` | 400 | Minimum acceptable code length |
| `VISION_REVIEW_ENABLED` | `true` | Enable/disable vision review |
| `MAX_MESH_FIX_ATTEMPTS` | 3 | Maximum mesh fix attempts |
| `STATIC_MAX_LEN` | 40000 | Maximum static analysis input size |

### Retry Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `API_BASE_DELAY` | 3.0 | Base delay between retries (seconds) |
| `API_MAX_RETRIES` | 3 | Maximum API retries |
| `API_INTER_PAUSE` | 8.0 | Pause between API calls (seconds) |
| `RETRY_BACKOFF_S` | 1.0 | Backoff increment per retry (seconds) |

### Blender

| Variable | Default | Description |
|----------|---------|-------------|
| `BLENDER_PATH` | — | Absolute path to Blender executable |
| `BLENDER_TIMEOUT` | 120 | Execution timeout (seconds) |

---

## Troubleshooting

| Problem | Likely Cause | Solution |
|---------|--------------|----------|
| `EnvironmentError: No API key found` | `.env` missing or incomplete | Copy `.env.example` to `.env` and enter valid API keys |
| `FileNotFoundError: blender not found` | `BLENDER_PATH` incorrect | Set the absolute path to Blender in `.env` |
| `ImportError: chromadb` | Missing dependencies | Run `pip install chromadb sentence-transformers` |
| `TIMEOUT: Blender did not finish` | Script in infinite loop or Blender is slow | Increase `BLENDER_TIMEOUT` in `.env` |
| `Vision review failed` | GPU unavailable or render error | Set `VISION_REVIEW_ENABLED=false` in `.env` |
| `All models in cascade failed` | Invalid API key or quota exhausted | Verify API keys and check quota on provider dashboard |
| `batch_max_jobs not working` | Empty value means unlimited | Set `BATCH_MAX_JOBS=10` (or your desired number) |
| `First token timeout` | API provider is slow | Increase `FIRST_CHUNK_TIMEOUT` in `.env` |
| Poor quality output | Weak model or missing context | Check `LLM_MODEL_ID`; add VectorDB snippets via `corpus_builder.py` |

---

## Key Design Decisions

- **1 BU = 1 mm**: Consistent measurement with slicers (Cura, PrusaSlicer). Global scale in STL export = 1000.
- **Solidify BEFORE Boolean**: Ensures manifold mesh — thickness first, then cutting operations.
- **Separate prompt files**: System prompts are `.txt` files in `prompts/`, editable without touching Python code.
- **VectorDB + semantic search**: Retrieval-Augmented Generation for `bpy` documentation instead of hardcoded snippets.
- **Vision Review with LLM**: Four orthographic renders analyzed by a vision-capable LLM for aesthetic evaluation.
- **Fallback cascade**: Seven models in order of capability. Graceful degradation if the primary model fails.
- **Standalone corpus builder**: `corpus_builder.py` is independent of the core pipeline, for dataset collection.

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_cfg.py -v
pytest tests/test_orchestrator.py -v

# Run with coverage (if pytest-cov is installed)
pytest tests/ --cov=. --cov-report=term
```

---

## License

MIT — see [LICENSE](LICENSE) for the full text.

---

## Full Documentation

See [GUIDE.txt](GUIDE.txt) for the complete user guide covering installation, configuration, pipeline architecture, troubleshooting, and prompt examples.
