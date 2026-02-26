"""Procesador E2: Nomina.

Genera movimientos bancarios para pago de nomina en DOS fases:

Fase 1 — Linea "NOMINA - PAGO DE NOMINA" del banco:
  construir_plan() crea solo la DISPERSION (poliza ~17 lineas con provision
  de acreedores 2120/040000 para secundarios).

Fase 2 — Lineas "Cobro de cheque:XXXX" del banco:
  construir_plan_cheque() matchea monto contra secundarios del Excel y crea
  1 movimiento + poliza 2 lineas (cancela acreedores).

Poliza principal (DISPERSION): ~19 lineas
  - Cargos: percepciones (cuentas 6200/XXXXXX)
  - Abonos: deducciones (cuentas 2140/XXXXXX)
  - Abono Banco (1120/040000) = monto dispersion
  - Abono Acreedores Nomina (2120/040000) = suma de secundarios

Polizas secundarias (CHEQUES, VAC, FINIQUITO): 2 lineas c/u
  - Cargo Acreedores Nomina (2120/040000)
  - Abono Banco (1120/040000)
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

from loguru import logger

from config.settings import CuentasContables
from src.models import (
    DatosMovimientoPM,
    DatosNomina,
    LineaContable,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


BANCO = 'BANREGIO'
CUENTA_EFECTIVO = '055003730017'
TIPO_MOVIMIENTO = 2  # Egreso manual
CLASE_NOMINA = 'NOMINA'
CLASE_FINIQUITO = 'FINIQUITO'
TOLERANCIA_MATCH = Decimal('0.50')

MESES_ES = {
    1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL',
    5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO',
    9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE',
}


class ProcesadorNomina:
    """Procesador para pago de nomina (E2)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.NOMINA]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        datos_nomina: Optional[DatosNomina] = None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan para la DISPERSION de nomina.

        Solo crea el movimiento principal (es_principal=True) con poliza
        completa que provisiona acreedores para los secundarios.

        Args:
            movimientos: Movimiento(s) de nomina del estado de cuenta.
            fecha: Fecha del cargo en estado de cuenta.
            datos_nomina: Datos parseados del archivo de nomina CONTPAQi.
        """
        plan = PlanEjecucion(
            tipo_proceso='NOMINA',
            descripcion=f'Nomina {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin movimientos de nomina para este dia")
            return plan

        if datos_nomina is None:
            plan.advertencias.append(
                "Sin datos de nomina CONTPAQi. "
                "No se pueden generar percepciones/deducciones."
            )
            return plan

        num_nomina = datos_nomina.numero_nomina
        concepto_base = _concepto_nomina(num_nomina, fecha)

        # Monto del banco para validacion cruzada
        monto_banco = movimientos[0].monto if movimientos else Decimal('0')

        # Solo crear movimiento principal (DISPERSION)
        for mov_nom in datos_nomina.movimientos:
            if not mov_nom.es_principal or mov_nom.monto <= 0:
                continue

            concepto_mov = concepto_base

            _agregar_movimiento_nomina(
                plan, fecha,
                monto=mov_nom.monto,
                concepto=concepto_mov,
                clase=mov_nom.clase,
                tipo_egreso=mov_nom.tipo_egreso,
            )

            lineas = _generar_poliza_principal(
                datos_nomina=datos_nomina,
                concepto=concepto_base,
            )
            plan.lineas_poliza.extend(lineas)
            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(len(lineas))

            # Validacion cruzada: monto Excel vs monto banco
            diff = abs(mov_nom.monto - monto_banco)
            if diff > Decimal('1.00'):
                plan.advertencias.append(
                    f"Diferencia banco vs Excel: "
                    f"banco=${monto_banco:,.2f}, "
                    f"Excel=${mov_nom.monto:,.2f} "
                    f"(diff=${diff:,.2f}). "
                    f"Se registro con monto del Excel."
                )

            break  # Solo hay 1 principal

        # Validaciones
        for mov_nom in datos_nomina.movimientos:
            plan.validaciones.append(
                f"{mov_nom.tipo}: ${mov_nom.monto:,.2f} "
                f"(clase={mov_nom.clase}, egreso={mov_nom.tipo_egreso})"
            )
        plan.validaciones.append(
            f"Total neto: ${datos_nomina.total_neto:,.2f}"
        )

        if datos_nomina.percepciones:
            total_perc = sum(p.monto for p in datos_nomina.percepciones)
            plan.validaciones.append(
                f"Percepciones: {len(datos_nomina.percepciones)} conceptos "
                f"(${total_perc:,.2f})"
            )
        else:
            plan.advertencias.append(
                "Sin detalle de percepciones (poliza tendra lineas genericas)"
            )

        if datos_nomina.total_secundarios > 0:
            plan.validaciones.append(
                f"Provision acreedores: ${datos_nomina.total_secundarios:,.2f} "
                f"(pendiente de cobros de cheque)"
            )

        return plan

    def construir_plan_cheque(
        self,
        fecha: date,
        datos_nomina: DatosNomina,
        monto_banco: Decimal,
        num_cheque: str = '',
    ) -> Optional[PlanEjecucion]:
        """Busca un movimiento secundario que matchee el monto del cobro de cheque.

        Args:
            fecha: Fecha del cobro de cheque en el estado de cuenta.
            datos_nomina: Datos parseados del archivo de nomina CONTPAQi.
            monto_banco: Monto del egreso en el banco.
            num_cheque: Numero de cheque extraido de la descripcion bancaria.

        Returns:
            PlanEjecucion con 1 movimiento + 2 lineas poliza, o None si no hay match.
        """
        num_nomina = datos_nomina.numero_nomina
        concepto_base = _concepto_nomina(num_nomina, fecha)

        # Buscar primer secundario no matcheado con monto similar
        mov_match = None
        for mov_nom in datos_nomina.movimientos:
            if mov_nom.es_principal or mov_nom.matched:
                continue
            if mov_nom.monto <= 0:
                continue
            if abs(mov_nom.monto - monto_banco) <= TOLERANCIA_MATCH:
                mov_match = mov_nom
                break

        if mov_match is None:
            return None

        # Marcar como matcheado para no reutilizar
        mov_match.matched = True

        concepto_mov = concepto_base

        plan = PlanEjecucion(
            tipo_proceso='NOMINA_CHEQUE',
            descripcion=f'Cobro cheque nomina {fecha} ({mov_match.tipo})',
            fecha_movimiento=fecha,
        )

        _agregar_movimiento_nomina(
            plan, fecha,
            monto=monto_banco,
            concepto=concepto_mov,
            clase=mov_match.clase,
            tipo_egreso=mov_match.tipo_egreso,
            num_cheque=num_cheque,
        )

        lineas = _generar_poliza_secundaria(
            monto=monto_banco,
            concepto=concepto_mov,
        )
        plan.lineas_poliza.extend(lineas)
        plan.facturas_por_movimiento.append(0)
        plan.lineas_por_movimiento.append(2)

        plan.validaciones.append(
            f"Match: {mov_match.tipo} ${mov_match.monto:,.2f} "
            f"↔ banco ${monto_banco:,.2f} (cheque #{num_cheque})"
        )

        return plan


def _agregar_movimiento_nomina(
    plan: PlanEjecucion,
    fecha: date,
    monto: Decimal,
    concepto: str,
    clase: str,
    tipo_egreso: str,
    num_cheque: str = '',
):
    """Agrega un movimiento de nomina al plan."""
    datos_pm = DatosMovimientoPM(
        banco=BANCO,
        cuenta=CUENTA_EFECTIVO,
        age=fecha.year,
        mes=fecha.month,
        dia=fecha.day,
        tipo=TIPO_MOVIMIENTO,
        ingreso=Decimal('0'),
        egreso=monto,
        concepto=concepto,
        clase=clase,
        fpago=None,
        tipo_egreso=tipo_egreso,
        conciliada=1,
        paridad=Decimal('1.0000'),
        tipo_poliza='EGRESO',
        num_factura='',
        num_cheque=num_cheque,
    )
    plan.movimientos_pm.append(datos_pm)


def _generar_poliza_principal(
    datos_nomina: DatosNomina,
    concepto: str,
) -> List[LineaPoliza]:
    """Genera la poliza principal de nomina (~19 lineas).

    Estructura:
    - Cargos: percepciones (cuentas 6200/XXXXXX)
    - Abonos: deducciones (cuentas 2140/XXXXXX)
    - Abono Banco (1120/040000) = monto dispersion
    - Abono Acreedores Nomina (2120/040000) = cheques + vacaciones + finiquito
    """
    lineas = []
    mov_num = 1

    cta_banco = CuentasContables.BANCO_EFECTIVO
    cta_acreedores = ('2120', '040000')  # Acreedores Diversos Nomina

    # Total que deben sumar las percepciones para que la poliza cuadre:
    # percepciones = deducciones + banco (dispersion) + acreedores (secundarios)
    total_deducciones = sum(d.monto for d in datos_nomina.deducciones)
    monto_acreedores_esperado = datos_nomina.total_secundarios
    total_percepciones_esperado = (
        total_deducciones + datos_nomina.total_dispersion + monto_acreedores_esperado
    )

    # --- Cargos: Percepciones ---
    if datos_nomina.percepciones:
        for perc in datos_nomina.percepciones:
            if perc.monto <= 0:
                continue
            lineas.append(LineaPoliza(
                movimiento=mov_num,
                cuenta=perc.cuenta,
                subcuenta=perc.subcuenta,
                tipo_ca=TipoCA.CARGO,
                cargo=perc.monto,
                abono=Decimal('0'),
                concepto=concepto,
            ))
            mov_num += 1

        # Cuadre: si las percepciones del Excel no cubren el total esperado,
        # agregar linea de ajuste (conceptos no desglosados en el Excel)
        total_percepciones_actual = sum(p.monto for p in datos_nomina.percepciones)
        faltante = total_percepciones_esperado - total_percepciones_actual
        if faltante > Decimal('0.01'):
            lineas.append(LineaPoliza(
                movimiento=mov_num,
                cuenta='6200',
                subcuenta='010000',
                tipo_ca=TipoCA.CARGO,
                cargo=faltante,
                abono=Decimal('0'),
                concepto=concepto,
            ))
            mov_num += 1
    else:
        # Sin detalle: cargo generico a 6200/010000
        lineas.append(LineaPoliza(
            movimiento=mov_num,
            cuenta='6200',
            subcuenta='010000',
            tipo_ca=TipoCA.CARGO,
            cargo=total_percepciones_esperado,
            abono=Decimal('0'),
            concepto=concepto,
        ))
        mov_num += 1

    # --- Abonos: Deducciones ---
    for ded in datos_nomina.deducciones:
        if ded.monto <= 0:
            continue
        lineas.append(LineaPoliza(
            movimiento=mov_num,
            cuenta=ded.cuenta,
            subcuenta=ded.subcuenta,
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=ded.monto,
            concepto=concepto,
        ))
        mov_num += 1

    # --- Abono Banco (monto dispersion) ---
    lineas.append(LineaPoliza(
        movimiento=mov_num,
        cuenta=cta_banco[0],
        subcuenta=cta_banco[1],
        tipo_ca=TipoCA.ABONO,
        cargo=Decimal('0'),
        abono=datos_nomina.total_dispersion,
        concepto=concepto,
    ))
    mov_num += 1

    # --- Abono Acreedores Nomina (suma de movimientos secundarios) ---
    monto_acreedores = datos_nomina.total_secundarios
    if monto_acreedores > 0:
        lineas.append(LineaPoliza(
            movimiento=mov_num,
            cuenta=cta_acreedores[0],
            subcuenta=cta_acreedores[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=monto_acreedores,
            concepto=concepto,
        ))

    return lineas


def _generar_poliza_secundaria(
    monto: Decimal,
    concepto: str,
) -> List[LineaPoliza]:
    """Genera poliza de 2 lineas para movimientos secundarios de nomina.

    1. Cargo Acreedores Nomina (2120/040000)
    2. Abono Banco (1120/040000)
    """
    cta_banco = CuentasContables.BANCO_EFECTIVO
    cta_acreedores = ('2120', '040000')

    return [
        LineaPoliza(
            movimiento=1,
            cuenta=cta_acreedores[0],
            subcuenta=cta_acreedores[1],
            tipo_ca=TipoCA.CARGO,
            cargo=monto,
            abono=Decimal('0'),
            concepto=concepto,
        ),
        LineaPoliza(
            movimiento=2,
            cuenta=cta_banco[0],
            subcuenta=cta_banco[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=monto,
            concepto=concepto,
        ),
    ]


def _concepto_nomina(num_nomina: int, fecha: date) -> str:
    """Genera concepto de nomina en formato PROD.

    Patron: "NOMINA S{num:02d}- {inicio:02d}/{fin:02d} {MES}"
    Donde fin = fecha.day - 3, inicio = max(1, fin - 6).

    Ejemplo: fecha=Feb 23, num=8 → "NOMINA S08- 14/20 FEBRERO"
    """
    fin_dia = max(1, fecha.day - 3)
    inicio_dia = max(1, fin_dia - 6)
    mes_nombre = MESES_ES[fecha.month]
    return f"NOMINA S{num_nomina:02d}- {inicio_dia:02d}/{fin_dia:02d} {mes_nombre}"
