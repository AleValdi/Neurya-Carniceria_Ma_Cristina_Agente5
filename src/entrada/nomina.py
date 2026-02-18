"""Parser del archivo de nomina CONTPAQi.

Lee archivos como 'NOMINA 03 CHEQUE.xlsx' con 4 hojas:
  - NOM 03: Nomina completa (empleados + totales)
  - DISPERSION: Transferencias bancarias
  - CHEQUE: Pagos en cheque
  - CHEQUE FINIQUITOS: Finiquitos por cheque

Estructura de hoja NOM XX:
  - Filas 73-78: Totales (DISPERSION, CHEQUES, etc.)
  - Fila 81: Vacaciones pagadas
  - Fila 84: Finiquito
"""

import re
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import openpyxl
from loguru import logger

from src.entrada.normalizacion import normalizar_monto
from src.models import DatosNomina, LineaContable


# Mapeo de percepciones: concepto → (cuenta, subcuenta)
PERCEPCIONES_CUENTAS = {
    'SUELDO': ('6200', '010000'),
    'SUELDOS': ('6200', '010000'),
    'SEPTIMO DIA': ('6200', '240000'),
    'PRIMA DOMINICAL': ('6200', '670000'),
    'BONO PUNTUALIDAD': ('6200', '770000'),
    'BONO DE PUNTUALIDAD': ('6200', '770000'),
    'VACACIONES': ('6200', '020000'),
    'PRIMA VACACIONAL': ('6200', '060000'),
    'AGUINALDO': ('6200', '030000'),
    'BONO ASISTENCIA': ('6200', '780000'),
    'BONO DE ASISTENCIA': ('6200', '780000'),
}

# Mapeo de deducciones: concepto → (cuenta, subcuenta)
DEDUCCIONES_CUENTAS = {
    'INFONAVIT VIVIENDA': ('2140', '270000'),
    'INFONAVIT FD': ('2140', '270000'),
    'INFONAVIT CF': ('2140', '270000'),
    'ISR': ('2140', '020000'),
    'ISR (MES)': ('2140', '020000'),
    'IMSS': ('2140', '010000'),
    'I.M.S.S.': ('2140', '010000'),
}


def parsear_nomina(ruta: Path) -> Optional[DatosNomina]:
    """Parsea un archivo de nomina CONTPAQi.

    Args:
        ruta: Ruta al archivo Excel de nomina.

    Returns:
        DatosNomina con totales y detalle de percepciones/deducciones,
        o None si no se pudo parsear.
    """
    logger.info("Parseando nomina: {}", ruta.name)

    # Extraer numero de nomina del nombre del archivo
    numero_nomina = _extraer_numero_nomina(ruta.name)

    wb = openpyxl.load_workbook(str(ruta), data_only=True)

    # Buscar hoja de nomina principal (NOM XX)
    hoja_nomina = _buscar_hoja_nomina(wb)
    if hoja_nomina is None:
        logger.error("No se encontro hoja de nomina en {}", ruta.name)
        wb.close()
        return None

    ws = wb[hoja_nomina]

    # Extraer totales de las filas resumen
    totales = _extraer_totales(ws)

    # Extraer percepciones y deducciones del detalle
    percepciones = _extraer_percepciones(ws)
    deducciones = _extraer_deducciones(ws)

    wb.close()

    datos = DatosNomina(
        numero_nomina=numero_nomina,
        total_dispersion=totales.get('dispersion', Decimal('0')),
        total_cheques=totales.get('cheques', Decimal('0')),
        total_vacaciones=totales.get('vacaciones', Decimal('0')),
        total_finiquito=totales.get('finiquito', Decimal('0')),
        percepciones=percepciones,
        deducciones=deducciones,
    )

    logger.info(
        "Nomina {}: dispersion=${}, cheques=${}, "
        "vacaciones=${}, finiquito=${}, neto=${}",
        numero_nomina,
        datos.total_dispersion,
        datos.total_cheques,
        datos.total_vacaciones,
        datos.total_finiquito,
        datos.total_neto,
    )

    return datos


def _extraer_numero_nomina(nombre_archivo: str) -> int:
    """Extrae el numero de nomina del nombre del archivo.

    Ej: 'NOMINA 03 CHEQUE.xlsx' → 3
    """
    match = re.search(r'NOMINA\s+(\d+)', nombre_archivo, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def _buscar_hoja_nomina(wb) -> Optional[str]:
    """Busca la hoja principal de nomina (NOM XX)."""
    for nombre in wb.sheetnames:
        if re.match(r'NOM\s+\d+', nombre, re.IGNORECASE):
            return nombre
    # Fallback: primera hoja
    if wb.sheetnames:
        return wb.sheetnames[0]
    return None


def _extraer_totales(ws) -> dict:
    """Extrae totales de las filas resumen (73-84).

    Busca por etiquetas en columna B/C para ser robusto ante cambios de fila.
    """
    totales = {
        'dispersion': Decimal('0'),
        'cheques': Decimal('0'),
        'vacaciones': Decimal('0'),
        'finiquito': Decimal('0'),
    }

    # Buscar en rango amplio (filas 70-90)
    for fila in range(70, 91):
        etiqueta = _celda_str(ws, 'B', fila) or _celda_str(ws, 'C', fila) or ''
        etiqueta_upper = etiqueta.upper().strip()

        # Valor tipicamente en columna H o I (neto)
        monto = None
        for col in ('H', 'I', 'J', 'K'):
            monto = normalizar_monto(_celda(ws, col, fila))
            if monto is not None and monto > 0:
                break

        if monto is None or monto <= 0:
            continue

        if 'DISPER' in etiqueta_upper:
            totales['dispersion'] = monto
        elif 'CHEQUE' in etiqueta_upper and 'FINIQ' not in etiqueta_upper:
            totales['cheques'] = monto
        elif 'VACACION' in etiqueta_upper:
            totales['vacaciones'] = monto
        elif 'FINIQ' in etiqueta_upper:
            totales['finiquito'] = monto

    return totales


def _extraer_percepciones(ws) -> List[LineaContable]:
    """Extrae percepciones de la nomina buscando conceptos conocidos."""
    percepciones = []

    # Las percepciones estan tipicamente en las filas de totales (73-78)
    # con el desglose en columnas especificas.
    # Por ahora extraemos lo que podemos de la estructura conocida.
    # TODO: Ajustar una vez veamos la estructura real del archivo

    return percepciones


def _extraer_deducciones(ws) -> List[LineaContable]:
    """Extrae deducciones de la nomina buscando conceptos conocidos."""
    deducciones = []

    # Similar a percepciones, necesita ajuste con archivo real
    # TODO: Ajustar una vez veamos la estructura real del archivo

    return deducciones


def _celda(ws, col: str, fila: int):
    """Obtiene el valor de una celda."""
    return ws[f'{col}{fila}'].value


def _celda_str(ws, col: str, fila: int) -> Optional[str]:
    """Obtiene el valor como string."""
    valor = _celda(ws, col, fila)
    if valor is None:
        return None
    return str(valor).strip() or None
