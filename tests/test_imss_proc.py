"""Tests para el procesador IMSS/INFONAVIT (E5 extension)."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.models import (
    DatosIMSS,
    MovimientoBancario,
    TipoCA,
    TipoProceso,
)
from src.procesadores.impuestos import ProcesadorImpuestos


# --- Fixtures ---


def _mov_imss(monto: Decimal) -> MovimientoBancario:
    """Crea un movimiento bancario IMSS de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 10),
        descripcion='(BE) Pago servicio: PAGO SUA/SIPARE_659522',
        cargo=monto,
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.IMPUESTO_IMSS,
    )


def _datos_solo_imss() -> DatosIMSS:
    """Datos solo IMSS, Enero 2026, $93,880.17."""
    return DatosIMSS(
        periodo='ENERO 2026',
        folio_sua='659522',
        total_imss=Decimal('93880.17'),
        total_a_pagar=Decimal('93880.17'),
        confianza_100=True,
    )


def _datos_imss_infonavit() -> DatosIMSS:
    """Datos IMSS+INFONAVIT, Octubre 2025, $300,701.47."""
    return DatosIMSS(
        periodo='OCTUBRE 2025',
        folio_sua='319821',
        total_imss=Decimal('92153.70'),
        retiro=Decimal('25722.43'),
        cesantia_vejez=Decimal('82681.60'),
        total_cuenta_individual=Decimal('108404.03'),
        aportacion_sin_credito=Decimal('54659.80'),
        aportacion_con_credito=Decimal('9646.09'),
        amortizacion=Decimal('35837.85'),
        total_infonavit=Decimal('100143.74'),
        total_a_pagar=Decimal('300701.47'),
        incluye_infonavit=True,
        confianza_100=True,
    )


