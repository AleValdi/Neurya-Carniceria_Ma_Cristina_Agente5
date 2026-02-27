"""Procesador E1: Conciliacion de Pagos a Proveedores.

Los pagos a proveedores ya existen en SAVCheqPM (capturados manualmente
o por otro modulo). Este procesador CONCILIA y AFECTA: encuentra el
movimiento existente que corresponde al cargo del estado de cuenta,
marca Conciliada=1, genera la poliza contable, y actualiza la factura
de compras (SAVRecC) y el registro de pago (SAVRecPago).

Caracteristicas:
- Conciliacion: UPDATE SAVCheqPM SET Conciliada=1
- Poliza: INSERT SAVPoliza (2-8 lineas segun IVA/IEPS/retenciones)
  usando el NumPoliza pre-asignado del movimiento
- Factura compras: UPDATE SAVRecC SET Saldo=0, Estatus='Tot.Pagada'
- Registro pago: UPDATE SAVRecPago SET Estatus='Pagado', FPago, Banco, Referencia...
- Matching: por monto + fecha (+-2 dias) + cuenta bancaria
- Tipo existente en BD: 3 (Egreso con Factura), Clase: PAGOS A PROVEEDORES
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from loguru import logger

from config.settings import CUENTA_POR_NUMERO, CUENTAS_BANCARIAS
from src.models import (
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


CLASE_BD = 'PAGOS A PROVEEDORES'
TIPO_BD = 3  # Egreso con Factura


class ProcesadorConciliacionPagos:
    """Procesador para conciliacion de pagos a proveedores (E1)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.PAGO_PROVEEDOR]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan de conciliacion para pagos del dia.

        Para cada SPEI del estado de cuenta, busca en SAVCheqPM un movimiento
        no conciliado con el mismo monto y fecha similar. Si lo encuentra,
        genera la conciliacion y las lineas de poliza contable.

        Args:
            movimientos: Pagos SPEI del dia (del estado de cuenta).
            fecha: Fecha del cargo en estado de cuenta.
            cursor: Cursor para consultar SAVCheqPM.
        """
        plan = PlanEjecucion(
            tipo_proceso='CONCILIACION_PAGOS',
            descripcion=f'Conciliacion pagos {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin pagos a proveedores para este dia")
            return plan

        if cursor is None:
            plan.advertencias.append(
                "Sin conexion a BD: no se puede buscar pagos existentes"
            )
            return plan

        for mov in movimientos:
            match = _buscar_pago_en_bd(
                cursor, mov.monto, fecha, mov.cuenta_banco,
            )

            if match:
                conc = {
                    'tabla': 'SAVCheqPM',
                    'folio': match['folio'],
                    'campo': 'Conciliada',
                    'valor_nuevo': 1,
                    'descripcion': (
                        f"Folio {match['folio']}: "
                        f"${mov.monto:,.2f} | {match['concepto'][:50]}"
                    ),
                }

                # Generar lineas de poliza si tenemos datos de SAVCheqPMP
                if match.get('num_poliza') and match.get('proveedor'):
                    clave_cuenta = CUENTA_POR_NUMERO.get(match['cuenta'])
                    if clave_cuenta:
                        cfg = CUENTAS_BANCARIAS[clave_cuenta]
                        lineas = _construir_lineas_poliza_pago(
                            match, cfg.cuenta_contable, cfg.subcuenta_contable,
                        )
                        plan.lineas_poliza.extend(lineas)
                        conc['num_poliza'] = match['num_poliza']
                        conc['lineas_poliza'] = len(lineas)
                        conc['banco_nombre'] = match['banco']

                # Datos para actualizar factura de compras y registro de pago
                if match.get('factura_num_rec'):
                    conc['factura_serie'] = match.get('factura_serie', 'F')
                    conc['factura_num_rec'] = match['factura_num_rec']
                    conc['cuenta_banco'] = match.get('cuenta', '')
                    conc['tipo_egreso'] = match.get('tipo_egreso', '')
                    conc['banco_nombre'] = match.get('banco', 'BANREGIO')

                plan.conciliaciones.append(conc)
                plan.validaciones.append(
                    f"Match: SPEI ${mov.monto:,.2f} -> "
                    f"Folio {match['folio']} ({match['concepto'][:40]})"
                )
            else:
                # Buscar si ya esta conciliado
                ya_conc = _buscar_pago_ya_conciliado(
                    cursor, mov.monto, fecha, mov.cuenta_banco,
                )
                if ya_conc:
                    plan.ya_conciliados.append({
                        'folio': ya_conc['folio'],
                        'descripcion': (
                            f"Ya conciliado: Folio {ya_conc['folio']} "
                            f"({ya_conc['concepto'][:40]})"
                        ),
                    })
                    plan.validaciones.append(
                        f"Ya conciliado: SPEI ${mov.monto:,.2f} -> "
                        f"Folio {ya_conc['folio']}"
                    )
                else:
                    plan.advertencias.append(
                        f"Sin match para SPEI ${mov.monto:,.2f} del {fecha} "
                        f"({mov.descripcion[:50]})"
                    )

        plan.validaciones.append(
            f"Pagos: {len(movimientos)} en EdoCta, "
            f"{len(plan.conciliaciones)} conciliados"
        )

        return plan


def _buscar_pago_en_bd(
    cursor,
    monto: Decimal,
    fecha: date,
    cuenta_banco: str,
    tolerancia_dias: int = 0,
    tolerancia_monto: Decimal = Decimal('0.01'),
) -> Optional[Dict]:
    """Busca un pago no conciliado en SAVCheqPM que coincida.

    Criterios:
    - Misma cuenta bancaria
    - Tipo 3 (egreso con factura)
    - Conciliada = 0
    - Monto dentro de tolerancia
    - Fecha dentro de rango

    Al encontrar match, consulta SAVCheqPMP y SAVProveedor para obtener
    los datos necesarios para generar la poliza contable.
    """
    fecha_min = fecha - timedelta(days=tolerancia_dias)
    fecha_max = fecha + timedelta(days=tolerancia_dias)

    try:
        cursor.execute("""
            SELECT Folio, Egreso, Concepto, Dia, Mes, Age,
                   NumPoliza, Banco, Cuenta, TipoEgreso
            FROM SAVCheqPM
            WHERE Cuenta = ?
              AND Tipo = ?
              AND Conciliada = 0
              AND DATEFROMPARTS(Age, Mes, Dia) BETWEEN ? AND ?
              AND ABS(Egreso - ?) <= ?
            ORDER BY ABS(Egreso - ?) ASC
        """, (
            cuenta_banco,
            TIPO_BD,
            fecha_min.isoformat(),
            fecha_max.isoformat(),
            float(monto),
            float(tolerancia_monto),
            float(monto),
        ))

        row = cursor.fetchone()
        if not row:
            return None

        result = {
            'folio': row[0],
            'egreso': Decimal(str(row[1])),
            'concepto': row[2].strip() if row[2] else '',
            'dia': row[3],
            'mes': row[4],
            'age': row[5],
            'num_poliza': row[6],
            'banco': row[7].strip() if row[7] else '',
            'cuenta': row[8].strip() if row[8] else '',
            'tipo_egreso': row[9].strip() if row[9] else '',
        }

        # Consultar SAVCheqPMP para datos de poliza
        _enriquecer_con_pmp(cursor, result)

        return result

    except Exception as e:
        logger.warning("Error buscando pago en BD: {}", e)

    return None


def _buscar_pago_ya_conciliado(
    cursor,
    monto: Decimal,
    fecha: date,
    cuenta_banco: str,
    tolerancia_dias: int = 0,
    tolerancia_monto: Decimal = Decimal('0.01'),
) -> Optional[Dict]:
    """Busca un pago YA conciliado en SAVCheqPM.

    Se invoca solo cuando _buscar_pago_en_bd() no encuentra match
    (Conciliada=0). Si hay un pago con Conciliada=1 que matchea,
    significa que ya fue procesado previamente.
    """
    fecha_min = fecha - timedelta(days=tolerancia_dias)
    fecha_max = fecha + timedelta(days=tolerancia_dias)

    try:
        cursor.execute("""
            SELECT Folio, Egreso, Concepto
            FROM SAVCheqPM
            WHERE Cuenta = ?
              AND Tipo = ?
              AND Conciliada = 1
              AND DATEFROMPARTS(Age, Mes, Dia) BETWEEN ? AND ?
              AND ABS(Egreso - ?) <= ?
            ORDER BY ABS(Egreso - ?) ASC
        """, (
            cuenta_banco,
            TIPO_BD,
            fecha_min.isoformat(),
            fecha_max.isoformat(),
            float(monto),
            float(tolerancia_monto),
            float(monto),
        ))

        row = cursor.fetchone()
        if row:
            return {
                'folio': row[0],
                'egreso': Decimal(str(row[1])),
                'concepto': row[2].strip() if row[2] else '',
            }
    except Exception as e:
        logger.warning("Error buscando pago ya conciliado: {}", e)

    return None


def _enriquecer_con_pmp(cursor, result: Dict):
    """Consulta SAVCheqPMP y SAVProveedor para obtener datos de poliza y factura.

    Suma IVA, IEPS, RetIVA, RetISR de todas las facturas del pago.
    Obtiene nombre del proveedor de SAVProveedor.
    Obtiene Serie/NumRec de la factura vinculada para actualizar SAVRecC/SAVRecPago.
    """
    folio = result['folio']

    try:
        # Agregar impuestos y datos de factura vinculada
        cursor.execute("""
            SELECT TOP 1 Proveedor, TipoRecepcion,
                   SUM(Iva) OVER () as total_iva,
                   SUM(IEPS) OVER () as total_ieps,
                   SUM(RetencionIVA) OVER () as total_retiva,
                   SUM(RetencionISR) OVER () as total_retisr,
                   Serie, NumRec, MontoPago, MontoFactura,
                   PorcIva, MetododePago, RFC, TipoProveedor
            FROM SAVCheqPMP
            WHERE Folio = ?
        """, (folio,))

        row = cursor.fetchone()
        if not row:
            logger.debug("Sin SAVCheqPMP para Folio {}", folio)
            return

        result['proveedor'] = row[0].strip() if row[0] else ''
        result['tipo_recepcion'] = row[1].strip() if row[1] else ''
        result['iva'] = Decimal(str(row[2])) if row[2] else Decimal('0')
        result['ieps'] = Decimal(str(row[3])) if row[3] else Decimal('0')
        result['retencion_iva'] = Decimal(str(row[4])) if row[4] else Decimal('0')
        result['retencion_isr'] = Decimal(str(row[5])) if row[5] else Decimal('0')
        # Datos de la factura vinculada (SAVRecC/SAVRecPago)
        result['factura_serie'] = row[6].strip() if row[6] else 'F'
        result['factura_num_rec'] = row[7]
        result['monto_pago'] = Decimal(str(row[8])) if row[8] else Decimal('0')
        result['monto_factura'] = Decimal(str(row[9])) if row[9] else Decimal('0')
        result['porc_iva'] = Decimal(str(row[10])) if row[10] else Decimal('0')
        result['metodo_pago'] = row[11].strip() if row[11] else ''
        result['rfc'] = row[12].strip() if row[12] else ''
        result['tipo_proveedor'] = row[13].strip() if row[13] else ''

        # Obtener nombre del proveedor
        if result['proveedor']:
            cursor.execute("""
                SELECT Empresa FROM SAVProveedor WHERE Clave = ?
            """, (result['proveedor'],))
            prov_row = cursor.fetchone()
            if prov_row:
                result['nombre_proveedor'] = (
                    prov_row[0].strip() if prov_row[0] else ''
                )
            else:
                result['nombre_proveedor'] = ''

    except Exception as e:
        logger.warning(
            "Error consultando SAVCheqPMP/Proveedor para Folio {}: {}",
            folio, e,
        )


def _construir_lineas_poliza_pago(
    match: Dict,
    cuenta_contable_banco: str,
    subcuenta_contable_banco: str,
) -> List[LineaPoliza]:
    """Genera 2-8 lineas de poliza para un pago a proveedor.

    Slots fijos del ERP:
    1     = Proveedores CARGO (total egreso)
    2-3   = IVA reclasificacion (PTE PAGO → PAGADO)
    4-5   = RetIVA reclasificacion
    6-7   = IEPS reclasificacion
    8-9   = RetISR reclasificacion
    ultimo = Banco ABONO (total egreso)

    Secciones con monto 0 se omiten y los numeros de movimiento
    saltan los slots vacios (patron verificado en PROD).
    """
    folio = match['folio']
    egreso = match['egreso']
    proveedor = match.get('proveedor', '')
    nombre = match.get('nombre_proveedor', '')[:10]
    tipo_recepcion = match.get('tipo_recepcion', '')
    iva = match.get('iva', Decimal('0'))
    ieps = match.get('ieps', Decimal('0'))
    retencion_iva = match.get('retencion_iva', Decimal('0'))
    retencion_isr = match.get('retencion_isr', Decimal('0'))

    # IVA neto = IVA total - Retencion IVA
    iva_neto = iva - retencion_iva

    prefijo = f"Prov:{proveedor} Nombre:{nombre} "
    banco_nombre = match.get('banco', 'BANREGIO')

    lineas = []
    slot = 1

    # --- Slot 1: Proveedores CARGO (siempre) ---
    lineas.append(LineaPoliza(
        movimiento=slot,
        cuenta='2110',
        subcuenta='010000',
        tipo_ca=TipoCA.CARGO,
        cargo=egreso,
        abono=Decimal('0'),
        concepto=f"{prefijo}Total Pago: {folio} Suc.5",
        doc_tipo='CHEQUES',
    ))

    # --- Slots 2-3: IVA reclasificacion ---
    if iva_neto > 0:
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='1240',
            subcuenta='010000',
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=iva_neto,
            concepto=f"{prefijo}IVAPP Suc.5",
            doc_tipo='CHEQUES',
        ))
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='1246',
            subcuenta='010000',
            tipo_ca=TipoCA.CARGO,
            cargo=iva_neto,
            abono=Decimal('0'),
            concepto=f"{prefijo}IVAP Suc.5",
            doc_tipo='CHEQUES',
        ))

    # --- Slots 4-5: Retencion IVA reclasificacion ---
    if retencion_iva > 0:
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='2140',
            subcuenta='260000',
            tipo_ca=TipoCA.CARGO,
            cargo=retencion_iva,
            abono=Decimal('0'),
            concepto=f"{prefijo}RetIVAPP {tipo_recepcion}",
            doc_tipo='CHEQUES',
        ))
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='2140',
            subcuenta='290000',
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=retencion_iva,
            concepto=f"{prefijo}RetIVAP {tipo_recepcion}",
            doc_tipo='CHEQUES',
        ))

    # --- Slots 6-7: IEPS reclasificacion ---
    if ieps > 0:
        # Cuando hay RetIVA, IEPS va en slots 6-7 (saltar si no hay RetIVA)
        if retencion_iva > 0:
            slot = 6
        else:
            slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='1240',
            subcuenta='020000',
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=ieps,
            concepto=f"{prefijo}IEPSPP Suc.5",
            doc_tipo='CHEQUES',
        ))
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='1246',
            subcuenta='020000',
            tipo_ca=TipoCA.CARGO,
            cargo=ieps,
            abono=Decimal('0'),
            concepto=f"{prefijo}IEPSP Suc.5",
            doc_tipo='CHEQUES',
        ))

    # --- Slots 8-9: Retencion ISR reclasificacion ---
    if retencion_isr > 0:
        # RetISR siempre en slots 8-9
        slot = 8
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='2140',
            subcuenta='140000',
            tipo_ca=TipoCA.CARGO,
            cargo=retencion_isr,
            abono=Decimal('0'),
            concepto=f"{prefijo}RetISRPP {tipo_recepcion}",
            doc_tipo='CHEQUES',
        ))
        slot += 1
        lineas.append(LineaPoliza(
            movimiento=slot,
            cuenta='2140',
            subcuenta='320000',
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=retencion_isr,
            concepto=f"{prefijo}RetISRP {tipo_recepcion}",
            doc_tipo='CHEQUES',
        ))

    # --- Ultimo slot: Banco ABONO (siempre) ---
    # Cuando hay retenciones, los slots 6-7 (IEPS) se reservan
    # aunque esten vacios, asi que Banco salta al slot correspondiente
    if retencion_iva > 0 and slot < 7:
        slot = 7  # Saltar slots 6-7 reservados para IEPS
    slot += 1
    lineas.append(LineaPoliza(
        movimiento=slot,
        cuenta=cuenta_contable_banco,
        subcuenta=subcuenta_contable_banco,
        tipo_ca=TipoCA.ABONO,
        cargo=Decimal('0'),
        abono=egreso,
        concepto=f"Banco: {banco_nombre}. Folio Pago: {folio}",
        doc_tipo='CHEQUES',
    ))

    return lineas
