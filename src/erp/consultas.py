"""Consultas de solo lectura al ERP SAV7.

Funciones para obtener datos de referencia: IVA/IEPS de facturas,
configuracion de cuentas bancarias, etc.
"""

from decimal import Decimal
from typing import Dict, Optional, Tuple

from loguru import logger


def obtener_iva_ieps_factura(
    cursor,
    serie: str,
    num_fac: int,
    bd_produccion: str = 'DBSAV71',
) -> Tuple[Decimal, Decimal]:
    """Obtiene IVA e IEPS de una factura de venta en SAVFactC.

    IMPORTANTE: En SAVCheqPMF la serie es 'FD' pero en SAVFactC es 'D'.
    Esta funcion espera la serie de SAVFactC (normalmente 'D').

    Args:
        cursor: Cursor activo.
        serie: Serie de la factura en SAVFactC (ej: 'D').
        num_fac: Numero de factura.
        bd_produccion: Nombre de la BD de produccion para queries calificados.

    Returns:
        Tupla (iva, ieps). Ambos Decimal, 0 si no tiene.
    """
    # Primero intentar en la BD default (sandbox)
    cursor.execute("""
        SELECT ISNULL(Iva, 0), ISNULL(IEPS, 0)
        FROM SAVFactC
        WHERE Serie = ? AND NumFac = ?
    """, (serie, num_fac))

    row = cursor.fetchone()
    if row:
        iva = Decimal(str(row[0]))
        ieps = Decimal(str(row[1]))
        logger.debug(
            "SAVFactC {}-{}: IVA=${}, IEPS=${}",
            serie, num_fac, iva, ieps,
        )
        return (iva, ieps)

    # Si no encontro, intentar en produccion
    try:
        cursor.execute(f"""
            SELECT ISNULL(Iva, 0), ISNULL(IEPS, 0)
            FROM {bd_produccion}.dbo.SAVFactC
            WHERE Serie = ? AND NumFac = ?
        """, (serie, num_fac))

        row = cursor.fetchone()
        if row:
            iva = Decimal(str(row[0]))
            ieps = Decimal(str(row[1]))
            logger.debug(
                "SAVFactC (PROD) {}-{}: IVA=${}, IEPS=${}",
                serie, num_fac, iva, ieps,
            )
            return (iva, ieps)
    except Exception as e:
        logger.warning(
            "No se pudo consultar produccion para {}-{}: {}",
            serie, num_fac, e,
        )

    logger.warning("Factura {}-{} no encontrada en SAVFactC", serie, num_fac)
    return (Decimal('0'), Decimal('0'))


def obtener_cuenta_bancaria(
    cursor,
    banco: str,
    cuenta: str,
) -> Optional[Dict[str, str]]:
    """Obtiene configuracion de una cuenta bancaria de SAVCheq.

    Returns:
        Dict con CuentaC, SubCuentaC, Nombre, etc. o None si no existe.
    """
    cursor.execute("""
        SELECT CuentaC, SubCuentaC, Nombre, Moneda
        FROM SAVCheq
        WHERE Banco = ? AND Cuenta = ?
    """, (banco, cuenta))

    row = cursor.fetchone()
    if row:
        return {
            'CuentaC': row[0].strip() if row[0] else '',
            'SubCuentaC': row[1].strip() if row[1] else '',
            'Nombre': row[2].strip() if row[2] else '',
            'Moneda': row[3].strip() if row[3] else 'PESOS',
        }

    logger.warning("Cuenta bancaria {}/{} no encontrada", banco, cuenta)
    return None


def verificar_periodo_abierto(
    cursor,
    banco: str,
    cuenta: str,
    age: int,
    mes: int,
) -> bool:
    """Verifica que el periodo este abierto en SAVCheqP.

    El periodo debe existir con Estatus='ABIERTO' para poder insertar.
    """
    cursor.execute("""
        SELECT Estatus
        FROM SAVCheqP
        WHERE Banco = ? AND Cuenta = ? AND Age = ? AND Mes = ?
    """, (banco, cuenta, age, mes))

    row = cursor.fetchone()
    if row is None:
        logger.warning(
            "Periodo {}/{} Age={} Mes={} no existe",
            banco, cuenta, age, mes,
        )
        return False

    estatus = row[0].strip() if row[0] else ''
    if estatus != 'ABIERTO':
        logger.warning(
            "Periodo {}/{} Age={} Mes={} tiene estatus '{}'",
            banco, cuenta, age, mes, estatus,
        )
        return False

    return True
