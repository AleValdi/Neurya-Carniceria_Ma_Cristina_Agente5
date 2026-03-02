"""Tests para ProcesadorPagoGastos (pagos desde cuenta de gastos)."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.models import (
    DatosMovimientoPM,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)
from src.procesadores.pago_gastos import (
    ProcesadorPagoGastos,
    _buscar_factura_no_pagada,
    _construir_lineas_poliza,
    _crear_datos_movimiento,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mov_gastos(monto: Decimal, fecha=None, descripcion='CARGO TARJETA'):
    return MovimientoBancario(
        fecha=fecha or date(2026, 2, 11),
        descripcion=descripcion,
        cargo=monto,
        abono=None,
        cuenta_banco='055003730157',
        nombre_hoja='BANREGIO GTS',
        tipo_proceso=TipoProceso.PAGO_GASTOS,
    )


def _match_factura(
    total=Decimal('4282.00'),
    iva=Decimal('590.62'),
    proveedor='003456',
    nombre='SAM S CLUB',
    rfc='NWM9709244W4',
):
    return {
        'serie': 'F',
        'num_rec': 68100,
        'total': total,
        'saldo': total,
        'iva': iva,
        'proveedor': proveedor,
        'proveedor_nombre': nombre,
        'rfc': rfc,
        'factura': '10022026',
        'fecha': date(2026, 2, 10),
        'estatus': 'No Pagada',
        'tipo_recepcion': 'GASTOS FERRETE',
        'pago_rec': 42,
        'metodo_pago': 'PUE',
        'nombre_empresa': nombre,
    }


# ---------------------------------------------------------------------------
# Tests: Clasificacion
# ---------------------------------------------------------------------------

class TestClasificacion:
    def test_gastos_egreso_se_clasifica_como_pago_gastos(self):
        from src.clasificador import clasificar_movimientos
        mov = _mov_gastos(Decimal('4282.00'), descripcion='NWM SAM S CLUB_12345')
        clasificar_movimientos([mov])
        assert mov.tipo_proceso == TipoProceso.PAGO_GASTOS

    def test_gastos_ingreso_traspaso_no_es_pago_gastos(self):
        from src.clasificador import clasificar_movimientos
        mov = MovimientoBancario(
            fecha=date(2026, 2, 3),
            descripcion='(NB) Recepcion de cuenta: 055003730017. Transferencia',
            cargo=None,
            abono=Decimal('4000'),
            cuenta_banco='055003730157',
            nombre_hoja='BANREGIO GTS',
        )
        clasificar_movimientos([mov])
        assert mov.tipo_proceso == TipoProceso.TRASPASO_INGRESO

    def test_gastos_egreso_traspaso_externo_es_pago_gastos(self):
        from src.clasificador import clasificar_movimientos
        mov = MovimientoBancario(
            fecha=date(2026, 2, 3),
            descripcion='(BE) Traspaso a cuenta: 058180000150703446',
            cargo=Decimal('5000'),
            abono=None,
            cuenta_banco='055003730157',
            nombre_hoja='BANREGIO GTS',
        )
        clasificar_movimientos([mov])
        assert mov.tipo_proceso == TipoProceso.PAGO_GASTOS

    def test_gastos_comision_se_clasifica_correctamente(self):
        from src.clasificador import clasificar_movimientos
        mov = MovimientoBancario(
            fecha=date(2026, 2, 3),
            descripcion='Comision Transferencia - 12345',
            cargo=Decimal('6.00'),
            abono=None,
            cuenta_banco='055003730157',
            nombre_hoja='BANREGIO GTS',
        )
        clasificar_movimientos([mov])
        assert mov.tipo_proceso == TipoProceso.COMISION_SPEI


# ---------------------------------------------------------------------------
# Tests: Procesador
# ---------------------------------------------------------------------------

class TestProcesadorPagoGastos:
    def test_tipos_soportados(self):
        proc = ProcesadorPagoGastos()
        assert TipoProceso.PAGO_GASTOS in proc.tipos_soportados

    def test_sin_movimientos_retorna_plan_vacio(self):
        proc = ProcesadorPagoGastos()
        plan = proc.construir_plan([], date(2026, 2, 11))
        assert len(plan.movimientos_pm) == 0
        assert len(plan.advertencias) == 1

    def test_sin_cursor_retorna_advertencia(self):
        proc = ProcesadorPagoGastos()
        mov = _mov_gastos(Decimal('4282.00'))
        plan = proc.construir_plan([mov], date(2026, 2, 11))
        assert len(plan.movimientos_pm) == 0
        assert 'Sin conexion' in plan.advertencias[0]

    @patch('src.procesadores.pago_gastos._buscar_factura_no_pagada')
    @patch('src.procesadores.pago_gastos._buscar_factura_ya_pagada')
    def test_match_genera_movimiento_y_poliza(self, mock_pagada, mock_buscar):
        match = _match_factura()
        mock_buscar.return_value = match
        mock_pagada.return_value = None

        proc = ProcesadorPagoGastos()
        mov = _mov_gastos(Decimal('4282.00'))
        plan = proc.construir_plan([mov], date(2026, 2, 11), cursor=MagicMock())

        assert len(plan.movimientos_pm) == 1
        assert len(plan.pagos_factura_existente) == 1
        assert plan.pagos_factura_existente[0]['num_rec'] == 68100

        pm = plan.movimientos_pm[0]
        assert pm.tipo == 3
        assert pm.egreso == Decimal('4282.00')
        assert pm.tipo_egreso == 'TARJETA'
        assert pm.clase == 'PAGOS A PROVEEDORES'
        assert pm.proveedor == '003456'
        assert pm.tipo_proveedor == 'CAJA CHICA'
        assert pm.concepto == 'PAGO DE FACTURAS DE COMPRAS'
        assert pm.conciliada == 1
        assert pm.pago_afectado == True

    @patch('src.procesadores.pago_gastos._buscar_factura_no_pagada')
    @patch('src.procesadores.pago_gastos._buscar_factura_ya_pagada')
    def test_sin_match_genera_advertencia(self, mock_pagada, mock_buscar):
        mock_buscar.return_value = None
        mock_pagada.return_value = None

        proc = ProcesadorPagoGastos()
        mov = _mov_gastos(Decimal('999.99'))
        plan = proc.construir_plan([mov], date(2026, 2, 11), cursor=MagicMock())

        assert len(plan.movimientos_pm) == 0
        assert any('Sin factura' in a for a in plan.advertencias)

    @patch('src.procesadores.pago_gastos._buscar_factura_no_pagada')
    @patch('src.procesadores.pago_gastos._buscar_factura_ya_pagada')
    def test_ya_pagada_genera_ya_conciliado(self, mock_pagada, mock_buscar):
        mock_buscar.return_value = None
        mock_pagada.return_value = {
            'serie': 'F', 'num_rec': 68100,
            'total': Decimal('4282.00'), 'nombre_empresa': 'SAM S CLUB',
        }

        proc = ProcesadorPagoGastos()
        mov = _mov_gastos(Decimal('4282.00'))
        plan = proc.construir_plan([mov], date(2026, 2, 11), cursor=MagicMock())

        assert len(plan.movimientos_pm) == 0
        assert len(plan.ya_conciliados) == 1
        assert 'Ya pagada' in plan.ya_conciliados[0]['descripcion']

    @patch('src.procesadores.pago_gastos._buscar_factura_no_pagada')
    @patch('src.procesadores.pago_gastos._buscar_factura_ya_pagada')
    def test_multiples_movimientos(self, mock_pagada, mock_buscar):
        match1 = _match_factura(total=Decimal('4282.00'), iva=Decimal('590.62'))
        match2 = _match_factura(
            total=Decimal('439.63'), iva=Decimal('60.63'),
            proveedor='002345', nombre='CASABLANCA',
        )
        mock_buscar.side_effect = [match1, match2, None]
        mock_pagada.return_value = None

        proc = ProcesadorPagoGastos()
        movs = [
            _mov_gastos(Decimal('4282.00')),
            _mov_gastos(Decimal('439.63')),
            _mov_gastos(Decimal('9999.99')),
        ]
        plan = proc.construir_plan(movs, date(2026, 2, 11), cursor=MagicMock())

        assert len(plan.movimientos_pm) == 2
        assert len(plan.pagos_factura_existente) == 2
        assert any('Sin factura' in a for a in plan.advertencias)


# ---------------------------------------------------------------------------
# Tests: Poliza
# ---------------------------------------------------------------------------

class TestPolizaGastos:
    def test_poliza_con_iva_tiene_4_lineas(self):
        match = _match_factura(iva=Decimal('590.62'))
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )
        assert len(lineas) == 4

    def test_poliza_sin_iva_tiene_2_lineas(self):
        match = _match_factura(iva=Decimal('0'))
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )
        assert len(lineas) == 2

    def test_poliza_estructura_correcta(self):
        match = _match_factura(iva=Decimal('590.62'))
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )

        # Linea 1: Proveedores CARGO
        assert lineas[0].cuenta == '2110'
        assert lineas[0].subcuenta == '010000'
        assert lineas[0].tipo_ca == TipoCA.CARGO
        assert lineas[0].cargo == Decimal('4282.00')

        # Linea 2: IVA PTE PAGO ABONO
        assert lineas[1].cuenta == '1240'
        assert lineas[1].subcuenta == '010000'
        assert lineas[1].tipo_ca == TipoCA.ABONO
        assert lineas[1].abono == Decimal('590.62')

        # Linea 3: IVA PAGADO CARGO
        assert lineas[2].cuenta == '1246'
        assert lineas[2].subcuenta == '010000'
        assert lineas[2].tipo_ca == TipoCA.CARGO
        assert lineas[2].cargo == Decimal('590.62')

        # Linea 4: Banco ABONO
        assert lineas[3].cuenta == '1120'
        assert lineas[3].subcuenta == '070000'
        assert lineas[3].tipo_ca == TipoCA.ABONO
        assert lineas[3].abono == Decimal('4282.00')

    def test_poliza_cargos_igualan_abonos(self):
        match = _match_factura(iva=Decimal('590.62'))
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )
        total_cargos = sum(l.cargo for l in lineas)
        total_abonos = sum(l.abono for l in lineas)
        assert total_cargos == total_abonos

    def test_poliza_conceptos_tienen_proveedor(self):
        match = _match_factura(proveedor='003456', nombre='SAM S CLUB')
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )
        assert 'Prov:003456' in lineas[0].concepto
        assert 'Nombre:SAM S CLUB' in lineas[0].concepto

    def test_poliza_doc_tipo_cheques(self):
        match = _match_factura()
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )
        for linea in lineas:
            assert linea.doc_tipo == 'CHEQUES'

    def test_poliza_placeholder_folio(self):
        match = _match_factura()
        lineas = _construir_lineas_poliza(
            match, Decimal('4282.00'), '1120', '070000',
        )
        # Primer y ultima linea tienen placeholder {folio}
        assert '{folio}' in lineas[0].concepto
        assert '{folio}' in lineas[-1].concepto


# ---------------------------------------------------------------------------
# Tests: DatosMovimientoPM
# ---------------------------------------------------------------------------

class TestDatosMovimiento:
    def test_campos_basicos(self):
        from config.settings import CUENTAS_BANCARIAS
        cfg = CUENTAS_BANCARIAS['gastos']
        mov = _mov_gastos(Decimal('4282.00'))
        match = _match_factura()

        datos = _crear_datos_movimiento(mov, match, date(2026, 2, 11), cfg)

        assert datos.banco == 'BANREGIO'
        assert datos.cuenta == '055003730157'
        assert datos.tipo == 3
        assert datos.egreso == Decimal('4282.00')
        assert datos.ingreso == Decimal('0')
        assert datos.tipo_egreso == 'TARJETA'
        assert datos.tipo_proveedor == 'CAJA CHICA'
        assert datos.conciliada == 1
        assert datos.pago_afectado == True
        assert datos.estatus == 'Afectado'
        assert datos.tipo_poliza == 'EGRESO'
        assert datos.proveedor == '003456'
        assert datos.rfc == 'NWM9709244W4'

    def test_fecha_correcta(self):
        from config.settings import CUENTAS_BANCARIAS
        cfg = CUENTAS_BANCARIAS['gastos']
        mov = _mov_gastos(Decimal('4282.00'))
        match = _match_factura()

        datos = _crear_datos_movimiento(mov, match, date(2026, 2, 11), cfg)

        assert datos.age == 2026
        assert datos.mes == 2
        assert datos.dia == 11


# ---------------------------------------------------------------------------
# Tests: Busqueda en BD (mock cursor)
# ---------------------------------------------------------------------------

class TestBusquedaBD:
    def test_buscar_retorna_match(self):
        cursor = MagicMock()
        cursor.connection = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda self, i: [
            'F', 68100, Decimal('4282.00'), Decimal('4282.00'), Decimal('590.62'),
            '003456', 'SAM S CLUB', 'NWM9709244W4',
            '10022026', date(2026, 2, 10), 'No Pagada',
            'GASTOS FERRETE', 42, 'PUE', 'SAM S CLUB',
        ][i]
        cursor.fetchone.return_value = row

        result = _buscar_factura_no_pagada(cursor, Decimal('4282.00'))

        assert result is not None
        assert result['num_rec'] == 68100
        assert result['iva'] == Decimal('590.62')
        assert result['proveedor'] == '003456'

    def test_buscar_retorna_none_sin_match(self):
        cursor = MagicMock()
        cursor.connection = MagicMock()
        cursor.fetchone.return_value = None

        result = _buscar_factura_no_pagada(cursor, Decimal('999999.99'))
        assert result is None

    def test_buscar_maneja_excepcion(self):
        cursor = MagicMock()
        cursor.connection = MagicMock()
        cursor.execute.side_effect = Exception("Connection error")

        result = _buscar_factura_no_pagada(cursor, Decimal('4282.00'))
        assert result is None
