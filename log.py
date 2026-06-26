"""Logger strutturato per BlenderAIAgent.

Fornisce un logger preconfigurato con formato timestamp, livello e messaggio.
Tutti i moduli del progetto importano 'logger' da questo file per garantire
output uniforme su console e (opzionalmente) su file.

L'utilizzo è intenzionalmente semplice: `from log import logger` ovunque,
invece di configurare logger separati per ogni modulo.
"""

import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "BlenderAIAgent",
    level: int = logging.INFO,
    log_file: str = None,
) -> logging.Logger:
    """Crea e restituisce un logger configurato con output su console.

    Utile per avere un unico punto di configurazione del logging in tutto
    il progetto. Se il logger esiste già (handlers presenti), lo restituisce
    senza riconfigurarlo, evitando duplicati.

    Args:
        name: Nome del logger (usato da logging.getLogger).
        level: Soglia minima di log (default: INFO).
        log_file: Se specificato, scrive anche su file con level DEBUG.

    Returns:
        Logger configurato con formato "[timestamp] [LIVELLO] messaggio".
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(level)
    logger.addHandler(console)

    if log_file:
        fh = logging.FileHandler(Path(log_file), encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)

    return logger


logger = setup_logger()
