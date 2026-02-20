"""Procesador I3: Cobros a Clientes.

Fase A: Crea el cobro completo cuando NO existe en SAVCheqPM.
    Parsea el numero de factura del EdoCta, busca en SAVFactC,
    y construye un DatosCobroCliente para ejecutar en 4 tablas
    (SAVFactCob + SAVFactC + SAVCheqPM + SAVPoliza).

Fase B: Concilia cuando el cobro YA existe en SAVCheqPM.
    Marca Conciliada=1 (flujo original, sin cambios).

Si no puede resolver la factura → marca REQUIERE INTERVENCION MANUAL.

Caracteristicas:
- Matching: por monto + fecha (+-2 dias) + cuenta bancaria
- Tipo existente en BD: 1 (Ingreso General), Clase: DEPOSITOS
- Concepto en BD: 'CLIENTE: XXXXXX-NOMBRE CM: XXXXX FACT: FC-XXXX'
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from loguru import logger

from config.settings import CUENTAS_BANCARIAS
from src.erp.cobros import (
    buscar_factura_cliente,
    buscar_factura_por_monto,
    obtener_nombre_cliente,
)
from src.models import (
    DatosCobroCliente,
    MovimientoBancario,
    PlanEjecucion,
    TipoProceso,
)


TIPO_BD = 1  # Ingreso General
CLASE_BD = 'DEPOSITOS'

# Regex para extraer numero de factura del texto del EdoCta.
# Ejemplo: "(NB) Recepcion de cuenta: 005637150016. FACTURA 1618_FACTURA 1618"
_RE_FACTURA = re.compile(r'FACTURA\s+(\d+)', re.IGNORECASE)


class ProcesadorConciliacionCobros:
    """Procesador para conciliacion de cobros a clientes (I3)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [TipoProceso.COBRO_CLIENTE]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan de conciliacion para cobros del dia.

        Para cada transferencia recibida de un cliente, busca en SAVCheqPM
        el movimiento no conciliado generado por el modulo Comercial.

        Args:
            movimientos: Transferencias de clientes del dia (del estado de cuenta).
            fecha: Fecha del abono en estado de cuenta.
            cursor: Cursor para consultar SAVCheqPM.
        """
        plan = PlanEjecucion(
            tipo_proceso='CONCILIACION_COBROS',
            descripcion=f'Conciliacion cobros {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin cobros de clientes para este dia")
            return plan

        if cursor is None:
            plan.advertencias.append(
                "Sin conexion a BD: no se puede buscar cobros existentes"
            )
            return plan

        for mov in movimientos:
            match = _buscar_cobro_en_bd(
                cursor, mov.monto, fecha, mov.cuenta_banco,
            )

            if match:
                # Fase B: cobro ya existe → conciliar
                plan.conciliaciones.append({
                    'tabla': 'SAVCheqPM',
                    'folio': match['folio'],
                    'campo': 'Conciliada',
                    'valor_nuevo': 1,
                    'descripcion': (
                        f"Folio {match['folio']}: "
                        f"${mov.monto:,.2f} | {match['concepto'][:50]}"
                    ),
                })
                plan.validaciones.append(
                    f"Match: Cobro ${mov.monto:,.2f} -> "
                    f"Folio {match['folio']} ({match['concepto'][:40]})"
                )
            else:
                # Fase A: cobro NO existe → intentar crear
                cobro_data = _intentar_crear_cobro(cursor, mov, fecha)
                if cobro_data:
                    plan.cobros_cliente.append(cobro_data)
                    plan.validaciones.append(
                        f"Cobro a crear: {cobro_data.serie}-{cobro_data.num_fac} "
                        f"${cobro_data.monto:,.2f} Cliente {cobro_data.cliente}"
                    )
                else:
                    plan.advertencias.append(
                        f"REQUIERE INTERVENCION: ${mov.monto:,.2f} del {fecha} - "
                        f"no se encontro factura pendiente "
                        f"({mov.descripcion[:60]})"
                    )

        resumen_partes = [
            f"{len(movimientos)} en EdoCta",
            f"{len(plan.conciliaciones)} a conciliar",
        ]
        if plan.cobros_cliente:
            resumen_partes.append(f"{len(plan.cobros_cliente)} a crear")
        plan.validaciones.append(f"Cobros: {', '.join(resumen_partes)}")

        return plan


def _buscar_cobro_en_bd(
    cursor,
    monto: Decimal,
    fecha: date,
    cuenta_banco: str,
    tolerancia_dias: int = 2,
    tolerancia_monto: Decimal = Decimal('0.01'),
) -> Optional[Dict]:
    """Busca un cobro no conciliado en SAVCheqPM que coincida.

    Criterios:
    - Misma cuenta bancaria
    - Tipo 1 (ingreso general)
    - Concepto contiene 'CLIENTE'
    - Conciliada = 0
    - Monto dentro de tolerancia
    - Fecha dentro de rango
    """
    fecha_min = fecha - timedelta(days=tolerancia_dias)
    fecha_max = fecha + timedelta(days=tolerancia_dias)

    try:
        cursor.execute("""
            SELECT Folio, Ingreso, Concepto, Dia, Mes, Age
            FROM SAVCheqPM
            WHERE Cuenta = ?
              AND Tipo = ?
              AND Conciliada = 0
              AND Concepto LIKE '%CLIENTE%'
              AND DATEFROMPARTS(Age, Mes, Dia) BETWEEN ? AND ?
              AND ABS(Ingreso - ?) <= ?
            ORDER BY ABS(Ingreso - ?) ASC
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
                'ingreso': Decimal(str(row[1])),
                'concepto': row[2].strip() if row[2] else '',
                'dia': row[3],
                'mes': row[4],
                'age': row[5],
            }
    except Exception as e:
        logger.warning("Error buscando cobro en BD: {}", e)

    return None