def _mock_cursor(retencion: Decimal) -> MagicMock:
    """Crea un cursor mock que retorna una retencion IMSS."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (retencion,)
    return cursor


# --- Tests Solo IMSS ---


class TestProcesadorIMSSSolo:
    """Tests con datos solo IMSS (mensual, 3 lineas)."""

    def test_genera_1_movimiento(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        assert len(plan.movimientos_pm) == 1

    def test_movimiento_campos_correctos(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        mov = plan.movimientos_pm[0]
        assert mov.egreso == Decimal('93880.17')
        assert mov.clase == 'PAGO IMSS'
        assert mov.tipo_egreso == 'TRANSFERENCIA'
        assert mov.tipo_poliza == 'EGRESO'
        assert mov.tipo == 2

    def test_concepto_formato(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        assert 'PAGO SUA ENERO 2026' in plan.movimientos_pm[0].concepto

    def test_3_lineas_poliza(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        assert len(plan.lineas_poliza) == 3
        assert plan.lineas_por_movimiento == [3]

    def test_poliza_linea1_retencion(self):
        """Linea 1: Cargo Retencion IMSS (2140/010000)."""
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        l1 = plan.lineas_poliza[0]
        assert l1.cuenta == '2140'
        assert l1.subcuenta == '010000'
        assert l1.tipo_ca == TipoCA.CARGO
        assert l1.cargo == Decimal('14548.30')

    def test_poliza_linea2_gasto(self):
        """Linea 2: Cargo IMSS Gasto (6200/070000) = total - retencion."""
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        l2 = plan.lineas_poliza[1]
        assert l2.cuenta == '6200'
        assert l2.subcuenta == '070000'
        assert l2.tipo_ca == TipoCA.CARGO
        # 93880.17 - 14548.30 = 79331.87
        assert l2.cargo == Decimal('79331.87')

    def test_poliza_linea3_banco(self):
        """Linea 3: Abono Banco (1120/040000) = total pago."""
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        l3 = plan.lineas_poliza[2]
        assert l3.cuenta == '1120'
        assert l3.subcuenta == '040000'
        assert l3.tipo_ca == TipoCA.ABONO
        assert l3.abono == Decimal('93880.17')

    def test_poliza_cuadra(self):
        """Suma cargos == suma abonos."""
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        total_cargos = sum(l.cargo for l in plan.lineas_poliza)
        total_abonos = sum(l.abono for l in plan.lineas_poliza)
        assert total_cargos == total_abonos


# --- Tests IMSS + INFONAVIT ---


class TestProcesadorIMSSInfonavit:
    """Tests con datos IMSS+INFONAVIT (bimestral, 7 lineas)."""

    def test_genera_1_movimiento(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        assert len(plan.movimientos_pm) == 1

    def test_concepto_incluye_infonavit(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        assert 'PAGO IMSS E INFONAVIT OCTUBRE 2025' in plan.movimientos_pm[0].concepto

    def test_7_lineas_poliza(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        assert len(plan.lineas_poliza) == 7
        assert plan.lineas_por_movimiento == [7]

    def test_poliza_linea3_retiro(self):
        """Linea 3: Cargo Retiro 2% SAR (6200/028000)."""
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        l3 = plan.lineas_poliza[2]
        assert l3.cuenta == '6200'
        assert l3.subcuenta == '028000'
        assert l3.cargo == Decimal('25722.43')

    def test_poliza_linea4_cesantia(self):
        """Linea 4: Cargo Cesantia y Vejez (6200/360000)."""
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        l4 = plan.lineas_poliza[3]
        assert l4.cuenta == '6200'
        assert l4.subcuenta == '360000'
        assert l4.cargo == Decimal('82681.60')

    def test_poliza_linea5_infonavit_5pct(self):
        """Linea 5: Cargo 5% INFONAVIT (6200/050000)."""
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        l5 = plan.lineas_poliza[4]
        assert l5.cuenta == '6200'
        assert l5.subcuenta == '050000'
        # 54659.80 + 9646.09 = 64305.89
        assert l5.cargo == Decimal('64305.89')

    def test_poliza_linea6_retencion_infonavit(self):
        """Linea 6: Cargo Retencion INFONAVIT (2140/270000) = Amortizacion."""
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        l6 = plan.lineas_poliza[5]
        assert l6.cuenta == '2140'
        assert l6.subcuenta == '270000'
        assert l6.cargo == Decimal('35837.85')

    def test_poliza_cuadra(self):
        """Suma cargos == suma abonos == $300,701.47."""
        procesador = ProcesadorImpuestos()
        datos = _datos_imss_infonavit()
        cursor = _mock_cursor(Decimal('13073.54'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('300701.47'))],
            fecha=date(2025, 11, 7),
            datos_imss=datos,
            cursor=cursor,
        )

        total_cargos = sum(l.cargo for l in plan.lineas_poliza)
        total_abonos = sum(l.abono for l in plan.lineas_poliza)
        assert total_cargos == total_abonos
        assert total_abonos == Decimal('300701.47')


# --- Tests de casos de error ---


class TestProcesadorIMSSErrors:
    """Tests de validaciones y errores."""

    def test_sin_datos_genera_advertencia(self):
        procesador = ProcesadorImpuestos()

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert any('Sin datos de IMSS' in adv for adv in plan.advertencias)

    def test_sin_confianza_no_genera(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        datos.confianza_100 = False
        datos.advertencias = ['Algo fallo']

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
        )

        assert len(plan.movimientos_pm) == 0
        assert any('sin 100% de confianza' in adv for adv in plan.advertencias)

    def test_monto_no_coincide(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        # Monto del movimiento no coincide con total_a_pagar
        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('99999.99'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        assert len(plan.movimientos_pm) == 0
        assert any('No se encontro movimiento bancario' in adv for adv in plan.advertencias)

    def test_sin_cursor_genera_advertencia(self):
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert any('retencion IMSS' in adv for adv in plan.advertencias)

    def test_no_tiene_facturas(self):
        """IMSS no tiene facturas PMF."""
        procesador = ProcesadorImpuestos()
        datos = _datos_solo_imss()
        cursor = _mock_cursor(Decimal('14548.30'))

        plan = procesador.construir_plan(
            movimientos=[_mov_imss(Decimal('93880.17'))],
            fecha=date(2026, 2, 10),
            datos_imss=datos,
            cursor=cursor,
        )

        assert all(f == 0 for f in plan.facturas_por_movimiento)
        assert len(plan.facturas_pmf) == 0


class TestObtenerRetencionIMSS:
    """Tests de la consulta a balanza."""

    def test_calcula_mes_m2_febrero(self):
        """Feb 2026 → Dic 2025 (mes 12, anio 2025)."""
        procesador = ProcesadorImpuestos()
        cursor = _mock_cursor(Decimal('14548.30'))

        resultado = procesador._obtener_retencion_imss(cursor, date(2026, 2, 10))

        assert resultado == Decimal('14548.30')
        # Verificar que la query uso DicAbonos y PeriodoAge=2025
        query = cursor.execute.call_args[0][0]
        assert 'DicAbonos' in query
        assert cursor.execute.call_args[0][1] == (2025,)

    def test_calcula_mes_m2_noviembre(self):
        """Nov 2025 → Sep 2025 (mes 9, anio 2025)."""
        procesador = ProcesadorImpuestos()
        cursor = _mock_cursor(Decimal('13073.54'))

        resultado = procesador._obtener_retencion_imss(cursor, date(2025, 11, 7))

        assert resultado == Decimal('13073.54')
        query = cursor.execute.call_args[0][0]
        assert 'SepAbonos' in query
        assert cursor.execute.call_args[0][1] == (2025,)

    def test_calcula_mes_m2_enero(self):
        """Ene 2026 → Nov 2025 (mes 11, anio 2025) — cruza anio."""
        procesador = ProcesadorImpuestos()
        cursor = _mock_cursor(Decimal('14014.55'))

        resultado = procesador._obtener_retencion_imss(cursor, date(2026, 1, 15))

        query = cursor.execute.call_args[0][0]
        assert 'NovAbonos' in query
        assert cursor.execute.call_args[0][1] == (2025,)

    def test_cursor_none_retorna_none(self):
        procesador = ProcesadorImpuestos()
        resultado = procesador._obtener_retencion_imss(None, date(2026, 2, 10))
        assert resultado is None

    def test_sin_datos_en_bd(self):
        """Cursor retorna None → retorna None."""
        procesador = ProcesadorImpuestos()
        cursor = MagicMock()
        cursor.fetchone.return_value = None

        resultado = procesador._obtener_retencion_imss(cursor, date(2026, 2, 10))
        assert resultado is None
