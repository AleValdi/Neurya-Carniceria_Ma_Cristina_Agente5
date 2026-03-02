"""Operaciones sobre SAVRecPago y SAVCheqPMP (pagos de facturas de compra).

Vincula un movimiento bancario (SAVCheqPM) con una factura de compra (SAVRecC)
a traves de la tabla de pagos programados (SAVRecPago) y el detalle de pago
del movimiento (SAVCheqPMP).

Usado por comisiones bancarias para replicar el flujo completo de PROD.
"""

from datetime import datetime
from decimal import Decimal

from loguru import logger


def insertar_rec_pago(
    cursor,
    serie: str,
    num_rec: int,
    proveedor: str,
    proveedor_nombre: str,
    fecha,
    monto: Decimal,
    banco: str,
    cuenta: str,
    folio: int,
    factura: str,
    tipo_recepcion: str = 'COMISIONES BANCARIAS',
    fpago: str = 'Transferencia',
    tipo_proveedor: str = 'NA',
) -> int:
    """Inserta un registro de pago en SAVRecPago.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        serie: Serie de la factura ('F').
        num_rec: NumRec de la factura (SAVRecC).
        proveedor: Clave del proveedor.
        proveedor_nombre: Nombre del proveedor.
        fecha: Fecha del pago.
        monto: Monto total pagado.
        banco: Banco del movimiento.
        cuenta: Cuenta bancaria.
        folio: Folio del movimiento en SAVCheqPM.
        factura: Referencia factura (DDMMAAAA).
        tipo_recepcion: Tipo de recepcion.

    Returns:
        Pago (consecutivo) asignado.
    """
    # Obtener siguiente Pago
    cursor.execute("SELECT ISNULL(MAX(Pago), 0) + 1 FROM SAVRecPago")
    pago = cursor.fetchone()[0]

    ahora = datetime.now()
    hora_alta = datetime(1899, 12, 30, ahora.hour, ahora.minute, ahora.second)
    referencia = f"{cuenta}F: {folio}"

    cursor.execute("""
        INSERT INTO SAVRecPago (
            Serie, NumRec, Pago, NumOC,
            Proveedor, Fecha, Monto, Moneda,
            FPago, Banco, Referencia, Estatus,
            Comprador, FechaFactura, Paridad,
            Capturo, Tipo, ProveedorNombre,
            Factura, UltimoCambio, UltimoCambioHora,
            SolicitudPago, TipoRecepcion, TipoProveedor,
            FechaRecep, MetododePago
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?
        )
    """, (
        serie,
        num_rec,
        pago,
        0,                          # NumOC
        proveedor,
        fecha,
        monto,
        'PESOS',
        fpago,                      # FPago
        banco,
        referencia,                 # "{cuenta}F: {folio}"
        'Pagado',                   # Estatus
        'AGENTE5',                  # Comprador
        fecha,                      # FechaFactura
        Decimal('1'),               # Paridad
        'AGENTE5',                  # Capturo
        'PAGO',                     # Tipo
        proveedor_nombre,
        factura,                    # DDMMAAAA
        ahora,                      # UltimoCambio
        hora_alta,                  # UltimoCambioHora
        1,                          # SolicitudPago
        tipo_recepcion,
        tipo_proveedor,             # TipoProveedor
        fecha,                      # FechaRecep
        'PUE',                      # MetododePago
    ))

    logger.debug(
        "INSERT SAVRecPago: Serie={}, NumRec={}, Pago={}, Monto=${:,.2f}",
        serie, num_rec, pago, monto,
    )

    return pago


def insertar_cheq_pmp(
    cursor,
    banco: str,
    cuenta: str,
    age: int,
    mes: int,
    folio: int,
    num_rec: int,
    pago: int,
    fecha,
    monto: Decimal,
    iva: Decimal,
    factura: str,
    proveedor: str,
    rfc: str = 'BRM940216EQ6',
    tipo_recepcion: str = 'COMISIONES BANCARIAS',
):
    """Inserta detalle de pago en SAVCheqPMP (vincula movimiento con factura).

    Args:
        cursor: Cursor activo (dentro de transaccion).
        banco: Banco del movimiento.
        cuenta: Cuenta bancaria.
        age: Anio.
        mes: Mes.
        folio: Folio del movimiento (SAVCheqPM).
        num_rec: NumRec de la factura (SAVRecC).
        pago: Pago de SAVRecPago.
        fecha: Fecha del movimiento.
        monto: Total del pago.
        iva: IVA del pago.
        factura: Referencia factura (DDMMAAAA).
        proveedor: Clave del proveedor.
        rfc: RFC del proveedor.
        tipo_recepcion: Tipo de recepcion.
    """
    cursor.execute("""
        INSERT INTO SAVCheqPMP (
            Banco, Cuenta, Age, Mes, Folio,
            NumRec, Pago, NumOC,
            FechaFactura, PagoAfectado,
            MontoFactura, MontoPago,
            Factura, Serie, Moneda, Paridad,
            Iva, RetencionIVA, RetencionISR, ParidadFactura,
            IEPS, Sucursal, PorcIva, NCredito,
            MetododePago, TipoRecepcion,
            ValorPagadoTasa15, ValorPagadoTasa10,
            ValorPagadoTasa8, ValorPagadoTasa0,
            ValorPagadoTasaExentos,
            ValorPagadoImpTasa15, ValorPagadoImpTasa10,
            ValorPagadoImpTasa8, ValorPagadoImpTasa0,
            ValorIvaRetenido, ValorIvaDevoluciones,
            RFC, Proveedor
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?
        )
    """, (
        banco,
        cuenta,
        age,
        mes,
        folio,
        num_rec,
        pago,
        0,                          # NumOC
        fecha,                      # FechaFactura
        1,                          # PagoAfectado = True
        monto,                      # MontoFactura
        monto,                      # MontoPago
        factura,                    # DDMMAAAA
        'F',                        # Serie
        'PESOS',
        Decimal('1'),               # Paridad
        iva,
        Decimal('0'),               # RetencionIVA
        Decimal('0'),               # RetencionISR
        Decimal('1'),               # ParidadFactura
        Decimal('0'),               # IEPS
        5,                          # Sucursal
        Decimal('16'),              # PorcIva
        Decimal('0'),               # NCredito
        'PUE',                      # MetododePago
        tipo_recepcion,
        Decimal('0'),               # ValorPagadoTasa15
        Decimal('0'),               # ValorPagadoTasa10
        Decimal('0'),               # ValorPagadoTasa8
        Decimal('0'),               # ValorPagadoTasa0
        Decimal('0'),               # ValorPagadoTasaExentos
        Decimal('0'),               # ValorPagadoImpTasa15
        Decimal('0'),               # ValorPagadoImpTasa10
        Decimal('0'),               # ValorPagadoImpTasa8
        Decimal('0'),               # ValorPagadoImpTasa0
        Decimal('0'),               # ValorIvaRetenido
        Decimal('0'),               # ValorIvaDevoluciones
        rfc,
        proveedor,
    ))

    logger.debug(
        "INSERT SAVCheqPMP: Folio={}, NumRec={}, Pago={}, Monto=${:,.2f}",
        folio, num_rec, pago, monto,
    )
