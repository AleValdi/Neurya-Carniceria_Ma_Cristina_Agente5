"""Tests para los procesadores de Conciliacion (E1 pagos, I3 cobros)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    MovimientoBancario,
    TipoProceso,
)


def _pago_spei(monto: float = 50000.00) -> MovimientoBancario:
    """Crea un pago SPEI a proveedor de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 5),
        descripcion='V1234567890 PROVEEDOR SA SPEI.',
        cargo=Decimal(str(monto)),
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.PAGO_PROVEEDOR,
    )


def _cobro_cliente(monto: float = 25000.00) -> MovimientoBancario:
    """Crea una transferencia de cobro a cliente de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 5),
        descripcion='Transferencia recibida de CLIENTE SA',
        cargo=None,
        abono=Decimal(str(monto)),
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.COBRO_CLIENTE,
    )


class TestProcesadorConciliacionPagos:
    """Tests del procesador de conciliacion de pagos."""

    def test_sin_cursor_genera_advertencia(self):
        """Sin conexion a BD, genera advertencia."""
        from src.procesadores.conciliacion_pagos import ProcesadorConciliacionPagos

        procesador = ProcesadorConciliacionPagos()
        movs = [_pago_spei(50000.00)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 5),
            cursor=None,
        )

        assert len(plan.movimientos_pm) == 0  # No crea movimientos
        assert len(plan.conciliaciones) == 0
        assert any('Sin conexion' in adv for adv in plan.advertencias)

    def test_sin_movimientos_genera_advertencia(self):
        """Sin pagos genera advertencia."""
        from src.procesadores.conciliacion_pagos import ProcesadorConciliacionPagos

        procesador = ProcesadorConciliacionPagos()

        plan = procesador.construir_plan(
            movimientos=[],
            fecha=date(2026, 2, 5),
            cursor=None,
        )

        assert len(plan.advertencias) > 0

    def test_no_genera_inserts(self):
        """Los procesadores de conciliacion nunca generan inserts."""
        from src.procesadores.conciliacion_pagos import ProcesadorConciliacionPagos

        procesador = ProcesadorConciliacionPagos()
        movs = [_pago_spei()]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 5),
            cursor=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.facturas_pmf) == 0
        assert len(plan.lineas_poliza) == 0
        assert len(plan.compras) == 0

    def test_tipos_soportados(self):
        """Soporta PAGO_PROVEEDOR."""
        from src.procesadores.conciliacion_pagos import ProcesadorConciliacionPagos

        procesador = ProcesadorConciliacionPagos()
        assert TipoProceso.PAGO_PROVEEDOR in procesador.tipos_soportados


class TestProcesadorConciliacionCobros:
    """Tests del procesador de conciliacion de cobros."""

    def test_sin_cursor_genera_advertencia(self):
        """Sin conexion a BD, genera advertencia."""
        from src.procesadores.conciliacion_cobros import ProcesadorConciliacionCobros

        procesador = ProcesadorConciliacionCobros()
        movs = [_cobro_cliente(25000.00)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 5),
            cursor=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.conciliaciones) == 0
        assert any('Sin conexion' in adv for adv in plan.advertencias)

    def test_sin_movimientos_genera_advertencia(self):
        """Sin cobros genera advertencia."""
        from src.procesadores.conciliacion_cobros import ProcesadorConciliacionCobros

        procesador = ProcesadorConciliacionCobros()

        plan = procesador.construir_plan(
            movimientos=[],
            fecha=date(2026, 2, 5),
            cursor=None,
        )

        assert len(plan.advertencias) > 0

    def test_no_genera_inserts(self):
        """Los procesadores de conciliacion nunca generan inserts."""
        from src.procesadores.conciliacion_cobros import ProcesadorConciliacionCobros

        procesador = ProcesadorConciliacionCobros()
        movs = [_cobro_cliente()]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 5),
            cursor=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.facturas_pmf) == 0
        assert len(plan.lineas_poliza) == 0
        assert len(plan.compras) == 0

    def test_tipos_soportados(self):
        """Soporta COBRO_CLIENTE."""
        from src.procesadores.conciliacion_cobros import ProcesadorConciliacionCobros

        procesador = ProcesadorConciliacionCobros()
        assert TipoProceso.COBRO_CLIENTE in procesador.tipos_soportados
