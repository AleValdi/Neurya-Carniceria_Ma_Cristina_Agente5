"""Tests para el procesador de Nomina (E2).

construir_plan() solo crea la DISPERSION (1 movimiento, poliza ~17 lineas).
construir_plan_cheque() crea movimientos secundarios (cheques, finiquito, etc.)
a partir de lineas "Cobro de cheque" del estado de cuenta.
"""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    DatosNomina,
    LineaContable,
    MovimientoBancario,
    MovimientoNomina,
    TipoCA,
    TipoProceso,
)


def _mov_nomina() -> MovimientoBancario:
    """Crea un movimiento de nomina de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 7),
        descripcion='NOMINA - PAGO DE NOMINA 03',
        cargo=Decimal('142972.80'),
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.NOMINA,
    )


def _datos_nomina_completa() -> DatosNomina:
    """Datos de nomina con percepciones y deducciones."""
    return DatosNomina(
        numero_nomina=3,
        movimientos=[
            MovimientoNomina(tipo='DISPERSION', monto=Decimal('117992.20'), clase='NOMINA', tipo_egreso='TRANSFERENCIA', es_principal=True),
            MovimientoNomina(tipo='CHEQUES', monto=Decimal('24980.60'), clase='NOMINA', tipo_egreso='CHEQUE', es_principal=False),
            MovimientoNomina(tipo='VAC PAGADAS', monto=Decimal('3905.20'), clase='NOMINA', tipo_egreso='TRANSFERENCIA', es_principal=False),
            MovimientoNomina(tipo='FINIQUITO PAGADO', monto=Decimal('3344.40'), clase='FINIQUITO', tipo_egreso='TRANSFERENCIA', es_principal=False),
        ],
        percepciones=[
            LineaContable(concepto='SUELDO', cuenta='6200', subcuenta='010000', monto=Decimal('119737.16')),
            LineaContable(concepto='SEPTIMO DIA', cuenta='6200', subcuenta='240000', monto=Decimal('20473.00')),
            LineaContable(concepto='PRIMA DOMINICAL', cuenta='6200', subcuenta='670000', monto=Decimal('4923.42')),
            LineaContable(concepto='VACACIONES', cuenta='6200', subcuenta='020000', monto=Decimal('7690.80')),
            LineaContable(concepto='PRIMA VACACIONAL', cuenta='6200', subcuenta='060000', monto=Decimal('2158.98')),
            LineaContable(concepto='AGUINALDO', cuenta='6200', subcuenta='030000', monto=Decimal('191.58')),
            LineaContable(concepto='BONO PUNTUALIDAD', cuenta='6200', subcuenta='770000', monto=Decimal('300.00')),
            LineaContable(concepto='BONO ASISTENCIA', cuenta='6200', subcuenta='780000', monto=Decimal('400.00')),
        ],
        deducciones=[
            LineaContable(concepto='IMSS', cuenta='2140', subcuenta='010000', monto=Decimal('635.78')),
            LineaContable(concepto='ISR', cuenta='2140', subcuenta='020000', monto=Decimal('2049.25')),
            LineaContable(concepto='INFONAVIT VIVIENDA', cuenta='2140', subcuenta='270000', monto=Decimal('15.00')),
            LineaContable(concepto='INFONAVIT FD', cuenta='2140', subcuenta='270000', monto=Decimal('380.33')),
            LineaContable(concepto='INFONAVIT CF', cuenta='2140', subcuenta='270000', monto=Decimal('3518.28')),
        ],
    )


def _datos_nomina_simple() -> DatosNomina:
    """Datos de nomina minimos (solo dispersion)."""
    return DatosNomina(
        numero_nomina=3,
        movimientos=[
            MovimientoNomina(tipo='DISPERSION', monto=Decimal('100000.00'), es_principal=True),
        ],
    )


class TestDispersion:
    """Tests para construir_plan() — solo DISPERSION."""

    def test_genera_1_movimiento_dispersion(self):
        """Con todos los componentes, genera solo 1 movimiento (dispersion)."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        assert len(plan.movimientos_pm) == 1
        assert plan.movimientos_pm[0].egreso == Decimal('117992.20')
        assert plan.movimientos_pm[0].tipo_egreso == 'TRANSFERENCIA'
        assert plan.movimientos_pm[0].clase == 'NOMINA'

    def test_solo_dispersion_simple(self):
        """Con solo dispersion, genera 1 movimiento."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_simple()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        assert len(plan.movimientos_pm) == 1
        assert plan.movimientos_pm[0].egreso == Decimal('100000.00')

    def test_poliza_principal_tiene_percepciones(self):
        """Poliza principal incluye cargos por percepciones."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        lineas_principales = plan.lineas_por_movimiento[0]
        assert lineas_principales >= 10

        cargos_6200 = [
            l for l in plan.lineas_poliza[:lineas_principales]
            if l.cuenta == '6200' and l.tipo_ca == TipoCA.CARGO
        ]
        assert len(cargos_6200) >= 8

    def test_poliza_principal_tiene_deducciones(self):
        """Poliza principal incluye abonos por deducciones."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        lineas_principales = plan.lineas_por_movimiento[0]

        abonos_2140 = [
            l for l in plan.lineas_poliza[:lineas_principales]
            if l.cuenta == '2140' and l.tipo_ca == TipoCA.ABONO
        ]
        assert len(abonos_2140) == 5

    def test_dispersion_provisiona_acreedores(self):
        """Poliza principal incluye abono banco y abono acreedores."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        lineas_principales = plan.lineas_por_movimiento[0]
        lineas_pp = plan.lineas_poliza[:lineas_principales]

        # Abono Banco (1120/040000) = dispersion
        abonos_banco = [
            l for l in lineas_pp
            if l.cuenta == '1120' and l.subcuenta == '040000' and l.tipo_ca == TipoCA.ABONO
        ]
        assert len(abonos_banco) == 1
        assert abonos_banco[0].abono == Decimal('117992.20')

        # Abono Acreedores (2120/040000) = cheques + vacaciones + finiquito
        abonos_acreedores = [
            l for l in lineas_pp
            if l.cuenta == '2120' and l.subcuenta == '040000' and l.tipo_ca == TipoCA.ABONO
        ]
        assert len(abonos_acreedores) == 1
        esperado = Decimal('24980.60') + Decimal('3905.20') + Decimal('3344.40')
        assert abonos_acreedores[0].abono == esperado

    def test_tracking_facturas_siempre_cero(self):
        """Nomina no tiene facturas PMF."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        assert all(f == 0 for f in plan.facturas_por_movimiento)
        assert len(plan.facturas_pmf) == 0

    def test_sin_datos_nomina_genera_advertencia(self):
        """Sin datos de nomina CONTPAQi genera advertencia."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=None,
        )

        assert len(plan.movimientos_pm) == 0
        assert any('Sin datos de nomina' in adv for adv in plan.advertencias)

    def test_concepto_incluye_numero_nomina(self):
        """Concepto incluye numero de nomina formateado."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_simple()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        # Concepto formato PROD: "NOMINA S03- 01/04 FEBRERO"
        concepto = plan.movimientos_pm[0].concepto
        assert 'NOMINA S03-' in concepto
        assert 'FEBRERO' in concepto


class TestCobroCheque:
    """Tests para construir_plan_cheque() — movimientos secundarios."""

    def test_cheque_match_por_monto(self):
        """construir_plan_cheque matchea secundario por monto."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('24980.60'),
            num_cheque='7632',
        )

        assert plan is not None
        assert len(plan.movimientos_pm) == 1
        assert plan.movimientos_pm[0].egreso == Decimal('24980.60')
        assert plan.movimientos_pm[0].clase == 'NOMINA'
        assert plan.movimientos_pm[0].tipo_egreso == 'CHEQUE'
        assert plan.movimientos_pm[0].num_cheque == '7632'

    def test_cheque_poliza_2_lineas(self):
        """Poliza secundaria tiene 2 lineas: Cargo 2120 + Abono 1120."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('3905.20'),  # VAC PAGADAS
            num_cheque='7633',
        )

        assert plan is not None
        assert len(plan.lineas_poliza) == 2
        assert plan.lineas_por_movimiento == [2]

        # Linea 1: Cargo Acreedores (2120/040000)
        assert plan.lineas_poliza[0].cuenta == '2120'
        assert plan.lineas_poliza[0].subcuenta == '040000'
        assert plan.lineas_poliza[0].tipo_ca == TipoCA.CARGO
        assert plan.lineas_poliza[0].cargo == Decimal('3905.20')

        # Linea 2: Abono Banco (1120/040000)
        assert plan.lineas_poliza[1].cuenta == '1120'
        assert plan.lineas_poliza[1].subcuenta == '040000'
        assert plan.lineas_poliza[1].tipo_ca == TipoCA.ABONO
        assert plan.lineas_poliza[1].abono == Decimal('3905.20')

    def test_cheque_sin_match_retorna_none(self):
        """Monto desconocido retorna None."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('99999.99'),  # No matchea nada
            num_cheque='0000',
        )

        assert plan is None

    def test_cheque_no_repite_match(self):
        """Segundo cobro mismo monto no re-matchea el mismo secundario."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        # Primer cobro: matchea CHEQUES $24,980.60
        plan1 = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('24980.60'),
            num_cheque='7632',
        )
        assert plan1 is not None

        # Segundo cobro: mismo monto, ya no hay secundario disponible
        plan2 = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('24980.60'),
            num_cheque='7634',
        )
        assert plan2 is None

    def test_cheque_match_finiquito(self):
        """Matchea finiquito con clase FINIQUITO."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('3344.40'),
            num_cheque='7635',
        )

        assert plan is not None
        assert plan.movimientos_pm[0].clase == 'FINIQUITO'

    def test_cheque_tolerancia_centavos(self):
        """Matchea con tolerancia de $0.50."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        # Monto con diferencia de $0.30 (dentro de tolerancia $0.50)
        plan = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('24980.30'),
            num_cheque='7632',
        )

        assert plan is not None
        assert plan.movimientos_pm[0].egreso == Decimal('24980.30')

    def test_cheque_no_matchea_principal(self):
        """No matchea el movimiento principal (dispersion)."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        # Monto exacto de la dispersion — no debe matchear
        plan = procesador.construir_plan_cheque(
            fecha=date(2026, 2, 13),
            datos_nomina=datos,
            monto_banco=Decimal('117992.20'),
            num_cheque='0000',
        )

        assert plan is None
