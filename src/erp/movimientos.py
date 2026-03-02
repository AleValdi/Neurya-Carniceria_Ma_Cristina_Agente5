"""Operaciones sobre SAVCheqPM (movimientos bancarios).

INSERT y consultas de movimientos en la tabla principal de bancos.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from loguru import logger

from src.erp.utils import numero_a_letra
from src.models import DatosMovimientoPM


def insertar_movimiento(
    cursor, datos: DatosMovimientoPM, folio: int,
    desfase_segundos: int = 0,
) -> int:
    """Inserta un movimiento bancario en SAVCheqPM.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        datos: Datos del movimiento.
        folio: Folio asignado por consecutivos.
        desfase_segundos: Offset en segundos para evitar colision de PK
            cuando se insertan multiples movimientos en la misma transaccion
            (PK incluye FechaAlta+HoraAlta, precision de 1 segundo).

    Returns:
        Folio del movimiento insertado.
    """
    ahora = datetime.now()
    # HoraAlta usa base 1899-12-30 con la hora del dia
    hora_base = datetime(1899, 12, 30, ahora.hour, ahora.minute, ahora.second)
    hora_alta = hora_base + timedelta(seconds=desfase_segundos) if desfase_segundos else hora_base
    # FechaAlta y UltimoCambio: fecha sin hora (00:00:00.000)
    fecha_alta = datetime(ahora.year, ahora.month, ahora.day)
    # FechaMov = fecha del movimiento bancario (Age/Mes/Dia), sin hora
    fecha_mov = datetime(datos.age, datos.mes, datos.dia)

    # TotalLetra: generar automaticamente si no viene pre-calculado
    monto = datos.ingreso if datos.ingreso > 0 else datos.egreso
    total_letra = datos.total_letra or numero_a_letra(monto)

    cursor.execute("""
        INSERT INTO SAVCheqPM (
            Banco, Cuenta, Age, Mes, Dia, Tipo, Folio,
            Ingreso, Egreso, Concepto, Clase, FPago, TipoEgreso,
            Conciliada, Paridad, ParidadDOF, Moneda,
            Cia, Fuente, Oficina, CuentaOficina,
            TipoPoliza, NumPoliza,
            Capturo, Sucursal, Saldo,
            FechaAlta, HoraAlta, FechaMov, UltimoCambio,
            NumFactura,
            Referencia, Referencia2, TotalLetra,
            Proveedor, ProveedorNombre, TipoProveedor,
            NumCheque,
            ConciliadaCapturo, ChequePara,
            PagoAfectado, NumPagos, FechaChequeCobrado,
            ValorPagadoTasa15, ValorPagadoImpTasa15,
            Estatus, RFC, LeyendaEspecial
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?
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
        datos.paridad_dof,
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
        fecha_alta,
        hora_alta,
        fecha_mov,
        fecha_alta,                         # UltimoCambio = FechaAlta (sin hora)
        datos.num_factura,
        datos.referencia,
        datos.referencia2,
        total_letra,
        datos.proveedor or '',
        datos.proveedor_nombre or '',
        datos.tipo_proveedor or '',
        datos.num_cheque,
        'AGENTE5' if datos.conciliada else None,  # ConciliadaCapturo
        datos.cheque_para,
        datos.pago_afectado,
        datos.num_pagos,
        fecha_alta,                         # FechaChequeCobrado = FechaAlta
        datos.valor_pagado_tasa15,
        datos.valor_pagado_imp_tasa15,
        datos.estatus,
        datos.rfc,
        datos.leyenda_especial,
    ))

    logger.debug(
        "INSERT SAVCheqPM: Folio={}, Tipo={}, {}=${}, Concepto='{}'",
        folio, datos.tipo,
        'Ingreso' if datos.ingreso > 0 else 'Egreso',
        datos.ingreso if datos.ingreso > 0 else datos.egreso,
        datos.concepto[:50],
    )

    return folio


def actualizar_num_poliza(
    cursor,
    folio: int,
    num_poliza: int,
    poliza_cargos: Decimal = Decimal('0'),
    poliza_abonos: Decimal = Decimal('0'),
):
    """Actualiza NumPoliza y totales de poliza despues de crear la poliza."""
    cursor.execute("""
        UPDATE SAVCheqPM
        SET NumPoliza = ?, PolizaCargos = ?, PolizaAbonos = ?
        WHERE Folio = ?
    """, (num_poliza, poliza_cargos, poliza_abonos, folio))

    logger.debug(
        "UPDATE SAVCheqPM: Folio={} → NumPoliza={}, "
        "PolizaCargos=${:,.2f}, PolizaAbonos=${:,.2f}",
        folio, num_poliza, poliza_cargos, poliza_abonos,
    )


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


def buscar_movimiento_existente(
    cursor,
    banco: str,
    cuenta: str,
    dia: int,
    mes: int,
    age: int,
    monto: Decimal,
    es_ingreso: bool,
) -> Optional[Tuple[int, bool]]:
    """Busca un movimiento existente por cuenta+fecha+monto+direccion.

    Busca sin filtrar por concepto para encontrar registros manuales
    que pudieran haberse ingresado con diferente redaccion.
    Prioriza movimientos NO conciliados (candidatos para conciliar).

    Args:
        cursor: Cursor activo.
        banco: Banco del movimiento.
        cuenta: Cuenta bancaria.
        dia, mes, age: Fecha del movimiento.
        monto: Monto a buscar.
        es_ingreso: True si es ingreso, False si es egreso.

    Returns:
        Tupla (Folio, ya_conciliado) si existe, None si no.
    """
    campo_monto = 'Ingreso' if es_ingreso else 'Egreso'
    cursor.execute("""
        SELECT TOP 1 Folio, Conciliada
        FROM SAVCheqPM
        WHERE Banco = ? AND Cuenta = ?
          AND Age = ? AND Mes = ? AND Dia = ?
          AND {} = ?
        ORDER BY Conciliada ASC, FechaAlta ASC
    """.format(campo_monto), (banco, cuenta, age, mes, dia, monto))

    row = cursor.fetchone()
    if row:
        return (row[0], bool(row[1]))
    return None


def conciliar_movimiento(cursor, folio: int):
    """Marca un movimiento existente como conciliado y afectado."""
    cursor.execute("""
        UPDATE SAVCheqPM
        SET Conciliada = 1,
            PagoAfectado = 1,
            Estatus = 'Afectado',
            ConciliadaCapturo = 'AGENTE5'
        WHERE Folio = ? AND Conciliada = 0
    """, (folio,))
    logger.info("Conciliado movimiento existente: Folio={}", folio)
