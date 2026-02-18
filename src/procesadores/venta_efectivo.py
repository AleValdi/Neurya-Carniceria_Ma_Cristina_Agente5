"""Procesador I2: Ventas en Efectivo.

Genera movimientos bancarios (SAVCheqPM Tipo 4) para depositos de efectivo
en la cuenta de cheques (055003730017).

Caracteristicas:
- Cada deposito del estado de cuenta = 1 movimiento en SAVCheqPM
- Cada movimiento lleva TODAS las facturas en SAVCheqPMF:
  - N facturas INDIVIDUAL (del reporte de tesoreria)
  - 1 factura GLOBAL (remanente despues de individuales)
- Poliza variable:
  Linea 1: Cargo Banco (1120/040000) = total deposito
  Por cada factura (individual + global):
    - Abono Clientes (1210/010000) = importe factura
    - Abono IVA Cobrado (2141/010000) + Cargo IVA Pte (2146/010000) si IVA > 0
    - Abono IEPS Cobrado (2141/020000) + Cargo IEPS Pte (2146/020000) si IEPS > 0
- FPago: siempre 'Efectivo'
- Concepto: 'VENTA DIARIA DD/MM/AAAA' (fecha del CORTE, no del deposito)
- El monto del movimiento viene del deposito en estado de cuenta
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from loguru import logger

from config.settings import CuentasContables
from src.erp.consultas import obtener_iva_ieps_factura
from src.models import (
    CorteVentaDiaria,
    DatosFacturaPMF,
    DatosMovimientoPM,
    FacturaVenta,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


# Constantes para cuenta efectivo
BANCO = 'BANREGIO'
CUENTA_EFECTIVO = '055003730017'
CLASE = 'VENTA DIARIA'
TIPO_MOVIMIENTO = 4  # Ingreso Venta Diaria


class ProcesadorVentaEfectivo:
    """Procesador para ventas en efectivo (I2)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.VENTA_EFECTIVO]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        corte_venta: Optional[CorteVentaDiaria] = None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan para depositos de efectivo de un dia.

        Args:
            movimientos: Depositos de efectivo del dia (del estado de cuenta).
            fecha: Fecha del deposito (dia del estado de cuenta).
            cursor: Cursor para consultar SAVFactC (IVA/IEPS).
            corte_venta: Datos de tesoreria del dia de la venta.
        """
        plan = PlanEjecucion(
            tipo_proceso='VENTA_EFECTIVO',
            descripcion=f'Ventas efectivo {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin depositos de efectivo para este dia")
            return plan

        if corte_venta is None:
            plan.advertencias.append(
                f"Sin datos de tesoreria para la fecha de venta. "
                f"No se pueden determinar facturas."
            )
            return plan

        if not corte_venta.factura_global_numero:
            plan.advertencias.append("Corte de tesoreria sin factura global")
            return plan

        num_factura_global = corte_venta.factura_global_numero
        importe_global = corte_venta.factura_global_importe or Decimal('0')
        fecha_corte = corte_venta.fecha_corte
        facturas_ind = corte_venta.facturas_individuales

        # Concepto usa fecha del CORTE de venta, no del deposito
        concepto = f"VENTA DIARIA {fecha_corte.strftime('%d/%m/%Y')}"

        # Para efectivo: normalmente 1 deposito por dia de venta
        # Cada deposito genera 1 movimiento con TODAS las facturas
        for mov in movimientos:
            monto_deposito = mov.monto

            # Calcular aplicacion a factura global = deposito - individuales
            suma_individuales = sum(f.importe for f in facturas_ind)
            aplicacion_global = monto_deposito - suma_individuales

            if aplicacion_global < 0:
                plan.advertencias.append(
                    f"Suma de facturas individuales (${suma_individuales:,.2f}) "
                    f"excede el deposito (${monto_deposito:,.2f})"
                )
                aplicacion_global = Decimal('0')

            # --- SAVCheqPM ---
            datos_pm = DatosMovimientoPM(
                banco=BANCO,
                cuenta=CUENTA_EFECTIVO,
                age=fecha.year,
                mes=fecha.month,
                dia=fecha.day,
                tipo=TIPO_MOVIMIENTO,
                ingreso=monto_deposito,
                egreso=Decimal('0'),
                concepto=concepto,
                clase=CLASE,
                fpago='Efectivo',
                tipo_egreso='NA',
                conciliada=1,
                paridad=Decimal('1.0000'),
                tipo_poliza='INGRESO',
                num_factura=f'D-{num_factura_global}',
            )
            plan.movimientos_pm.append(datos_pm)

            # --- SAVCheqPMF (INDIVIDUAL + GLOBAL) ---
            # Facturas individuales primero
            for fact_ind in facturas_ind:
                datos_pmf = DatosFacturaPMF(
                    serie='FD',
                    num_factura=fact_ind.numero,
                    ingreso=fact_ind.importe,
                    fecha_factura=fecha_corte,
                    tipo_factura='INDIVIDUAL',
                    monto_factura=fact_ind.importe,
                    saldo_factura=Decimal('0'),
                )
                plan.facturas_pmf.append(datos_pmf)

            # Factura global al final
            datos_pmf_global = DatosFacturaPMF(
                serie='FD',
                num_factura=num_factura_global,
                ingreso=aplicacion_global,
                fecha_factura=fecha_corte,
                tipo_factura='GLOBAL',
                monto_factura=importe_global,
                saldo_factura=Decimal('0'),
            )
            plan.facturas_pmf.append(datos_pmf_global)

            # --- SAVPoliza (variable) ---
            lineas = _generar_poliza_venta_efectivo(
                monto_total=monto_deposito,
                facturas_individuales=facturas_ind,
                aplicacion_global=aplicacion_global,
                num_factura_global=num_factura_global,
                cursor=cursor,
            )
            plan.lineas_poliza.extend(lineas)

            # Tracking: N individuales + 1 global facturas, variable lineas
            n_facturas = len(facturas_ind) + 1  # individuales + global
            plan.facturas_por_movimiento.append(n_facturas)
            plan.lineas_por_movimiento.append(len(lineas))

        # Validaciones
        suma_depositos = sum(m.monto for m in movimientos)
        plan.validaciones.append(
            f"Suma depositos efectivo: ${suma_depositos:,.2f}"
        )
        if corte_venta.total_efectivo:
            plan.validaciones.append(
                f"Total efectivo tesoreria: ${corte_venta.total_efectivo:,.2f}"
            )
        plan.validaciones.append(
            f"Facturas: {len(facturas_ind)} individuales + 1 global"
        )

        return plan


def _generar_poliza_venta_efectivo(
    monto_total: Decimal,
    facturas_individuales: List[FacturaVenta],
    aplicacion_global: Decimal,
    num_factura_global: str,
    cursor=None,
) -> List[LineaPoliza]:
    """Genera lineas de poliza para un movimiento de venta en efectivo.

    Estructura:
    Linea 1: Cargo Banco Efectivo (1120/040000) = total
    Por cada factura (individual + global):
      - Abono Clientes (1210/010000) = importe
      - Abono IVA Cobrado + Cargo IVA Pte (si IVA > 0)
      - Abono IEPS Cobrado + Cargo IEPS Pte (si IEPS > 0)
    """
    lineas = []
    mov_num = 1

    cta_banco = CuentasContables.BANCO_EFECTIVO

    # Linea 1: Cargo Banco
    lineas.append(LineaPoliza(
        movimiento=mov_num,
        cuenta=cta_banco[0],
        subcuenta=cta_banco[1],
        tipo_ca=TipoCA.CARGO,
        cargo=monto_total,
        abono=Decimal('0'),
        concepto=f"Banco: BANREGIO. FactG: FD-{num_factura_global} FolioI: {{folio}}",
    ))
    mov_num += 1

    # Facturas individuales
    for fact in facturas_individuales:
        iva, ieps = _obtener_iva_ieps(cursor, fact.numero)
        lineas_fact = _lineas_factura(
            mov_inicio=mov_num,
            importe=fact.importe,
            iva=iva,
            ieps=ieps,
            num_factura=fact.numero,
            tipo_factura='INDIVIDUAL',
        )
        lineas.extend(lineas_fact)
        mov_num += len(lineas_fact)

    # Factura global al final
    iva_global, ieps_global = _obtener_iva_ieps(cursor, num_factura_global)
    lineas_global = _lineas_factura(
        mov_inicio=mov_num,
        importe=aplicacion_global,
        iva=iva_global,
        ieps=ieps_global,
        num_factura=num_factura_global,
        tipo_factura='GLOBAL',
    )
    lineas.extend(lineas_global)

    return lineas


def _obtener_iva_ieps(
    cursor, num_factura: str,
) -> Tuple[Decimal, Decimal]:
    """Obtiene IVA/IEPS de una factura, (0, 0) si no hay cursor."""
    if cursor is None:
        return (Decimal('0'), Decimal('0'))
    try:
        return obtener_iva_ieps_factura(cursor, 'D', int(num_factura))
    except (ValueError, Exception) as e:
        logger.warning(
            "Error obteniendo IVA/IEPS para factura {}: {}", num_factura, e,
        )
        return (Decimal('0'), Decimal('0'))


def _lineas_factura(
    mov_inicio: int,
    importe: Decimal,
    iva: Decimal,
    ieps: Decimal,
    num_factura: str,
    tipo_factura: str,
) -> List[LineaPoliza]:
    """Genera lineas de poliza para una factura (individual o global).

    Retorna 1-5 lineas dependiendo de si hay IVA y/o IEPS.
    """
    lineas = []
    prefijo = 'FactG' if tipo_factura == 'GLOBAL' else 'FactI'
    concepto_base = f"Clase:VENTA DIARIA {prefijo}: FD-{num_factura}"

    cta_clientes = CuentasContables.CLIENTES_GLOBAL
    cta_iva_cobrado = CuentasContables.IVA_ACUMULABLE_COBRADO
    cta_iva_pte = CuentasContables.IVA_ACUMULABLE_PTE_COBRO
    cta_ieps_cobrado = CuentasContables.IEPS_ACUMULABLE_COBRADO
    cta_ieps_pte = CuentasContables.IEPS_ACUMULABLE_PTE_COBRO

    # Abono Clientes
    lineas.append(LineaPoliza(
        movimiento=mov_inicio,
        cuenta=cta_clientes[0],
        subcuenta=cta_clientes[1],
        tipo_ca=TipoCA.ABONO,
        cargo=Decimal('0'),
        abono=importe,
        concepto=f"{concepto_base} Cob.",
    ))

    # IVA (si > 0)
    if iva > Decimal('0'):
        lineas.append(LineaPoliza(
            movimiento=mov_inicio + len(lineas),
            cuenta=cta_iva_cobrado[0],
            subcuenta=cta_iva_cobrado[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=iva,
            concepto=f"{concepto_base} Iva.",
        ))
        lineas.append(LineaPoliza(
            movimiento=mov_inicio + len(lineas),
            cuenta=cta_iva_pte[0],
            subcuenta=cta_iva_pte[1],
            tipo_ca=TipoCA.CARGO,
            cargo=iva,
            abono=Decimal('0'),
            concepto=f"{concepto_base} Iva.",
        ))

    # IEPS (si > 0)
    if ieps > Decimal('0'):
        lineas.append(LineaPoliza(
            movimiento=mov_inicio + len(lineas),
            cuenta=cta_ieps_cobrado[0],
            subcuenta=cta_ieps_cobrado[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=ieps,
            concepto=f"{concepto_base} Ieps.",
        ))
        lineas.append(LineaPoliza(
            movimiento=mov_inicio + len(lineas),
            cuenta=cta_ieps_pte[0],
            subcuenta=cta_ieps_pte[1],
            tipo_ca=TipoCA.CARGO,
            cargo=ieps,
            abono=Decimal('0'),
            concepto=f"{concepto_base} Ieps.",
        ))

    return lineas
