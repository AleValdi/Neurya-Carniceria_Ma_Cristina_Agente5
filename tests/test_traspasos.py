"""Tests para el procesador de Traspasos (E4)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    MovimientoBancario,
    TipoCA,
    TipoProceso,
)


def _traspaso_egreso(
    monto: float = 500000.00,
    cuenta_destino: str = '038900320016',
) -> MovimientoBancario:
    """Crea un traspaso egreso de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion=f'(BE) Traspaso a cuenta: {cuenta_destino}_ref123',
        cargo=Decimal(str(monto)),
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.TRASPASO,
    )


class TestProcesadorTraspasos:
    """Tests del procesador sin conexion a BD."""

    def test_plan_basico(self):
        """Genera 2 movimientos (egreso + ingreso) por cada traspaso."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()
        movs = [_traspaso_egreso(500000.00)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        # 2 movimientos: egreso + ingreso
        assert len(plan.movimientos_pm) == 2

        # Movimiento 1: Egreso
        egreso = plan.movimientos_pm[0]
        assert egreso.tipo == 2
        assert egreso.egreso == Decimal('500000.00')
        assert egreso.ingreso == Decimal('0')
        assert egreso.cuenta == '055003730017'
        assert egreso.clase == 'ENTRE CUENTAS PROPIA'
        assert egreso.tipo_poliza == 'DIARIO'
        assert egreso.tipo_egreso == 'INTERBANCARIO'
        assert egreso.fpago is None
        assert egreso.paridad_dof == Decimal('20.0000')

        # Movimiento 2: Ingreso
        ingreso = plan.movimientos_pm[1]
        assert ingreso.tipo == 1
        assert ingreso.ingreso == Decimal('500000.00')
        assert ingreso.egreso == Decimal('0')
        assert ingreso.cuenta == '038900320016'
        assert ingreso.tipo_egreso == 'INTERBANCARIO'
        assert ingreso.fpago is None
        assert ingreso.paridad_dof is None  # Solo el egreso tiene ParidadDOF

    def test_concepto_incluye_datos_banco(self):
        """Concepto del egreso menciona banco y cuenta destino."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()
        movs = [_traspaso_egreso(100000.00, '038900320016')]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        concepto_egreso = plan.movimientos_pm[0].concepto
        assert 'TRASPASO A BANCO' in concepto_egreso
        assert '038900320016' in concepto_egreso
        assert 'PESOS' in concepto_egreso

    def test_poliza_2_lineas_doc_traspasos(self):
        """Poliza tiene 2 lineas con DocTipo=TRASPASOS y concepto produccion."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()
        movs = [_traspaso_egreso(500000.00)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        assert len(plan.lineas_poliza) == 2

        # Linea 1: Cargo cuenta destino (tarjeta 1120/060000)
        cargo = plan.lineas_poliza[0]
        assert cargo.cuenta == '1120'
        assert cargo.subcuenta == '060000'
        assert cargo.tipo_ca == TipoCA.CARGO
        assert cargo.cargo == Decimal('500000.00')
        assert cargo.doc_tipo == 'TRASPASOS'
        # Concepto produccion: "TRASPASO de BANREGIO-055003 a BANREGIO-038900"
        assert 'TRASPASO de' in cargo.concepto
        assert 'BANREGIO' in cargo.concepto

        # Linea 2: Abono cuenta origen (efectivo 1120/040000)
        abono = plan.lineas_poliza[1]
        assert abono.cuenta == '1120'
        assert abono.subcuenta == '040000'
        assert abono.tipo_ca == TipoCA.ABONO
        assert abono.abono == Decimal('500000.00')
        assert abono.doc_tipo == 'TRASPASOS'
        # Concepto produccion: "TRASPASO de Banco: BANREGIO"
        assert 'TRASPASO de Banco' in abono.concepto

    def test_tracking_por_movimiento(self):
        """facturas_por_movimiento y lineas_por_movimiento correctos."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()
        movs = [_traspaso_egreso(100000.00)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        # 2 movimientos: egreso (0 facturas, 2 lineas) + ingreso (0 facturas, 0 lineas)
        assert plan.facturas_por_movimiento == [0, 0]
        assert plan.lineas_por_movimiento == [2, 0]

    def test_cuenta_destino_no_reconocida(self):
        """Cuenta destino desconocida genera advertencia."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()
        mov = MovimientoBancario(
            fecha=date(2026, 2, 3),
            descripcion='(BE) Traspaso a cuenta: 999999999999_ref',
            cargo=Decimal('100000'),
            abono=None,
            cuenta_banco='055003730017',
            nombre_hoja='Banregio F',
            tipo_proceso=TipoProceso.TRASPASO,
        )

        plan = procesador.construir_plan(
            movimientos=[mov],
            fecha=date(2026, 2, 3),
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.advertencias) > 0
        assert any('no reconocida' in adv for adv in plan.advertencias)

    def test_sin_movimientos(self):
        """Sin movimientos genera advertencia y plan vacio."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()

        plan = procesador.construir_plan(
            movimientos=[],
            fecha=date(2026, 2, 3),
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.advertencias) > 0

    def test_multiples_traspasos_mismo_dia(self):
        """Multiples traspasos generan pares de movimientos independientes."""
        from src.procesadores.traspasos import ProcesadorTraspasos

        procesador = ProcesadorTraspasos()
        movs = [
            _traspaso_egreso(500000.00, '038900320016'),
            _traspaso_egreso(200000.00, '055003730157'),
        ]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        # 2 traspasos * 2 movimientos = 4 movimientos
        assert len(plan.movimientos_pm) == 4
        # 2 traspasos * 2 lineas = 4 lineas de poliza
        assert len(plan.lineas_poliza) == 4


class TestExtraerCuentaDestino:
    """Tests para la extraccion de cuenta destino de la descripcion."""

    def test_formato_normal(self):
        from src.procesadores.traspasos import _extraer_cuenta_destino

        assert _extraer_cuenta_destino(
            '(BE) Traspaso a cuenta: 038900320016_ref123'
        ) == '038900320016'

    def test_sin_patron(self):
        from src.procesadores.traspasos import _extraer_cuenta_destino

        assert _extraer_cuenta_destino('Otro movimiento') is None

    def test_con_espacios_extra(self):
        from src.procesadores.traspasos import _extraer_cuenta_destino

        assert _extraer_cuenta_destino(
            '(BE)  Traspaso a cuenta:  055003730157_abc'
        ) == '055003730157'
