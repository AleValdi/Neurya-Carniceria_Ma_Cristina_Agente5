"""Validacion cruzada entre fuentes de datos.

Verifica consistencia entre estado de cuenta, tesoreria y BD
antes de generar movimientos.
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from loguru import logger

from src.models import CorteVentaDiaria, MovimientoBancario, TipoProceso


def validar_venta_tdc(
    movimientos_tdc: List[MovimientoBancario],
    corte_venta: Optional[CorteVentaDiaria],
    tolerancia: Decimal = Decimal('1.00'),
) -> List[str]:
    """Valida montos de venta TDC contra tesoreria.

    Verifica que la suma de abonos TDC del dia en el estado de cuenta
    sea igual al total TDC reportado en tesoreria.

    Returns:
        Lista de mensajes de error. Lista vacia = todo OK.
    """
    errores = []

    if not movimientos_tdc:
        return errores

    suma_edo_cuenta = sum(m.monto for m in movimientos_tdc)

    if corte_venta is None:
        errores.append(
            f"Sin datos de tesoreria para validar. "
            f"Suma estado de cuenta: ${suma_edo_cuenta:,.2f}"
        )
        return errores

    if corte_venta.total_tdc is None:
        errores.append("Tesoreria sin total TDC para el dia")
        return errores

    diferencia = abs(suma_edo_cuenta - corte_venta.total_tdc)

    if diferencia > tolerancia:
        errores.append(
            f"DISCREPANCIA TDC: "
            f"Estado de cuenta=${suma_edo_cuenta:,.2f}, "
            f"Tesoreria=${corte_venta.total_tdc:,.2f}, "
            f"Diferencia=${diferencia:,.2f} "
            f"(tolerancia=${tolerancia:,.2f})"
        )
    else:
        logger.info(
            "Validacion TDC OK: EdoCta=${:,.2f} ≈ Tesoreria=${:,.2f} (dif=${:,.2f})",
            suma_edo_cuenta, corte_venta.total_tdc, diferencia,
        )

    return errores


def validar_venta_efectivo(
    movimientos_efectivo: List[MovimientoBancario],
    corte_venta: Optional[CorteVentaDiaria],
    tolerancia: Decimal = Decimal('1.00'),
) -> List[str]:
    """Valida montos de venta en efectivo contra tesoreria.

    Returns:
        Lista de mensajes de error. Lista vacia = todo OK.
    """
    errores = []

    if not movimientos_efectivo:
        return errores

    suma_edo_cuenta = sum(m.monto for m in movimientos_efectivo)

    if corte_venta is None:
        errores.append(
            f"Sin datos de tesoreria. "
            f"Suma estado de cuenta: ${suma_edo_cuenta:,.2f}"
        )
        return errores

    if corte_venta.total_efectivo is None:
        errores.append("Tesoreria sin total efectivo para el dia")
        return errores

    diferencia = abs(suma_edo_cuenta - corte_venta.total_efectivo)

    if diferencia > tolerancia:
        errores.append(
            f"DISCREPANCIA EFECTIVO: "
            f"Estado de cuenta=${suma_edo_cuenta:,.2f}, "
            f"Tesoreria=${corte_venta.total_efectivo:,.2f}, "
            f"Diferencia=${diferencia:,.2f}"
        )

    return errores
