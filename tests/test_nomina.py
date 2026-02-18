"""Tests para el procesador de Nomina (E2)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    DatosNomina,
    LineaContable,
    MovimientoBancario,
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
        total_dispersion=Decimal('117992.20'),
        total_cheques=Decimal('24980.60'),
        total_vacaciones=Decimal('3905.20'),
        total_finiquito=Decimal('3344.40'),
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
        total_dispersion=Decimal('100000.00'),
        total_cheques=Decimal('0'),
        total_vacaciones=Decimal('0'),
        total_finiquito=Decimal('0'),
    )


class TestProcesadorNomina:
    """Tests del procesador sin conexion a BD."""

    def test_genera_4_movimientos(self):
        """Con todos los componentes, genera 4 movimientos."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        assert len(plan.movimientos_pm) == 4

        # 1. Dispersion
        assert plan.movimientos_pm[0].egreso == Decimal('117992.20')
        assert plan.movimientos_pm[0].tipo_egreso == 'TRANSFERENCIA'
        assert plan.movimientos_pm[0].clase == 'NOMINA'

        # 2. Cheques
        assert plan.movimientos_pm[1].egreso == Decimal('24980.60')
        assert plan.movimientos_pm[1].tipo_egreso == 'CHEQUE'

        # 3. Vacaciones
        assert plan.movimientos_pm[2].egreso == Decimal('3905.20')

        # 4. Finiquito
        assert plan.movimientos_pm[3].egreso == Decimal('3344.40')
        assert plan.movimientos_pm[3].clase == 'FINIQUITO'

    def test_solo_dispersion(self):
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

        # Poliza principal: percepciones (8) + deducciones (5) + banco + acreedores = 15+
        lineas_principales = plan.lineas_por_movimiento[0]
        assert lineas_principales >= 10

        # Verificar que hay cargos 6200
        cargos_6200 = [
            l for l in plan.lineas_poliza[:lineas_principales]
            if l.cuenta == '6200' and l.tipo_ca == TipoCA.CARGO
        ]
        assert len(cargos_6200) == 8  # 8 percepciones

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

        # Verificar abonos 2140 (deducciones)
        abonos_2140 = [
            l for l in plan.lineas_poliza[:lineas_principales]
            if l.cuenta == '2140' and l.tipo_ca == TipoCA.ABONO
        ]
        assert len(abonos_2140) == 5

    def test_poliza_principal_tiene_banco_y_acreedores(self):
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

    def test_poliza_secundaria_2_lineas(self):
        """Polizas secundarias tienen 2 lineas (Cargo Acreedores + Abono Banco)."""
        from src.procesadores.nomina_proc import ProcesadorNomina

        procesador = ProcesadorNomina()
        datos = _datos_nomina_completa()

        plan = procesador.construir_plan(
            movimientos=[_mov_nomina()],
            fecha=date(2026, 2, 7),
            datos_nomina=datos,
        )

        # Movimientos 2, 3, 4 → polizas de 2 lineas cada una
        assert plan.lineas_por_movimiento[1] == 2  # Cheques
        assert plan.lineas_por_movimiento[2] == 2  # Vacaciones
        assert plan.lineas_por_movimiento[3] == 2  # Finiquito

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

        assert 'NOMINA 03' in plan.movimientos_pm[0].concepto
