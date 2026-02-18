"""Tests para el procesador de Impuestos (E5)."""

from datetime import date
from decimal import Decimal

import pytest

from src.models import (
    DatosImpuestoEstatal,
    DatosImpuestoFederal,
    MovimientoBancario,
    RetencionIVAProveedor,
    TipoCA,
    TipoProceso,
)
from src.procesadores.impuestos import ProcesadorImpuestos


FECHA = date(2026, 2, 11)


def _datos_federal() -> DatosImpuestoFederal:
    """Datos federales de ejemplo (Enero 2026)."""
    return DatosImpuestoFederal(
        periodo='ENERO 2026',
        isr_ret_honorarios=Decimal('523'),
        isr_ret_arrendamiento=Decimal('4959'),
        ieps_neto=Decimal('1340'),
        ieps_acumulable=Decimal('11713'),
        ieps_acreditable=Decimal('10373'),
        total_primera=Decimal('6822'),
        isr_personas_morales=Decimal('17060'),
        isr_ret_salarios=Decimal('12168'),
        iva_acumulable=Decimal('46399'),
        iva_acreditable=Decimal('162263'),
        iva_a_favor=Decimal('115864'),
        retenciones_iva=[
            RetencionIVAProveedor(proveedor='001640', nombre='AUTOTRANSPORTE', monto=Decimal('154')),
            RetencionIVAProveedor(proveedor='001352', nombre='SERVICIOS PERSONALES', monto=Decimal('336')),
            RetencionIVAProveedor(proveedor='001513', nombre='USO O GOCE', monto=Decimal('5290')),
        ],
        total_segunda=Decimal('35008'),
        confianza_100=True,
        advertencias=[],
    )


def _datos_estatal() -> DatosImpuestoEstatal:
    """Datos estatales de ejemplo."""
    return DatosImpuestoEstatal(
        periodo='ENERO 2026',
        monto=Decimal('22971.00'),
        confianza_100=True,
        advertencias=[],
    )


def _mov_federal(monto: Decimal) -> MovimientoBancario:
    return MovimientoBancario(
        fecha=FECHA,
        descripcion='(BE) Pago servicio PAGO REFERENCIADO',
        cargo=monto,
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.IMPUESTO_FEDERAL,
    )


def _mov_estatal(monto: Decimal) -> MovimientoBancario:
    return MovimientoBancario(
        fecha=FECHA,
        descripcion='SECRETARIA DE FINANZAS',
        cargo=monto,
        abono=None,
        cuenta_banco='055003730017',
        nombre_hoja='Banregio F',
        tipo_proceso=TipoProceso.IMPUESTO_ESTATAL,
    )


def _movimientos_completos():
    """Todos los movimientos del ejemplo."""
    return [
        _mov_federal(Decimal('6822')),      # 1a declaracion
        _mov_federal(Decimal('29228')),     # 2a principal (ISR PM + ISR sal)
        _mov_federal(Decimal('154')),       # Ret IVA autotransporte
        _mov_federal(Decimal('336')),       # Ret IVA serv personales
        _mov_federal(Decimal('5290')),      # Ret IVA uso o goce
        _mov_estatal(Decimal('22971.00')),  # Estatal 3%
    ]


class TestProcesadorImpuestosTiposSoportados:
    def test_tipos_soportados(self):
        proc = ProcesadorImpuestos()
        assert TipoProceso.IMPUESTO_FEDERAL in proc.tipos_soportados
        assert TipoProceso.IMPUESTO_ESTATAL in proc.tipos_soportados


class TestProcesadorSinDatos:
    def test_sin_movimientos(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan([], FECHA)
        assert plan.advertencias
        assert len(plan.movimientos_pm) == 0

    def test_federal_sin_datos_pdf(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=None,
        )
        assert any('Sin datos' in a for a in plan.advertencias)
        assert len(plan.movimientos_pm) == 0

    def test_federal_sin_confianza(self):
        datos = _datos_federal()
        datos.confianza_100 = False
        datos.advertencias = ['Algo no cuadra']
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=datos,
        )
        assert any('confianza' in a.lower() for a in plan.advertencias)
        assert len(plan.movimientos_pm) == 0

    def test_estatal_sin_datos_pdf(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('22971'))], FECHA,
            datos_estatal=None,
        )
        assert any('Sin datos' in a for a in plan.advertencias)
        assert len(plan.movimientos_pm) == 0


