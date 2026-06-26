# BlenderAIAgent — 3D Print-Ready Pipeline

Autonomous pipeline that transforms natural language descriptions into production-ready Blender Python scripts, then into STL files for 3D printing.

```
"crea un vaso floreale con base esagonale, pareti 2mm, altezza 15cm"
    → bpy Python script → validated manifold mesh → STL
```

## Architecture

Multi-stage pipeline with 10 phases across 2 nested loops:

```
Prompt → [F1] Enhancement → [F1.5] Math Planner → [F2] Code Generation
    ┌───────────────── VISION LOOP (max 8x) ─────────────────┐
    │  [F3A] Morph Review → [F3B] Printability Review         │
    │      ┌─────────── FIX LOOP (max 6x) ─────────┐          │
    │      │  [F3.5] Static Analysis → [F6] Fix      │          │
    │      │  [F4]   Blender Execute  → [F6] Fix      │          │
    │      └──────────────────────────────────────────┘          │
    │  [F4.5] Vision Review (LLM vision) → [F6-VIS] Vis Fix      │
    └────────────────────────────────────────────────────────────┘
    → Script Python Blender finale
```

| Fase | Input | Output | Cosa fa |
|------|-------|--------|---------|
| **F1** | Prompt utente | Specifica tecnica | Traduce richiesta in parametri numerici, sceglie archetipo (A-E) |
| **F1.5** | Specifica tecnica | Piano matematico | Produce formule r(z), punti di controllo, primitive con valori |
| **F2** | Spec + piano + docs | Script bpy | Genera il primo script Blender completo |
| **F3A** | Script + spec | Script corretto | Verifica fedelta' morfologica: formule, silhouette, fondo chiuso |
| **F3B** | Script + spec | Script corretto | Verifica stampabilita': manifold, spessore, ordine modifier, API |
| **F3.5** | Script | Errori/Warning | AST linter: sintassi, moduli pericolosi, euristiche 3D print |
| **F4** | Script | Output Blender | Esecuzione headless con timeout; cattura traceback |
| **F4.5** | Script + spec | Report visione | Render 4 viste ortografiche; LLM vision valuta estetica/fedelta' |
| **F6** | Script + errore | Script corretto | Correzione errore runtime/statico con retrieval VectorDB |
| **F6-VIS** | Script + report vis | Script corretto | Correzione problemi visivi/estetici |

### Archetypes

Ogni oggetto viene classificato in uno dei 5 archetipi geometrici:

| Code | Type | Examples | Method |
|------|------|----------|--------|
| **A** | Revolution | Bicchieri, vasi, ciotole | r(z) parametrico, N_SEGS>=128 |
| **B** | Extrusion/Loft | Manici, profili | Curve NURBS/Bezier + sweep |
| **C** | Boolean Composite | Scatole, carter | Primitive + booleane DIFFERENCE/UNION |
| **D** | Voronoi/Fractal | Paralumi, sculture | bmesh + noise + subdivision |
| **E** | Hybrid | Oggetti complessi | Mix di piu' approcci |

## Project Structure

