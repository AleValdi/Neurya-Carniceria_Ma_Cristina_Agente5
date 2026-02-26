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
    # HoraAlta/UltimoCambioHora: base 1899-12-30 + hora del dia (patron ERP)
    hora_base = datetime(1899, 12, 30, ahora.hour, ahora.minute, ahora.second)
    # TotalLetra en SAVRecC: sin parentesis (a diferencia de SAVCheqPM)
    total_letra_raw = numero_a_letra(datos.total)
    total_letra = total_letra_raw.removeprefix('( ').removesuffix(' )')
    # Patron produccion: {RFC}_REC_F{NumRec:06d}_{YYYYMMDD}
    factura_electronica = (
        f"{RFC_BANCO}_REC_{serie}{num_rec:06d}_{datos.fecha.strftime('%Y%m%d')}"
    )

    # INSERT SAVRecC (encabezado)
    try:
        cursor.execute("""
            INSERT INTO SAVRecC (
                Serie, NumRec, Proveedor, ProveedorNombre,
                Fecha, FacturaFecha, FechaAlta, UltimoCambio,
                FechaAltaHora, UltimoCambioHora,
                SubTotal1, SubTotal2, Iva, Total, Saldo, Pagado,
                Factura, FacturaElectronica, FacturaElectronicaTotal,
                Estatus, Procesada, Tipo,
                ProcesadaFecha, ProcesadaHora,
                Comprador, Capturo, CapturoCambio,
                TotalLetra, TotalRecibidoNeto,
                Consolidacion, Consolida,
                Articulos, Partidas, PartidasMovInv,
                Paridad, Moneda, MetododePago,
                Sucursal, NumOC,
                RFC, SerieRFC,
                TipoRecepcion, Referencia,
                Ciudad, Estado, TipoProveedor
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
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
            hora_base,                  # FechaAltaHora (1899-12-30 + hora)
            hora_base,                  # UltimoCambioHora
            datos.subtotal,             # SubTotal1
            datos.subtotal,             # SubTotal2 = SubTotal1
            datos.iva,
            datos.total,
            datos.total,                # Saldo = Total (pendiente de pago)
            Decimal('0'),               # Pagado = 0 (aun no procesada)
            datos.factura,              # Factura (DDMMAAAA o DDMMAAAAF)
            factura_electronica,        # FacturaElectronica
            Decimal('0'),               # FacturaElectronicaTotal
            'Pendiente',                # Estatus (pendiente hasta que el modulo de pagos procese)
            1,                          # Procesada = true (como produccion)
            'Credito',                  # Tipo (sin acento para compatibilidad ODBC)
            ahora,                      # ProcesadaFecha
            hora_base,                  # ProcesadaHora
            'AGENTE5',                  # Comprador
            'AGENTE5',                  # Capturo
            'AGENTE5',                  # CapturoCambio
            total_letra,                # TotalLetra (sin parentesis)
            datos.total,                # TotalRecibidoNeto = Total
            0,                          # Consolidacion
            0,                          # Consolida
            1,                          # Articulos (1 concepto: comision)
            1,                          # Partidas (1 linea de detalle)
            1,                          # PartidasMovInv = Partidas
            Decimal('20.00'),           # Paridad
            'PESOS',                    # Moneda
            'PUE',                      # MetododePago
            5,                          # Sucursal
            0,                          # NumOC
            RFC_BANCO,                  # RFC
            'DCM02072238A',             # SerieRFC (RFC de la empresa)
            'COMISIONES BANCARIAS',     # TipoRecepcion
            'CREDITO',                  # Referencia
            'MONTERREY',                # Ciudad (proveedor 001081)
            'NUEVO LEON',               # Estado (proveedor 001081)
            'NA',                       # TipoProveedor (como produccion)
        ))
    except Exception as e:
        logger.error(
            "TRUNCATION DEBUG SAVRecC: Factura='{}' ({}), "
            "FactElec='{}' ({}), TotalLetra='{}' ({}), "
            "Tipo='Credito' (7), TipoRecepcion='COMISIONES BANCARIAS' (20)",
            datos.factura, len(datos.factura),
            factura_electronica, len(factura_electronica),
            total_letra[:80], len(total_letra),
        )
        raise

    logger.debug(
        "INSERT SAVRecC: Serie={}, NumRec={}, Total=${:,.2f}, Estatus=Pendiente",
        serie, num_rec, datos.total,
    )

    # INSERT SAVRecD (detalle: 1 linea para comision)
    try:
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
            Decimal('0'),           # CostoImp (siempre 0 en produccion)
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
    except Exception as e:
        raise RuntimeError(f"Error INSERT SAVRecD: {e}") from e

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
