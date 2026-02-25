"""Procesador E4: Traspasos entre Cuentas Bancarias.

Genera dos movimientos bancarios (egreso + ingreso) para transferencias
entre cuentas propias, y una poliza de 2 lineas.

Caracteristicas:
- Patron estado de cuenta: "(BE) Traspaso a cuenta: {CTA_DESTINO}"
- Genera 2 movimientos SAVCheqPM:
  1. Egreso (Tipo 2) en la cuenta origen
  2. Ingreso (Tipo 1) en la cuenta destino
- Poliza: 2 lineas, DocTipo=TRASPASOS, TipoPoliza=DIARIO
  1. Cargo cuenta destino (CuentaC/SubCuentaC del banco destino)
  2. Abono cuenta origen (CuentaC/SubCuentaC del banco origen)
- Clase: 'ENTRE CUENTAS PROPIA'
- Concepto: 'TRASPASO A BANCO: BANREGIO CUENTA: {cuenta} MONEDA: PESOS'
- ParidadDOF: 20.0000
"""

import re
from datetime import date
from decimal import Decimal
from typing import List, Optional

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


CLASE = 'ENTRE CUENTAS PROPIA'
TIPO_EGRESO = 2   # Egreso manual
TIPO_INGRESO = 1  # Ingreso general

# Regex para extraer cuenta destino de la descripcion del traspaso
RE_CUENTA_DESTINO = re.compile(
    r'\(BE\)\s*Traspaso a cuenta:\s*(\d+)',
    re.IGNORECASE,
)


