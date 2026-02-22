"""Tests para los procesadores de Conciliacion (E1 pagos, I3 cobros)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    LineaPoliza,
    MovimientoBancario,
    TipoCA,
    TipoProceso,
)
from src.procesadores.conciliacion_pagos import _construir_lineas_poliza_pago


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


# --- Helpers para tests de poliza de pagos ---

def _match_base(**overrides) -> dict:
    """Crea un match base para tests de _construir_lineas_poliza_pago."""
    base = {
        'folio': 126842,
        'egreso': Decimal('10000.00'),
        'concepto': 'PAGO FACTURA TEST',
        'num_poliza': 125260,
        'banco': 'BANREGIO',
        'cuenta': '055003730017',
        'proveedor': '001057',
        'nombre_proveedor': 'ANGEL MARIO GARZA SADA',
        'tipo_recepcion': '',
        'iva': Decimal('0'),
        'ieps': Decimal('0'),
        'retencion_iva': Decimal('0'),
        'retencion_isr': Decimal('0'),
    }
    base.update(overrides)
    return base


class TestPolizaPago:
    """Tests de _construir_lineas_poliza_pago()."""

    def test_sin_impuestos_2_lineas(self):
        """Pago sin IVA/IEPS → 2 lineas: Proveedores CARGO + Banco ABONO."""
        match = _match_base(egreso=Decimal('1440.00'))
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        assert len(lineas) == 2

        # Linea 1: Proveedores CARGO
        assert lineas[0].movimiento == 1
        assert lineas[0].cuenta == '2110'
        assert lineas[0].subcuenta == '010000'
        assert lineas[0].tipo_ca == TipoCA.CARGO
        assert lineas[0].cargo == Decimal('1440.00')
        assert 'Total Pago: 126842' in lineas[0].concepto

        # Linea 2: Banco ABONO
        assert lineas[1].movimiento == 2
        assert lineas[1].cuenta == '1120'
        assert lineas[1].subcuenta == '040000'
        assert lineas[1].tipo_ca == TipoCA.ABONO
        assert lineas[1].abono == Decimal('1440.00')
        assert 'Folio Pago: 126842' in lineas[1].concepto

    def test_con_iva_4_lineas(self):
        """Pago con IVA → 4 lineas (Proveedores + IVAPP + IVAP + Banco)."""
        match = _match_base(
            egreso=Decimal('9579.50'),
            iva=Decimal('1321.31'),
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        assert len(lineas) == 4

        # Linea 1: Proveedores
        assert lineas[0].movimiento == 1
        assert lineas[0].cargo == Decimal('9579.50')

        # Linea 2: IVA PTE PAGO (ABONO)
        assert lineas[1].movimiento == 2
        assert lineas[1].cuenta == '1240'
        assert lineas[1].subcuenta == '010000'
        assert lineas[1].tipo_ca == TipoCA.ABONO
        assert lineas[1].abono == Decimal('1321.31')
        assert 'IVAPP' in lineas[1].concepto

        # Linea 3: IVA PAGADO (CARGO)
        assert lineas[2].movimiento == 3
        assert lineas[2].cuenta == '1246'
        assert lineas[2].subcuenta == '010000'
        assert lineas[2].tipo_ca == TipoCA.CARGO
        assert lineas[2].cargo == Decimal('1321.31')
        assert 'IVAP' in lineas[2].concepto

        # Linea 4: Banco
        assert lineas[3].movimiento == 4
        assert lineas[3].abono == Decimal('9579.50')

    def test_con_ieps_4_lineas(self):
        """Pago con IEPS → 4 lineas (Proveedores + IEPSPP + IEPSP + Banco)."""
        match = _match_base(
            egreso=Decimal('3975.71'),
            proveedor='001087',
            nombre_proveedor='BIMBO ',
            ieps=Decimal('294.48'),
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        assert len(lineas) == 4

        # Linea 2: IEPS PTE PAGO
        assert lineas[1].cuenta == '1240'
        assert lineas[1].subcuenta == '020000'
        assert lineas[1].tipo_ca == TipoCA.ABONO
        assert lineas[1].abono == Decimal('294.48')
        assert 'IEPSPP' in lineas[1].concepto

        # Linea 3: IEPS PAGADO
        assert lineas[2].cuenta == '1246'
        assert lineas[2].subcuenta == '020000'
        assert lineas[2].tipo_ca == TipoCA.CARGO
        assert lineas[2].cargo == Decimal('294.48')
        assert 'IEPSP' in lineas[2].concepto

    def test_con_iva_retiva_6_lineas(self):
        """Pago con IVA + Ret IVA → 6 lineas."""
        match = _match_base(
            folio=127037,
            egreso=Decimal('320.32'),
            proveedor='001640',
            nombre_proveedor='SERVICIO INTEGRAL DE SEGURIDAD',
            iva=Decimal('22.88'),
            retencion_iva=Decimal('5.72'),
            tipo_recepcion='SERVICIO DE TRASLADO',
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        assert len(lineas) == 6

        # IVA neto = 22.88 - 5.72 = 17.16
        iva_neto = Decimal('17.16')

        # Linea 1: Proveedores CARGO $320.32
        assert lineas[0].cargo == Decimal('320.32')

        # Linea 2: IVAPP ABONO $17.16
        assert lineas[1].abono == iva_neto
        assert lineas[1].cuenta == '1240'

        # Linea 3: IVAP CARGO $17.16
        assert lineas[2].cargo == iva_neto
        assert lineas[2].cuenta == '1246'

        # Linea 4: RetIVAPP CARGO $5.72
        assert lineas[3].movimiento == 4
        assert lineas[3].cuenta == '2140'
        assert lineas[3].subcuenta == '260000'
        assert lineas[3].tipo_ca == TipoCA.CARGO
        assert lineas[3].cargo == Decimal('5.72')
        assert 'RetIVAPP' in lineas[3].concepto
        assert 'SERVICIO DE TRASLADO' in lineas[3].concepto

        # Linea 5: RetIVAP ABONO $5.72
        assert lineas[4].movimiento == 5
        assert lineas[4].cuenta == '2140'
        assert lineas[4].subcuenta == '290000'
        assert lineas[4].tipo_ca == TipoCA.ABONO
        assert lineas[4].abono == Decimal('5.72')

        # Linea 6: Banco ABONO $320.32 (slot 8 — salta 6,7 de IEPS)
        assert lineas[5].movimiento == 8
        assert lineas[5].abono == Decimal('320.32')

    def test_con_iva_retiva_retisr_8_lineas(self):
        """Pago con IVA + Ret IVA + Ret ISR → 8 lineas."""
        match = _match_base(
            folio=127222,
            egreso=Decimal('47278.32'),
            proveedor='001319',
            nombre_proveedor='HOMERO GARZA MARTINEZ',
            iva=Decimal('7934.83'),
            retencion_iva=Decimal('5289.90'),
            retencion_isr=Decimal('4959.27'),
            tipo_recepcion='ARRENDAMIENTO',
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        assert len(lineas) == 8

        # IVA neto = 7934.83 - 5289.90 = 2644.93
        iva_neto = Decimal('2644.93')

        # Linea 1: Proveedores
        assert lineas[0].movimiento == 1
        assert lineas[0].cargo == Decimal('47278.32')

        # Linea 2-3: IVA
        assert lineas[1].movimiento == 2
        assert lineas[1].abono == iva_neto
        assert lineas[2].movimiento == 3
        assert lineas[2].cargo == iva_neto

        # Linea 4-5: RetIVA
        assert lineas[3].movimiento == 4
        assert lineas[3].cargo == Decimal('5289.90')
        assert 'ARRENDAMIENTO' in lineas[3].concepto
        assert lineas[4].movimiento == 5
        assert lineas[4].abono == Decimal('5289.90')

        # Linea 6-7: RetISR (slots 8-9, saltando 6-7 de IEPS)
        assert lineas[5].movimiento == 8
        assert lineas[5].cuenta == '2140'
        assert lineas[5].subcuenta == '140000'
        assert lineas[5].tipo_ca == TipoCA.CARGO
        assert lineas[5].cargo == Decimal('4959.27')
        assert 'RetISRPP' in lineas[5].concepto

        assert lineas[6].movimiento == 9
        assert lineas[6].cuenta == '2140'
        assert lineas[6].subcuenta == '320000'
        assert lineas[6].tipo_ca == TipoCA.ABONO
        assert lineas[6].abono == Decimal('4959.27')

        # Linea 8: Banco (slot 10)
        assert lineas[7].movimiento == 10
        assert lineas[7].abono == Decimal('47278.32')

    def test_nombre_proveedor_truncado_10_chars(self):
        """Nombre del proveedor se trunca a 10 caracteres."""
        match = _match_base(
            nombre_proveedor='SERVICIO INTEGRAL DE SEGURIDAD',
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        assert 'Nombre:SERVICIO I ' in lineas[0].concepto

    def test_concepto_doc_tipo_cheques(self):
        """Todas las lineas usan DocTipo='CHEQUES'."""
        match = _match_base(iva=Decimal('100.00'))
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        for linea in lineas:
            assert linea.doc_tipo == 'CHEQUES'

    def test_cuenta_banco_gastos(self):
        """Pago por cuenta gastos usa 1120/070000."""
        match = _match_base()
        lineas = _construir_lineas_poliza_pago(match, '1120', '070000')

        banco_linea = lineas[-1]
        assert banco_linea.cuenta == '1120'
        assert banco_linea.subcuenta == '070000'

    def test_balance_cargo_abono(self):
        """Suma cargos = suma abonos en toda la poliza."""
        # Con IVA + RetIVA + RetISR
        match = _match_base(
            egreso=Decimal('47278.32'),
            iva=Decimal('7934.83'),
            retencion_iva=Decimal('5289.90'),
            retencion_isr=Decimal('4959.27'),
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        total_cargo = sum(l.cargo for l in lineas)
        total_abono = sum(l.abono for l in lineas)
        assert total_cargo == total_abono

    def test_balance_solo_ieps(self):
        """Suma cargos = suma abonos con solo IEPS."""
        match = _match_base(
            egreso=Decimal('5000.00'),
            ieps=Decimal('300.00'),
        )
        lineas = _construir_lineas_poliza_pago(match, '1120', '040000')

        total_cargo = sum(l.cargo for l in lineas)
        total_abono = sum(l.abono for l in lineas)
        assert total_cargo == total_abono
