"""Tests para el parser de PDFs del SUA (IMSS/INFONAVIT)."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.entrada.impuestos_pdf import parsear_imss
from src.models import DatosIMSS


# Rutas a los PDFs de referencia
PDF_SOLO_IMSS = Path('contexto/ConciliacionImssInfonavit/resumen liquidacion_gbl1.pdf')
PDF_IMSS_INFONAVIT = Path('contexto/ConciliacionImssInfonavit/resumen liquidacion_gbl2.pdf')


@pytest.fixture
def datos_solo_imss() -> DatosIMSS:
    """Parsea el PDF de solo IMSS (Enero 2026)."""
    resultado = parsear_imss(PDF_SOLO_IMSS)
    assert resultado is not None
    return resultado


@pytest.fixture
def datos_imss_infonavit() -> DatosIMSS:
    """Parsea el PDF de IMSS+INFONAVIT (Octubre 2025)."""
    resultado = parsear_imss(PDF_IMSS_INFONAVIT)
    assert resultado is not None
    return resultado


class TestParserSoloIMSS:
    """Tests con el PDF gbl1 — Solo IMSS, Enero 2026, $93,880.17."""

    def test_periodo(self, datos_solo_imss):
        assert datos_solo_imss.periodo == 'ENERO 2026'

    def test_folio_sua(self, datos_solo_imss):
        assert datos_solo_imss.folio_sua == '659522'

    def test_total_imss(self, datos_solo_imss):
        assert datos_solo_imss.total_imss == Decimal('93880.17')

    def test_total_a_pagar(self, datos_solo_imss):
        assert datos_solo_imss.total_a_pagar == Decimal('93880.17')

    def test_no_incluye_infonavit(self, datos_solo_imss):
        assert datos_solo_imss.incluye_infonavit is False

    def test_cuenta_individual_cero(self, datos_solo_imss):
        assert datos_solo_imss.retiro == Decimal('0')
        assert datos_solo_imss.cesantia_vejez == Decimal('0')
        assert datos_solo_imss.total_cuenta_individual == Decimal('0')

    def test_infonavit_cero(self, datos_solo_imss):
        assert datos_solo_imss.aportacion_sin_credito == Decimal('0')
        assert datos_solo_imss.aportacion_con_credito == Decimal('0')
        assert datos_solo_imss.amortizacion == Decimal('0')
        assert datos_solo_imss.total_infonavit == Decimal('0')

    def test_confianza_100(self, datos_solo_imss):
        assert datos_solo_imss.confianza_100 is True

    def test_sin_advertencias(self, datos_solo_imss):
        assert datos_solo_imss.advertencias == []

    def test_validacion_cruzada(self, datos_solo_imss):
        """total_imss + cuenta_individual + infonavit == total_a_pagar."""
        d = datos_solo_imss
        assert d.total_imss + d.total_cuenta_individual + d.total_infonavit == d.total_a_pagar


class TestParserIMSSInfonavit:
    """Tests con el PDF gbl2 — IMSS+INFONAVIT, Octubre 2025, $300,701.47."""

    def test_periodo(self, datos_imss_infonavit):
        assert datos_imss_infonavit.periodo == 'OCTUBRE 2025'

    def test_folio_sua(self, datos_imss_infonavit):
        assert datos_imss_infonavit.folio_sua == '319821'

    def test_incluye_infonavit(self, datos_imss_infonavit):
        assert datos_imss_infonavit.incluye_infonavit is True

    def test_total_imss(self, datos_imss_infonavit):
        assert datos_imss_infonavit.total_imss == Decimal('92153.70')

    def test_retiro(self, datos_imss_infonavit):
        assert datos_imss_infonavit.retiro == Decimal('25722.43')

    def test_cesantia_vejez(self, datos_imss_infonavit):
        assert datos_imss_infonavit.cesantia_vejez == Decimal('82681.60')

    def test_total_cuenta_individual(self, datos_imss_infonavit):
        assert datos_imss_infonavit.total_cuenta_individual == Decimal('108404.03')

    def test_aportacion_sin_credito(self, datos_imss_infonavit):
        assert datos_imss_infonavit.aportacion_sin_credito == Decimal('54659.80')

    def test_aportacion_con_credito(self, datos_imss_infonavit):
        assert datos_imss_infonavit.aportacion_con_credito == Decimal('9646.09')

    def test_amortizacion(self, datos_imss_infonavit):
        assert datos_imss_infonavit.amortizacion == Decimal('35837.85')

    def test_total_infonavit(self, datos_imss_infonavit):
        assert datos_imss_infonavit.total_infonavit == Decimal('100143.74')

    def test_total_a_pagar(self, datos_imss_infonavit):
        assert datos_imss_infonavit.total_a_pagar == Decimal('300701.47')

    def test_infonavit_5pct(self, datos_imss_infonavit):
        """5% INFONAVIT = Ap. sin credito + Ap. con credito."""
        esperado = Decimal('54659.80') + Decimal('9646.09')  # = 64305.89
        assert datos_imss_infonavit.infonavit_5pct == esperado

    def test_confianza_100(self, datos_imss_infonavit):
        assert datos_imss_infonavit.confianza_100 is True

    def test_sin_advertencias(self, datos_imss_infonavit):
        assert datos_imss_infonavit.advertencias == []

    def test_validacion_cruzada(self, datos_imss_infonavit):
        """total_imss + cuenta_individual + infonavit == total_a_pagar."""
        d = datos_imss_infonavit
        assert d.total_imss + d.total_cuenta_individual + d.total_infonavit == d.total_a_pagar


class TestParserEdgeCases:
    """Tests de casos limite."""

    def test_pdf_inexistente(self):
        resultado = parsear_imss(Path('/tmp/no_existe.pdf'))
        assert resultado is None

    def test_retorno_tipo_correcto(self, datos_solo_imss):
        assert isinstance(datos_solo_imss, DatosIMSS)
