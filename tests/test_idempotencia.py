"""Tests para la logica de idempotencia y conciliacion en el ejecutor."""

import sys
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pytest

from src.models import (
    DatosMovimientoPM,
    LineaPoliza,
    PlanEjecucion,
    TipoCA,
)

# Mock pyodbc si no esta instalado (desarrollo local sin BD)
if 'pyodbc' not in sys.modules:
    sys.modules['pyodbc'] = MagicMock()


def _plan_con_movimiento(concepto: str = 'TEST', monto: Decimal = Decimal('1000')) -> PlanEjecucion:
    """Crea un plan simple con 1 movimiento para tests."""
    plan = PlanEjecucion(
        tipo_proceso='TEST',
        descripcion='Test idempotencia',
        fecha_movimiento=date(2026, 2, 5),
    )
    plan.movimientos_pm.append(DatosMovimientoPM(
        banco='BANREGIO',
        cuenta='055003730017',
        age=2026,
        mes=2,
        dia=5,
        tipo=2,
        ingreso=Decimal('0'),
        egreso=monto,
        concepto=concepto,
        clase='TEST',
        fpago=None,
        tipo_egreso='TRANSFERENCIA',
        conciliada=1,
        paridad=Decimal('1.0000'),
        tipo_poliza='EGRESO',
        num_factura='',
    ))
    plan.facturas_por_movimiento.append(0)
    plan.lineas_por_movimiento.append(2)
    plan.lineas_poliza.extend([
        LineaPoliza(
            movimiento=1, cuenta='1120', subcuenta='040000',
            tipo_ca=TipoCA.CARGO, cargo=monto, abono=Decimal('0'),
            concepto=concepto,
        ),
        LineaPoliza(
            movimiento=2, cuenta='2110', subcuenta='010000',
            tipo_ca=TipoCA.ABONO, cargo=Decimal('0'), abono=monto,
            concepto=concepto,
        ),
    ])
    return plan


class TestIdempotenciaEnEjecutor:
    """Tests que verifican que el ejecutor maneja movimientos existentes."""

    @patch('src.orquestador.buscar_movimiento_existente')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    @patch('src.orquestador.obtener_siguiente_poliza')
    @patch('src.orquestador.insertar_poliza')
    @patch('src.orquestador.actualizar_num_poliza')
    def test_movimiento_nuevo_se_inserta(
        self,
        mock_actualizar, mock_insertar_poliza,
        mock_sig_poliza, mock_sig_folio,
        mock_insertar_mov, mock_buscar,
    ):
        """Cuando el movimiento NO existe, se inserta normalmente."""
        from src.orquestador import _ejecutar_plan

        mock_buscar.return_value = None  # No existe
        mock_sig_folio.return_value = 999
        mock_sig_poliza.return_value = 888

        plan = _plan_con_movimiento()
        connector = MagicMock()
        cursor = MagicMock()
        connector.get_cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        connector.get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        resultado = _ejecutar_plan(plan, connector)

        assert resultado.exito
        mock_insertar_mov.assert_called_once()
        assert 999 in resultado.folios

    @patch('src.orquestador.conciliar_movimiento')
    @patch('src.orquestador.buscar_movimiento_existente')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    def test_movimiento_existente_no_conciliado_se_concilia(
        self,
        mock_sig_folio, mock_insertar_mov,
        mock_buscar, mock_conciliar,
    ):
        """Cuando existe un movimiento NO conciliado, se concilia sin insertar."""
        from src.orquestador import _ejecutar_plan

        mock_buscar.return_value = (12345, False)  # Existe, no conciliado

        plan = _plan_con_movimiento()
        connector = MagicMock()
        cursor = MagicMock()
        connector.get_cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        connector.get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        resultado = _ejecutar_plan(plan, connector)

        assert resultado.exito
        mock_insertar_mov.assert_not_called()
        mock_sig_folio.assert_not_called()
        mock_conciliar.assert_called_once_with(cursor, 12345)
        assert 12345 in resultado.folios
        assert any('conciliados' in v for v in plan.validaciones)

    @patch('src.orquestador.buscar_movimiento_existente')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    def test_movimiento_existente_conciliado_se_salta(
        self,
        mock_sig_folio, mock_insertar_mov, mock_buscar,
    ):
        """Cuando existe un movimiento YA conciliado, se salta sin insertar."""
        from src.orquestador import _ejecutar_plan

        mock_buscar.return_value = (12345, True)  # Existe, ya conciliado

        plan = _plan_con_movimiento()
        connector = MagicMock()
        cursor = MagicMock()
        connector.get_cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        connector.get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        resultado = _ejecutar_plan(plan, connector)

        assert resultado.exito
        mock_insertar_mov.assert_not_called()
        mock_sig_folio.assert_not_called()
        assert len(resultado.folios) == 0
        assert any('ya existian' in adv for adv in plan.advertencias)

    @patch('src.orquestador.conciliar_movimiento')
    @patch('src.orquestador.buscar_movimiento_existente')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    @patch('src.orquestador.obtener_siguiente_poliza')
    @patch('src.orquestador.insertar_poliza')
    @patch('src.orquestador.actualizar_num_poliza')
    def test_mixto_conciliar_y_nuevo(
        self,
        mock_actualizar, mock_insertar_poliza,
        mock_sig_poliza, mock_sig_folio,
        mock_insertar_mov, mock_buscar, mock_conciliar,
    ):
        """Con 2 movimientos: uno existente (conciliar) y uno nuevo (insertar)."""
        from src.orquestador import _ejecutar_plan

        # Primer movimiento: existe no conciliado. Segundo: no existe.
        mock_buscar.side_effect = [(55555, False), None]
        mock_sig_folio.return_value = 100
        mock_sig_poliza.return_value = 200

        plan = _plan_con_movimiento('MOV1', Decimal('500'))

        # Agregar segundo movimiento
        plan.movimientos_pm.append(DatosMovimientoPM(
            banco='BANREGIO', cuenta='055003730017',
            age=2026, mes=2, dia=5, tipo=2,
            ingreso=Decimal('0'), egreso=Decimal('300'),
            concepto='MOV2', clase='TEST', fpago=None,
            tipo_egreso='TRANSFERENCIA', conciliada=1,
            paridad=Decimal('1.0000'), tipo_poliza='EGRESO',
            num_factura='',
        ))
        plan.facturas_por_movimiento.append(0)
        plan.lineas_por_movimiento.append(2)
        plan.lineas_poliza.extend([
            LineaPoliza(
                movimiento=1, cuenta='1120', subcuenta='040000',
                tipo_ca=TipoCA.CARGO, cargo=Decimal('300'), abono=Decimal('0'),
                concepto='MOV2',
            ),
            LineaPoliza(
                movimiento=2, cuenta='2110', subcuenta='010000',
                tipo_ca=TipoCA.ABONO, cargo=Decimal('0'), abono=Decimal('300'),
                concepto='MOV2',
            ),
        ])

        connector = MagicMock()
        cursor = MagicMock()
        connector.get_cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        connector.get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        resultado = _ejecutar_plan(plan, connector)

        assert resultado.exito
        # Primer mov: conciliado (no insertado)
        mock_conciliar.assert_called_once_with(cursor, 55555)
        # Segundo mov: insertado normalmente
        assert mock_insertar_mov.call_count == 1
        # Folios: 55555 (conciliado) + 100 (nuevo)
        assert 55555 in resultado.folios
        assert 100 in resultado.folios
        assert any('1 movimientos existentes conciliados' in v for v in plan.validaciones)


