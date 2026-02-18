"""Procesador E2: Nomina.

Genera hasta 4 movimientos bancarios para el pago de nomina:
1. Dispersion (transferencias): Tipo 2, Clase NOMINA, TipoEgreso=TRANSFERENCIA
2. Cheques (efectivo): Tipo 2, Clase NOMINA, TipoEgreso=CHEQUE
3. Vacaciones pagadas: Tipo 2, Clase NOMINA, TipoEgreso=TRANSFERENCIA
4. Finiquito: Tipo 2, Clase FINIQUITO, TipoEgreso=TRANSFERENCIA

Poliza principal (dispersion): ~19 lineas
  - Cargos: percepciones (cuentas 6200/XXXXXX)
  - Abonos: deducciones (cuentas 2140/XXXXXX)
  - Abono Banco (1120/040000) = monto dispersion
  - Abono Acreedores Nomina (2120/040000) = cheques + vacaciones + finiquito

Polizas secundarias (cheques, vacaciones, finiquito): 2 lineas c/u
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

        # --- Movimiento 1: Dispersion (transferencias) ---
        if datos_nomina.total_dispersion > 0:
            _agregar_movimiento_nomina(
                plan, fecha,
                monto=datos_nomina.total_dispersion,
                concepto=f"{concepto_base} DISPERSION",
                clase=CLASE_NOMINA,
                tipo_egreso='TRANSFERENCIA',
            )

            # Poliza principal: percepciones + deducciones + banco + acreedores
            lineas = _generar_poliza_principal(
                datos_nomina=datos_nomina,
                concepto=concepto_base,
            )
            plan.lineas_poliza.extend(lineas)

            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(len(lineas))

        # --- Movimiento 2: Cheques (efectivo) ---
        if datos_nomina.total_cheques > 0:
            _agregar_movimiento_nomina(
                plan, fecha,
                monto=datos_nomina.total_cheques,
                concepto=f"{concepto_base} CHEQUES",
                clase=CLASE_NOMINA,
                tipo_egreso='CHEQUE',
            )

            lineas = _generar_poliza_secundaria(
                monto=datos_nomina.total_cheques,
                concepto=f"{concepto_base} CHEQUES",
            )
            plan.lineas_poliza.extend(lineas)

            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(2)

        # --- Movimiento 3: Vacaciones pagadas ---
        if datos_nomina.total_vacaciones > 0:
            _agregar_movimiento_nomina(
                plan, fecha,
                monto=datos_nomina.total_vacaciones,
                concepto=f"{concepto_base} VACACIONES",
                clase=CLASE_NOMINA,
                tipo_egreso='TRANSFERENCIA',
            )

            lineas = _generar_poliza_secundaria(
                monto=datos_nomina.total_vacaciones,
                concepto=f"{concepto_base} VACACIONES",
            )
            plan.lineas_poliza.extend(lineas)

            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(2)

        # --- Movimiento 4: Finiquito ---
        if datos_nomina.total_finiquito > 0:
            _agregar_movimiento_nomina(
                plan, fecha,
                monto=datos_nomina.total_finiquito,
                concepto=f"{concepto_base} FINIQUITO",
                clase=CLASE_FINIQUITO,
                tipo_egreso='TRANSFERENCIA',
            )

            lineas = _generar_poliza_secundaria(
                monto=datos_nomina.total_finiquito,
                concepto=f"{concepto_base} FINIQUITO",
            )
            plan.lineas_poliza.extend(lineas)

            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(2)

        # Validaciones
        plan.validaciones.append(
            f"Nomina #{num_nomina}: "
            f"Dispersion=${datos_nomina.total_dispersion:,.2f}, "
            f"Cheques=${datos_nomina.total_cheques:,.2f}, "
            f"Vacaciones=${datos_nomina.total_vacaciones:,.2f}, "
            f"Finiquito=${datos_nomina.total_finiquito:,.2f}"
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
    else:
        # Sin detalle: cargo generico a 6200/010000
        total_percepciones = datos_nomina.total_neto
        # Si tenemos deducciones, el total percepciones = neto + deducciones
        total_deducciones = sum(d.monto for d in datos_nomina.deducciones)
        total_percepciones = datos_nomina.total_neto + total_deducciones

        lineas.append(LineaPoliza(
            movimiento=mov_num,
            cuenta='6200',
            subcuenta='010000',
            tipo_ca=TipoCA.CARGO,
            cargo=total_percepciones,
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

    # --- Abono Acreedores Nomina (cheques + vacaciones + finiquito) ---
    monto_acreedores = (
        datos_nomina.total_cheques
        + datos_nomina.total_vacaciones
        + datos_nomina.total_finiquito
    )
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
