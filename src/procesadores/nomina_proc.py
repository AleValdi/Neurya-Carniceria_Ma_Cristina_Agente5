"""Procesador E2: Nomina.

Genera N movimientos bancarios para el pago de nomina, descubiertos
dinamicamente del Excel CONTPAQi:
- DISPERSION (transferencias): Tipo 2, Clase NOMINA, TipoEgreso=TRANSFERENCIA
- CHEQUES (efectivo): Tipo 2, Clase NOMINA, TipoEgreso=CHEQUE
- VAC PAGADAS, FINIQUITO PAGADO, u otros segun columna O del Excel

Poliza principal (primer movimiento es_principal=True): ~19 lineas
  - Cargos: percepciones (cuentas 6200/XXXXXX)
  - Abonos: deducciones (cuentas 2140/XXXXXX)
  - Abono Banco (1120/040000) = monto dispersion
  - Abono Acreedores Nomina (2120/040000) = suma de secundarios

Polizas secundarias: 2 lineas c/u
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
        """Construye plan para pago de nomina.

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
        concepto_base = f"NOMINA {num_nomina:02d}"

        # Iterar sobre movimientos descubiertos dinamicamente
        poliza_principal_generada = False

        for mov_nom in datos_nomina.movimientos:
            if mov_nom.monto <= 0:
                continue

            concepto_mov = f"{concepto_base} {mov_nom.tipo}"

            _agregar_movimiento_nomina(
                plan, fecha,
                monto=mov_nom.monto,
                concepto=concepto_mov,
                clase=mov_nom.clase,
                tipo_egreso=mov_nom.tipo_egreso,
            )

            if mov_nom.es_principal and not poliza_principal_generada:
                # Poliza completa: percepciones + deducciones + banco + acreedores
                lineas = _generar_poliza_principal(
                    datos_nomina=datos_nomina,
                    concepto=concepto_base,
                )
                plan.lineas_poliza.extend(lineas)
                plan.facturas_por_movimiento.append(0)
                plan.lineas_por_movimiento.append(len(lineas))
                poliza_principal_generada = True
            else:
                # Poliza secundaria: 2 lineas (Acreedores + Banco)
                lineas = _generar_poliza_secundaria(
                    monto=mov_nom.monto,
                    concepto=concepto_mov,
                )
                plan.lineas_poliza.extend(lineas)
                plan.facturas_por_movimiento.append(0)
                plan.lineas_por_movimiento.append(2)

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

        return plan


def _agregar_movimiento_nomina(
    plan: PlanEjecucion,
    fecha: date,
    monto: Decimal,
    concepto: str,
    clase: str,
    tipo_egreso: str,
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
                concepto=f"{concepto} {perc.concepto}",
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
                concepto=f"{concepto} Otras percepciones",
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
            concepto=f"{concepto} SUELDOS Y SALARIOS",
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
            concepto=f"{concepto} {ded.concepto}",
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
        concepto=f"Banco: BANREGIO {concepto}",
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
            concepto=f"{concepto} Acreedores Diversos Nomina",
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
            concepto=f"{concepto} Acreedores Nomina",
        ),
        LineaPoliza(
            movimiento=2,
            cuenta=cta_banco[0],
            subcuenta=cta_banco[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=monto,
            concepto=f"Banco: BANREGIO {concepto}",
        ),
    ]
