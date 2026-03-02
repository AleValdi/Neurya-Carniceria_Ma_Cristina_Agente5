"""Procesador: Pagos a Proveedores desde Cuenta de Gastos.

A diferencia de E1 (conciliacion_pagos), los pagos desde la cuenta de gastos
(055003730157) NO pre-existen en SAVCheqPM. Este procesador CREA el movimiento
bancario buscando la factura correspondiente en SAVRecC por monto.

Flujo:
1. Para cada egreso en cuenta gastos, buscar factura no pagada en SAVRecC
   (match por monto, Saldo > 0)
2. Construir DatosMovimientoPM (Tipo 3, TipoEgreso='TARJETA')
3. Generar poliza contable (4 lineas: Proveedores + IVA reclasif + Banco)
4. Al ejecutar: INSERT SAVCheqPM + SAVCheqPMP, UPDATE SAVRecPago + SAVRecC

Verificado contra PROD (Folio 127306):
- SAVCheqPM: Tipo=3, Clase='PAGOS A PROVEEDORES', TipoEgreso='TARJETA'
- SAVCheqPMP: vincula folio con factura, TipoRecepcion del proveedor
- SAVRecPago: SolicitudPago=1, Estatus='Pagado', FPago='TARJETA',
  TipoProveedor=(preserva el original de SAVRecPago), Referencia='055003730157F: {folio}'
- SAVPoliza: 4 lineas (Proveedores CARGO, IVA PTE PAGO, IVA PAGADO, Banco ABONO)
"""

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from loguru import logger

