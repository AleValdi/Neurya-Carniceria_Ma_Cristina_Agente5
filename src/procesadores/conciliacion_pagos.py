"""Procesador E1: Conciliacion de Pagos a Proveedores.

Los pagos a proveedores ya existen en SAVCheqPM (capturados manualmente
o por otro modulo). Este procesador solo CONCILIA: encuentra el movimiento
existente que corresponde al SPEI del estado de cuenta y marca Conciliada=1.

Caracteristicas:
- NO crea movimientos, facturas ni polizas
- Solo genera conciliaciones: UPDATE SAVCheqPM SET Conciliada=1
- Matching: por monto + fecha (+-2 dias) + cuenta bancaria
- Tipo existente en BD: 3 (Egreso con Factura), Clase: PAGOS A PROVEEDORES
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
        no conciliado con el mismo monto y fecha similar.

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
                    f"Match: SPEI ${mov.monto:,.2f} -> "
                    f"Folio {match['folio']} ({match['concepto'][:40]})"
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
    tolerancia_dias: int = 2,
    tolerancia_monto: Decimal = Decimal('0.01'),
) -> Optional[Dict]:
    """Busca un pago no conciliado en SAVCheqPM que coincida.

    Criterios:
    - Misma cuenta bancaria
    - Tipo 3 (egreso con factura)
    - Conciliada = 0
    - Monto dentro de tolerancia
    - Fecha dentro de rango
    """
    fecha_min = fecha - timedelta(days=tolerancia_dias)
    fecha_max = fecha + timedelta(days=tolerancia_dias)

    try:
        cursor.execute("""
            SELECT Folio, Egreso, Concepto, Dia, Mes, Age
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
        if row:
            return {
                'folio': row[0],
                'egreso': Decimal(str(row[1])),
                'concepto': row[2].strip() if row[2] else '',
                'dia': row[3],
                'mes': row[4],
                'age': row[5],
            }
    except Exception as e:
        logger.warning("Error buscando pago en BD: {}", e)

    return None