class TestProcesadorFederal1a:
    def test_genera_1_movimiento(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        assert len(plan.movimientos_pm) == 1

    def test_movimiento_egreso(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        pm = plan.movimientos_pm[0]
        assert pm.egreso == Decimal('6822')
        assert pm.tipo == 2
        assert pm.tipo_egreso == 'TRANSFERENCIA'
        assert pm.tipo_poliza == 'EGRESO'
        assert pm.clase == 'PAGO IMPUESTOS'

    def test_poliza_5_lineas(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        assert plan.lineas_por_movimiento == [5]
        assert len(plan.lineas_poliza) == 5

    def test_poliza_linea_1_isr_honorarios(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[0]
        assert linea.cuenta == '2140'
        assert linea.subcuenta == '070000'
        assert linea.tipo_ca == TipoCA.CARGO
        assert linea.cargo == Decimal('523')

    def test_poliza_linea_2_isr_arrendamiento(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[1]
        assert linea.cuenta == '2140'
        assert linea.subcuenta == '320000'
        assert linea.tipo_ca == TipoCA.CARGO
        assert linea.cargo == Decimal('4959')

    def test_poliza_linea_3_banco(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[2]
        assert linea.cuenta == '1120'
        assert linea.subcuenta == '040000'
        assert linea.tipo_ca == TipoCA.ABONO
        assert linea.abono == Decimal('6822')

    def test_poliza_linea_4_ieps_acumulable(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[3]
        assert linea.cuenta == '2141'
        assert linea.subcuenta == '020000'
        assert linea.tipo_ca == TipoCA.CARGO
        assert linea.cargo == Decimal('11713')

    def test_poliza_linea_5_ieps_acreditable(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('6822'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[4]
        assert linea.cuenta == '1246'
        assert linea.subcuenta == '020000'
        assert linea.tipo_ca == TipoCA.ABONO
        assert linea.abono == Decimal('10373')


class TestProcesadorFederal2a:
    def test_principal_6_lineas(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('29228'))], FECHA,
            datos_federal=_datos_federal(),
        )
        # Solo el principal (sin retenciones IVA porque no hay movimientos para ellas)
        assert len(plan.movimientos_pm) == 1
        assert plan.lineas_por_movimiento == [6]

    def test_principal_monto_isr(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('29228'))], FECHA,
            datos_federal=_datos_federal(),
        )
        pm = plan.movimientos_pm[0]
        assert pm.egreso == Decimal('29228')  # ISR PM + ISR sal

    def test_principal_poliza_isr_provisional(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('29228'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[0]
        assert linea.cuenta == '1245'
        assert linea.subcuenta == '010000'
        assert linea.cargo == Decimal('17060')

    def test_principal_poliza_iva_a_favor(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('29228'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[5]  # Linea 6
        assert linea.cuenta == '1247'
        assert linea.subcuenta == '010000'
        assert linea.cargo == Decimal('115864')


class TestProcesadorRetenciones:
    def test_retencion_genera_4_lineas(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('154'))], FECHA,
            datos_federal=_datos_federal(),
        )
        assert len(plan.movimientos_pm) == 1
        assert plan.lineas_por_movimiento == [4]

    def test_retencion_iva_retenido(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('5290'))], FECHA,
            datos_federal=_datos_federal(),
        )
        linea = plan.lineas_poliza[0]
        assert linea.cuenta == '2140'
        assert linea.subcuenta == '290000'
        assert linea.cargo == Decimal('5290')

    def test_retencion_reclasificacion(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_federal(Decimal('336'))], FECHA,
            datos_federal=_datos_federal(),
        )
        # Linea 3: Cargo IVA Acreditable Pagado
        assert plan.lineas_poliza[2].cuenta == '1246'
        assert plan.lineas_poliza[2].subcuenta == '010000'
        assert plan.lineas_poliza[2].cargo == Decimal('336')
        # Linea 4: Abono IVA Acreditable Pte Pago
        assert plan.lineas_poliza[3].cuenta == '1240'
        assert plan.lineas_poliza[3].subcuenta == '010000'
        assert plan.lineas_poliza[3].abono == Decimal('336')


class TestProcesadorEstatal:
    def test_genera_1_movimiento(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('22971.00'))], FECHA,
            datos_estatal=_datos_estatal(),
        )
        assert len(plan.movimientos_pm) == 1

    def test_movimiento_clase(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('22971.00'))], FECHA,
            datos_estatal=_datos_estatal(),
        )
        pm = plan.movimientos_pm[0]
        assert pm.clase == 'PAGO 3% NOMINA'
        assert pm.egreso == Decimal('22971.00')

    def test_poliza_2_lineas(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('22971.00'))], FECHA,
            datos_estatal=_datos_estatal(),
        )
        assert plan.lineas_por_movimiento == [2]
        assert len(plan.lineas_poliza) == 2

    def test_poliza_cargo_nominas(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('22971.00'))], FECHA,
            datos_estatal=_datos_estatal(),
        )
        linea = plan.lineas_poliza[0]
        assert linea.cuenta == '6200'
        assert linea.subcuenta == '850000'
        assert linea.tipo_ca == TipoCA.CARGO
        assert linea.cargo == Decimal('22971.00')

    def test_poliza_abono_banco(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('22971.00'))], FECHA,
            datos_estatal=_datos_estatal(),
        )
        linea = plan.lineas_poliza[1]
        assert linea.cuenta == '1120'
        assert linea.subcuenta == '040000'
        assert linea.tipo_ca == TipoCA.ABONO
        assert linea.abono == Decimal('22971.00')

    def test_estatal_monto_no_coincide(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            [_mov_estatal(Decimal('99999'))], FECHA,
            datos_estatal=_datos_estatal(),
        )
        assert len(plan.movimientos_pm) == 0
        assert any('No se encontro' in a for a in plan.advertencias)


