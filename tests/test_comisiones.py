"""Tests para el procesador de Comisiones Bancarias (E3)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    DatosCompraPM,
    LineaPoliza,
    MovimientoBancario,
    TipoCA,
    TipoProceso,
)


def _comision_spei(monto: float = 6.00) -> MovimientoBancario:
    """Crea una comision SPEI base de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion='Comision Transferencia - 0001234',
        cargo=Decimal(str(monto)),
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.COMISION_SPEI,
    )


def _iva_spei(monto: float = 0.96) -> MovimientoBancario:
    """Crea un IVA de comision SPEI de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion='IVA de Comision Transfer',
        cargo=Decimal(str(monto)),
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.COMISION_SPEI_IVA,
    )


def _comision_tdc(monto: float = 3500.00) -> MovimientoBancario:
    """Crea una comision TDC base de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion='Aplicacion de Tasas de Descuento',
        cargo=Decimal(str(monto)),
        abono=None,
        cuenta_banco='038900320016',
        nombre_hoja='Banregio T ',
        tipo_proceso=TipoProceso.COMISION_TDC,
    )


def _iva_tdc(monto: float = 560.00) -> MovimientoBancario:
    """Crea un IVA de comision TDC de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion='IVA Aplicacion de Tasas',
        cargo=Decimal(str(monto)),
        abono=None,
        cuenta_banco='038900320016',
        nombre_hoja='Banregio T ',
        tipo_proceso=TipoProceso.COMISION_TDC_IVA,
    )


class TestProcesadorComisiones:
    """Tests del procesador sin conexion a BD."""

    def test_plan_comision_spei(self):
        """Genera plan correcto para comisiones SPEI (cuenta efectivo)."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [
            _comision_spei(6.00),
            _iva_spei(0.96),
            _comision_spei(6.00),
            _iva_spei(0.96),
        ]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        # 1 movimiento (agrupado por cuenta)
        assert len(plan.movimientos_pm) == 1
        pm = plan.movimientos_pm[0]
        assert pm.tipo == 3
        assert pm.egreso == Decimal('13.92')  # (6+0.96)*2
        assert pm.clase == 'COMISIONES BANCARIAS'
        assert pm.fpago is None
        assert pm.tipo_egreso == 'TRANSFERENCIA'
        assert pm.tipo_poliza == 'EGRESO'

    def test_plan_comision_tdc(self):
        """Genera plan correcto para comisiones TDC (cuenta tarjeta)."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [_comision_tdc(3500.00), _iva_tdc(560.00)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        assert len(plan.movimientos_pm) == 1
        pm = plan.movimientos_pm[0]
        assert pm.egreso == Decimal('4060.00')
        assert pm.cuenta == '038900320016'

    def test_agrupacion_por_cuenta(self):
        """Comisiones de diferentes cuentas generan movimientos separados."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [
            _comision_spei(6.00),   # cuenta efectivo
            _iva_spei(0.96),        # cuenta efectivo
            _comision_tdc(3500.00), # cuenta tarjeta
            _iva_tdc(560.00),       # cuenta tarjeta
        ]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        # 2 movimientos (uno por cuenta)
        assert len(plan.movimientos_pm) == 2
        cuentas = {pm.cuenta for pm in plan.movimientos_pm}
        assert '055003730017' in cuentas
        assert '038900320016' in cuentas

    def test_factura_compra_datos(self):
        """Genera factura de compras (SAVRecC/RecD) con datos correctos."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [_comision_spei(6.00), _iva_spei(0.96)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        assert len(plan.compras) == 1
        compra = plan.compras[0]
        assert compra.proveedor == '001081'
        assert compra.subtotal == Decimal('6.00')
        assert compra.iva == Decimal('0.96')
        assert compra.total == Decimal('6.96')
        assert compra.factura == '03022026'  # DDMMAAAA
        assert compra.fecha == date(2026, 2, 3)

    def test_poliza_4_lineas(self):
        """Poliza tiene exactamente 4 lineas con estructura correcta."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [_comision_spei(6.00), _iva_spei(0.96)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        assert len(plan.lineas_poliza) == 4

        # Linea 1: Cargo Proveedores (2110/010000) = total
        assert plan.lineas_poliza[0].cuenta == '2110'
        assert plan.lineas_poliza[0].subcuenta == '010000'
        assert plan.lineas_poliza[0].tipo_ca == TipoCA.CARGO
        assert plan.lineas_poliza[0].cargo == Decimal('6.96')

        # Linea 2: Cargo IVA Acreditable Pte Pago (1240/010000) = IVA
        assert plan.lineas_poliza[1].cuenta == '1240'
        assert plan.lineas_poliza[1].subcuenta == '010000'
        assert plan.lineas_poliza[1].tipo_ca == TipoCA.CARGO
        assert plan.lineas_poliza[1].cargo == Decimal('0.96')

        # Linea 3: Abono IVA Acreditable Pagado (1246/010000) = IVA
        assert plan.lineas_poliza[2].cuenta == '1246'
        assert plan.lineas_poliza[2].subcuenta == '010000'
        assert plan.lineas_poliza[2].tipo_ca == TipoCA.ABONO
        assert plan.lineas_poliza[2].abono == Decimal('0.96')

        # Linea 4: Abono Banco = total
        assert plan.lineas_poliza[3].tipo_ca == TipoCA.ABONO
        assert plan.lineas_poliza[3].abono == Decimal('6.96')

    def test_poliza_banco_segun_cuenta(self):
        """Linea 4 de poliza usa cuenta contable correcta segun banco."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()

        # Cuenta efectivo → 1120/040000
        movs_ef = [_comision_spei(6.00), _iva_spei(0.96)]
        plan_ef = procesador.construir_plan(
            movimientos=movs_ef, fecha=date(2026, 2, 3),
        )
        assert plan_ef.lineas_poliza[3].cuenta == '1120'
        assert plan_ef.lineas_poliza[3].subcuenta == '040000'

        # Cuenta tarjeta → 1120/060000
        movs_tdc = [_comision_tdc(100.00), _iva_tdc(16.00)]
        plan_tdc = procesador.construir_plan(
            movimientos=movs_tdc, fecha=date(2026, 2, 3),
        )
        assert plan_tdc.lineas_poliza[3].cuenta == '1120'
        assert plan_tdc.lineas_poliza[3].subcuenta == '060000'

    def test_concepto_incluye_fecha(self):
        """Concepto del movimiento incluye la fecha formateada."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [_comision_spei(6.00), _iva_spei(0.96)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        assert '03/02/2026' in plan.movimientos_pm[0].concepto
        assert 'COMISIONES BANCARIAS' in plan.movimientos_pm[0].concepto

    def test_tracking_por_movimiento(self):
        """facturas_por_movimiento y lineas_por_movimiento se llenan correctamente."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        movs = [_comision_spei(6.00), _iva_spei(0.96)]

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        # Comisiones usan compras, no facturas PMF
        assert plan.facturas_por_movimiento == [0]
        assert plan.lineas_por_movimiento == [4]

    def test_sin_movimientos_genera_advertencia(self):
        """Sin movimientos genera advertencia y plan vacio."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()

        plan = procesador.construir_plan(
            movimientos=[],
            fecha=date(2026, 2, 3),
        )

        assert len(plan.movimientos_pm) == 0
        assert len(plan.advertencias) > 0

    def test_multiples_spei_mismo_dia(self):
        """Varias comisiones SPEI del mismo dia se suman en 1 movimiento."""
        from src.procesadores.comisiones import ProcesadorComisiones

        procesador = ProcesadorComisiones()
        # 5 SPEIs + 5 IVAs
        movs = []
        for _ in range(5):
            movs.append(_comision_spei(6.00))
            movs.append(_iva_spei(0.96))

        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=date(2026, 2, 3),
        )

        assert len(plan.movimientos_pm) == 1
        # 5 * 6 + 5 * 0.96 = 30 + 4.80 = 34.80
        assert plan.movimientos_pm[0].egreso == Decimal('34.80')

        # Compra refleja subtotal vs IVA
        assert plan.compras[0].subtotal == Decimal('30.00')
        assert plan.compras[0].iva == Decimal('4.80')
