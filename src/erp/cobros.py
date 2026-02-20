"""Operaciones sobre SAVFactCob y SAVFactC para cobros a clientes.

Permite crear cobros programaticamente (equivalente a Crea Cobro Multiple
en el modulo Comercial del ERP) y actualizar el saldo de facturas.
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from loguru import logger


def buscar_factura_cliente(
    cursor,
    serie: str,
    num_fac: int,
) -> Optional[Dict]:
    """Busca una factura de cliente en SAVFactC.

    Retorna info completa de la factura para construir el cobro.
    No filtra por Saldo > 0 para permitir validacion posterior.

    Args:
        cursor: Cursor activo.
        serie: Serie de la factura (ej: 'FC').
        num_fac: Numero de factura.

    Returns:
        Dict con datos de la factura, o None si no existe.
    """
    cursor.execute("""
        SELECT Serie, NumFac, Cliente, Total, Saldo, SubTotal1,
               Iva, IEPS, Estatus, Vendedor, Fecha
        FROM SAVFactC
        WHERE Serie = ? AND NumFac = ?
    """, (serie, num_fac))

    row = cursor.fetchone()
    if row:
        return {
            'Serie': row[0],
            'NumFac': row[1],
            'Cliente': row[2].strip() if row[2] else '',
            'Total': Decimal(str(row[3])),
            'Saldo': Decimal(str(row[4])),
            'SubTotal1': Decimal(str(row[5])),
            'Iva': Decimal(str(row[6])),
            'IEPS': Decimal(str(row[7])),
            'Estatus': row[8].strip() if row[8] else '',
            'Vendedor': row[9].strip() if row[9] else '',
            'Fecha': row[10],
        }
    return None


def buscar_factura_por_monto(
    cursor,
    monto: Decimal,
    fecha,
    serie: str = 'FC',
    tolerancia_dias: int = 5,
    tolerancia_monto: Decimal = Decimal('0.01'),
) -> Optional[Dict]:
    """Busca factura pendiente de cobro por monto exacto (fallback).

    Util cuando no se puede parsear el numero de factura del EdoCta.
    Prioriza facturas mas cercanas a la fecha.

    Args:
        cursor: Cursor activo.
        monto: Monto a buscar.
        fecha: Fecha del EdoCta.
        serie: Serie de factura (default: 'FC').
        tolerancia_dias: Rango de fecha.
        tolerancia_monto: Tolerancia en monto.

    Returns:
        Dict con datos de la factura, o None.
    """
    from datetime import timedelta
    fecha_min = fecha - timedelta(days=tolerancia_dias)
    fecha_max = fecha + timedelta(days=tolerancia_dias)

    cursor.execute("""
        SELECT TOP 1 Serie, NumFac, Cliente, Total, Saldo, SubTotal1,
               Iva, IEPS, Estatus, Vendedor, Fecha
        FROM SAVFactC
        WHERE Serie = ?
          AND Saldo > 0
          AND ABS(Total - ?) <= ?
          AND Fecha BETWEEN ? AND ?
        ORDER BY ABS(DATEDIFF(day, Fecha, ?)) ASC
    """, (
        serie,
        float(monto),
        float(tolerancia_monto),
        fecha_min.isoformat(),
        fecha_max.isoformat(),
        fecha.isoformat(),
    ))

    row = cursor.fetchone()
    if row:
        return {
            'Serie': row[0],
            'NumFac': row[1],
            'Cliente': row[2].strip() if row[2] else '',
            'Total': Decimal(str(row[3])),
            'Saldo': Decimal(str(row[4])),
            'SubTotal1': Decimal(str(row[5])),
            'Iva': Decimal(str(row[6])),
            'IEPS': Decimal(str(row[7])),
            'Estatus': row[8].strip() if row[8] else '',
            'Vendedor': row[9].strip() if row[9] else '',
            'Fecha': row[10],
        }
    return None


def obtener_nombre_cliente(cursor, cliente: str) -> str:
    """Obtiene el nombre del cliente desde SAVFactC (ultimo registro).

    Usa SAVFactC porque no siempre existe SAVCliente separado.
    """
    cursor.execute("""
        SELECT TOP 1 ClienteNombre
        FROM SAVFactCob
        WHERE Cliente = ?
        ORDER BY Cobro DESC
    """, (cliente,))

    row = cursor.fetchone()
    if row and row[0]:
        return row[0].strip()
    return ''


def obtener_siguiente_cobro(cursor) -> int:
    """Obtiene el siguiente numero de Cobro para SAVFactCob.

    Debe llamarse dentro de una transaccion activa.
    """
    cursor.execute("""
        SELECT ISNULL(MAX(Cobro), 0) + 1
        FROM SAVFactCob WITH (UPDLOCK, HOLDLOCK)
    """)
    cobro = cursor.fetchone()[0]
    logger.debug("Siguiente Cobro: {}", cobro)
    return cobro


def obtener_siguiente_cobro_multiple(cursor) -> int:
    """Obtiene el siguiente numero de CobroMultiple para SAVFactCob.

    Debe llamarse dentro de una transaccion activa.
    """
    cursor.execute("""
        SELECT ISNULL(MAX(CobroMultiple), 0) + 1
        FROM SAVFactCob WITH (UPDLOCK, HOLDLOCK)
    """)
    cm = cursor.fetchone()[0]
    logger.debug("Siguiente CobroMultiple: {}", cm)
    return cm


def insertar_cobro_factcob(
    cursor,
    datos,
    cobro: int,
    cobro_multiple: int,
):
    """Inserta un cobro en SAVFactCob.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        datos: DatosCobroCliente con la informacion del cobro.
        cobro: Numero de cobro asignado.
        cobro_multiple: Numero de CobroMultiple asignado.
    """
    ahora = datetime.now()
    hora = datetime(1899, 12, 30, ahora.hour, ahora.minute, ahora.second)

    # Determinar desglose fiscal
    # Si la factura tiene IVA 16%, SubTotalIva16 = SubTotal, SubTotalIva0 = 0
    # Si no tiene IVA, SubTotalIva0 = Total
    subtotal_iva0 = datos.subtotal_iva0
    subtotal_iva16 = datos.subtotal_iva16
    if subtotal_iva0 == 0 and subtotal_iva16 == 0:
        # Default: todo va a tasa 0 si no hay desglose
        subtotal_iva0 = datos.monto

    cursor.execute("""
        INSERT INTO SAVFactCob (
            Serie, NumFac, Cobro, NumPed, Cliente,
            Fecha, Monto, Moneda, FPago, Banco,
            Referencia, Estatus, Vendedor, FechaFactura,
            Paridad, Capturo, Tipo, BancoDeposito,
            ClienteNombre, CobroMultiple,
            Devuelto, DevueltoComision,
            BancoCuenta, SerieCFD, FacturaCFD,
            UltimoCambio, UltimoCambioHora,
            TipoFactura, Recordatorio, FechaAdicional,
            UltimoCambioH, EnviadoFecha, Importado,
            Sucursal, Revisado, Cobrador,
            Parcialidad, SaldoAnterior, SaldoPendiente,
            Ubicacion,
            IEPS, Iva, IvaRetencion, ISRRetencion,
            SubTotalIEPS6, SubTotalIEPS7, SubTotalIEPS8, SubTotalIEPS9,
            SubTotalIva0, SubTotalIva8, SubTotalIva16,
            SubTotalIvaExento, SubTotalIvaNoObjeto,
            SubTotalIvaRetencion4, SubTotalIvaRetencion6
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?
        )
    """, (
        datos.serie,                        # Serie
        datos.num_fac,                      # NumFac
        cobro,                              # Cobro
        0,                                  # NumPed
        datos.cliente,                      # Cliente
        datos.fecha_cobro,                  # Fecha
        datos.monto,                        # Monto
        'PESOS',                            # Moneda
        'Transferencia',                    # FPago
        datos.banco,                        # Banco
        '',                                 # Referencia
        'Cobrado',                          # Estatus
        datos.vendedor,                     # Vendedor
        datos.fecha_factura,                # FechaFactura
        Decimal('1'),                       # Paridad
        'AGENTE5',                          # Capturo
        'COBRO',                            # Tipo
        datos.banco,                        # BancoDeposito
        datos.cliente_nombre,               # ClienteNombre
        cobro_multiple,                     # CobroMultiple
        0,                                  # Devuelto
        Decimal('0'),                       # DevueltoComision
        datos.cuenta_banco,                 # BancoCuenta
        datos.serie,                        # SerieCFD
        datos.num_fac,                      # FacturaCFD
        ahora,                              # UltimoCambio
        hora,                               # UltimoCambioHora
        'NORMAL',                           # TipoFactura
        0,                                  # Recordatorio
        datos.fecha_cobro,                  # FechaAdicional
        ahora,                              # UltimoCambioH
        ahora,                              # EnviadoFecha
        0,                                  # Importado
        5,                                  # Sucursal
        0,                                  # Revisado
        'NA',                               # Cobrador
        1,                                  # Parcialidad
        datos.monto,                        # SaldoAnterior
        Decimal('0'),                       # SaldoPendiente
        0,                                  # Ubicacion
        datos.ieps,                         # IEPS
        datos.iva,                          # Iva
        Decimal('0'),                       # IvaRetencion
        Decimal('0'),                       # ISRRetencion
        Decimal('0'),                       # SubTotalIEPS6
        Decimal('0'),                       # SubTotalIEPS7
        Decimal('0'),                       # SubTotalIEPS8
        Decimal('0'),                       # SubTotalIEPS9
        subtotal_iva0,                      # SubTotalIva0
        Decimal('0'),                       # SubTotalIva8
        subtotal_iva16,                     # SubTotalIva16
        Decimal('0'),                       # SubTotalIvaExento
        Decimal('0'),                       # SubTotalIvaNoObjeto
        Decimal('0'),                       # SubTotalIvaRetencion4
        Decimal('0'),                       # SubTotalIvaRetencion6
    ))

    logger.debug(
        "INSERT SAVFactCob: Cobro={}, CM={}, {}-{}, ${}, Cliente={}",
        cobro, cobro_multiple, datos.serie, datos.num_fac,
        datos.monto, datos.cliente,
    )


def actualizar_factura_cobrada(
    cursor,
    serie: str,
    num_fac: int,
    monto_cobro: Decimal,
):
    """Actualiza SAVFactC despues de aplicar un cobro.

    Reduce el saldo y cambia estatus a 'Tot.Cobrada' si queda en 0.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        serie: Serie de la factura.
        num_fac: Numero de factura.
        monto_cobro: Monto cobrado.
    """
    cursor.execute("""
        UPDATE SAVFactC
        SET Saldo = Saldo - ?,
            Estatus = CASE
                WHEN (Saldo - ?) <= 0 THEN 'Tot.Cobrada'
                ELSE Estatus
            END
        WHERE Serie = ? AND NumFac = ?
    """, (monto_cobro, monto_cobro, serie, num_fac))

    logger.debug(
        "UPDATE SAVFactC: {}-{} Saldo -= ${} ",
        serie, num_fac, monto_cobro,
    )