class TestProcesadorCompleto:
    """Tests de integracion con todos los movimientos."""

    def test_completo_6_movimientos(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            _movimientos_completos(), FECHA,
            datos_federal=_datos_federal(),
            datos_estatal=_datos_estatal(),
        )
        assert len(plan.movimientos_pm) == 6

    def test_completo_25_lineas_poliza(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            _movimientos_completos(), FECHA,
            datos_federal=_datos_federal(),
            datos_estatal=_datos_estatal(),
        )
        assert len(plan.lineas_poliza) == 25  # 5 + 6 + 4 + 4 + 4 + 2

    def test_completo_lineas_por_movimiento(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            _movimientos_completos(), FECHA,
            datos_federal=_datos_federal(),
            datos_estatal=_datos_estatal(),
        )
        assert plan.lineas_por_movimiento == [5, 6, 4, 4, 4, 2]

    def test_completo_sin_advertencias(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            _movimientos_completos(), FECHA,
            datos_federal=_datos_federal(),
            datos_estatal=_datos_estatal(),
        )
        assert plan.advertencias == []

    def test_completo_6_validaciones(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            _movimientos_completos(), FECHA,
            datos_federal=_datos_federal(),
            datos_estatal=_datos_estatal(),
        )
        assert len(plan.validaciones) == 6

    def test_completo_total_egresos(self):
        proc = ProcesadorImpuestos()
        plan = proc.construir_plan(
            _movimientos_completos(), FECHA,
            datos_federal=_datos_federal(),
            datos_estatal=_datos_estatal(),
        )
        total = sum(pm.egreso for pm in plan.movimientos_pm)
        expected = Decimal('6822') + Decimal('29228') + Decimal('154') + Decimal('336') + Decimal('5290') + Decimal('22971')
        assert total == expected
