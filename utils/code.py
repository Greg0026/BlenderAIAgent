"""Utility per estrazione e manipolazione di codice Python da risposte LLM.

Fornisce funzioni per:
  - Estrarre codice Python da risposte LLM (contenenti markdown, spiegazioni)
  - Estrarre sezioni di traceback/errore dall'output di Blender
  - Formattare errori per query semantiche al VectorDB
  - Troncare errori lunghi a lunghezza gestibile per il contesto LLM

Queste funzioni sono usate da core/llm.py, core/orchestrator.py e
dalle fasi della pipeline per processare le risposte dei modelli.
"""

import re
from typing import Optional


def extract_code(text: str) -> Optional[str]:
    """Estrae codice Python da una risposta LLM che può contenere markdown.

    Strategia di estrazione (in ordine):
    1. Cerca blocchi ```python ... ``` o ``` ... ``` (fence)
    2. Cerca codice inline `...` con import bpy
    3. Cerca righe che iniziano con import bpy/bmesh o contengono bpy.
       ATTENZIONE: evita falsi positivi su commenti (# bpy...) grazie
       al pattern regex \\bbpy\\. (word boundary).

    Args:
        text: Risposta testuale del LLM (può contenere spiegazioni).

    Returns:
        Codice Python estratto, o None se non rilevato codice valido.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    code_fence = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if code_fence:
        candidate = code_fence.group(1).strip()
        if candidate:
            return candidate

    inline = re.search(r"`([^`]+)`", text)
    if inline:
        candidate = inline.group(1).strip()
        if candidate and ("import bpy" in candidate or "bpy." in candidate):
            return candidate

    lines = text.split("\n")
    code_lines = []
    started = False
    bpy_ref = re.compile(r"\b(import bpy\b|bpy\.|bmesh\.)")
    for line in lines:
        stripped = line.strip()
        if not started:
            if stripped.startswith("import bpy") or stripped.startswith("import bmesh") or stripped.startswith("from bpy"):
                started = True
                code_lines.append(line)
            elif bpy_ref.search(stripped) and not stripped.startswith("#") and not stripped.startswith("//"):
                started = True
                code_lines.append(line)
        elif stripped.startswith("#") or stripped.startswith("//"):
            code_lines.append(line)
        elif stripped:
            code_lines.append(line)
        else:
            code_lines.append(line)

    code = "\n".join(code_lines).strip()
    if code and ("import bpy" in code or "bpy." in code):
        return code

    non_comment_lines = [l for l in lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("//")]
    non_comment_text = "\n".join(non_comment_lines)
    if "import bpy" in non_comment_text or "bpy." in non_comment_text:
        return non_comment_text

    return None


def extract_error_section(output: str, max_lines: int = 60) -> str:
    """Estrae la sezione di traceback dall'output di Blender.

    Cerca il marker "Traceback (most recent call last):" e restituisce
    le righe successive fino a max_lines. Se non trova traceback,
    restituisce le ultime max_lines righe dell'output.

    Args:
        output: Output completo di Blender (stdout + stderr).
        max_lines: Numero massimo di righe da includere.

    Returns:
        Sezione di traceback o ultime righe dell'output.
    """
    lines = output.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines)
            if "Traceback (most recent call last):" in line
            or "Python: Traceback" in line
        )
        return "\n".join(lines[start:start + max_lines])
    except StopIteration:
        return "\n".join(lines[-max_lines:])


def format_error_for_query(error_text: str) -> str:
    """Formatta un errore per usarlo come query nel VectorDB.

    Estrae le righe più significative dall'errore (Error, Warning, Exception)
    per creare una query semantica compatta. Evita di usare l'intero stack
    trace (decine di righe) come query, che produrrebbe risultati irrilevanti
    dal database vettoriale.

    Args:
        error_text: Testo completo dell'errore (stack trace).

    Returns:
        Query compatta (max 5 righe chiave) per ricerca vettoriale.
    """
    lines = error_text.splitlines()
    key_lines = [l for l in lines if "Error:" in l or "Warning:" in l or "Exception:" in l or l.strip().startswith("File")]
    if key_lines:
        return " ".join(key_lines[:5])
    return " ".join(lines[-3:])


def summarize_error(error_text: str, max_len: int = 300) -> str:
    """Tronca un errore lungo a una lunghezza massima per il contesto LLM.

    Prende le ultime max_len caratteri (dove c'è la parte più utile
    dello stack trace: il messaggio di errore e le ultime chiamate).

    Args:
        error_text: Testo completo dell'errore.
        max_len: Lunghezza massima in caratteri del risultato.

    Returns:
        Errore troncato (dalla fine).
    """
    if len(error_text) <= max_len:
        return error_text
    return error_text[-max_len:]
