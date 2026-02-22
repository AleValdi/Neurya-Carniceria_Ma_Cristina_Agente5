"""Parser para ajustes opcionales de impuestos desde Excel.

Si el archivo AJUSTES_IMPUESTOS.xlsx existe en data/reportes/,
los valores que contenga sobreescriben los extraidos de los PDFs.
Celdas vacias se ignoran (se usa el valor del PDF).
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict

from loguru import logger
import openpyxl


def parsear_ajustes_impuestos(ruta: Path) -> Dict[str, Decimal]:
    """Lee ajustes opcionales de impuestos desde Excel.

    Estructura esperada (posicion fija):
        B2 = IMSS total
        B3 = IVA Acumulable
        B4 = IVA Acreditable

    Returns:
        Dict con solo las claves que tienen valor numerico:
        - 'total_imss', 'iva_acumulable', 'iva_acreditable'
    """
    MAPA = {
        2: 'total_imss',
        3: 'iva_acumulable',
        4: 'iva_acreditable',
    }

    try:
        wb = openpyxl.load_workbook(ruta, data_only=True)
        ws = wb.active

        ajustes = {}
        for fila, clave in MAPA.items():
            valor = ws.cell(row=fila, column=2).value
            if valor is not None:
                try:
                    ajustes[clave] = Decimal(str(valor))
                except (InvalidOperation, ValueError):
                    logger.warning(
                        "Ajuste impuestos: celda B{} no es numerico ({}), ignorando",
                        fila, valor,
                    )

        wb.close()
        return ajustes

    except Exception as e:
        logger.warning("Error leyendo ajustes de impuestos {}: {}", ruta, e)
        return {}
