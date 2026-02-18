"""Fixtures compartidas para tests del Agente5."""

import sys
from pathlib import Path

import pytest

# Agregar raiz del proyecto al path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Rutas a archivos de prueba reales
DATA_DIR = ROOT / 'data' / 'reportes'
CONTEXTO_DIR = ROOT / 'contexto' / 'listaRaya'

ESTADO_CUENTA_PATH = DATA_DIR / 'PRUEBA.xlsx'
TESORERIA_PATH = DATA_DIR / 'FEBRERO INGRESOS 2026.xlsx'
NOMINA_PATH = CONTEXTO_DIR / 'NOMINA 03 CHEQUE.xlsx'


@pytest.fixture
def ruta_estado_cuenta() -> Path:
    """Ruta al archivo de estado de cuenta de prueba."""
    if not ESTADO_CUENTA_PATH.exists():
        pytest.skip(f"Archivo no disponible: {ESTADO_CUENTA_PATH}")
    return ESTADO_CUENTA_PATH


@pytest.fixture
def ruta_tesoreria() -> Path:
    """Ruta al archivo de tesoreria de prueba."""
    if not TESORERIA_PATH.exists():
        pytest.skip(f"Archivo no disponible: {TESORERIA_PATH}")
    return TESORERIA_PATH


@pytest.fixture
def ruta_nomina() -> Path:
    """Ruta al archivo de nomina de prueba."""
    if not NOMINA_PATH.exists():
        pytest.skip(f"Archivo no disponible: {NOMINA_PATH}")
    return NOMINA_PATH