class ProcesadorTraspasos:
    """Procesador para traspasos entre cuentas propias (E4)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.TRASPASO]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan para traspasos de un dia.

        Cada traspaso genera:
        - 1 movimiento egreso (cuenta origen)
        - 1 movimiento ingreso (cuenta destino)
        - 1 poliza de 2 lineas (DocTipo=TRASPASOS)

        Args:
            movimientos: Traspasos egreso del dia (patron "(BE) Traspaso a cuenta").
            fecha: Fecha del movimiento en el estado de cuenta.
        """
        plan = PlanEjecucion(
            tipo_proceso='TRASPASOS',
            descripcion=f'Traspasos {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin traspasos para este dia")
            return plan

        for mov in movimientos:
            # Extraer cuenta destino de la descripcion
            cuenta_destino = _extraer_cuenta_destino(mov.descripcion)
            if not cuenta_destino:
                plan.advertencias.append(
                    f"No se pudo extraer cuenta destino de: {mov.descripcion[:80]}"
                )
                continue

            cuenta_origen = mov.cuenta_banco
            monto = mov.monto

            # Resolver CLABE a cuenta corta si es necesario
            cuenta_destino = _resolver_cuenta(cuenta_destino)

            # Validar que ambas cuentas existen en la config
            clave_origen = CUENTA_POR_NUMERO.get(cuenta_origen)
            clave_destino = CUENTA_POR_NUMERO.get(cuenta_destino)

            if not clave_origen:
                plan.advertencias.append(
                    f"Cuenta origen {cuenta_origen} no reconocida"
                )
                continue
            if not clave_destino:
                plan.advertencias.append(
                    f"Cuenta destino {cuenta_destino} no reconocida"
                )
                continue

            cfg_origen = CUENTAS_BANCARIAS[clave_origen]
            cfg_destino = CUENTAS_BANCARIAS[clave_destino]

            concepto = (
                f"TRASPASO A BANCO: {cfg_destino.banco} "
                f"CUENTA: {cuenta_destino} MONEDA: PESOS"
            )

            # Cuenta corta para concepto poliza (primeros 6 digitos)
            cta_origen_corta = cuenta_origen[:6]
            cta_destino_corta = cuenta_destino[:6]

            # --- Movimiento 1: Egreso en cuenta origen ---
            datos_egreso = DatosMovimientoPM(
                banco=cfg_origen.banco,
                cuenta=cuenta_origen,
                age=fecha.year,
                mes=fecha.month,
                dia=fecha.day,
                tipo=TIPO_EGRESO,
                ingreso=Decimal('0'),
                egreso=monto,
                concepto=concepto,
                clase=CLASE,
                fpago=None,
                tipo_egreso='INTERBANCARIO',
                conciliada=1,
                paridad=Decimal('1.0000'),
                tipo_poliza='DIARIO',
                num_factura='',
                paridad_dof=Decimal('20.0000'),
                referencia='TRASPASO AUTOMATICO',
            )
            plan.movimientos_pm.append(datos_egreso)

            # --- Movimiento 2: Ingreso en cuenta destino ---
            concepto_ingreso = (
                f"TRASPASO DE BANCO: {cfg_origen.banco} "
                f"CUENTA: {cuenta_origen} MONEDA: PESOS"
            )
            datos_ingreso = DatosMovimientoPM(
                banco=cfg_destino.banco,
                cuenta=cuenta_destino,
                age=fecha.year,
                mes=fecha.month,
                dia=fecha.day,
                tipo=TIPO_INGRESO,
                ingreso=monto,
                egreso=Decimal('0'),
                concepto=concepto_ingreso,
                clase=CLASE,
                fpago=None,
                tipo_egreso='INTERBANCARIO',
                conciliada=1,
                paridad=Decimal('1.0000'),
                tipo_poliza='DIARIO',
                num_factura='',
                paridad_dof=None,
                referencia='TRASPASO AUTOMATICO',
            )
            plan.movimientos_pm.append(datos_ingreso)

            # --- Poliza: 2 lineas, DocTipo=TRASPASOS ---
            # Concepto poliza en formato produccion:
            # Cargo: "TRASPASO de BANREGIO-038900 a BANREGIO-055003"
            # Abono: "TRASPASO de Banco: BANREGIO"
            concepto_poliza_cargo = (
                f"TRASPASO de {cfg_origen.banco}-{cta_origen_corta} "
                f"a {cfg_destino.banco}-{cta_destino_corta}"
            )
            concepto_poliza_abono = (
                f"TRASPASO de Banco: {cfg_origen.banco}"
            )
            lineas = _generar_poliza_traspaso(
                monto=monto,
                cta_origen=(cfg_origen.cuenta_contable, cfg_origen.subcuenta_contable),
                cta_destino=(cfg_destino.cuenta_contable, cfg_destino.subcuenta_contable),
                concepto_cargo=concepto_poliza_cargo,
                concepto_abono=concepto_poliza_abono,
            )
            plan.lineas_poliza.extend(lineas)

            # Tracking: 0 facturas por cada movimiento, poliza compartida
            # Egreso: 0 facturas, 2 lineas (poliza completa va con el egreso)
            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(2)
            # Ingreso: 0 facturas, 0 lineas (poliza ya se adjunto al egreso)
            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(0)

        # Validaciones
        suma_traspasos = sum(m.monto for m in movimientos)
        plan.validaciones.append(
            f"Total traspasos dia: ${suma_traspasos:,.2f} "
            f"({len(movimientos)} traspasos)"
        )

        return plan


def _resolver_cuenta(numero: str) -> str:
    """Resuelve un numero de cuenta o CLABE a cuenta corta conocida.

    Si el numero ya esta en CUENTA_POR_NUMERO, lo retorna tal cual.
    Si no, busca si alguna cuenta conocida esta embebida en el CLABE
    (la CLABE de 18 digitos contiene la cuenta corta como substring).
    """
    if numero in CUENTA_POR_NUMERO:
        return numero

    # Buscar si alguna cuenta conocida es substring del CLABE
    for cuenta_conocida in CUENTA_POR_NUMERO:
        if cuenta_conocida in numero:
            return cuenta_conocida

    return numero


def _extraer_cuenta_destino(descripcion: str) -> Optional[str]:
    """Extrae el numero de cuenta destino de la descripcion del traspaso."""
    match = RE_CUENTA_DESTINO.search(descripcion)
    if match:
        return match.group(1)
    return None


def _generar_poliza_traspaso(
    monto: Decimal,
    cta_origen: tuple,
    cta_destino: tuple,
    concepto_cargo: str,
    concepto_abono: str,
) -> List[LineaPoliza]:
    """Genera las 2 lineas de poliza para un traspaso.

    1. Cargo cuenta destino = monto
    2. Abono cuenta origen = monto
    DocTipo: TRASPASOS (no CHEQUES)
    """
    return [
        # 1. Cargo cuenta destino
        LineaPoliza(
            movimiento=1,
            cuenta=cta_destino[0],
            subcuenta=cta_destino[1],
            tipo_ca=TipoCA.CARGO,
            cargo=monto,
            abono=Decimal('0'),
            concepto=concepto_cargo,
            doc_tipo='TRASPASOS',
        ),
        # 2. Abono cuenta origen
        LineaPoliza(
            movimiento=2,
            cuenta=cta_origen[0],
            subcuenta=cta_origen[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=monto,
            concepto=concepto_abono,
            doc_tipo='TRASPASOS',
        ),
    ]