```
3dideaexp/
├── main.py                 # Entry point: singolo prompt
├── afk.py                  # Batch runner autonomo (40+ categorie)
├── cfg.py                  # Configurazione centralizzata (legge .env)
├── log.py                  # Logger strutturato (importa da ovunque)
├── requirements.txt        # Dipendenze Python
├── .env.example            # Template configurazione (da copiare in .env)
├── .gitignore
├── README.md
│
├── prompts/                # System prompts LLM (testo puro, modificabili)
│   ├── f1_enhance.txt          # Prompt enhancement
│   ├── f15_math_planner.txt    # Math shape planner
│   ├── f2_codegen.txt          # Code generation
│   ├── f3a_morph.txt           # Morphological review
│   ├── f3b_printability.txt    # Printability & API safety
│   ├── f6_fix.txt              # Static/runtime fix
│   ├── f6_vis_fix.txt          # Vision fix
│   ├── definition_of_done.txt  # Criteri di completamento (D1-D6)
│   ├── shape_archetype_guide.txt  # Guida archetipi A-E
│   └── common_pitfalls.txt     # 12 errori frequenti
│
├── core/                   # Pipeline core
│   ├── __init__.py
│   ├── orchestrator.py     # Orchestratore 8 fasi + loop annidati
│   ├── phases.py           # Implementazioni fasi (F1-F6-VIS)
│   └── llm.py              # Wrapper LLM con fallback cascade + cache prompt
│
├── utils/                  # Utility modules
│   ├── __init__.py
│   ├── runner.py           # Blender subprocess executor (asyncio)
│   ├── code.py             # Estrazione codice, formattazione errori
│   └── errors.py           # ErrorHistory + OscillationDetector
│
├── analyzers/              # Analizzatori pre/post esecuzione
│   ├── __init__.py
│   ├── static_analyzer.py  # AST linter (sicurezza, sintassi, euristiche)
│   └── mesh_validator.py   # Validazione mesh bmesh (non-manifold, volume)
│
├── review/                 # Revisione visiva
│   ├── __init__.py
│   └── vision_reviewer.py  # Render Blender + LLM vision (F4.5)
│
├── vectordb/               # Vector database
│   ├── __init__.py
│   └── vectordb.py         # ChromaDB + sentence-transformers (13 snippet interni)
│
└── corpus/                 # Corpus builder (standalone)
    ├── __init__.py
    └── corpus_builder.py   # Raccolta snippet da 6 fonti (SE, GitHub, docs, forum)
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Extra per corpus_builder:
```bash
pip install requests beautifulsoup4 PyGitHub
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your API keys (see `.env.example` for all options):

| Variable | Required | Description |
|----------|----------|-------------|
| `NVIDIA_API_KEY` | Yes | NVIDIA NIM key (primario per codegen) |
| `OPENROUTER_API_KEY` | Yes | OpenRouter key (fallback + batch) |
| `BLENDER_PATH` | Yes | Path assoluto eseguibile Blender |
| `LLM_MODEL_ID` | No | Modello primario (default: z-ai/glm-5.1) |
| `LLM_FALLBACK_MODELS` | No | Modelli fallback separati da virgola |
| `GITHUB_TOKEN` | No | Per corpus_builder.py (5000 req/h vs 60 anonimo) |

### 3. Verify Blender

```bash
blender --version
```
Blender 3.0+ required. Set `BLENDER_PATH` in `.env` if not in PATH.

## Usage

### Single prompt

```bash
python main.py
```

Custom prompt via CLI arg o env var:
```bash
python main.py --prompt "crea un portapenne da scrivania con 3 scomparti, design moderno"
python main.py -p "crea un vaso floreale" -o vaso.py
PROMPT="crea un portapenne da scrivania con 3 scomparti, design moderno. staminalo in 3D." python main.py
```

Output: `3dtest.py` (default, o `-o` / `OUTPUT_FILE` env var). Esegui lo script:
```bash
blender --background --python 3dtest.py
```

### Batch (AFK) mode

Genera continuamente modelli 3D per categorie casuali (40+ categorie):

```bash
python afk.py
```

Configurable via env vars:
- `BATCH_MAX_JOBS` — max job (vuoto = illimitato)
- `BATCH_PAUSE` — secondi tra job (default: 10)
- `BATCH_OUTPUT_DIR` — directory output (default: output_scripts/)

### Build corpus

Raccogli snippet bpy da 6 fonti per il VectorDB:

```bash
python corpus_builder.py -v
```

Flags:
- `--index-only` — solo indicizzazione ChromaDB
- `--load-only` — riusa corpus.jsonl esistente
- `--no-github` — salta GitHub
- `--github-token` — token GitHub (o env `GITHUB_TOKEN`)

