"""Tests para el clasificador de movimientos bancarios."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import MovimientoBancario, TipoProceso


def _crear_mov(descripcion: str, cuenta: str = '055003730017',
               abono=None, cargo=None) -> MovimientoBancario:
    """Helper para crear un movimiento de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 3),
        descripcion=descripcion,
        cargo=Decimal(str(cargo)) if cargo else None,
        abono=Decimal(str(abono)) if abono else None,
        cuenta_banco=cuenta,
        nombre_hoja='Test',
    )


class TestClasificador:
    """Tests de clasificacion por patron."""

    def test_venta_tdc(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('ABONO VENTAS TDC_8996711', '038900320016', abono=88643.24)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.VENTA_TDC

    def test_venta_tdd(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('ABONO VENTAS TDD_8996711', '038900320016', abono=215370.52)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.VENTA_TDD

    def test_deposito_efectivo(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('Deposito en efectivo_ZJWej2f4nX', '055003730017', abono=308698.00)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.VENTA_EFECTIVO

    def test_traspaso_egreso(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('(BE) Traspaso a cuenta: 038900320016. Transferencia', '055003730017', cargo=1400000)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.TRASPASO

    def test_traspaso_ingreso(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('(NB) Recepcion de cuenta: 055003730017. Transferencia', '038900320016', abono=1400000)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.TRASPASO_INGRESO

    def test_comision_spei(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('Comision Transferencia - 26/01/2026', '055003730017', cargo=6.00)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.COMISION_SPEI

    def test_comision_spei_iva(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('IVA de Comision Transferencia', '055003730017', cargo=0.96)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.COMISION_SPEI_IVA

    def test_comision_tdc(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('Aplicacion de Tasas de Descuento de credito; miscelaneas_899', '038900320016', cargo=11.67)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.COMISION_TDC

    def test_comision_tdc_iva(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('IVA Aplicacion de Tasas de Descuento', '038900320016', cargo=1.87)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.COMISION_TDC_IVA

    def test_cobro_cheque(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('Cobro de cheque:0000000007632_0007632', '055003730017', cargo=24980.60)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.COBRO_CHEQUE

    def test_nomina(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('NOMINA - PAGO DE NOMINA_055003730017', '055003730017', cargo=114649.60)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.NOMINA

    def test_desconocido(self):
        from src.clasificador import clasificar_movimientos

        movs = [_crear_mov('Algo completamente desconocido XYZ', '055003730017', cargo=100)]
        clasificar_movimientos(movs)
        assert movs[0].tipo_proceso == TipoProceso.DESCONOCIDO


class TestClasificadorConDatosReales:
    """Tests usando el archivo real de estado de cuenta."""

    def test_cobertura_clasificacion(self, ruta_estado_cuenta):
        """Al menos 80% de movimientos deben clasificarse."""
        from src.entrada.estado_cuenta import parsear_estado_cuenta_plano
        from src.clasificador import clasificar_movimientos, resumen_clasificacion

        movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
        clasificar_movimientos(movimientos)
        resumen = resumen_clasificacion(movimientos)

        total = sum(resumen.values())
        desconocidos = resumen.get('DESCONOCIDO', 0)
        cobertura = ((total - desconocidos) / total * 100) if total > 0 else 0

        assert cobertura >= 80, (
            f"Cobertura de clasificacion: {cobertura:.1f}% "
            f"({desconocidos} desconocidos de {total}). "
            f"Resumen: {resumen}"
        )

    def test_tdc_detectados(self, ruta_estado_cuenta):
        """Debe detectar abonos TDC en cuenta tarjeta."""
        from src.entrada.estado_cuenta import parsear_estado_cuenta_plano
        from src.clasificador import clasificar_movimientos

        movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
        clasificar_movimientos(movimientos)

        tdc = [m for m in movimientos if m.tipo_proceso in (TipoProceso.VENTA_TDC, TipoProceso.VENTA_TDD)]
        assert len(tdc) > 20, f"Esperaba >20 abonos TDC, encontro {len(tdc)}"

    def test_agrupacion_tdc_por_fecha(self, ruta_estado_cuenta):
        """TDC deben agruparse por fecha."""
        from src.entrada.estado_cuenta import parsear_estado_cuenta_plano
        from src.clasificador import clasificar_movimientos, agrupar_ventas_tdc_por_fecha

        movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
        clasificar_movimientos(movimientos)
        grupos = agrupar_ventas_tdc_por_fecha(movimientos)

        assert len(grupos) > 5, f"Esperaba >5 dias con TDC, encontro {len(grupos)}"

        # Cada dia debe tener al menos 1 movimiento TDC
        for fecha, movs in grupos.items():
            assert len(movs) >= 1, f"Dia {fecha}: sin movimientos TDC"
