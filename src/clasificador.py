"""Clasificador de movimientos bancarios.

Asigna un TipoProceso a cada MovimientoBancario basandose en patrones
regex de la descripcion y la cuenta de origen.
"""

import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from loguru import logger

from src.models import MovimientoBancario, TipoProceso


# Patrones de clasificacion: (regex_compilado, tipo_proceso, cuenta_filtro)
# cuenta_filtro: si es None, aplica a cualquier cuenta.
# Se evaluan en orden; el primero que matchea gana.
PATRONES: List[Tuple[re.Pattern, TipoProceso, Optional[str]]] = [
    # --- Ingresos venta tarjeta (cuenta tarjeta) ---
    (
        re.compile(r'ABONO VENTAS TDC', re.IGNORECASE),
        TipoProceso.VENTA_TDC,
        '038900320016',
    ),
    (
        re.compile(r'ABONO VENTAS TDD', re.IGNORECASE),
        TipoProceso.VENTA_TDD,
        '038900320016',
    ),

    # --- Ingresos venta efectivo (cuenta cheques) ---
    (
        re.compile(r'Dep[oó]sito en efectivo', re.IGNORECASE),
        TipoProceso.VENTA_EFECTIVO,
        '055003730017',
    ),

    # --- Traspasos ---
    (
        re.compile(r'\(BE\)\s*Traspaso a cuenta', re.IGNORECASE),
        TipoProceso.TRASPASO,
        None,
    ),
    (
        re.compile(r'\(NB\)\s*Recepci[oó]n de cuenta', re.IGNORECASE),
        TipoProceso.TRASPASO_INGRESO,
        None,
    ),

    # --- Comisiones bancarias (IVA ANTES que base para evitar match parcial) ---
    (
        re.compile(r'IVA de Comisi[oó]n Transfer', re.IGNORECASE),
        TipoProceso.COMISION_SPEI_IVA,
        None,
    ),
    (
        re.compile(r'Comisi[oó]n Transferencia', re.IGNORECASE),
        TipoProceso.COMISION_SPEI,
        None,
    ),
    (
        re.compile(r'IVA Aplicaci[oó]n de Tasas', re.IGNORECASE),
        TipoProceso.COMISION_TDC_IVA,
        '038900320016',
    ),
    (
        re.compile(r'Aplicaci[oó]n de Tasas de Descuento', re.IGNORECASE),
        TipoProceso.COMISION_TDC,
        '038900320016',
    ),

    # --- Nomina ---
    (
        re.compile(r'NOMINA.*PAGO DE NOMINA', re.IGNORECASE),
        TipoProceso.NOMINA,
        None,
    ),

    # --- Impuestos federales (pago referenciado SAT) ---
    (
        re.compile(r'\(BE\)\s*Pago servicio.*PAGO REFERENCIADO', re.IGNORECASE),
        TipoProceso.IMPUESTO_FEDERAL,
        '055003730017',
    ),

    # --- Impuesto estatal (SPEI a Secretaria de Finanzas NL) ---
    (
        re.compile(r'SECRETARIA DE FINANZAS', re.IGNORECASE),
        TipoProceso.IMPUESTO_ESTATAL,
        None,
    ),

    # --- IMSS/INFONAVIT (pago SUA/SIPARE) ---
    (
        re.compile(r'\(BE\)\s*Pago servicio.*PAGO SUA', re.IGNORECASE),
        TipoProceso.IMPUESTO_IMSS,
        '055003730017',
    ),

    # --- Pagos SPEI a proveedores (ultimo recurso para egresos) ---
    # Patron: cadena alfanumerica seguida de SPEI
    (
        re.compile(r'[A-Z0-9]{5,}.*SPEI', re.IGNORECASE),
        TipoProceso.PAGO_PROVEEDOR,
        '055003730017',
    ),
]