class TestBuscarMovimientoExistente:
    """Tests para buscar_movimiento_existente."""

    def test_encuentra_no_conciliado(self):
        """Retorna folio y False si encuentra no conciliado."""
        from src.erp.movimientos import buscar_movimiento_existente

        cursor = MagicMock()
        cursor.fetchone.return_value = (12345, 0)

        resultado = buscar_movimiento_existente(
            cursor, 'BANREGIO', '055003730017',
            5, 2, 2026, Decimal('1000'), es_ingreso=False,
        )

        assert resultado == (12345, False)
        cursor.execute.assert_called_once()
        # Verificar que busca por Egreso (no ingreso)
        sql = cursor.execute.call_args[0][0]
        assert 'Egreso' in sql

    def test_encuentra_conciliado(self):
        """Retorna folio y True si encuentra conciliado."""
        from src.erp.movimientos import buscar_movimiento_existente

        cursor = MagicMock()
        cursor.fetchone.return_value = (99999, 1)

        resultado = buscar_movimiento_existente(
            cursor, 'BANREGIO', '038900320016',
            3, 2, 2026, Decimal('5000'), es_ingreso=True,
        )

        assert resultado == (99999, True)
        sql = cursor.execute.call_args[0][0]
        assert 'Ingreso' in sql

    def test_no_encuentra_nada(self):
        """Retorna None si no hay coincidencias."""
        from src.erp.movimientos import buscar_movimiento_existente

        cursor = MagicMock()
        cursor.fetchone.return_value = None

        resultado = buscar_movimiento_existente(
            cursor, 'BANREGIO', '055003730017',
            5, 2, 2026, Decimal('1000'), es_ingreso=False,
        )

        assert resultado is None


class TestExisteMovimiento:
    """Tests para la funcion existe_movimiento (legacy, idempotencia estricta)."""

    def test_existe_con_cursor_mock(self):
        """Verifica que la query se ejecuta correctamente."""
        from src.erp.movimientos import existe_movimiento

        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)  # Hay 1 coincidencia

        resultado = existe_movimiento(
            cursor, 'BANREGIO', '055003730017',
            5, 2, 2026, 'TEST', Decimal('1000'),
        )

        assert resultado is True
        cursor.execute.assert_called_once()

    def test_no_existe_con_cursor_mock(self):
        """Sin coincidencias retorna False."""
        from src.erp.movimientos import existe_movimiento

        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)

        resultado = existe_movimiento(
            cursor, 'BANREGIO', '055003730017',
            5, 2, 2026, 'TEST', Decimal('1000'),
        )

        assert resultado is False
