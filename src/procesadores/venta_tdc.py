"""Procesador I1: Ventas con Tarjeta de Credito/Debito.

Genera movimientos bancarios (SAVCheqPM Tipo 4) para abonos TDC/TDD
en la cuenta de tarjeta (038900320016).

Caracteristicas:
- Cada abono del estado de cuenta = 1 movimiento en SAVCheqPM
- Cada movimiento lleva SOLO la factura GLOBAL en SAVCheqPMF
- Poliza fija de 6 lineas por movimiento:
  1. Cargo Banco (1120/060000)
  2. Abono Clientes (1210/010000)
  3. Abono IVA Cobrado (2141/010000)
  4. Cargo IVA Pte Cobro (2146/010000)
  5. Abono IEPS Cobrado (2141/020000)
  6. Cargo IEPS Pte Cobro (2146/020000)
- FPago: 'Tarjeta Debito' o 'Tarjeta Credito' (segun patron TDD/TDC)
- Concepto: 'VENTA DIARIA DD/MM/AAAA' (fecha del CORTE, no del deposito)
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from loguru import logger

from config.settings import CuentasContables
from src.erp.consultas import obtener_iva_ieps_factura
from src.models import (
    CorteVentaDiaria,
    DatosFacturaPMF,
    DatosMovimientoPM,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


# Constantes para cuenta tarjeta
BANCO = 'BANREGIO'
CUENTA_TARJETA = '038900320016'
CLASE = 'VENTA DIARIA'
TIPO_MOVIMIENTO = 4  # Ingreso Venta Diaria


class ProcesadorVentaTDC:
    """Procesador para ventas con tarjeta de credito/debito (I1)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.VENTA_TDC, TipoProceso.VENTA_TDD]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        corte_venta: Optional[CorteVentaDiaria] = None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan para abonos TDC/TDD de un dia.

        Args:
            movimientos: Abonos TDC/TDD del dia (del estado de cuenta).
            fecha: Fecha del deposito (dia del estado de cuenta).
            cursor: Cursor para consultar SAVFactC (IVA/IEPS).
            corte_venta: Datos de tesoreria del dia de la venta.
        """
        plan = PlanEjecucion(
            tipo_proceso='VENTA_TDC',
            descripcion=f'Ventas tarjeta {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin movimientos TDC para este dia")
            return plan

        # Validar que tenemos corte de tesoreria
        if corte_venta is None:
            plan.advertencias.append(
                f"Sin datos de tesoreria para la fecha de venta. "
                f"No se puede determinar factura global."
            )
            return plan

        # Obtener factura global
        if not corte_venta.factura_global_numero:
            plan.advertencias.append("Corte de tesoreria sin factura global")
            return plan

        num_factura_global = corte_venta.factura_global_numero
        importe_global = corte_venta.factura_global_importe or Decimal('0')
        fecha_corte = corte_venta.fecha_corte

        # Obtener IVA/IEPS de la factura global (Serie D en SAVFactC)
        iva_global = Decimal('0')
        ieps_global = Decimal('0')
        if cursor is not None:
            iva_global, ieps_global = obtener_iva_ieps_factura(
                cursor, 'D', int(num_factura_global),
            )
        else:
            plan.advertencias.append(
                "Sin conexion a BD: IVA/IEPS se calcularan como 0"
            )

        # Concepto usa la fecha del CORTE de venta, no la fecha del deposito
        concepto = f"VENTA DIARIA {fecha_corte.strftime('%d/%m/%Y')}"

        # Generar un movimiento por cada abono TDC/TDD
        for mov in movimientos:
            monto = mov.monto

            # Determinar forma de pago por tipo
            if mov.tipo_proceso == TipoProceso.VENTA_TDC:
                fpago = 'Tarjeta Crédito'
            else:
                fpago = 'Tarjeta Débito'

            # --- SAVCheqPM ---
            datos_pm = DatosMovimientoPM(
                banco=BANCO,
                cuenta=CUENTA_TARJETA,
                age=fecha.year,
                mes=fecha.month,
                dia=fecha.day,
                tipo=TIPO_MOVIMIENTO,
                ingreso=monto,
                egreso=Decimal('0'),
                concepto=concepto,
                clase=CLASE,
                fpago=fpago,
                tipo_egreso='NA',
                conciliada=1,
                paridad=Decimal('1.0000'),
                tipo_poliza='INGRESO',
                num_factura=f'D-{num_factura_global}',
            )
            plan.movimientos_pm.append(datos_pm)

            # --- SAVCheqPMF (solo GLOBAL) ---
            datos_pmf = DatosFacturaPMF(
                serie='FD',
                num_factura=num_factura_global,
                ingreso=monto,
                fecha_factura=fecha_corte,
                tipo_factura='GLOBAL',
                monto_factura=importe_global,
                saldo_factura=Decimal('0'),
            )
            plan.facturas_pmf.append(datos_pmf)

            # --- SAVPoliza (6 lineas fijas) ---
            lineas = _generar_poliza_venta_tdc(
                monto=monto,
                iva=iva_global,
                ieps=ieps_global,
                num_factura_global=num_factura_global,
                folio_placeholder=0,  # Se asigna al ejecutar
            )
            plan.lineas_poliza.extend(lineas)

            # Tracking: 1 factura y 6 lineas por movimiento
            plan.facturas_por_movimiento.append(1)
            plan.lineas_por_movimiento.append(6)

        # Validaciones
        suma_abonos = sum(m.monto for m in movimientos)
        plan.validaciones.append(
            f"Suma abonos TDC del dia: ${suma_abonos:,.2f}"
        )
        if corte_venta.total_tdc:
            plan.validaciones.append(
                f"Total TDC tesoreria: ${corte_venta.total_tdc:,.2f}"
            )

        return plan


def _generar_poliza_venta_tdc(
    monto: Decimal,
    iva: Decimal,
    ieps: Decimal,
    num_factura_global: str,
    folio_placeholder: int,
) -> List[LineaPoliza]:
    """Genera las 6 lineas de poliza para un movimiento de venta TDC.

    Patron verificado contra produccion (folio 126931):
    1. Cargo monto → 1120/060000 (Banco Tarjeta)
    2. Abono monto → 1210/010000 (Clientes Global)
    3. Abono IVA → 2141/010000 (IVA Acumulable Cobrado)
    4. Cargo IVA → 2146/010000 (IVA Acumulable Pte Cobro)
    5. Abono IEPS → 2141/020000 (IEPS Acumulable Cobrado)
    6. Cargo IEPS → 2146/020000 (IEPS Acumulable Pte Cobro)
    """
    concepto_banco = (
        f"Banco: BANREGIO. FactG: FD-{num_factura_global} "
        f"FolioI: {{folio}}"
    )
    concepto_clientes = (
        f"Clase:VENTA DIARIA Cob.FactG: FD-{num_factura_global}"
    )
    concepto_iva = (
        f"Clase:VENTA DIARIA Iva.FactG: FD-{num_factura_global}"
    )
    concepto_ieps = (
        f"Clase:VENTA DIARIA Ieps.FactG: FD-{num_factura_global}"
    )

    cta_banco = CuentasContables.BANCO_TARJETA
    cta_clientes = CuentasContables.CLIENTES_GLOBAL
    cta_iva_cobrado = CuentasContables.IVA_ACUMULABLE_COBRADO
    cta_iva_pte = CuentasContables.IVA_ACUMULABLE_PTE_COBRO
    cta_ieps_cobrado = CuentasContables.IEPS_ACUMULABLE_COBRADO
    cta_ieps_pte = CuentasContables.IEPS_ACUMULABLE_PTE_COBRO

    return [
        # 1. Cargo Banco Tarjeta
        LineaPoliza(
            movimiento=1,
            cuenta=cta_banco[0],
            subcuenta=cta_banco[1],
            tipo_ca=TipoCA.CARGO,
            cargo=monto,
            abono=Decimal('0'),
            concepto=concepto_banco,
        ),
        # 2. Abono Clientes Global
        LineaPoliza(
            movimiento=2,
            cuenta=cta_clientes[0],
            subcuenta=cta_clientes[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=monto,
            concepto=concepto_clientes,
        ),
        # 3. Abono IVA Acumulable Cobrado
        LineaPoliza(
            movimiento=3,
            cuenta=cta_iva_cobrado[0],
            subcuenta=cta_iva_cobrado[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=iva,
            concepto=concepto_iva,
        ),
        # 4. Cargo IVA Acumulable Pte Cobro
        LineaPoliza(
            movimiento=4,
            cuenta=cta_iva_pte[0],
            subcuenta=cta_iva_pte[1],
            tipo_ca=TipoCA.CARGO,
            cargo=iva,
            abono=Decimal('0'),
            concepto=concepto_iva,
        ),
        # 5. Abono IEPS Acumulable Cobrado
        LineaPoliza(
            movimiento=5,
            cuenta=cta_ieps_cobrado[0],
            subcuenta=cta_ieps_cobrado[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=ieps,
            concepto=concepto_ieps,
        ),
        # 6. Cargo IEPS Acumulable Pte Cobro
        LineaPoliza(
            movimiento=6,
            cuenta=cta_ieps_pte[0],
            subcuenta=cta_ieps_pte[1],
            tipo_ca=TipoCA.CARGO,
            cargo=ieps,
            abono=Decimal('0'),
            concepto=concepto_ieps,
        ),
    ]
