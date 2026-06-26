"""Tracciamento errori e rilevamento oscillazioni per la pipeline.

Fornisce due classi fondamentali per la stabilità della pipeline:

ErrorHistory:
  Mantiene la cronologia degli errori incontrati nelle fasi F6 e F6-VIS.
  Ogni errore viene registrato con una "firma" (signature) che identifica
  il tipo di errore (basata sul messaggio, non sull'intero stack trace).
  La cronologia viene passata al LLM nelle chiamate successive per evitare
  che ripeta fix già tentati senza successo.

OscillationDetector:
  Rileva pattern di oscillazione nelle iterazioni della pipeline.
  Confronta le impronte MD5 degli script prodotti a ogni iterazione.
  Se lo script torna a uno stato già visto (pattern A→B→A→B), rileva
  l'oscillazione e permette all'orchestratore di interrompere il ciclo.
"""

import hashlib
import re
from typing import List, Optional


class ErrorHistory:
    """Cronologia degli errori con rilevamento ripetizioni.

    Utile per evitare che la pipeline ripeta all'infinito lo stesso fix
    senza successo. Ogni errore viene convertito in una "firma" estratta
    dal messaggio principale (linea Error: o Exception:), ignorando le
    parti variabili come indirizzi di memoria o timestamp.

    Args:
        max_history: Numero massimo di errori da mantenere in cronologia.
    """

    def __init__(self, max_history: int = 10):
        """Inizializza la cronologia vuota con capacità massima.

        Args:
            max_history: Numero massimo di errori da tracciare (FIFO).
        """
        self._errors: List[str] = []
        self._fixes: List[str] = []
        self._max = max_history

    def add(self, error: str, fix_approach: str = ""):
        """Registra un nuovo errore e il fix tentato.

        Se lo stesso errore (stessa firma) è già presente, non viene
        duplicato. Il fix_approach è una descrizione testuale opzionale
        di cosa è stato tentato (utile per il prompt LLM).

        Args:
            error: Testo completo dell'errore.
            fix_approach: Descrizione del fix tentato (es. "static fix attempt").
        """
        error_sig = self._signature(error)
        if error_sig not in self._errors:
            self._errors.append(error_sig)
            self._fixes.append(fix_approach)
        if len(self._errors) > self._max:
            self._errors.pop(0)
            self._fixes.pop(0)

    def is_repeated(self, error: str) -> bool:
        """Verifica se un errore è già stato visto in precedenza.

        Args:
            error: Testo completo dell'errore da verificare.

        Returns:
            True se lo stesso errore (stessa firma) è già in cronologia.
        """
        return self._signature(error) in self._errors

    def get_history_block(self) -> str:
        """Restituisce un blocco di testo formattato per il prompt LLM.

        Il blocco elenca gli errori precedenti e i fix tentati, con
        l'istruzione "NON RIPETERE" per prevenire cicli infiniti.

        Returns:
            Testo formattato con cronologia errori, o "Nessun tentativo precedente."
        """
        if not self._errors:
            return "Nessun tentativo precedente."
        lines = ["TENTATIVI PRECEDENTI (NON RIPETERE):"]
        for i, (err, fix) in enumerate(zip(self._errors, self._fixes), 1):
            lines.append(f"  {i}. Errore: {err[:120]}")
            if fix:
                lines.append(f"     Fix tentato: {fix[:120]}")
        return "\n".join(lines)

    def clear(self):
        """Resetta la cronologia cancellando tutti gli errori registrati."""
        self._errors.clear()
        self._fixes.clear()

    @staticmethod
    def _signature(error: str) -> str:
        """Estrae una firma univoca da un messaggio di errore.

        La firma è la prima riga che contiene "Error:" o "Exception:"
        (troncata a 150 caratteri). Se nessuna riga corrisponde, usa
        l'ultima riga non vuota. Questo permette di raggruppare errori
        simili anche se gli indirizzi di memoria o i percorsi file variano.

        Args:
            error: Testo completo dell'errore.

        Returns:
            Stringa firma (max 150 caratteri).
        """
        lines = error.strip().splitlines()
        for line in lines:
            if "Error:" in line:
                return line.strip()[:150]
        for line in lines:
            if "Exception:" in line:
                return line.strip()[:150]
        for line in lines:
            if "line " in line and ("Error" in line or "invalid" in line or "cannot" in line):
                return line.strip()[:150]
        return lines[-1][:150] if lines else error[:150]


class OscillationDetector:
    """Rileva pattern di oscillazione nelle iterazioni della pipeline.

    Confronta le impronte MD5 degli script prodotti a ogni iterazione
    del loop di visione. Se lo script torna a uno stato già visto
    (pattern ciclico A→B→A→B o A→B→C→A→B→C), segnala l'oscillazione.

    Args:
        max_history: Numero massimo di snapshot da mantenere in memoria.
    """

    def __init__(self, max_history: int = 5):
        """Inizializza il detector con capacità massima.

        Args:
            max_history: Numero di snapshot recenti da conservare.
        """
        self._snapshots: List[str] = []
        self._max = max_history

    def add_snapshot(self, script: str):
        """Aggiunge lo snapshot corrente (impronta MD5 dello script).

        Args:
            script: Testo completo dello script da tracciare.
        """
        sig = self._signature(script)
        if not sig:
            return
        self._snapshots.append(sig)
        if len(self._snapshots) > self._max:
            self._snapshots.pop(0)

    def is_oscillating(self) -> bool:
        """Verifica se è in corso un'oscillazione (pattern A→B→A→B o cicli piu' lunghi).

        Controlla pattern di periodo 2 (A→B→A→B) con almeno 4 snapshot,
        e pattern di periodo 3 (A→B→C→A→B→C) con almeno 6 snapshot.

        Returns:
            True se è stato rilevato un pattern oscillatorio.
        """
        if len(self._snapshots) < 4:
            return False
        for period in range(2, 4):
            if len(self._snapshots) >= period * 2:
                pattern = self._snapshots[-period:]
                prev = self._snapshots[-(period * 2):-period]
                if pattern == prev:
                    return True
        return False

    @staticmethod
    def _signature(script: str) -> str:
        """Calcola l'impronta MD5 normalizzata per confronto robusto.

        Rimuove commenti, spazi ridondanti e righe vuote prima di
        calcolare l'hash, in modo che variazioni cosmetiche (commenti,
        spazi bianchi) non impediscano il rilevamento dell'oscillazione.

        Args:
            script: Testo dello script.

        Returns:
            Impronta MD5 esadecimale, o stringa vuota se script vuoto.
        """
        if not script:
            return ""
        norm = re.sub(r'#.*', '', script)
        norm = re.sub(r'\s+', ' ', norm).strip()
        return hashlib.md5(norm.encode()).hexdigest()