## How It Works

### Pipeline Details

1. **F1 - Enhancement**: Il prompt utente viene trasformato in una specifica tecnica strutturata con archetipo, parametri numerici, e regole di stampabilita'.

2. **F1.5 - Math Planner**: Produce formule parametriche esplicite (es. r(z) = 20 + 5*sin(z*pi/H) per rivoluzioni).

3. **F2 - Code Generation**: LLM genera il primo script bpy basato su specifica + piano + documentazione VectorDB.

4. **F3A + F3B - Review**: Due LLM specializzati verificano fedelta' morfologica e stampabilita'. Se OK, script invariato; se no, corretto.

5. **F3.5 - Static Analysis**: AST linter controlla sintassi, moduli pericolosi, euristiche 3D print (ordine modifier, weld, normals).

6. **F4 - Execution**: Esegue script in Blender headless. Se fallisce, F6 corregge con l'aiuto del VectorDB.

7. **F4.5 - Vision Review**: Render 4 viste ortografiche + LLM vision valuta estetica e fedelta'.

8. **F6 - Targeted Fix**: Correzione errori con storia (ErrorHistory) per evitare loop infiniti.

### Stability Mechanisms

- **OscillationDetector**: Rileva pattern ciclici (A->B->A->B) e forza la terminazione
- **ErrorHistory**: Tiene traccia degli ultimi 10 errori; passa "NON RIPETERE" al LLM
- **Fallback script**: Se la pipeline non produce risultati, genera un cubo base con Solidify + Weld
- **Model cascade**: Se il modello primario fallisce, prova fino a 6 fallback in ordine di capacita'
- **Weak model filter**: Rimuove dalla cascade modelli <7B parametri (phi-4-mini, llama-3.2-1b)

## Requirements

```
openai>=1.0.0
aiohttp
python-dotenv
chromadb
sentence-transformers
```

## Troubleshooting

| Problema | Causa probabile | Soluzione |
|----------|----------------|-----------|
| `EnvironmentError: Nessuna API key trovata` | .env mancante o incompleto | Copia `.env.example` in `.env` e inserisci le chiavi |
| `FileNotFoundError: blender non trovato` | BLENDER_PATH errato | Imposta il path assoluto in `.env` |
| `ImportError: chromadb` | Dipendenze mancanti | `pip install chromadb sentence-transformers` |
| `TIMEOUT: Blender non ha terminato` | Script in loop o Blender lento | Aumenta `BLENDER_TIMEOUT` in `.env` |
| `Vision review fallita` | GPU non disponibile | Imposta `VISION_REVIEW_ENABLED=false` |
| `Tutti i modelli della cascata hanno fallito` | API key invalida o quota esaurita | Verifica `NVIDIA_API_KEY` o `OPENROUTER_API_KEY` |
| `batch_max_jobs non funziona` | Valore vuoto = illimitato | Imposta `BATCH_MAX_JOBS=10` (o il numero desiderato) |

## Key Design Decisions

- **1 BU = 1 mm**: Coerenza con gli slicer (Cura, PrusaSlicer si aspettano mm). Global scale in export STL = 1000.
- **Solidify PRIMA di Boolean**: L'ordine corretto per garantire mesh manifold. Prima lo spessore, poi il taglio.
- **Prompt separati in prompts/**: I system prompt sono file .txt modificabili senza toccare codice Python.
- **VectorDB + semantic search**: Retrieval Augmented Generation per documentazione bpy invece di hardcoding.
- **Vision Review con LLM**: 4 render ortografici + LLM vision (kimi-k2.6/gpt-4o) per valutazione estetica.
- **Fallback cascade**: 7 modelli in ordine di capacita'. Se il top fallisce, si scende.
- **corpus_builder.py standalone**: Non fa parte della pipeline core; script separato per raccolta dataset.

## License

MIT — see LICENSE file.
