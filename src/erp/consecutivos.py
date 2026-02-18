"""Generacion de consecutivos (Folio y Poliza).

Usa UPDLOCK + HOLDLOCK para evitar colisiones en entornos
concurrentes. DEBE llamarse dentro de una transaccion activa.
"""

from loguru import logger


def obtener_siguiente_folio(cursor) -> int:
    """Obtiene el siguiente Folio global para SAVCheqPM.

    IMPORTANTE: Debe llamarse dentro de una transaccion activa.
    Usa UPDLOCK+HOLDLOCK para prevenir duplicados.

    Returns:
        Siguiente numero de Folio disponible.
    """
    cursor.execute("""
        SELECT ISNULL(MAX(Folio), 0) + 1
        FROM SAVCheqPM WITH (UPDLOCK, HOLDLOCK)
    """)
    folio = cursor.fetchone()[0]
    logger.debug("Siguiente Folio: {}", folio)
    return folio


def obtener_siguiente_poliza(cursor, fuente: str = 'SAV7-CHEQUES') -> int:
    """Obtiene el siguiente numero de Poliza para SAVPoliza.

    IMPORTANTE: Debe llamarse dentro de una transaccion activa.
    Usa UPDLOCK+HOLDLOCK para prevenir duplicados.

    Args:
        fuente: Fuente de la poliza (default: 'SAV7-CHEQUES').

    Returns:
        Siguiente numero de Poliza disponible.
    """
    cursor.execute("""
        SELECT ISNULL(MAX(Poliza), 0) + 1
        FROM SAVPoliza WITH (UPDLOCK, HOLDLOCK)
        WHERE Fuente = ?
    """, (fuente,))
    poliza = cursor.fetchone()[0]
    logger.debug("Siguiente Poliza (fuente={}): {}", fuente, poliza)
    return poliza