def clasificar_movimientos(
    movimientos: List[MovimientoBancario],
) -> List[MovimientoBancario]:
    """Clasifica cada movimiento asignando tipo_proceso.

    Modifica los objetos in-place y retorna la misma lista.
    Los movimientos que no matchean ningun patron quedan como DESCONOCIDO.
    """
    conteo: Dict[TipoProceso, int] = defaultdict(int)

    for mov in movimientos:
        tipo = _clasificar_uno(mov)
        mov.tipo_proceso = tipo
        conteo[tipo] += 1

    # Log resumen
    for tipo in sorted(conteo.keys(), key=lambda t: t.value):
        logger.info("  {} → {} movimientos", tipo.value, conteo[tipo])

    return movimientos


def _clasificar_uno(mov: MovimientoBancario) -> TipoProceso:
    """Clasifica un solo movimiento."""
    for patron, tipo, cuenta_filtro in PATRONES:
        # Filtrar por cuenta si aplica
        if cuenta_filtro and mov.cuenta_banco != cuenta_filtro:
            continue

        if patron.search(mov.descripcion):
            return tipo

    return TipoProceso.DESCONOCIDO


def agrupar_por_proceso_y_fecha(
    movimientos: List[MovimientoBancario],
) -> Dict[Tuple[TipoProceso, date], List[MovimientoBancario]]:
    """Agrupa movimientos clasificados por (tipo_proceso, fecha).

    Util para procesar por lotes: todos los TDC del dia 1, todos los
    SPEI del dia 3, etc.
    """
    grupos: Dict[Tuple[TipoProceso, date], List[MovimientoBancario]] = defaultdict(list)

    for mov in movimientos:
        if mov.tipo_proceso is None:
            continue
        clave = (mov.tipo_proceso, mov.fecha)
        grupos[clave].append(mov)

    return dict(grupos)


def agrupar_ventas_tdc_por_fecha(
    movimientos: List[MovimientoBancario],
) -> Dict[date, List[MovimientoBancario]]:
    """Agrupa movimientos TDC y TDD por fecha.

    Combina VENTA_TDC y VENTA_TDD porque ambos van al mismo procesador.
    """
    grupos: Dict[date, List[MovimientoBancario]] = defaultdict(list)

    for mov in movimientos:
        if mov.tipo_proceso in (TipoProceso.VENTA_TDC, TipoProceso.VENTA_TDD):
            grupos[mov.fecha].append(mov)

    return dict(grupos)


def agrupar_comisiones_por_fecha(
    movimientos: List[MovimientoBancario],
) -> Dict[date, Dict[str, List[MovimientoBancario]]]:
    """Agrupa comisiones por fecha, separando base e IVA.

    Retorna: {fecha: {'base': [...], 'iva': [...]}}
    """
    grupos: Dict[date, Dict[str, List[MovimientoBancario]]] = {}

    tipos_base = (TipoProceso.COMISION_SPEI, TipoProceso.COMISION_TDC)
    tipos_iva = (TipoProceso.COMISION_SPEI_IVA, TipoProceso.COMISION_TDC_IVA)

    for mov in movimientos:
        if mov.tipo_proceso in tipos_base:
            if mov.fecha not in grupos:
                grupos[mov.fecha] = {'base': [], 'iva': []}
            grupos[mov.fecha]['base'].append(mov)
        elif mov.tipo_proceso in tipos_iva:
            if mov.fecha not in grupos:
                grupos[mov.fecha] = {'base': [], 'iva': []}
            grupos[mov.fecha]['iva'].append(mov)

    return grupos


def resumen_clasificacion(
    movimientos: List[MovimientoBancario],
) -> Dict[str, int]:
    """Retorna conteo de movimientos por tipo_proceso."""
    conteo: Dict[str, int] = defaultdict(int)
    for mov in movimientos:
        tipo = mov.tipo_proceso.value if mov.tipo_proceso else 'SIN_CLASIFICAR'
        conteo[tipo] += 1
    return dict(conteo)
