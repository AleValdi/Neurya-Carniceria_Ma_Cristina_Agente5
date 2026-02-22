"""Operaciones sobre SAVPoliza (polizas contables).

INSERT de lineas de poliza contable vinculadas a movimientos bancarios.
"""

from datetime import datetime
from decimal import Decimal
from typing import List

from loguru import logger

from config.settings import NOMBRES_CUENTAS
from src.models import LineaPoliza


def insertar_poliza(
    cursor,
    num_poliza: int,
    lineas: List[LineaPoliza],
    folio: int,
    fecha: datetime,
    tipo_poliza: str,
    concepto_encabezado: str,
    cia: str = 'DCM',
    fuente: str = 'SAV7-CHEQUES',
    oficina: str = '01',
    sucursal: int = 5,
):
    """Inserta todas las lineas de una poliza contable.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        num_poliza: Numero de poliza asignado por consecutivos.
        lineas: Lista de LineaPoliza a insertar.
        folio: Folio del movimiento bancario padre.
        fecha: Fecha de la poliza (DocFecha = fecha del movimiento).
        tipo_poliza: 'INGRESO', 'EGRESO', o 'DIARIO'.
        concepto_encabezado: Concepto general de la poliza.
    """
    ahora = datetime.now()
    hora_alta = datetime(1899, 12, 30, ahora.hour, ahora.minute, ahora.second)

    for linea in lineas:
        # Resolver nombre de cuenta: usar el de la linea, o buscar en catalogo
        nombre = linea.nombre or NOMBRES_CUENTAS.get(
            (linea.cuenta, linea.subcuenta), ''
        )

        cursor.execute("""
            INSERT INTO SAVPoliza (
                Cia, Fuente, Poliza, Oficina,
                DocTipo, Movimiento,
                CuentaOficina, Cuenta, SubCuenta, Nombre,
                TipoCA, Cargo, Abono,
                Concepto, DocFolio,
                TipoPoliza, DocFecha,
                MovimientoFecha, MovimientoHora,
                Capturo, Sucursal,
                TipoCambio, Moneda
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?
            )
        """, (
            cia,
            fuente,
            num_poliza,
            oficina,
            linea.doc_tipo,
            linea.movimiento,
            oficina,  # CuentaOficina = Oficina
            linea.cuenta,
            linea.subcuenta,
            nombre,
            linea.tipo_ca.value,
            linea.cargo,
            linea.abono,
            linea.concepto[:60],
            folio,
            tipo_poliza,
            fecha,
            fecha,
            hora_alta,
            'AGENTE5',
            sucursal,
            Decimal('1.0000'),
            'PESOS',
        ))

    logger.debug(
        "INSERT SAVPoliza: Poliza={}, {} lineas, DocFolio={}, Tipo={}",
        num_poliza, len(lineas), folio, tipo_poliza,
    )
