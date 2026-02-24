"""Operaciones sobre SAVRecC/SAVRecD (facturas de compra).

INSERT para facturas de compra generadas por comisiones bancarias.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from loguru import logger

from src.erp.utils import numero_a_letra
from src.models import DatosCompraPM

RFC_BANCO = 'BRM940216EQ6'


def insertar_factura_compra(
    cursor,
    datos: DatosCompraPM,
    serie: str = 'F',
    num_rec: Optional[int] = None,
) -> int:
    """Inserta una factura de compra en SAVRecC + SAVRecD.

    Si num_rec es None, obtiene el siguiente disponible.

    Args:
        cursor: Cursor activo (dentro de transaccion).
        datos: Datos de la compra.
        serie: Serie de la recepcion (default 'F').
        num_rec: Numero de recepcion (si None, calcula MAX+1).

    Returns:
        NumRec asignado.
    """
    # Obtener siguiente NumRec si no se proporciona
    if num_rec is None:
        num_rec = _siguiente_num_rec(cursor, serie)

    ahora = datetime.now()
    total_letra = numero_a_letra(datos.total)
    # Patron produccion: {RFC}_REC_F{NumRec:06d}_{YYYYMMDD}
    factura_electronica = (
        f"{RFC_BANCO}_REC_{serie}{num_rec:06d}_{datos.fecha.strftime('%Y%m%d')}"
    )

    # INSERT SAVRecC (encabezado)
    cursor.execute("""
        INSERT INTO SAVRecC (
            Serie, NumRec, Proveedor, ProveedorNombre,
            Fecha, FacturaFecha, FechaAlta, UltimoCambio,
            SubTotal1, SubTotal2, Iva, Total, Saldo,
            Factura, FacturaElectronica,
            Estatus, Procesada, Tipo,
            Comprador, Capturo, CapturoCambio,
            TotalLetra,
            Consolidacion, Consolida,
            Articulos, Partidas,
            Paridad, Moneda, MetododePago,
            Sucursal, NumOC,
            RFC, TipoRecepcion, Referencia
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?
        )
    """, (
        serie,
        num_rec,
        datos.proveedor,
        'BANCO REGIONAL',
        datos.fecha,                # Fecha
        datos.fecha,                # FacturaFecha = Fecha
        ahora,                      # FechaAlta
        ahora,                      # UltimoCambio
        datos.subtotal,             # SubTotal1
        datos.subtotal,             # SubTotal2 = SubTotal1
        datos.iva,
        datos.total,
        Decimal('0'),               # Saldo = 0 (pagada inmediata)
        datos.factura,              # Factura (DDMMAAAA o DDMMAAAAF)
        factura_electronica,        # FacturaElectronica
        'Tot.Pagada',               # Estatus (como produccion)
        1,                          # Procesada = true (como produccion)
        'Crédito',                  # Tipo (como produccion)
        'AGENTE5',                  # Comprador
        'AGENTE5',                  # Capturo
        'AGENTE5',                  # CapturoCambio
        total_letra,                # TotalLetra
        0,                          # Consolidacion
        0,                          # Consolida
        1,                          # Articulos (1 concepto: comision)
        1,                          # Partidas (1 linea de detalle)
        Decimal('20.00'),           # Paridad
        'PESOS',                    # Moneda
        'PUE',                      # MetododePago
        5,                          # Sucursal
        0,                          # NumOC
        RFC_BANCO,                  # RFC
        'COMISIONES BANCARIAS',     # TipoRecepcion
        'CREDITO',                  # Referencia
    ))

    logger.debug(
        "INSERT SAVRecC: Serie={}, NumRec={}, Total=${:,.2f}, Estatus=Tot.Pagada",
        serie, num_rec, datos.total,
    )

    # INSERT SAVRecD (detalle: 1 linea para comision)
    cursor.execute("""
        INSERT INTO SAVRecD (
            Serie, NumRec, Producto, Talla, Nombre, Proveedor,
            Cantidad, Costo, CostoImp, PorcIva,
            Unidad, Orden,
            PorcDesc, NumOC, Unidad2Valor, Servicio,
            Registro1, ControlTalla, PorcDesc2, Pedimento,
            ComplementoIva, RetencionIvaPorc, RetencionISRPorc,
            IEPSPorc, CantidadUM2,
            PorcDesc3, PorcDesc4, PorcDesc5, PorcDesc6,
            CantidadRegalo, Precio, Lotes, CantidadNeta,
            CostoDif, UltimoCostoC
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?
        )
    """, (
        serie,
        num_rec,
        '001002002',            # Producto: COMISION TERMINAL
        '001',                  # Talla (default produccion)
        'COMISION TERMINAL',    # Nombre
        datos.proveedor,        # Proveedor
        Decimal('1.0000'),      # Cantidad
        datos.subtotal,         # Costo unitario (sin IVA)
        datos.total,            # Costo con impuestos
        Decimal('16.0000'),     # PorcIva (16%)
        'PZA',                  # Unidad
        1,                      # Orden
        Decimal('0'), 0, Decimal('1'), 1,       # PorcDesc, NumOC, Unidad2Valor, Servicio
        1, 0, Decimal('0'), 0,                  # Registro1, ControlTalla, PorcDesc2, Pedimento
        Decimal('0'), Decimal('0'), Decimal('0'),  # ComplementoIva, RetIva, RetISR
        Decimal('0'), Decimal('0'),             # IEPSPorc, CantidadUM2
        Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0'),  # PorcDesc3-6
        Decimal('0'), Decimal('0'), 0, Decimal('1'),  # CantidadRegalo, Precio, Lotes, CantidadNeta
        Decimal('0'), Decimal('0'),             # CostoDif, UltimoCostoC
    ))

    logger.debug(
        "INSERT SAVRecD: Serie={}, NumRec={}, Subtotal=${:,.2f}",
        serie, num_rec, datos.subtotal,
    )

    return num_rec


def _siguiente_num_rec(cursor, serie: str) -> int:
    """Obtiene el siguiente NumRec disponible para una serie."""
    cursor.execute("""
        SELECT ISNULL(MAX(NumRec), 0) + 1
        FROM SAVRecC
        WHERE Serie = ?
    """, (serie,))

    return cursor.fetchone()[0]
