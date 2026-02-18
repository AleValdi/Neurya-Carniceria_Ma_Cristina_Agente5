"""Base para procesadores de movimientos bancarios.

Define el protocolo que todos los procesadores deben implementar.
"""

from datetime import date
from typing import List, Optional, Protocol

from src.models import (
    CorteVentaDiaria,
    DatosNomina,
    MovimientoBancario,
    PlanEjecucion,
    TipoProceso,
)


class ProcesadorBase(Protocol):
    """Protocolo que todos los procesadores deben implementar.

    Un procesador recibe movimientos clasificados y datos de contexto,
    y genera un PlanEjecucion con todo lo que se insertaria en la BD.
    El procesador NO ejecuta escrituras — solo construye el plan.
    """

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        """Lista de TipoProceso que este procesador maneja."""
        ...

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        corte_venta: Optional[CorteVentaDiaria] = None,
        datos_nomina: Optional[DatosNomina] = None,
    ) -> PlanEjecucion:
        """Construye un plan de ejecucion para un grupo de movimientos.

        Este metodo es de SOLO LECTURA respecto a la BD.
        Puede hacer SELECTs (via cursor) pero NO INSERT/UPDATE/DELETE.

        Args:
            movimientos: Movimientos del mismo tipo y fecha.
            fecha: Fecha de los movimientos.
            cursor: Cursor de BD para consultas de lectura (IVA, cuentas, etc.)
            corte_venta: Datos de tesoreria del dia (para ventas).
            datos_nomina: Datos de nomina (para proceso E2).

        Returns:
            PlanEjecucion con todo lo que se insertaria.
        """
        ...
