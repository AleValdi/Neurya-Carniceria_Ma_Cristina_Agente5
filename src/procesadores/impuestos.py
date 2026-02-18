"""Procesador E5: Impuestos Federales y Estatales.

Genera movimientos bancarios y polizas para 3 tipos de pago de impuestos:

A. Federal 1a Declaracion — Retenciones + IEPS
   - 1 movimiento + 5 lineas de poliza

B. Federal 2a Declaracion — ISR + IVA
   - 1 movimiento principal (ISR PM + ISR sal) + 6 lineas
   - N movimientos por retenciones IVA por proveedor + 4 lineas c/u

C. Estatal 3% Nomina
   - 1 movimiento + 2 lineas de poliza
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from loguru import logger

from config.settings import CuentasContables
from src.models import (
    DatosImpuestoEstatal,
    DatosImpuestoFederal,
    DatosMovimientoPM,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


BANCO = 'BANREGIO'
CUENTA_EFECTIVO = '055003730017'
TIPO_EGRESO_MANUAL = 2


class ProcesadorImpuestos:
    """Procesador para pago de impuestos (E5)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.IMPUESTO_FEDERAL, TipoProceso.IMPUESTO_ESTATAL]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        datos_federal: Optional[DatosImpuestoFederal] = None,
        datos_estatal: Optional[DatosImpuestoEstatal] = None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan para pago de impuestos.

        Args:
            movimientos: Movimientos de impuestos del estado de cuenta.
            fecha: Fecha de los movimientos.
            datos_federal: Datos parseados de acuses federales.
            datos_estatal: Datos parseados de formato estatal.
        """
        plan = PlanEjecucion(
            tipo_proceso='IMPUESTOS',
            descripcion=f'Impuestos {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin movimientos de impuestos para este dia")
            return plan

        # Separar por tipo
        federales = [m for m in movimientos if m.tipo_proceso == TipoProceso.IMPUESTO_FEDERAL]
        estatales = [m for m in movimientos if m.tipo_proceso == TipoProceso.IMPUESTO_ESTATAL]

        # --- Federal ---
        if federales:
            self._procesar_federal(plan, federales, fecha, datos_federal)

        # --- Estatal ---
        if estatales:
            self._procesar_estatal(plan, estatales, fecha, datos_estatal)

        return plan

    def _procesar_federal(
        self,
        plan: PlanEjecucion,
        movimientos: List[MovimientoBancario],
        fecha: date,
        datos: Optional[DatosImpuestoFederal],
    ):
        """Genera movimientos y polizas para impuestos federales."""
        if datos is None:
            plan.advertencias.append(
                "Sin datos de acuse federal — no se pueden generar movimientos"
            )
            return

        if not datos.confianza_100:
            plan.advertencias.append(
                "Datos federales sin 100% de confianza — no se generan movimientos"
            )
            for adv in datos.advertencias:
                plan.advertencias.append(f"  PDF: {adv}")
            return

        periodo = datos.periodo

        # Matchear movimientos bancarios con montos del acuse
        mov_1a = None  # total_primera
        mov_2a_principal = None  # ISR PM + ISR sal
        movs_2a_ret = []  # retenciones IVA por proveedor

        monto_2a_principal = datos.isr_personas_morales + datos.isr_ret_salarios
        montos_ret = {r.monto: r for r in datos.retenciones_iva}

        for mov in movimientos:
            monto = mov.monto
            if monto == datos.total_primera and mov_1a is None:
                mov_1a = mov
            elif monto == monto_2a_principal and mov_2a_principal is None:
                mov_2a_principal = mov
            elif monto in montos_ret:
                movs_2a_ret.append((mov, montos_ret[monto]))
            elif monto == datos.total_segunda and mov_2a_principal is None:
                # Fallback: total 2a como movimiento principal
                mov_2a_principal = mov

        # --- A. Federal 1a: Retenciones + IEPS ---
        if mov_1a:
            self._generar_federal_1a(plan, fecha, datos, periodo)
            plan.validaciones.append(
                f"Federal 1a: ${datos.total_primera:,.0f} "
                f"(ISR hon ${datos.isr_ret_honorarios:,.0f} + "
                f"ISR arr ${datos.isr_ret_arrendamiento:,.0f} + "
                f"IEPS ${datos.ieps_neto:,.0f})"
            )
        else:
            plan.advertencias.append(
                f"No se encontro movimiento bancario para 1a declaracion "
                f"(${datos.total_primera:,.0f})"
            )

        # --- B. Federal 2a: ISR + IVA ---
        if mov_2a_principal:
            self._generar_federal_2a_principal(plan, fecha, datos, periodo)
            plan.validaciones.append(
                f"Federal 2a principal: ${monto_2a_principal:,.0f} "
                f"(ISR PM ${datos.isr_personas_morales:,.0f} + "
                f"ISR sal ${datos.isr_ret_salarios:,.0f})"
            )
        else:
            plan.advertencias.append(
                f"No se encontro movimiento bancario para 2a declaracion principal "
                f"(${monto_2a_principal:,.0f})"
            )

        # Retenciones IVA por proveedor
        for mov, retencion in movs_2a_ret:
            self._generar_federal_2a_retencion(plan, fecha, retencion, periodo)
            plan.validaciones.append(
                f"Federal 2a retencion: {retencion.nombre} ${retencion.monto:,.0f}"
            )

        # Verificar que todas las retenciones fueron matcheadas
        montos_matcheados = {r.monto for _, r in movs_2a_ret}
        for ret in datos.retenciones_iva:
            if ret.monto not in montos_matcheados:
                plan.advertencias.append(
                    f"Retencion IVA {ret.nombre} (${ret.monto:,.0f}) "
                    f"sin movimiento bancario"
                )

    def _generar_federal_1a(
        self,
        plan: PlanEjecucion,
        fecha: date,
        datos: DatosImpuestoFederal,
        periodo: str,
    ):
        """Genera 1 movimiento + 5 lineas poliza para 1a declaracion."""
        concepto = f"PAGO IMPUESTOS (RETENCIONES) {periodo}"

        # Movimiento PM
        plan.movimientos_pm.append(DatosMovimientoPM(
            banco=BANCO,
            cuenta=CUENTA_EFECTIVO,
            age=fecha.year,
            mes=fecha.month,
            dia=fecha.day,
            tipo=TIPO_EGRESO_MANUAL,
            ingreso=Decimal('0'),
            egreso=datos.total_primera,
            concepto=concepto,
            clase='PAGO IMPUESTOS',
            fpago=None,
            tipo_egreso='TRANSFERENCIA',
            conciliada=1,
            paridad=Decimal('1.0000'),
            tipo_poliza='EGRESO',
            num_factura='',
        ))

        # Poliza: 5 lineas
        cta = CuentasContables
        lineas = [
            # 1. Cargo ISR Ret Honorarios
            LineaPoliza(
                movimiento=1,
                cuenta=cta.ISR_RET_HONORARIOS[0],
                subcuenta=cta.ISR_RET_HONORARIOS[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.isr_ret_honorarios,
                abono=Decimal('0'),
                concepto=f"Ret ISR Honorarios {periodo}",
            ),
            # 2. Cargo ISR Ret Arrendamiento
            LineaPoliza(
                movimiento=2,
                cuenta=cta.ISR_RET_ARRENDAMIENTO[0],
                subcuenta=cta.ISR_RET_ARRENDAMIENTO[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.isr_ret_arrendamiento,
                abono=Decimal('0'),
                concepto=f"Ret 10% ISR Arrendamiento {periodo}",
            ),
            # 3. Abono Banco (total pago)
            LineaPoliza(
                movimiento=3,
                cuenta=cta.BANCO_EFECTIVO[0],
                subcuenta=cta.BANCO_EFECTIVO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=datos.total_primera,
                concepto=f"Banco: BANREGIO {concepto}",
            ),
            # 4. Cargo IEPS Acumulable Cobrado
            LineaPoliza(
                movimiento=4,
                cuenta=cta.IEPS_ACUMULABLE_COBRADO[0],
                subcuenta=cta.IEPS_ACUMULABLE_COBRADO[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.ieps_acumulable,
                abono=Decimal('0'),
                concepto=f"IEPS Acumulable Cobrado {periodo}",
            ),
            # 5. Abono IEPS Acreditable Pagado
            LineaPoliza(
                movimiento=5,
                cuenta=cta.IEPS_ACREDITABLE_PAGADO[0],
                subcuenta=cta.IEPS_ACREDITABLE_PAGADO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=datos.ieps_acreditable,
                concepto=f"IEPS Acreditable Pagado {periodo}",
            ),
        ]
        plan.lineas_poliza.extend(lineas)
        plan.facturas_por_movimiento.append(0)
        plan.lineas_por_movimiento.append(5)

    def _generar_federal_2a_principal(
        self,
        plan: PlanEjecucion,
        fecha: date,
        datos: DatosImpuestoFederal,
        periodo: str,
    ):
        """Genera 1 movimiento + 6 lineas poliza para 2a declaracion (ISR+IVA)."""
        concepto = f"PAGO IMPUESTOS ISR E IVA {periodo}"
        monto_pago = datos.isr_personas_morales + datos.isr_ret_salarios

        # Movimiento PM
        plan.movimientos_pm.append(DatosMovimientoPM(
            banco=BANCO,
            cuenta=CUENTA_EFECTIVO,
            age=fecha.year,
            mes=fecha.month,
            dia=fecha.day,
            tipo=TIPO_EGRESO_MANUAL,
            ingreso=Decimal('0'),
            egreso=monto_pago,
            concepto=concepto,
            clase='PAGO IMPUESTOS',
            fpago=None,
            tipo_egreso='TRANSFERENCIA',
            conciliada=1,
            paridad=Decimal('1.0000'),
            tipo_poliza='EGRESO',
            num_factura='',
        ))

        # Poliza: 6 lineas
        cta = CuentasContables
        lineas = [
            # 1. Cargo ISR Provisional (Personas Morales)
            LineaPoliza(
                movimiento=1,
                cuenta=cta.ISR_PROVISIONAL[0],
                subcuenta=cta.ISR_PROVISIONAL[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.isr_personas_morales,
                abono=Decimal('0'),
                concepto=f"Pago Provisional ISR {periodo}",
            ),
            # 2. Cargo Retencion ISR Salarios
            LineaPoliza(
                movimiento=2,
                cuenta=cta.RETENCION_ISR[0],
                subcuenta=cta.RETENCION_ISR[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.isr_ret_salarios,
                abono=Decimal('0'),
                concepto=f"Retencion ISPT {periodo}",
            ),
            # 3. Abono Banco (ISR PM + ISR sal)
            LineaPoliza(
                movimiento=3,
                cuenta=cta.BANCO_EFECTIVO[0],
                subcuenta=cta.BANCO_EFECTIVO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=monto_pago,
                concepto=f"Banco: BANREGIO {concepto}",
            ),
            # 4. Cargo IVA Acumulable Cobrado
            LineaPoliza(
                movimiento=4,
                cuenta=cta.IVA_ACUMULABLE_COBRADO[0],
                subcuenta=cta.IVA_ACUMULABLE_COBRADO[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.iva_acumulable,
                abono=Decimal('0'),
                concepto=f"IVA Acumulable Cobrado {periodo}",
            ),
            # 5. Abono IVA Acreditable Pagado
            LineaPoliza(
                movimiento=5,
                cuenta=cta.IVA_ACREDITABLE_PAGADO[0],
                subcuenta=cta.IVA_ACREDITABLE_PAGADO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=datos.iva_acreditable,
                concepto=f"IVA Acreditable Pagado {periodo}",
            ),
            # 6. Cargo IVA a Favor
            LineaPoliza(
                movimiento=6,
                cuenta=cta.IVA_A_FAVOR[0],
                subcuenta=cta.IVA_A_FAVOR[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.iva_a_favor,
                abono=Decimal('0'),
                concepto=f"IVA a Favor {periodo}",
            ),
        ]
        plan.lineas_poliza.extend(lineas)
        plan.facturas_por_movimiento.append(0)
        plan.lineas_por_movimiento.append(6)

    def _generar_federal_2a_retencion(
        self,
        plan: PlanEjecucion,
        fecha: date,
        retencion,
        periodo: str,
    ):
        """Genera 1 movimiento + 4 lineas poliza por retencion IVA proveedor."""
        concepto = f"PAGO IMPUESTOS RETENCIONES IVA {periodo}"

        # Movimiento PM
        plan.movimientos_pm.append(DatosMovimientoPM(
            banco=BANCO,
            cuenta=CUENTA_EFECTIVO,
            age=fecha.year,
            mes=fecha.month,
            dia=fecha.day,
            tipo=TIPO_EGRESO_MANUAL,
            ingreso=Decimal('0'),
            egreso=retencion.monto,
            concepto=concepto,
            clase='PAGO IMPUESTOS',
            fpago=None,
            tipo_egreso='TRANSFERENCIA',
            conciliada=1,
            paridad=Decimal('1.0000'),
            tipo_poliza='EGRESO',
            num_factura='',
        ))

        # Poliza: 4 lineas
        cta = CuentasContables
        lineas = [
            # 1. Cargo IVA Retenido Pagado
            LineaPoliza(
                movimiento=1,
                cuenta=cta.IVA_RETENIDO_PAGADO[0],
                subcuenta=cta.IVA_RETENIDO_PAGADO[1],
                tipo_ca=TipoCA.CARGO,
                cargo=retencion.monto,
                abono=Decimal('0'),
                concepto=f"IVA Retenido {retencion.nombre} {periodo}",
            ),
            # 2. Abono Banco
            LineaPoliza(
                movimiento=2,
                cuenta=cta.BANCO_EFECTIVO[0],
                subcuenta=cta.BANCO_EFECTIVO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=retencion.monto,
                concepto=f"Banco: BANREGIO {concepto}",
            ),
            # 3. Cargo IVA Acreditable Pagado (reclasificacion)
            LineaPoliza(
                movimiento=3,
                cuenta=cta.IVA_ACREDITABLE_PAGADO[0],
                subcuenta=cta.IVA_ACREDITABLE_PAGADO[1],
                tipo_ca=TipoCA.CARGO,
                cargo=retencion.monto,
                abono=Decimal('0'),
                concepto=f"IVA Acreditable {retencion.nombre} {periodo}",
            ),
            # 4. Abono IVA Acreditable Pendiente de Pago
            LineaPoliza(
                movimiento=4,
                cuenta=cta.IVA_ACREDITABLE_PTE_PAGO[0],
                subcuenta=cta.IVA_ACREDITABLE_PTE_PAGO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=retencion.monto,
                concepto=f"IVA Pte Pago {retencion.nombre} {periodo}",
            ),
        ]
        plan.lineas_poliza.extend(lineas)
        plan.facturas_por_movimiento.append(0)
        plan.lineas_por_movimiento.append(4)

    def _procesar_estatal(
        self,
        plan: PlanEjecucion,
        movimientos: List[MovimientoBancario],
        fecha: date,
        datos: Optional[DatosImpuestoEstatal],
    ):
        """Genera movimiento y poliza para impuesto estatal 3% nomina."""
        if datos is None:
            plan.advertencias.append(
                "Sin datos de impuesto estatal — no se pueden generar movimientos"
            )
            return

        if not datos.confianza_100:
            plan.advertencias.append(
                "Datos estatales sin 100% de confianza — no se generan movimientos"
            )
            for adv in datos.advertencias:
                plan.advertencias.append(f"  PDF: {adv}")
            return

        # Buscar movimiento bancario que coincida con el monto
        mov_estatal = None
        for mov in movimientos:
            if mov.monto == datos.monto:
                mov_estatal = mov
                break

        if mov_estatal is None:
            plan.advertencias.append(
                f"No se encontro movimiento bancario para impuesto estatal "
                f"(${datos.monto:,.2f})"
            )
            return

        periodo = datos.periodo
        concepto = f"PAGO 3% NOMINA {periodo}"

        # Movimiento PM
        plan.movimientos_pm.append(DatosMovimientoPM(
            banco=BANCO,
            cuenta=CUENTA_EFECTIVO,
            age=fecha.year,
            mes=fecha.month,
            dia=fecha.day,
            tipo=TIPO_EGRESO_MANUAL,
            ingreso=Decimal('0'),
            egreso=datos.monto,
            concepto=concepto,
            clase='PAGO 3% NOMINA',
            fpago=None,
            tipo_egreso='TRANSFERENCIA',
            conciliada=1,
            paridad=Decimal('1.0000'),
            tipo_poliza='EGRESO',
            num_factura='',
        ))

        # Poliza: 2 lineas
        cta = CuentasContables
        lineas = [
            # 1. Cargo 3% Nominas
            LineaPoliza(
                movimiento=1,
                cuenta=cta.NOMINAS_3_PCT[0],
                subcuenta=cta.NOMINAS_3_PCT[1],
                tipo_ca=TipoCA.CARGO,
                cargo=datos.monto,
                abono=Decimal('0'),
                concepto=f"3% Nominas {periodo}",
            ),
            # 2. Abono Banco
            LineaPoliza(
                movimiento=2,
                cuenta=cta.BANCO_EFECTIVO[0],
                subcuenta=cta.BANCO_EFECTIVO[1],
                tipo_ca=TipoCA.ABONO,
                cargo=Decimal('0'),
                abono=datos.monto,
                concepto=f"Banco: BANREGIO {concepto}",
            ),
        ]
        plan.lineas_poliza.extend(lineas)
        plan.facturas_por_movimiento.append(0)
        plan.lineas_por_movimiento.append(2)

        plan.validaciones.append(
            f"Estatal 3% nomina: ${datos.monto:,.2f} {periodo}"
        )