from config.settings import CUENTAS_BANCARIAS, CUENTA_POR_NUMERO
from src.models import (
    DatosMovimientoPM,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


CLASE_BD = 'PAGOS A PROVEEDORES'
TIPO_BD = 3  # Egreso con Factura
CUENTA_GASTOS = '055003730157'


class ProcesadorPagoGastos:
    """Procesador para pagos a proveedores desde cuenta de gastos."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.PAGO_GASTOS]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan de pago para egresos de la cuenta de gastos.

        Para cada egreso del estado de cuenta, busca en SAVRecC una factura
        no pagada con monto similar. Si la encuentra, genera el movimiento
        bancario, poliza contable, y datos para vincular con la factura.

        Args:
            movimientos: Egresos del dia en cuenta de gastos.
            fecha: Fecha del cargo en estado de cuenta.
            cursor: Cursor para consultar SAVRecC.
        """
        plan = PlanEjecucion(
            tipo_proceso='PAGO_GASTOS',
            descripcion=f'Pagos gastos {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin egresos en cuenta gastos")
            return plan

        if cursor is None:
            plan.advertencias.append(
                "Sin conexion a BD: no se puede buscar facturas"
            )
            return plan

        cfg = CUENTAS_BANCARIAS['gastos']

        for mov in movimientos:
            match = _buscar_factura_no_pagada(cursor, mov.monto)

            if match:
                # Construir movimiento bancario
                datos_pm = _crear_datos_movimiento(mov, match, fecha, cfg)
                plan.movimientos_pm.append(datos_pm)

                # Construir poliza (4+ lineas)
                lineas = _construir_lineas_poliza(
                    match, mov.monto,
                    cfg.cuenta_contable, cfg.subcuenta_contable,
                )
                plan.lineas_poliza.extend(lineas)
                plan.lineas_por_movimiento.append(len(lineas))
                plan.facturas_por_movimiento.append(0)  # Sin SAVCheqPMF

                # Guardar datos de la factura para ejecutar
                plan.pagos_factura_existente.append(match)

                plan.validaciones.append(
                    f"Match: ${mov.monto:,.2f} -> {match['serie']}-{match['num_rec']} "
                    f"({match['proveedor']} {match['nombre_empresa'][:30]})"
                )
            else:
                # Buscar si ya esta pagada (idempotencia)
                ya_pagada = _buscar_factura_ya_pagada(cursor, mov.monto)
                if ya_pagada:
                    plan.ya_conciliados.append({
                        'folio': 0,
                        'descripcion': (
                            f"Ya pagada: {ya_pagada['serie']}-{ya_pagada['num_rec']} "
                            f"${ya_pagada['total']:,.2f} "
                            f"({ya_pagada['nombre_empresa'][:30]})"
                        ),
                    })
                    plan.validaciones.append(
                        f"Ya pagada: ${mov.monto:,.2f} -> "
                        f"{ya_pagada['serie']}-{ya_pagada['num_rec']}"
                    )
                else:
                    plan.advertencias.append(
                        f"Sin factura para egreso ${mov.monto:,.2f} del {fecha} "
                        f"({mov.descripcion[:50]})"
                    )

        plan.validaciones.append(
            f"Gastos: {len(movimientos)} egresos, "
            f"{len(plan.movimientos_pm)} con factura"
        )

        return plan


def _buscar_factura_no_pagada(
    cursor,
    monto: Decimal,
    tolerancia: Decimal = Decimal('0.50'),
) -> Optional[Dict]:
    """Busca una factura no pagada en SAVRecC con monto similar.

    Busca facturas donde Saldo > 0 y Total ≈ monto del egreso bancario.
    Obtiene datos del proveedor y de SAVRecPago para la vinculacion.
    """
    try:
        # Forzar encoding latin-1 para varchar con caracteres especiales
        import pyodbc as _pyodbc
        cursor.connection.setdecoding(_pyodbc.SQL_CHAR, encoding='latin-1')
    except Exception:
        pass

    try:
        cursor.execute("""
            SELECT TOP 1
                r.Serie, r.NumRec, r.Total, r.Saldo, r.Iva,
                r.Proveedor, r.ProveedorNombre, r.RFC,
                r.Factura, r.Fecha, r.Estatus,
                ISNULL(rp.TipoRecepcion, '') as TipoRecepcion,
                ISNULL(rp.Pago, 0) as Pago,
                ISNULL(rp.MetododePago, '') as MetododePago,
                ISNULL(CAST(prov.Empresa AS NVARCHAR(120)), '') as NombreEmpresa,
                ISNULL(rp.TipoProveedor, 'NA') as TipoProveedor
            FROM SAVRecC r
            LEFT JOIN SAVRecPago rp
                ON rp.Serie = r.Serie AND rp.NumRec = r.NumRec
            LEFT JOIN SAVProveedor prov
                ON prov.Clave = r.Proveedor
            WHERE r.Saldo > 0
              AND r.Estatus NOT IN ('Tot.Pagada', 'Cancelada')
              AND ABS(r.Total - ?) <= ?
            ORDER BY ABS(r.Total - ?) ASC, r.Fecha DESC
        """, (float(monto), float(tolerancia), float(monto)))

        row = cursor.fetchone()
        if not row:
            return None

        iva = Decimal(str(row[4])) if row[4] else Decimal('0')

        return {
            'serie': row[0].strip() if row[0] else 'F',
            'num_rec': row[1],
            'total': Decimal(str(row[2])),
            'saldo': Decimal(str(row[3])),
            'iva': iva,
            'proveedor': row[5].strip() if row[5] else '',
            'proveedor_nombre': row[6].strip() if row[6] else '',
            'rfc': row[7].strip() if row[7] else '',
            'factura': row[8].strip() if row[8] else '',
            'fecha': row[9],
            'estatus': row[10].strip() if row[10] else '',
            'tipo_recepcion': row[11].strip() if row[11] else '',
            'pago_rec': row[12],
            'metodo_pago': row[13].strip() if row[13] else 'PUE',
            'nombre_empresa': row[14].strip() if row[14] else '',
            'tipo_proveedor': row[15].strip() if row[15] else 'NA',
        }

    except Exception as e:
        logger.warning("Error buscando factura no pagada: {}", e)
        return None


def _buscar_factura_ya_pagada(
    cursor,
    monto: Decimal,
    tolerancia: Decimal = Decimal('0.50'),
) -> Optional[Dict]:
    """Busca una factura YA pagada con monto similar (idempotencia)."""
    try:
        cursor.execute("""
            SELECT TOP 1
                r.Serie, r.NumRec, r.Total,
                ISNULL(CAST(prov.Empresa AS NVARCHAR(120)), '') as NombreEmpresa
            FROM SAVRecC r
            LEFT JOIN SAVProveedor prov ON prov.Clave = r.Proveedor
            WHERE r.Estatus = 'Tot.Pagada'
              AND ABS(r.Total - ?) <= ?
            ORDER BY ABS(r.Total - ?) ASC
        """, (float(monto), float(tolerancia), float(monto)))

        row = cursor.fetchone()
        if row:
            return {
                'serie': row[0].strip() if row[0] else 'F',
                'num_rec': row[1],
                'total': Decimal(str(row[2])),
                'nombre_empresa': row[3].strip() if row[3] else '',
            }
    except Exception as e:
        logger.warning("Error buscando factura ya pagada: {}", e)
    return None


def _crear_datos_movimiento(
    mov: MovimientoBancario,
    match: Dict,
    fecha: date,
    cfg,
) -> DatosMovimientoPM:
    """Crea DatosMovimientoPM para un pago desde cuenta gastos."""
    return DatosMovimientoPM(
        banco=cfg.banco,
        cuenta=cfg.cuenta,
        age=fecha.year,
        mes=fecha.month,
        dia=fecha.day,
        tipo=TIPO_BD,
        ingreso=Decimal('0'),
        egreso=mov.monto,
        concepto='PAGO DE FACTURAS DE COMPRAS',
        clase=CLASE_BD,
        fpago=None,
        tipo_egreso='TARJETA',
        conciliada=1,
        paridad=Decimal('1.0000'),
        tipo_poliza='EGRESO',
        proveedor=match['proveedor'],
        proveedor_nombre=match['nombre_empresa'][:60],
        tipo_proveedor=match.get('tipo_proveedor', 'NA'),
        pago_afectado=True,
        num_pagos=1,
        estatus='Afectado',
        rfc=match['rfc'],
        num_factura=None,
        cheque_para=match['nombre_empresa'][:60] if match['nombre_empresa'] else None,
        valor_pagado_tasa15=match['total'] - match['iva'],
        valor_pagado_imp_tasa15=match['iva'],
    )


def _construir_lineas_poliza(
    match: Dict,
    monto: Decimal,
    cuenta_contable_banco: str,
    subcuenta_contable_banco: str,
) -> List[LineaPoliza]:
    """Genera lineas de poliza para un pago desde cuenta gastos.

    Estructura verificada contra PROD (4 lineas basicas):
    1. Proveedores CARGO (2110/010000) — total egreso
    2. IVA PTE PAGO ABONO (1240/010000) — IVA
    3. IVA PAGADO CARGO (1246/010000) — IVA
    4. Banco ABONO (cuenta gastos) — total egreso
    """
    proveedor = match.get('proveedor', '')
    nombre = match.get('nombre_empresa', '')[:10]
    iva = match.get('iva', Decimal('0'))

    prefijo = f"Prov:{proveedor} Nombre:{nombre} "

    lineas = []
    slot = 1

    # --- Slot 1: Proveedores CARGO (siempre) ---
    lineas.append(LineaPoliza(
        movimiento=slot,
        cuenta='2110',
        subcuenta='010000',
        tipo_ca=TipoCA.CARGO,
        cargo=monto,
        abono=Decimal('0'),
        concepto=f"{prefijo}Total Pago: {{folio}} Suc.5",
        doc_tipo='CHEQUES',
    ))

    # --- Slots 2-3: IVA reclasificacion ---
    if iva > 0:
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='1240',
            subcuenta='010000',
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=iva,
            concepto=f"{prefijo}IVAPP Suc.5",
            doc_tipo='CHEQUES',
        ))
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='1246',
            subcuenta='010000',
            tipo_ca=TipoCA.CARGO,
            cargo=iva,
            abono=Decimal('0'),
            concepto=f"{prefijo}IVAP Suc.5",
            doc_tipo='CHEQUES',
        ))

    # --- Ultimo: Banco ABONO (siempre) ---
    slot += 1
    lineas.append(LineaPoliza(
        movimiento=slot,
        cuenta=cuenta_contable_banco,
        subcuenta=subcuenta_contable_banco,
        tipo_ca=TipoCA.ABONO,
        cargo=Decimal('0'),
        abono=monto,
        concepto="Banco: BANREGIO. Folio Pago: {folio}",
        doc_tipo='CHEQUES',
    ))

    return lineas
