"""Tests para la logica de idempotencia en el ejecutor."""

import sys
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

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
    """Tests que verifican que el ejecutor salta movimientos duplicados."""

    @patch('src.orquestador.existe_movimiento')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    @patch('src.orquestador.obtener_siguiente_poliza')
    @patch('src.orquestador.insertar_poliza')
    @patch('src.orquestador.actualizar_num_poliza')
    def test_movimiento_nuevo_se_inserta(
        self,
        mock_actualizar, mock_insertar_poliza,
        mock_sig_poliza, mock_sig_folio,
        mock_insertar_mov, mock_existe,
    ):
        """Cuando el movimiento NO existe, se inserta normalmente."""
        from src.orquestador import _ejecutar_plan

        mock_existe.return_value = False
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

    @patch('src.orquestador.existe_movimiento')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    def test_movimiento_duplicado_se_salta(
        self,
        mock_sig_folio, mock_insertar_mov, mock_existe,
    ):
        """Cuando el movimiento YA existe, se salta sin insertar."""
        from src.orquestador import _ejecutar_plan

        mock_existe.return_value = True

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

    @patch('src.orquestador.existe_movimiento')
    @patch('src.orquestador.insertar_movimiento')
    @patch('src.orquestador.obtener_siguiente_folio')
    @patch('src.orquestador.obtener_siguiente_poliza')
    @patch('src.orquestador.insertar_poliza')
    @patch('src.orquestador.actualizar_num_poliza')
    def test_mixto_nuevo_y_duplicado(
        self,
        mock_actualizar, mock_insertar_poliza,
        mock_sig_poliza, mock_sig_folio,
        mock_insertar_mov, mock_existe,
    ):
        """Con 2 movimientos, uno nuevo y uno duplicado, solo inserta el nuevo."""
        from src.orquestador import _ejecutar_plan

        # Primer movimiento: ya existe. Segundo: nuevo.
        mock_existe.side_effect = [True, False]
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
        # Solo un INSERT (el segundo movimiento)
        assert mock_insertar_mov.call_count == 1
        assert len(resultado.folios) == 1
        assert any('1 movimientos ya existian' in adv for adv in plan.advertencias)


class TestExisteMovimiento:
    """Tests para la funcion existe_movimiento de movimientos.py."""

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