def _intentar_crear_cobro(
    cursor,
    mov: MovimientoBancario,
    fecha: date,
) -> Optional[DatosCobroCliente]:
    """Intenta encontrar la factura pendiente y construir datos del cobro.

    Estrategia:
    1. Parsear numero de factura de la descripcion del EdoCta.
    2. Buscar esa factura en SAVFactC y validar que el monto coincida.
    3. Fallback: buscar factura por monto exacto si no se parseo el numero.
    4. Retorna None si no se pudo resolver (intervencion manual).
    """
    # 1. Intentar parsear numero de factura
    m = _RE_FACTURA.search(mov.descripcion)
    if m:
        num_fac = int(m.group(1))
        logger.debug(
            "Parseado FACTURA {} de descripcion: {}",
            num_fac, mov.descripcion[:60],
        )

        factura = buscar_factura_cliente(cursor, 'FC', num_fac)
        if factura:
            if factura['Saldo'] <= 0:
                logger.info(
                    "FC-{} ya cobrada (Saldo={}), intentando fallback por monto",
                    num_fac, factura['Saldo'],
                )
            elif abs(factura['Saldo'] - mov.monto) <= Decimal('0.01'):
                return _construir_datos_cobro(cursor, factura, mov, fecha)
            else:
                logger.info(
                    "FC-{} Saldo=${} != EdoCta ${}, intentando fallback por monto",
                    num_fac, factura['Saldo'], mov.monto,
                )

    # 2. Fallback: buscar por monto exacto
    factura = buscar_factura_por_monto(cursor, mov.monto, fecha)
    if factura:
        logger.info(
            "Fallback por monto: encontrada FC-{} Saldo=${} para EdoCta ${}",
            factura['NumFac'], factura['Saldo'], mov.monto,
        )
        return _construir_datos_cobro(cursor, factura, mov, fecha)

    logger.warning(
        "No se encontro factura pendiente para ${} del {} ({})",
        mov.monto, fecha, mov.descripcion[:60],
    )
    return None


def _construir_datos_cobro(
    cursor,
    factura: Dict,
    mov: MovimientoBancario,
    fecha: date,
) -> DatosCobroCliente:
    """Construye DatosCobroCliente a partir de datos de SAVFactC y el movimiento."""
    # Obtener nombre del cliente desde SAVFactCob (cobros anteriores)
    cliente_nombre = obtener_nombre_cliente(cursor, factura['Cliente'])

    # Obtener config de cuenta bancaria
    cuenta_cfg = None
    for cfg in CUENTAS_BANCARIAS.values():
        if cfg.cuenta == mov.cuenta_banco:
            cuenta_cfg = cfg
            break

    banco = cuenta_cfg.banco if cuenta_cfg else 'BANREGIO'
    cuenta_contable = cuenta_cfg.cuenta_contable if cuenta_cfg else '1120'
    subcuenta_contable = cuenta_cfg.subcuenta_contable if cuenta_cfg else '040000'

    # Desglose fiscal: calcular SubTotalIva0/SubTotalIva16 desde la factura
    iva = factura.get('Iva', Decimal('0'))
    ieps = factura.get('IEPS', Decimal('0'))
    subtotal = factura.get('SubTotal1', Decimal('0'))

    if iva > 0:
        subtotal_iva16 = subtotal - ieps
        subtotal_iva0 = Decimal('0')
    else:
        subtotal_iva16 = Decimal('0')
        subtotal_iva0 = subtotal

    return DatosCobroCliente(
        serie=factura['Serie'],
        num_fac=factura['NumFac'],
        cliente=factura['Cliente'],
        cliente_nombre=cliente_nombre,
        fecha_cobro=fecha,
        fecha_factura=factura['Fecha'],
        monto=mov.monto,
        vendedor=factura.get('Vendedor', ''),
        banco=banco,
        cuenta_banco=mov.cuenta_banco,
        cuenta_contable=cuenta_contable,
        subcuenta_contable=subcuenta_contable,
        subtotal_iva0=subtotal_iva0,
        subtotal_iva16=subtotal_iva16,
        iva=iva,
        ieps=ieps,
    )
