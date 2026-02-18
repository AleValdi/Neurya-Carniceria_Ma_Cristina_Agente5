"""Procesador I3: Conciliacion de Cobros a Clientes.

Los cobros a clientes ya existen en SAVCheqPM (generados automaticamente
por el modulo Comercial → Cobranza → Crea Cobro Multiple).
Este procesador solo CONCILIA: marca Conciliada=1.

Caracteristicas:
- NO crea movimientos, facturas ni polizas
- Solo genera conciliaciones: UPDATE SAVCheqPM SET Conciliada=1
- Matching: por monto + fecha (+-2 dias) + cuenta bancaria
- Tipo existente en BD: 1 (Ingreso General), Clase: DEPOSITOS
- Concepto en BD: 'CLIENTE: XXXXXX-NOMBRE CM: XXXXX FACT: FC-XXXX'
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from loguru import logger

from src.models import (
    MovimientoBancario,
    PlanEjecucion,
    TipoProceso,
)


TIPO_BD = 1  # Ingreso General
CLASE_BD = 'DEPOSITOS'


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
                plan.advertencias.append(
                    f"Sin match para cobro ${mov.monto:,.2f} del {fecha} "
                    f"({mov.descripcion[:50]})"
                )

        plan.validaciones.append(
            f"Cobros: {len(movimientos)} en EdoCta, "
            f"{len(plan.conciliaciones)} conciliados"
        )

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
