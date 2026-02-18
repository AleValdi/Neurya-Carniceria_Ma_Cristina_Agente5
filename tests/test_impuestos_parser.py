"""Tests para el parser de PDFs de impuestos."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.entrada.impuestos_pdf import (
    _extraer_periodo,
    _extraer_periodo_de_nombre,
    _parsear_acuse_federal_1,
    _parsear_acuse_federal_2,
    _parsear_declaracion_iva,
    _parsear_detalle_ieps,
    _parsear_estatal_3pct,
    _parsear_monto,
    _parsear_tabla_retenciones_iva,
    parsear_impuesto_estatal,
    parsear_impuesto_federal,
)
from src.models import DatosImpuestoEstatal, DatosImpuestoFederal


# --- Tests de utilidades ---


class TestParsearMonto:
    def test_entero_con_comas(self):
        assert _parsear_monto('6,822') == Decimal('6822')

    def test_con_signo_pesos(self):
        assert _parsear_monto('$6,822') == Decimal('6822')

    def test_con_decimales(self):
        assert _parsear_monto('22,971.00') == Decimal('22971.00')

    def test_con_pesos_y_decimales(self):
        assert _parsear_monto('$22,971.00') == Decimal('22971.00')

    def test_sin_comas(self):
        assert _parsear_monto('523') == Decimal('523')

    def test_vacio(self):
        assert _parsear_monto('') is None

    def test_none(self):
        assert _parsear_monto(None) is None

    def test_invalido(self):
        assert _parsear_monto('abc') is None


class TestExtraerPeriodo:
    def test_periodo_basico(self):
        texto = "Períododeladeclaración: Enero Ejercicio: 2026"
        resultado = _extraer_periodo(texto)
        assert resultado == 'ENERO 2026'

    def test_periodo_febrero(self):
        texto = "Periodo: Febrero 2025 algo mas"
        resultado = _extraer_periodo(texto)
        assert resultado == 'FEBRERO 2025'

    def test_sin_periodo(self):
        texto = "No hay nada relevante aqui"
        resultado = _extraer_periodo(texto)
        assert resultado is None


class TestExtraerPeriodoDeNombre:
    def test_nombre_estatal(self):
        assert _extraer_periodo_de_nombre('3% SN Enero 2026.pdf') == 'ENERO 2026'

    def test_nombre_febrero(self):
        assert _extraer_periodo_de_nombre('Impuesto Febrero 2025.pdf') == 'FEBRERO 2025'

    def test_sin_periodo(self):
        assert _extraer_periodo_de_nombre('archivo.pdf') is None


# --- Tests de parsers internos ---


# Texto simulado de acuse 1a declaracion SAT
TEXTO_ACUSE_1 = """ACUSEDERECIBO
DECLARACIÓNPROVISIONALODEFINITIVADEIMPUESTOSFEDERALES
RFC: DCM02072238A Hoja1de3
Períododeladeclaración: Enero Ejercicio: 2026
ISRRETENCIONESPORSERVICIOSPROFESIONALES/RÉGIMENSIMPLIFICADODE
Conceptodepago1:1
CONFIANZA
Impuestoacargo: 523
Parteactualizada: 0
Cantidadacargo: 523
Cantidadapagar: 523
ISRPORPAGOSPORCUENTADETERCEROS10%ARRENDAMIENTODEINMUEBLES
Conceptodepago2:2
Impuestoacargo: 4,959
Parteactualizada: 0
Cantidadacargo: 4,959
Cantidadapagar: 4,959
Conceptodepago3:3 IEPSPORALIMENTOSNOBÁSICOSCONALTADENSIDADCALÓRICA
Impuestoacargo: 1,340
Parteactualizada: 0
Cantidadacargo: 1,340
Cantidadapagar: 1,340
LINEADECAPTURA
0426 117G 2600 4884 0483 $6,822
"""

TEXTO_ACUSE_2 = """ACUSEDERECIBO
DECLARACIÓNPROVISIONALODEFINITIVADEIMPUESTOSFEDERALES
RFC: DCM02072238A
Períododeladeclaración: Enero Ejercicio: 2026
Conceptodepago1: ISRpersonasmorales
Acargo: 17,060
Conceptodepago2: ISRretencionesporsalarios
Acargo: 12,168
Conceptodepago3: ImpuestoalValorAgregado.Personasmorales
Afavor: 115,864
Conceptodepago4: IVAretenciones
Acargo: 5,780
LINEADECAPTURA
$35,008
"""

TEXTO_DETALLE_IEPS = """DECLARACIÓN
ALIMENTOSNOBÁSICOSCONALTADENSIDADCALÓRICA
TOTALDELIMPUESTOCAUSADODE
ALIMENTOSNOBÁSICOSCONALTA 11,713
DENSIDADCALÓRICA
IEPSACREDITABLEPORALIMENTOSNO
OTRASCANTIDADESACARGODEL
BÁSICOSCONALTADENSIDAD 10,373
CONTRIBUYENTE
CALÓRICA
IMPUESTOACARGODEALIMENTOSNO
BÁSICOSCONALTADENSIDAD 1,340
"""

TEXTO_DECLARACION_IVA = """DeclaraciónProvisionaloDefinitivadeImpuestosFederales
DETERMINACIÓN
TOTALDEIVAACARGO 46,399
TOTALDEIVAACREDITABLE 162,263
SALDOAFAVOR 115,864
IVAretenciones
DETERMINACIÓN
CONSECUTIVO ACTOOACTIVIDAD VALORDELA IVATRASLADADO IVARETENIDO
1 SERVICIOSDE 3,861 618 154
AUTOTRANSPORTE
TERRESTREDE
BIENES
2 SERVICIOS 3,147 504 336
PERSONALES
INDEPENDIENTES
3 USOOGOCE 49,593 7,935 5,290
TEMPORALDE
BIENESOTORGADO
Total 56,601 9,057 5,780
"""

TEXTO_ESTATAL = """SECRETARIADEFINANZASYTESORERIAGENERALDELESTADODENUEVOLEON
Fechadevencimiento: 2026-02-17 Montoapagar: $22,971.00
ImpuestosobreNomina
"""


class TestParsearAcuseFederal1:
    def test_extrae_isr_honorarios(self):
        resultado = _parsear_acuse_federal_1(TEXTO_ACUSE_1)
        assert resultado['isr_ret_honorarios'] == Decimal('523')

    def test_extrae_isr_arrendamiento(self):
        resultado = _parsear_acuse_federal_1(TEXTO_ACUSE_1)
        assert resultado['isr_ret_arrendamiento'] == Decimal('4959')

    def test_extrae_ieps_neto(self):
        resultado = _parsear_acuse_federal_1(TEXTO_ACUSE_1)
        assert resultado['ieps_neto'] == Decimal('1340')

    def test_extrae_total(self):
        resultado = _parsear_acuse_federal_1(TEXTO_ACUSE_1)
        assert resultado['total'] == Decimal('6822')

    def test_extrae_periodo(self):
        resultado = _parsear_acuse_federal_1(TEXTO_ACUSE_1)
        assert resultado['periodo'] == 'ENERO 2026'

    def test_sin_advertencias(self):
        resultado = _parsear_acuse_federal_1(TEXTO_ACUSE_1)
        assert resultado['advertencias'] == []


class TestParsearAcuseFederal2:
    def test_extrae_isr_pm(self):
        resultado = _parsear_acuse_federal_2(TEXTO_ACUSE_2)
        assert resultado['isr_personas_morales'] == Decimal('17060')

    def test_extrae_isr_salarios(self):
        resultado = _parsear_acuse_federal_2(TEXTO_ACUSE_2)
        assert resultado['isr_ret_salarios'] == Decimal('12168')

    def test_extrae_iva_retenciones(self):
        resultado = _parsear_acuse_federal_2(TEXTO_ACUSE_2)
        assert resultado['iva_ret_total'] == Decimal('5780')

    def test_extrae_total(self):
        resultado = _parsear_acuse_federal_2(TEXTO_ACUSE_2)
        assert resultado['total'] == Decimal('35008')


class TestParsearDetalleIEPS:
    def test_extrae_ieps_acumulable(self):
        resultado = _parsear_detalle_ieps(TEXTO_DETALLE_IEPS)
        assert resultado['ieps_acumulable'] == Decimal('11713')

    def test_extrae_ieps_acreditable(self):
        resultado = _parsear_detalle_ieps(TEXTO_DETALLE_IEPS)
        assert resultado['ieps_acreditable'] == Decimal('10373')

    def test_sin_advertencias(self):
        resultado = _parsear_detalle_ieps(TEXTO_DETALLE_IEPS)
        assert resultado['advertencias'] == []


class TestParsearDeclaracionIVA:
    def test_extrae_iva_acumulable(self):
        resultado = _parsear_declaracion_iva(TEXTO_DECLARACION_IVA)
        assert resultado['iva_acumulable'] == Decimal('46399')

    def test_extrae_iva_acreditable(self):
        resultado = _parsear_declaracion_iva(TEXTO_DECLARACION_IVA)
        assert resultado['iva_acreditable'] == Decimal('162263')

    def test_extrae_iva_a_favor(self):
        resultado = _parsear_declaracion_iva(TEXTO_DECLARACION_IVA)
        assert resultado['iva_a_favor'] == Decimal('115864')

    def test_extrae_retenciones_3_proveedores(self):
        resultado = _parsear_declaracion_iva(TEXTO_DECLARACION_IVA)
        retenciones = resultado['retenciones_iva']
        assert len(retenciones) == 3

    def test_retenciones_montos(self):
        resultado = _parsear_declaracion_iva(TEXTO_DECLARACION_IVA)
        montos = [r.monto for r in resultado['retenciones_iva']]
        assert montos == [Decimal('154'), Decimal('336'), Decimal('5290')]

    def test_retenciones_proveedores_mapeados(self):
        resultado = _parsear_declaracion_iva(TEXTO_DECLARACION_IVA)
        provs = [r.proveedor for r in resultado['retenciones_iva']]
        assert provs[0] == '001640'  # Autotransporte
        assert provs[1] == '001352'  # Servicios personales
        assert provs[2] == '001513'  # Uso o goce temporal


class TestParsearEstatal:
    def test_extrae_monto(self):
        resultado = _parsear_estatal_3pct(TEXTO_ESTATAL)
        assert resultado['monto'] == Decimal('22971.00')

    def test_sin_advertencias(self):
        resultado = _parsear_estatal_3pct(TEXTO_ESTATAL)
        assert resultado['advertencias'] == []


# --- Tests de funciones publicas ---


class TestParsearImpuestoFederalIntegracion:
    """Tests de integracion que usan texto simulado via mock."""

    def _mock_federal(self, texto_1, texto_2, texto_ieps=None, texto_decl=None):
        """Parsea con textos simulados."""
        textos = {
            'acuse1.pdf': texto_1,
            'acuse2.pdf': texto_2,
            'ieps.pdf': texto_ieps,
            'decl.pdf': texto_decl,
        }

        def fake_extract(ruta):
            return textos.get(ruta.name)

        with patch('src.entrada.impuestos_pdf._extraer_texto_pdf', side_effect=fake_extract), \
             patch.object(Path, 'exists', return_value=True):
            return parsear_impuesto_federal(
                ruta_acuse_1=Path('acuse1.pdf'),
                ruta_acuse_2=Path('acuse2.pdf'),
                ruta_detalle_ieps=Path('ieps.pdf') if texto_ieps else None,
                ruta_declaracion_completa=Path('decl.pdf') if texto_decl else None,
            )

    def test_federal_completo_confianza_true(self):
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        assert resultado is not None
        assert resultado.confianza_100 is True

    def test_federal_montos_correctos(self):
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        assert resultado.isr_ret_honorarios == Decimal('523')
        assert resultado.isr_ret_arrendamiento == Decimal('4959')
        assert resultado.ieps_neto == Decimal('1340')
        assert resultado.total_primera == Decimal('6822')
        assert resultado.isr_personas_morales == Decimal('17060')
        assert resultado.isr_ret_salarios == Decimal('12168')
        assert resultado.total_segunda == Decimal('35008')

    def test_federal_ieps_brutos(self):
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        assert resultado.ieps_acumulable == Decimal('11713')
        assert resultado.ieps_acreditable == Decimal('10373')

    def test_federal_iva_brutos(self):
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        assert resultado.iva_acumulable == Decimal('46399')
        assert resultado.iva_acreditable == Decimal('162263')
        assert resultado.iva_a_favor == Decimal('115864')

    def test_federal_retenciones_iva(self):
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        assert len(resultado.retenciones_iva) == 3
        assert sum(r.monto for r in resultado.retenciones_iva) == Decimal('5780')

    def test_federal_sin_ieps_confianza_false(self):
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            texto_ieps=None, texto_decl=TEXTO_DECLARACION_IVA,
        )
        assert resultado.confianza_100 is False

    def test_federal_validacion_cruzada_1a(self):
        """Suma conceptos 1a == total acuse."""
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        suma_1a = (
            resultado.isr_ret_honorarios
            + resultado.isr_ret_arrendamiento
            + resultado.ieps_neto
        )
        assert suma_1a == resultado.total_primera

    def test_federal_validacion_cruzada_2a(self):
        """Suma conceptos 2a == total acuse."""
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        suma_2a = (
            resultado.isr_personas_morales
            + resultado.isr_ret_salarios
            + sum(r.monto for r in resultado.retenciones_iva)
        )
        assert suma_2a == resultado.total_segunda

    def test_federal_validacion_ieps_brutos(self):
        """IEPS acumulable - acreditable == neto."""
        resultado = self._mock_federal(
            TEXTO_ACUSE_1, TEXTO_ACUSE_2,
            TEXTO_DETALLE_IEPS, TEXTO_DECLARACION_IVA,
        )
        assert resultado.ieps_acumulable - resultado.ieps_acreditable == resultado.ieps_neto


class TestParsearImpuestoEstatalIntegracion:
    def test_estatal_con_periodo_en_nombre(self):
        with patch('src.entrada.impuestos_pdf._extraer_texto_pdf', return_value=TEXTO_ESTATAL):
            resultado = parsear_impuesto_estatal(Path('3% SN Enero 2026.pdf'))
        assert resultado is not None
        assert resultado.monto == Decimal('22971.00')
        assert resultado.periodo == 'ENERO 2026'
        assert resultado.confianza_100 is True

    def test_estatal_sin_monto(self):
        texto_sin_monto = "Un texto sin monto a pagar"
        with patch('src.entrada.impuestos_pdf._extraer_texto_pdf', return_value=texto_sin_monto):
            resultado = parsear_impuesto_estatal(Path('archivo.pdf'))
        assert resultado is not None
        assert resultado.monto == Decimal('0')
        assert resultado.confianza_100 is False

    def test_estatal_pdf_no_leible(self):
        with patch('src.entrada.impuestos_pdf._extraer_texto_pdf', return_value=None):
            resultado = parsear_impuesto_estatal(Path('corrupto.pdf'))
        assert resultado is None


# --- Tests con PDFs reales (solo si existen) ---


PDF_ACUSE_1 = Path('contexto/impuestos/ImpuestoFederal/acusePdf-1011.pdf')
PDF_ACUSE_2 = Path('contexto/impuestos/ImpuestoFederal/Acuse.DCM02072238A.38.2026.pdf')
PDF_DETALLE_IEPS = Path('contexto/impuestos/ImpuestoFederal/Declaracion.Acuse.0.pdf')
PDF_DECLARACION = Path('contexto/impuestos/ImpuestoFederal/DCM02072238A.38.2026.pdf')
PDF_ESTATAL = Path('contexto/impuestos/ImpuestoEstatal/3% SN Enero 2026.pdf')

necesita_pdfs = pytest.mark.skipif(
    not PDF_ACUSE_1.exists(),
    reason="PDFs de impuestos no disponibles",
)


@necesita_pdfs
class TestParsearConPDFsReales:
    """Tests de integracion con los PDFs reales del ejemplo."""

    def test_federal_completo(self):
        resultado = parsear_impuesto_federal(
            ruta_acuse_1=PDF_ACUSE_1,
            ruta_acuse_2=PDF_ACUSE_2,
            ruta_detalle_ieps=PDF_DETALLE_IEPS,
            ruta_declaracion_completa=PDF_DECLARACION,
        )
        assert resultado is not None
        assert resultado.confianza_100 is True
        assert resultado.periodo == 'ENERO 2026'

    def test_federal_montos_ejemplo(self):
        resultado = parsear_impuesto_federal(
            ruta_acuse_1=PDF_ACUSE_1,
            ruta_acuse_2=PDF_ACUSE_2,
            ruta_detalle_ieps=PDF_DETALLE_IEPS,
            ruta_declaracion_completa=PDF_DECLARACION,
        )
        assert resultado.isr_ret_honorarios == Decimal('523')
        assert resultado.isr_ret_arrendamiento == Decimal('4959')
        assert resultado.ieps_neto == Decimal('1340')
        assert resultado.total_primera == Decimal('6822')
        assert resultado.isr_personas_morales == Decimal('17060')
        assert resultado.isr_ret_salarios == Decimal('12168')
        assert resultado.total_segunda == Decimal('35008')

    def test_estatal_monto_ejemplo(self):
        resultado = parsear_impuesto_estatal(PDF_ESTATAL)
        assert resultado is not None
        assert resultado.monto == Decimal('22971.00')
        assert resultado.confianza_100 is True
