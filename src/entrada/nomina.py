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
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import openpyxl
from loguru import logger

from src.entrada.normalizacion import normalizar_monto
from src.models import DatosNomina, LineaContable


def _normalizar_texto(texto: str) -> str:
    """Normaliza texto: quita acentos, puntos, y pasa a mayusculas.

    Ej: 'Séptimo día' → 'SEPTIMO DIA'
        'I.S.R. (mes)' → 'ISR (MES)'
        'Préstamo infonavit (FD)' → 'PRESTAMO INFONAVIT (FD)'
    """
    # Quitar acentos
    nfkd = unicodedata.normalize('NFKD', texto)
    sin_acentos = ''.join(c for c in nfkd if not unicodedata.combining(c))
    # Quitar puntos
    sin_puntos = sin_acentos.replace('.', '')
    return sin_puntos.upper().strip()


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
    'INFONAVIT (FD)': ('2140', '270000'),
    'INFONAVIT CF': ('2140', '270000'),
    'INFONAVIT (CF)': ('2140', '270000'),
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
    percepciones, deducciones = _extraer_percepciones_deducciones(ws)

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
    """Extrae totales de las filas resumen (73-90).

    Estructura real del Excel CONTPAQi:
      - Fila 74: col I='DISPERSION', col J=monto
      - Fila 75: col I='CHEQUES', col J=monto
      - Fila 80: col A='VACACIONES PAGADAS' (encabezado)
      - Fila 81: col J=monto vacaciones (fila de detalle)
      - Fila 83: col A='FINIQUITOS PAGADOS' (encabezado)
      - Fila 84: col J=monto finiquito (fila de detalle)

    Busca etiquetas en columnas A, B, C, I para ser robusto.
    """
    totales = {
        'dispersion': Decimal('0'),
        'cheques': Decimal('0'),
        'vacaciones': Decimal('0'),
        'finiquito': Decimal('0'),
    }

    # Buscar en rango amplio (filas 70-90)
    fila_vacaciones = None
    fila_finiquito = None

    for fila in range(70, 91):
        # Buscar etiqueta en todas las columnas relevantes
        etiqueta = ''
        for col in ('A', 'B', 'C', 'I'):
            val = _celda_str(ws, col, fila)
            if val and len(val.strip()) > 2:
                etiqueta = val.upper().strip()
                break

        # Monto en columna J (principal) o H, I, K como fallback
        monto = None
        for col in ('J', 'H', 'I', 'K'):
            monto = normalizar_monto(_celda(ws, col, fila))
            if monto is not None and monto > 0:
                break

        # Etiquetas de sección (VACACIONES PAGADAS, FINIQUITOS PAGADOS)
        # marcan que la SIGUIENTE fila con datos tiene el monto
        if 'VACACION' in etiqueta and 'PAGAD' in etiqueta:
            fila_vacaciones = fila
            continue
        elif 'FINIQ' in etiqueta and 'PAGAD' in etiqueta:
            fila_finiquito = fila
            continue

        if monto is None or monto <= 0:
            continue

        if 'DISPER' in etiqueta:
            totales['dispersion'] = monto
        elif 'CHEQUE' in etiqueta and 'FINIQ' not in etiqueta:
            totales['cheques'] = monto
        elif fila_vacaciones and fila == fila_vacaciones + 1:
            totales['vacaciones'] = monto
        elif fila_finiquito and fila == fila_finiquito + 1:
            totales['finiquito'] = monto

    return totales


def _extraer_percepciones_deducciones(ws) -> tuple:
    """Extrae percepciones y deducciones de la nomina.

    Estructura del Excel CONTPAQi (NOM XX):
      - Fila 5: Headers de columnas (C=Sueldo, D=Séptimo día, etc.)
      - Fila 73: Totales por columna (suma de todos los empleados)

    Lee los headers de fila 5, busca conceptos conocidos en PERCEPCIONES_CUENTAS
    y DEDUCCIONES_CUENTAS, y extrae los totales de fila 73.

    Returns:
        Tupla (percepciones, deducciones) como listas de LineaContable.
    """
    percepciones = []
    deducciones = []

    # Buscar la fila de totales: primera fila despues de los empleados
    # donde columna A no tiene numero de empleado y columna C tiene valor
    fila_totales = None
    for fila in range(70, 91):
        val_a = ws[f'A{fila}'].value
        val_c = ws[f'C{fila}'].value
        # Fila de totales: no tiene codigo de empleado en A, pero tiene montos en C+
        if val_a is None and val_c is not None:
            monto = normalizar_monto(val_c)
            if monto is not None and monto > 0:
                fila_totales = fila
                break

    if fila_totales is None:
        return percepciones, deducciones

    # Leer headers de fila 5 y totales de la fila encontrada
    for col in 'CDEFGHIJK':
        header = _celda_str(ws, col, 5)
        if not header:
            continue

        monto = normalizar_monto(_celda(ws, col, fila_totales))
        if monto is None or monto <= 0:
            continue

        header_norm = _normalizar_texto(header)

        # Buscar en percepciones
        cuenta_info = None
        for concepto, cta in PERCEPCIONES_CUENTAS.items():
            if concepto in header_norm or header_norm in concepto:
                cuenta_info = cta
                break

        if cuenta_info:
            percepciones.append(LineaContable(
                cuenta=cuenta_info[0],
                subcuenta=cuenta_info[1],
                concepto=header.strip(),
                monto=monto,
            ))
            continue

        # Buscar en deducciones
        cuenta_info = None
        for concepto, cta in DEDUCCIONES_CUENTAS.items():
            if concepto in header_norm or header_norm in concepto:
                cuenta_info = cta
                break

        if cuenta_info:
            deducciones.append(LineaContable(
                cuenta=cuenta_info[0],
                subcuenta=cuenta_info[1],
                concepto=header.strip(),
                monto=monto,
            ))

    return percepciones, deducciones


def _celda(ws, col: str, fila: int):
    """Obtiene el valor de una celda."""
    return ws[f'{col}{fila}'].value


def _celda_str(ws, col: str, fila: int) -> Optional[str]:
    """Obtiene el valor como string."""
    valor = _celda(ws, col, fila)
    if valor is None:
        return None
    return str(valor).strip() or None
