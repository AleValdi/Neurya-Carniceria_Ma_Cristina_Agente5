"""Tests para el procesador de Venta Efectivo (I2)."""

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


def _mov_efectivo(monto: float) -> MovimientoBancario:
    """Crea un movimiento de deposito en efectivo de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 10),
        descripcion='Deposito en efectivo_fl7letCsFU',
        cargo=None,
        abono=Decimal(str(monto)),
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.VENTA_EFECTIVO,
    )


def _corte_con_facturas() -> CorteVentaDiaria:
    """Corte de tesoreria con 3 facturas individuales + 1 global."""
    return CorteVentaDiaria(
        fecha_corte=date(2026, 2, 6),
        nombre_hoja='6',
        facturas_individuales=[
            FacturaVenta(serie='FD', numero='20210', importe=Decimal('1238.64')),
            FacturaVenta(serie='FD', numero='20211', importe=Decimal('629.20')),
            FacturaVenta(serie='FD', numero='20212', importe=Decimal('731.79')),
        ],
        factura_global_numero='20213',
        factura_global_importe=Decimal('470254.13'),
        total_efectivo=Decimal('400457.50'),
        total_tdc=Decimal('334082.48'),
    )


class TestProcesadorVentaEfectivo:
    """Tests del procesador sin conexion a BD."""

    def test_plan_basico(self):
        """Genera plan correcto para 1 deposito de efectivo."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        movs = [_mov_efectivo(400457.50)]
        corte = _corte_con_facturas()

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 10),
            corte_venta=corte,
        )

        # 1 movimiento PM
        assert len(plan.movimientos_pm) == 1
        pm = plan.movimientos_pm[0]
        assert pm.tipo == 4
        assert pm.ingreso == Decimal('400457.50')
        assert pm.clase == 'VENTA DIARIA'
        assert pm.fpago == 'Efectivo'
        assert 'VENTA DIARIA 06/02/2026' in pm.concepto

    def test_facturas_individual_y_global(self):
        """Debe tener facturas INDIVIDUAL + GLOBAL en SAVCheqPMF."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        movs = [_mov_efectivo(400457.50)]
        corte = _corte_con_facturas()

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 10),
            corte_venta=corte,
        )

        # 3 individuales + 1 global = 4 facturas
        assert len(plan.facturas_pmf) == 4

        individuales = [f for f in plan.facturas_pmf if f.tipo_factura == 'INDIVIDUAL']
        globales = [f for f in plan.facturas_pmf if f.tipo_factura == 'GLOBAL']

        assert len(individuales) == 3
        assert len(globales) == 1

        # Importes individuales coinciden con tesoreria
        assert individuales[0].ingreso == Decimal('1238.64')
        assert individuales[1].ingreso == Decimal('629.20')
        assert individuales[2].ingreso == Decimal('731.79')

        # Global = deposito - suma individuales
        suma_ind = Decimal('1238.64') + Decimal('629.20') + Decimal('731.79')
        assert globales[0].ingreso == Decimal('400457.50') - suma_ind

    def test_aplicacion_global_es_remanente(self):
        """La aplicacion de la factura global = deposito - suma individuales."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        movs = [_mov_efectivo(10000.00)]
        corte = CorteVentaDiaria(
            fecha_corte=date(2026, 2, 1),
            nombre_hoja='1',
            facturas_individuales=[
                FacturaVenta(serie='FD', numero='20100', importe=Decimal('3000.00')),
                FacturaVenta(serie='FD', numero='20101', importe=Decimal('2000.00')),
            ],
            factura_global_numero='20102',
            factura_global_importe=Decimal('500000.00'),
            total_efectivo=Decimal('10000.00'),
        )

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )

        globales = [f for f in plan.facturas_pmf if f.tipo_factura == 'GLOBAL']
        assert len(globales) == 1
        # 10000 - 3000 - 2000 = 5000
        assert globales[0].ingreso == Decimal('5000.00')

    def test_poliza_estructura_basica(self):
        """La poliza tiene estructura correcta: Cargo Banco + lineas por factura."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        movs = [_mov_efectivo(10000.00)]
        corte = CorteVentaDiaria(
            fecha_corte=date(2026, 2, 1),
            nombre_hoja='1',
            facturas_individuales=[
                FacturaVenta(serie='FD', numero='20100', importe=Decimal('3000.00')),
            ],
            factura_global_numero='20102',
            factura_global_importe=Decimal('500000.00'),
            total_efectivo=Decimal('10000.00'),
        )

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )

        lineas = plan.lineas_poliza
        assert len(lineas) >= 3  # Al menos: Cargo Banco + 2 Abono Clientes

        # Linea 1: Cargo Banco Efectivo (1120/040000)
        assert lineas[0].cuenta == '1120'
        assert lineas[0].subcuenta == '040000'
        assert lineas[0].tipo_ca == TipoCA.CARGO
        assert lineas[0].cargo == Decimal('10000.00')

        # Linea 2: Abono Clientes para factura individual
        assert lineas[1].cuenta == '1210'
        assert lineas[1].subcuenta == '010000'
        assert lineas[1].tipo_ca == TipoCA.ABONO
        assert lineas[1].abono == Decimal('3000.00')

    def test_tracking_facturas_y_lineas(self):
        """facturas_por_movimiento y lineas_por_movimiento se llenan correctamente."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        movs = [_mov_efectivo(10000.00)]
        corte = CorteVentaDiaria(
            fecha_corte=date(2026, 2, 1),
            nombre_hoja='1',
            facturas_individuales=[
                FacturaVenta(serie='FD', numero='20100', importe=Decimal('3000.00')),
            ],
            factura_global_numero='20102',
            factura_global_importe=Decimal('500000.00'),
            total_efectivo=Decimal('10000.00'),
        )

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
            corte_venta=corte,
        )

        # 1 movimiento PM → 2 facturas (1 individual + 1 global)
        assert len(plan.facturas_por_movimiento) == 1
        assert plan.facturas_por_movimiento[0] == 2

        # Lineas de poliza deben coincidir
        assert len(plan.lineas_por_movimiento) == 1
        assert plan.lineas_por_movimiento[0] == len(plan.lineas_poliza)

    def test_sin_corte_genera_advertencia(self):
        """Sin corte de tesoreria, genera advertencia y plan vacio."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        movs = [_mov_efectivo(100000)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 10),
            corte_venta=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.advertencias) > 0

    def test_concepto_usa_fecha_corte(self):
        """Concepto debe usar fecha del corte de venta, no del deposito."""
        from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo

        procesador = ProcesadorVentaEfectivo()
        corte = _corte_con_facturas()  # fecha_corte = 2026-02-06

        plan = procesador.construir_plan(
            movimientos=[_mov_efectivo(400457.50)],
            fecha=date(2026, 2, 10),  # deposito 4 dias despues
            corte_venta=corte,
        )

        # Concepto debe decir 06/02/2026 (corte), NO 10/02/2026 (deposito)
        assert '06/02/2026' in plan.movimientos_pm[0].concepto
        assert '10/02/2026' not in plan.movimientos_pm[0].concepto
