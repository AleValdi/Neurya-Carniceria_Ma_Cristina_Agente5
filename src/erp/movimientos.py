"""Operaciones sobre SAVCheqPM (movimientos bancarios).

INSERT y consultas de movimientos en la tabla principal de bancos.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from loguru import logger

from src.models import DatosMovimientoPM


def insertar_movimiento(cursor, datos: DatosMovimientoPM, folio: int) -> int:
    """Inserta un movimiento bancario en SAVCheqPM.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        datos: Datos del movimiento.
        folio: Folio asignado por consecutivos.

    Returns:
        Folio del movimiento insertado.
    """
    ahora = datetime.now()
    # HoraAlta usa base 1899-12-30 con la hora del dia
    hora_alta = datetime(1899, 12, 30, ahora.hour, ahora.minute, ahora.second)

    cursor.execute("""
        INSERT INTO SAVCheqPM (
            Banco, Cuenta, Age, Mes, Dia, Tipo, Folio,
            Ingreso, Egreso, Concepto, Clase, FPago, TipoEgreso,
            Conciliada, Paridad, ParidadDOF, Moneda,
            Cia, Fuente, Oficina, CuentaOficina,
            TipoPoliza, NumPoliza,
            Capturo, Sucursal, Saldo,
            FechaAlta, HoraAlta,
            NumFactura
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?
        )
    """, (
        datos.banco,
        datos.cuenta,
        datos.age,
        datos.mes,
        datos.dia,
        datos.tipo,
        folio,
        datos.ingreso,
        datos.egreso,
        datos.concepto,
        datos.clase,
        datos.fpago,
        datos.tipo_egreso,
        datos.conciliada,
        datos.paridad,
        Decimal('20.0000'),  # ParidadDOF
        'PESOS',
        'DCM',
        'SAV7-CHEQUES',
        '01',
        '01',
        datos.tipo_poliza,
        0,  # NumPoliza se actualiza despues
        'AGENTE5',
        5,
        Decimal('0'),
        ahora,
        hora_alta,
        datos.num_factura,
    ))

    logger.debug(
        "INSERT SAVCheqPM: Folio={}, Tipo={}, {}=${}, Concepto='{}'",
        folio, datos.tipo,
        'Ingreso' if datos.ingreso > 0 else 'Egreso',
        datos.ingreso if datos.ingreso > 0 else datos.egreso,
        datos.concepto[:50],
    )

    return folio


def actualizar_num_poliza(cursor, folio: int, num_poliza: int):
    """Actualiza el NumPoliza de un movimiento despues de crear la poliza."""
    cursor.execute("""
        UPDATE SAVCheqPM
        SET NumPoliza = ?
        WHERE Folio = ?
    """, (num_poliza, folio))

    logger.debug("UPDATE SAVCheqPM: Folio={} → NumPoliza={}", folio, num_poliza)


def existe_movimiento(
    cursor,
    banco: str,
    cuenta: str,
    dia: int,
    mes: int,
    age: int,
    concepto: str,
    monto: Decimal,
) -> bool:
    """Verifica si ya existe un movimiento similar (idempotencia).

    Busca por cuenta + dia + concepto similar + monto exacto.
    """
    cursor.execute("""
        SELECT COUNT(*)
        FROM SAVCheqPM
        WHERE Banco = ? AND Cuenta = ?
          AND Age = ? AND Mes = ? AND Dia = ?
          AND Concepto = ?
          AND (Ingreso = ? OR Egreso = ?)
    """, (banco, cuenta, age, mes, dia, concepto, monto, monto))

    count = cursor.fetchone()[0]
    return count > 0
