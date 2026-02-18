"""Tests para el procesador de Venta TDC (I1)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    CorteVentaDiaria,
    FacturaVenta,
    MovimientoBancario,
    TipoCA,
    TipoProceso,
)


def _mov_tdc(monto: float, tipo: TipoProceso = TipoProceso.VENTA_TDD) -> MovimientoBancario:
    """Crea un movimiento TDC de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion='ABONO VENTAS TDD_8996711',
        cargo=None,
        abono=Decimal(str(monto)),
        cuenta_banco='038900320016',
        nombre_hoja='Banregio T',
        tipo_proceso=tipo,
    )


def _corte_dia_1() -> CorteVentaDiaria:
    """Corte de tesoreria del dia 1 (datos verificados)."""
    return CorteVentaDiaria(
        fecha_corte=date(2026, 2, 1),
        nombre_hoja='1',
        facturas_individuales=[
            FacturaVenta(serie='FD', numero='20205', importe=Decimal('1234.00')),
        ],
        factura_global_numero='20204',
        factura_global_importe=Decimal('725897.52'),
        total_efectivo=Decimal('400457.50'),
        total_tdc=Decimal('334082.48'),
    )


class TestProcesadorVentaTDC:
    """Tests del procesador sin conexion a BD."""

    def test_plan_basico(self):
        """Genera plan correcto para 1 movimiento TDC."""
        from src.procesadores.venta_tdc import ProcesadorVentaTDC

        procesador = ProcesadorVentaTDC()
        movs = [_mov_tdc(215370.52)]
        corte = _corte_dia_1()

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )

        # 1 movimiento PM
        assert len(plan.movimientos_pm) == 1
        pm = plan.movimientos_pm[0]
        assert pm.tipo == 4
        assert pm.ingreso == Decimal('215370.52')
        assert pm.clase == 'VENTA DIARIA'
        assert pm.fpago == 'Tarjeta Débito'
        assert 'VENTA DIARIA 01/02/2026' in pm.concepto

        # 1 factura PMF (solo GLOBAL)
        assert len(plan.facturas_pmf) == 1
        pmf = plan.facturas_pmf[0]
        assert pmf.serie == 'FD'
        assert pmf.num_factura == '20204'
        assert pmf.tipo_factura == 'GLOBAL'
        assert pmf.ingreso == Decimal('215370.52')

        # 6 lineas poliza
        assert len(plan.lineas_poliza) == 6

    def test_plan_multiples_abonos(self):
        """Genera plan correcto para 4 movimientos TDC del mismo dia."""
        from src.procesadores.venta_tdc import ProcesadorVentaTDC

        procesador = ProcesadorVentaTDC()
        movs = [
            _mov_tdc(215370.52, TipoProceso.VENTA_TDD),
            _mov_tdc(88643.24, TipoProceso.VENTA_TDC),
            _mov_tdc(6560.71, TipoProceso.VENTA_TDD),
            _mov_tdc(23508.01, TipoProceso.VENTA_TDC),
        ]
        corte = _corte_dia_1()

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )

        # 4 movimientos PM
        assert len(plan.movimientos_pm) == 4

        # 4 facturas PMF (1 por movimiento, todas GLOBAL)
        assert len(plan.facturas_pmf) == 4
        for pmf in plan.facturas_pmf:
            assert pmf.tipo_factura == 'GLOBAL'

        # 24 lineas poliza (6 x 4)
        assert len(plan.lineas_poliza) == 24

    def test_poliza_estructura_correcta(self):
        """Las 6 lineas de poliza tienen las cuentas correctas."""
        from src.procesadores.venta_tdc import ProcesadorVentaTDC

        procesador = ProcesadorVentaTDC()
        movs = [_mov_tdc(100000.00)]
        corte = _corte_dia_1()

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )

        lineas = plan.lineas_poliza
        assert len(lineas) == 6

        # Linea 1: Cargo Banco Tarjeta (1120/060000)
        assert lineas[0].cuenta == '1120'
        assert lineas[0].subcuenta == '060000'
        assert lineas[0].tipo_ca == TipoCA.CARGO
        assert lineas[0].cargo == Decimal('100000.00')

        # Linea 2: Abono Clientes (1210/010000)
        assert lineas[1].cuenta == '1210'
        assert lineas[1].subcuenta == '010000'
        assert lineas[1].tipo_ca == TipoCA.ABONO
        assert lineas[1].abono == Decimal('100000.00')

        # Linea 3: Abono IVA Cobrado (2141/010000)
        assert lineas[2].cuenta == '2141'
        assert lineas[2].subcuenta == '010000'
        assert lineas[2].tipo_ca == TipoCA.ABONO

        # Linea 4: Cargo IVA Pte Cobro (2146/010000)
        assert lineas[3].cuenta == '2146'
        assert lineas[3].subcuenta == '010000'
        assert lineas[3].tipo_ca == TipoCA.CARGO

        # Linea 5: Abono IEPS Cobrado (2141/020000)
        assert lineas[4].cuenta == '2141'
        assert lineas[4].subcuenta == '020000'
        assert lineas[4].tipo_ca == TipoCA.ABONO

        # Linea 6: Cargo IEPS Pte Cobro (2146/020000)
        assert lineas[5].cuenta == '2146'
        assert lineas[5].subcuenta == '020000'
        assert lineas[5].tipo_ca == TipoCA.CARGO

    def test_fpago_distingue_tdc_tdd(self):
        """FPago debe ser 'Tarjeta Credito' para TDC y 'Tarjeta Debito' para TDD."""
        from src.procesadores.venta_tdc import ProcesadorVentaTDC

        procesador = ProcesadorVentaTDC()
        corte = _corte_dia_1()

        # TDD
        plan_tdd = procesador.construir_plan(
            movimientos=[_mov_tdc(1000, TipoProceso.VENTA_TDD)],
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )
        assert plan_tdd.movimientos_pm[0].fpago == 'Tarjeta Débito'

        # TDC
        plan_tdc = procesador.construir_plan(
            movimientos=[_mov_tdc(2000, TipoProceso.VENTA_TDC)],
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )
        assert plan_tdc.movimientos_pm[0].fpago == 'Tarjeta Crédito'

    def test_sin_corte_genera_advertencia(self):
        """Sin corte de tesoreria, genera advertencia y plan vacio."""
        from src.procesadores.venta_tdc import ProcesadorVentaTDC

        procesador = ProcesadorVentaTDC()
        movs = [_mov_tdc(100000)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.advertencias) > 0

    def test_concepto_usa_fecha_corte(self):
        """Concepto debe usar fecha del corte de venta, no del deposito."""
        from src.procesadores.venta_tdc import ProcesadorVentaTDC

        procesador = ProcesadorVentaTDC()
        corte = _corte_dia_1()  # fecha_corte = 2026-02-01

        plan = procesador.construir_plan(
            movimientos=[_mov_tdc(50000)],
            fecha=date(2026, 2, 3),  # Fecha deposito = 2026-02-03
            corte_venta=corte,
        )

        # Concepto debe decir 01/02/2026 (fecha corte), NO 03/02/2026 (deposito)
        assert '01/02/2026' in plan.movimientos_pm[0].concepto
        assert '03/02/2026' not in plan.movimientos_pm[0].concepto
