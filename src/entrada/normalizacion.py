"""Utilidades de normalizacion de texto y fechas.

Corrige problemas de encoding (mojibake) en descripciones del
estado de cuenta bancario y provee helpers para parseo de fechas.
"""

import re
import unicodedata
from datetime import date, datetime
from typing import Optional


# Tabla de reemplazos comunes de mojibake (Latin-1 -> UTF-8 mal decodificado)
# Los reemplazos largos (palabras completas) van primero para evitar reemplazos parciales
MOJIBAKE_REEMPLAZOS_PALABRAS = {
    "N\u00c3\u201cMINA": "NOMINA",
    "N\u00c3\u00b3mina": "Nomina",
    "Comisi\u00c3\u00b3n": "Comision",
    "comisi\u00c3\u00b3n": "comision",
    "Recepci\u00c3\u00b3n": "Recepcion",
    "Aplicaci\u00c3\u00b3n": "Aplicacion",
    "Transacci\u00c3\u00b3n": "Transaccion",
    "Dep\u00c3\u00b3sito": "Deposito",
    "dep\u00c3\u00b3sito": "deposito",
}

# Reemplazos de caracteres individuales mojibake
MOJIBAKE_REEMPLAZOS_CHARS = {
    "\u00c3\u0081": "A",       # A con tilde
    "\u00c3\u2030": "E",       # E con tilde
    "\u00c3\u008d": "I",       # I con tilde
    "\u00c3\u201c": "O",       # O con tilde
    "\u00c3\u0161": "U",       # U con tilde
    "\u00c3\u2019": "N",       # N con tilde (Ñ mayuscula)
    "\u00c3\u00a1": "a",       # a con tilde
    "\u00c3\u00a9": "e",       # e con tilde
    "\u00c3\u00ad": "i",       # i con tilde
    "\u00c3\u00b3": "o",       # o con tilde
    "\u00c3\u00ba": "u",       # u con tilde
    "\u00c3\u00b1": "n",       # n con tilde
    "\u00c3\u00bc": "u",       # u con dieresis
    "\u00c2\u00b0": "\u00b0",  # grado
    "\u00c2\u00bf": "\u00bf",  # signo interrogacion invertido
    "\u00c2\u00a1": "\u00a1",  # signo exclamacion invertido
}


def fix_mojibake(texto: str) -> str:
    """Corrige problemas de encoding mojibake en texto.

    Aplica reemplazos conocidos y normaliza caracteres Unicode.
    """
    if not texto:
        return texto

    resultado = texto

    # Aplicar reemplazos de palabras completas primero
    for malo, bueno in MOJIBAKE_REEMPLAZOS_PALABRAS.items():
        resultado = resultado.replace(malo, bueno)

    # Luego reemplazos de caracteres individuales
    for malo, bueno in MOJIBAKE_REEMPLAZOS_CHARS.items():
        resultado = resultado.replace(malo, bueno)

    # Intentar decodificar secuencias UTF-8 residuales
    try:
        # Si el texto tiene bytes sueltos, intentar re-encodear
        resultado_bytes = resultado.encode('latin-1', errors='ignore')
        resultado_utf8 = resultado_bytes.decode('utf-8', errors='ignore')
        if resultado_utf8 and len(resultado_utf8) > len(resultado) * 0.5:
            resultado = resultado_utf8
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass

    # Normalizar Unicode (NFC)
    resultado = unicodedata.normalize('NFC', resultado)

    # Limpiar espacios multiples
    resultado = re.sub(r'\s+', ' ', resultado).strip()

    return resultado


def parsear_fecha_excel(valor) -> Optional[date]:
    """Convierte un valor de celda Excel a date.

    Maneja datetime, date, string (DD/MM/AAAA) y numeros seriales de Excel.
    """
    if valor is None:
        return None

    if isinstance(valor, datetime):
        return valor.date()

    if isinstance(valor, date):
        return valor

    if isinstance(valor, (int, float)):
        # Numero serial de Excel (dias desde 1900-01-01)
        try:
            from datetime import timedelta
            # Excel usa epoch 1899-12-30 (con bug de 1900 como bisiesto)
            base = datetime(1899, 12, 30)
            return (base + timedelta(days=int(valor))).date()
        except (ValueError, OverflowError):
            return None

    if isinstance(valor, str):
        valor = valor.strip()
        # Intentar DD/MM/AAAA
        for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y'):
            try:
                return datetime.strptime(valor, fmt).date()
            except ValueError:
                continue
        return None

    return None


def normalizar_monto(valor) -> Optional['Decimal']:
    """Convierte un valor de celda Excel a Decimal.

    Maneja float, int, string con formato de moneda.
    """
    from decimal import Decimal, InvalidOperation

    if valor is None:
        return None

    if isinstance(valor, Decimal):
        return valor

    if isinstance(valor, (int, float)):
        if valor == 0:
            return None
        return Decimal(str(valor))

    if isinstance(valor, str):
        # Limpiar formato de moneda: $1,234.56
        limpio = valor.replace('$', '').replace(',', '').strip()
        if not limpio or limpio == '-':
            return None
        try:
            return Decimal(limpio)
        except InvalidOperation:
            return None

    return None
