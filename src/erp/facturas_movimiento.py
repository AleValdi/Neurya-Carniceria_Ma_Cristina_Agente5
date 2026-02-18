"""Operaciones sobre SAVCheqPMF (facturas vinculadas a movimientos).

INSERT de facturas asociadas a movimientos bancarios de tipo venta.
"""

from datetime import datetime
from decimal import Decimal

from loguru import logger

from src.models import DatosFacturaPMF


def insertar_factura_movimiento(
    cursor,
    datos: DatosFacturaPMF,
    banco: str,
    cuenta: str,
    age: int,
    mes: int,
    folio: int,
    dia: int,
    sucursal: int = 5,
):
    """Inserta una factura vinculada a un movimiento en SAVCheqPMF.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        datos: Datos de la factura.
        banco, cuenta, age, mes, folio: Claves del movimiento padre.
        dia: Dia del movimiento.
        sucursal: Sucursal (default: 5).
    """
    cursor.execute("""
        INSERT INTO SAVCheqPMF (
            Banco, Cuenta, Age, Mes, Folio, Sucursal,
            Serie, NumFactura,
            Ingreso, FechaFactura,
            TipoFactura, MontoFactura, SaldoFactura,
            Dia, FechaIngreso, PosterioralDeposito
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?
        )
    """, (
        banco,
        cuenta,
        age,
        mes,
        folio,
        sucursal,
        datos.serie,
        datos.num_factura,
        datos.ingreso,
        datos.fecha_factura,
        datos.tipo_factura,
        datos.monto_factura,
        datos.saldo_factura,
        dia,
        datetime.now(),
        0,  # PosterioralDeposito
    ))

    logger.debug(
        "INSERT SAVCheqPMF: Folio={}, {}-{} ({}), ${}",
        folio, datos.serie, datos.num_factura,
        datos.tipo_factura, datos.ingreso,
    )
