"""Tests para el parser de reporte de tesoreria."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest


class TestParserTesoreria:
    """Tests usando el archivo FEBRERO INGRESOS 2026.xlsx real."""

    def test_parsea_dias_con_datos(self, ruta_tesoreria):
        """Debe parsear al menos 15 dias con datos."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)

        assert len(resultado) >= 15, (
            f"Esperaba al menos 15 dias, obtuvo {len(resultado)}"
        )

    def test_dia_1_tiene_datos_completos(self, ruta_tesoreria):
        """El dia 1 de febrero debe tener datos verificados."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)

        # Buscar corte del 1 de febrero
        fecha_1 = date(2026, 2, 1)
        assert fecha_1 in resultado, f"No se encontro corte para {fecha_1}"

        corte = resultado[fecha_1]

        # Factura global
        assert corte.factura_global_numero is not None, "Falta factura global"
        assert corte.factura_global_importe is not None, "Falta importe global"
        assert corte.factura_global_importe > 0, "Importe global debe ser > 0"

        # Facturas individuales
        assert len(corte.facturas_individuales) > 0, "Debe tener facturas individuales"

        # Totales
        assert corte.total_efectivo is not None, "Falta total efectivo"
        assert corte.total_tdc is not None, "Falta total TDC"

    def test_factura_global_dia_1(self, ruta_tesoreria):
        """Dia 1: factura global FD-20204, $725,897.52 (aprox)."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)
        corte = resultado.get(date(2026, 2, 1))
        if corte is None:
            pytest.skip("Dia 1 no disponible")

        # Verificar numero de factura global (20204 segun CLAUDE.md)
        assert corte.factura_global_numero == '20204', (
            f"Factura global esperada: 20204, obtuvo: {corte.factura_global_numero}"
        )

    def test_facturas_individuales_dia_1(self, ruta_tesoreria):
        """Dia 1: debe tener ~11 facturas individuales."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)
        corte = resultado.get(date(2026, 2, 1))
        if corte is None:
            pytest.skip("Dia 1 no disponible")

        n = len(corte.facturas_individuales)
        assert 9 <= n <= 19, (
            f"Dia 1: esperaba 9-19 facturas individuales, obtuvo {n}"
        )

    def test_identidad_financiera(self, ruta_tesoreria):
        """Ventas = Factura Global + Individuales = Efectivo + TDC + Otros."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)

        for fecha, corte in resultado.items():
            if corte.total_efectivo and corte.total_tdc:
                # Verificar: Efectivo + TDC + Otros ≈ Total ventas
                suma_medios = (
                    corte.total_efectivo
                    + corte.total_tdc
                    + (corte.total_otros or Decimal('0'))
                )
                if corte.total_ventas and corte.total_ventas > 0:
                    diff = abs(suma_medios - corte.total_ventas)
                    assert diff < Decimal('1.00'), (
                        f"Dia {fecha}: identidad financiera falla. "
                        f"Efectivo+TDC+Otros={suma_medios}, Ventas={corte.total_ventas}, "
                        f"Diferencia={diff}"
                    )

    def test_anomalia_k19_k20(self, ruta_tesoreria):
        """Dias con datos deben tener factura global (K19 o K20)."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)

        sin_global = []
        for fecha, corte in resultado.items():
            # Solo verificar dias que tienen datos reales (facturas individuales)
            if len(corte.facturas_individuales) > 0 and corte.factura_global_numero is None:
                sin_global.append(fecha)

        assert len(sin_global) == 0, (
            f"Dias CON datos pero SIN factura global: {sin_global}"
        )

    def test_todas_facturas_tienen_serie_fd(self, ruta_tesoreria):
        """Todas las facturas deben tener serie FD."""
        from src.entrada.tesoreria import parsear_tesoreria

        resultado = parsear_tesoreria(ruta_tesoreria)

        for fecha, corte in resultado.items():
            for f in corte.facturas_individuales:
                assert f.serie == 'FD', (
                    f"Dia {fecha}: factura {f.numero} tiene serie '{f.serie}', "
                    f"esperaba 'FD'"
                )
