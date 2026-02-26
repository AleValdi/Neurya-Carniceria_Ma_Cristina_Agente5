"""Tests para la asignacion secuencial con split de depositos TDC
y busqueda de cortes por rango dinamico."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.models import CorteVentaDiaria, FacturaVenta, MovimientoBancario, TipoProceso
from src.orquestador import (
    _asignar_secuencial_con_split,
    _buscar_cortes_tdc,
    _clonar_deposito,
)


def _dep(monto: float, tipo=TipoProceso.VENTA_TDD) -> MovimientoBancario:
    """Crea un deposito TDC de prueba."""
    return MovimientoBancario(
        fecha=date(2026, 2, 23),
        descripcion=f'ABONO VENTAS TDD_{monto}',
        cargo=None,
        abono=Decimal(str(monto)),
        cuenta_banco='038900320016',
        nombre_hoja='Banregio T',
        tipo_proceso=tipo,
    )


def _corte(dia: int, tdc: float) -> CorteVentaDiaria:
    """Crea un corte de tesoreria de prueba."""
    return CorteVentaDiaria(
        fecha_corte=date(2026, 2, dia),
        nombre_hoja=str(dia),
        total_tdc=Decimal(str(tdc)),
        factura_global_numero=str(20200 + dia),
        factura_global_importe=Decimal(str(tdc * 2)),
    )


class TestClonarDeposito:
    """Tests para _clonar_deposito."""

    def test_clon_ingreso(self):
        """Clonar un ingreso preserva campos y cambia monto."""
        original = _dep(100000.00)
        clon = _clonar_deposito(original, Decimal('60000.00'))

        assert clon.monto == Decimal('60000.00')
        assert clon.abono == Decimal('60000.00')
        assert clon.cargo is None
        assert clon.fecha == original.fecha
        assert clon.descripcion == original.descripcion
        assert clon.cuenta_banco == original.cuenta_banco
        assert clon.tipo_proceso == original.tipo_proceso
        assert id(clon) != id(original)

    def test_clon_egreso(self):
        """Clonar un egreso preserva campos y cambia monto."""
        original = MovimientoBancario(
            fecha=date(2026, 2, 23),
            descripcion='EGRESO TEST',
            cargo=Decimal('5000.00'),
            abono=None,
            cuenta_banco='055003730017',
            nombre_hoja='Banregio F',
            tipo_proceso=TipoProceso.TRASPASO,
        )
        clon = _clonar_deposito(original, Decimal('3000.00'))

        assert clon.monto == Decimal('3000.00')
        assert clon.cargo == Decimal('3000.00')
        assert clon.abono is None


class TestAsignacionSecuencial:
    """Tests para _asignar_secuencial_con_split."""

    def test_depositos_exactos_sin_split(self):
        """Depositos que caben exacto en cortes no generan splits."""
        depositos = [_dep(100000), _dep(50000), _dep(80000)]
        cortes = [_corte(20, 150000), _corte(21, 80000)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        # Corte 20: 100K + 50K = 150K
        assert len(asignaciones) == 2
        assert len(asignaciones[0][1]) == 2
        assert sum(d.monto for d in asignaciones[0][1]) == Decimal('150000')

        # Corte 21: 80K
        assert len(asignaciones[1][1]) == 1
        assert sum(d.monto for d in asignaciones[1][1]) == Decimal('80000')

        assert len(sobrantes) == 0
        assert len(mapa) == 0  # Sin splits

    def test_split_basico(self):
        """Un deposito que excede el target se parte en dos."""
        # Deposito de 150K, pero corte 1 necesita solo 100K
        depositos = [_dep(150000)]
        cortes = [_corte(20, 100000), _corte(21, 50000)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        # Corte 20: 100K (primera parte del split)
        assert len(asignaciones) == 2
        assert sum(d.monto for d in asignaciones[0][1]) == Decimal('100000')

        # Corte 21: 50K (remanente del split)
        assert sum(d.monto for d in asignaciones[1][1]) == Decimal('50000')

        # Ambas partes son virtuales (split)
        assert len(mapa) == 2
        # Ambas partes apuntan al mismo original
        original_ids = set(mapa.values())
        assert len(original_ids) == 1

        assert len(sobrantes) == 0

    def test_split_con_sobrante(self):
        """Split deja sobrante cuando depositos exceden targets."""
        depositos = [_dep(200000)]
        cortes = [_corte(20, 100000)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        # Corte 20: 100K
        assert len(asignaciones) == 1
        assert sum(d.monto for d in asignaciones[0][1]) == Decimal('100000')

        # Sobrante: 100K
        assert len(sobrantes) == 1
        assert sobrantes[0].monto == Decimal('100000')

    def test_multiples_depositos_multiples_cortes(self):
        """Escenario tipo lunes: 3 cortes, depositos combinados."""
        # Simula Feb 23 (lunes): cortes vie/sab/dom
        depositos = [
            _dep(300000),  # Deposito combinado que cruza cortes
            _dep(150000),
            _dep(50000),
        ]
        cortes = [
            _corte(20, 250000),  # Viernes
            _corte(21, 200000),  # Sabado
            _corte(22, 50000),   # Domingo
        ]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        assert len(asignaciones) == 3

        # Corte 20: 250K del deposito de 300K
        assert sum(d.monto for d in asignaciones[0][1]) == Decimal('250000')

        # Corte 21: 50K (remanente 300K) + 150K = 200K
        assert sum(d.monto for d in asignaciones[1][1]) == Decimal('200000')

        # Corte 22: 50K
        assert sum(d.monto for d in asignaciones[2][1]) == Decimal('50000')

        assert len(sobrantes) == 0

    def test_tolerancia(self):
        """Tolerancia permite diferencias de centavos."""
        depositos = [_dep(100001.50)]
        cortes = [_corte(20, 100000)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes, tolerancia=Decimal('2.00'),
        )

        # Deposito cabe dentro de tolerancia, no se parte
        assert len(asignaciones) == 1
        assert len(asignaciones[0][1]) == 1
        assert asignaciones[0][1][0].monto == Decimal('100001.50')
        assert len(mapa) == 0  # Sin splits

    def test_orden_preservado(self):
        """Depositos se consumen en orden de aparicion."""
        dep_a = _dep(10000)
        dep_b = _dep(20000)
        dep_c = _dep(30000)
        depositos = [dep_a, dep_b, dep_c]
        cortes = [_corte(20, 60000)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        # Todos asignados al unico corte, en orden
        assert len(asignaciones) == 1
        deps_asignados = asignaciones[0][1]
        assert deps_asignados[0] is dep_a
        assert deps_asignados[1] is dep_b
        assert deps_asignados[2] is dep_c

    def test_sin_depositos(self):
        """Sin depositos, no hay asignaciones."""
        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            [], [_corte(20, 100000)],
        )
        assert len(asignaciones) == 0
        assert len(sobrantes) == 0

    def test_sin_cortes(self):
        """Sin cortes, todos los depositos son sobrantes."""
        depositos = [_dep(10000)]
        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, [],
        )
        assert len(asignaciones) == 0
        assert len(sobrantes) == 1

    def test_corte_sin_tdc(self):
        """Cortes con total_tdc=0 o None se saltan."""
        depositos = [_dep(10000)]
        corte_vacio = CorteVentaDiaria(
            fecha_corte=date(2026, 2, 20),
            nombre_hoja='20',
            total_tdc=Decimal('0'),
        )
        corte_none = CorteVentaDiaria(
            fecha_corte=date(2026, 2, 21),
            nombre_hoja='21',
            total_tdc=None,
        )
        corte_real = _corte(22, 10000)

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, [corte_vacio, corte_none, corte_real],
        )

        assert len(asignaciones) == 1
        assert asignaciones[0][0].fecha_corte == date(2026, 2, 22)

    def test_split_doble(self):
        """Un deposito puede ser splitado en mas de 2 cortes."""
        # Deposito gigante que abarca 3 cortes
        depositos = [_dep(60000)]
        cortes = [
            _corte(20, 20000),
            _corte(21, 20000),
            _corte(22, 20000),
        ]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        assert len(asignaciones) == 3
        for i in range(3):
            assert sum(d.monto for d in asignaciones[i][1]) == Decimal('20000')

        # Todas las piezas apuntan al mismo original
        original_ids = set(mapa.values())
        assert len(original_ids) == 1
        assert len(sobrantes) == 0

    def test_mapa_virtual_encadena(self):
        """Cuando un remanente se vuelve a partir, mapa apunta al original real."""
        depositos = [_dep(100)]
        cortes = [_corte(20, 30), _corte(21, 30), _corte(22, 40)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        original_id = id(depositos[0])

        # Todas las partes virtuales mapean al deposito original real
        for vid, oid in mapa.items():
            assert oid == original_id

    def test_mezcla_completos_y_splits(self):
        """Algunos depositos se asignan completos, otros se parten."""
        depositos = [
            _dep(50000),   # Cabe completo en corte 1
            _dep(120000),  # Se parte: 50K a corte 1, 70K a corte 2
            _dep(30000),   # Cabe completo en corte 2
        ]
        cortes = [_corte(20, 100000), _corte(21, 100000)]

        asignaciones, sobrantes, mapa = _asignar_secuencial_con_split(
            depositos, cortes,
        )

        # Corte 20: 50K (completo) + 50K (split de 120K) = 100K
        assert len(asignaciones) == 2
        assert sum(d.monto for d in asignaciones[0][1]) == Decimal('100000')

        # Corte 21: 70K (remanente 120K) + 30K (completo) = 100K
        assert sum(d.monto for d in asignaciones[1][1]) == Decimal('100000')

        # Solo el deposito de 120K fue splitado
        assert len(set(mapa.values())) == 1
        assert len(sobrantes) == 0


class TestBuscarCortesPorRango:
    """Tests para _buscar_cortes_tdc con rango dinamico."""

    def _cortes_dict(self, dias):
        """Crea dict de cortes para los dias indicados de Feb 2026."""
        return {
            date(2026, 2, d): CorteVentaDiaria(
                fecha_corte=date(2026, 2, d),
                nombre_hoja=str(d),
                total_tdc=Decimal('100000'),
            )
            for d in dias
        }

    def test_martes_normal(self):
        """Martes normal: solo el lunes (dia anterior)."""
        # Depositos: Lun 2, Mar 3, Mie 4
        fechas_tdc = [date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 3), cortes, fechas_tdc,
        )

        assert len(resultado) == 1
        assert resultado[0].fecha_corte == date(2026, 2, 2)

    def test_lunes_normal(self):
        """Lunes normal: viernes + sabado + domingo."""
        # Depositos: Vie 6, Lun 9
        fechas_tdc = [date(2026, 2, 6), date(2026, 2, 9)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 9), cortes, fechas_tdc,
        )

        assert len(resultado) == 3
        fechas_resultado = [c.fecha_corte for c in resultado]
        assert date(2026, 2, 6) in fechas_resultado
        assert date(2026, 2, 7) in fechas_resultado
        assert date(2026, 2, 8) in fechas_resultado

    def test_martes_con_lunes_festivo(self):
        """Martes cuando lunes fue festivo: vie + sab + dom + lun."""
        # Depositos: Vie 6, Mar 10 (no hay deposito el Lun 9 = festivo)
        fechas_tdc = [date(2026, 2, 6), date(2026, 2, 10)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 10), cortes, fechas_tdc,
        )

        # Debe incluir vie 6, sab 7, dom 8, lun 9
        assert len(resultado) == 4
        fechas_resultado = [c.fecha_corte for c in resultado]
        assert date(2026, 2, 6) in fechas_resultado
        assert date(2026, 2, 7) in fechas_resultado
        assert date(2026, 2, 8) in fechas_resultado
        assert date(2026, 2, 9) in fechas_resultado

    def test_miercoles_con_martes_festivo(self):
        """Miercoles cuando martes fue festivo: lun + mar."""
        # Depositos: Lun 9, Mie 11 (no hay deposito Mar 10 = festivo)
        fechas_tdc = [date(2026, 2, 9), date(2026, 2, 11)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 11), cortes, fechas_tdc,
        )

        # Cortes: lun 9, mar 10
        assert len(resultado) == 2
        fechas_resultado = [c.fecha_corte for c in resultado]
        assert date(2026, 2, 9) in fechas_resultado
        assert date(2026, 2, 10) in fechas_resultado

    def test_primer_deposito_del_periodo(self):
        """Primer deposito busca hasta 7 dias atras."""
        # Solo un deposito el lunes 9
        fechas_tdc = [date(2026, 2, 9)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 9), cortes, fechas_tdc,
        )

        # Busca 7 dias atras desde Feb 9 = Feb 2 a Feb 8
        assert len(resultado) == 7
        fechas_resultado = [c.fecha_corte for c in resultado]
        assert date(2026, 2, 2) in fechas_resultado
        assert date(2026, 2, 8) in fechas_resultado

    def test_sin_cortes_en_rango(self):
        """Si no hay cortes en el rango, retorna vacio."""
        fechas_tdc = [date(2026, 2, 3), date(2026, 2, 4)]
        cortes = {}  # Sin cortes

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 4), cortes, fechas_tdc,
        )

        assert len(resultado) == 0

    def test_fecha_no_en_lista(self):
        """Si la fecha de deposito no esta en la lista, retorna vacio."""
        fechas_tdc = [date(2026, 2, 3), date(2026, 2, 5)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 4), cortes, fechas_tdc,  # Feb 4 no esta en la lista
        )

        assert len(resultado) == 0

    def test_fallback_sin_fechas_lunes(self):
        """Sin fechas_deposito_tdc, usa fallback por dia de semana (lunes)."""
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 9), cortes,  # Lunes, sin fechas_tdc
        )

        # Fallback: viernes + sabado + domingo
        assert len(resultado) == 3

    def test_fallback_sin_fechas_martes(self):
        """Sin fechas_deposito_tdc, usa fallback (solo dia anterior)."""
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 10), cortes,  # Martes, sin fechas_tdc
        )

        # Fallback: solo lunes
        assert len(resultado) == 1
        assert resultado[0].fecha_corte == date(2026, 2, 9)

    def test_orden_resultado(self):
        """Resultado viene ordenado por fecha de corte."""
        fechas_tdc = [date(2026, 2, 6), date(2026, 2, 9)]
        cortes = self._cortes_dict(range(1, 15))

        resultado = _buscar_cortes_tdc(
            date(2026, 2, 9), cortes, fechas_tdc,
        )

        fechas = [c.fecha_corte for c in resultado]
        assert fechas == sorted(fechas)
