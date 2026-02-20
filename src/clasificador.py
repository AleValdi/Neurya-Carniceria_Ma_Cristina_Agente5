"""Clasificador de movimientos bancarios.

Asigna un TipoProceso a cada MovimientoBancario basandose en patrones
regex de la descripcion y la cuenta de origen.
"""

import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from loguru import logger

from config.settings import CUENTA_POR_NUMERO
from src.models import MovimientoBancario, TipoProceso


# Patrones de clasificacion: (regex, tipo_proceso, cuenta_filtro, es_ingreso)
# cuenta_filtro: si es None, aplica a cualquier cuenta.
# es_ingreso: None=sin filtro, True=solo ingresos, False=solo egresos.
# Se evaluan en orden; el primero que matchea gana.
PATRONES: List[Tuple[re.Pattern, TipoProceso, Optional[str], Optional[bool]]] = [
    # --- Ingresos venta tarjeta (cuenta tarjeta) ---
    (
        re.compile(r'ABONO VENTAS TDC', re.IGNORECASE),
        TipoProceso.VENTA_TDC,
        '038900320016', None,
    ),
    (
        re.compile(r'ABONO VENTAS TDD', re.IGNORECASE),
        TipoProceso.VENTA_TDD,
        '038900320016', None,
    ),

    # --- Ingresos venta efectivo (cuenta cheques) ---
    (
        re.compile(r'Dep[oó]sito en efectivo', re.IGNORECASE),
        TipoProceso.VENTA_EFECTIVO,
        '055003730017', None,
    ),

    # --- Traspasos ---
    # NOTA: "(BE) Traspaso a cuenta" y "(NB) Recepcion de cuenta" se manejan
    # en _clasificar_uno() porque requieren extraer la cuenta del texto y
    # verificar si es propia (TRASPASO) o ajena (PAGO_PROVEEDOR / COBRO_CLIENTE).

    # --- Comisiones bancarias (IVA ANTES que base para evitar match parcial) ---
    (
        re.compile(r'IVA de Comisi[oó]n Transfer', re.IGNORECASE),
        TipoProceso.COMISION_SPEI_IVA,
        None, None,
    ),
    (
        re.compile(r'Comisi[oó]n Transferencia', re.IGNORECASE),
        TipoProceso.COMISION_SPEI,
        None, None,
    ),
    (
        re.compile(r'IVA Aplicaci[oó]n de Tasas', re.IGNORECASE),
        TipoProceso.COMISION_TDC_IVA,
        '038900320016', None,
    ),
    (
        re.compile(r'Aplicaci[oó]n de Tasas de Descuento', re.IGNORECASE),
        TipoProceso.COMISION_TDC,
        '038900320016', None,
    ),

    # --- Nomina ---
    (
        re.compile(r'NOMINA.*PAGO DE NOMINA', re.IGNORECASE),
        TipoProceso.NOMINA,
        None, None,
    ),

    # --- Impuestos federales (pago referenciado SAT) ---
    (
        re.compile(r'\(BE\)\s*Pago servicio.*PAGO REFERENCIADO', re.IGNORECASE),
        TipoProceso.IMPUESTO_FEDERAL,
        '055003730017', None,
    ),

    # --- Impuesto estatal (SPEI a Secretaria de Finanzas NL) ---
    (
        re.compile(r'SECRETARIA DE FINANZAS', re.IGNORECASE),
        TipoProceso.IMPUESTO_ESTATAL,
        None, None,
    ),

    # --- IMSS/INFONAVIT (pago SUA/SIPARE) ---
    (
        re.compile(r'\(BE\)\s*Pago servicio.*PAGO SUA', re.IGNORECASE),
        TipoProceso.IMPUESTO_IMSS,
        '055003730017', None,
    ),

    # --- Cobros SPEI de clientes (ingresos en cuenta cheques) ---
    (
        re.compile(r'[A-Z0-9]{5,}.*SPEI', re.IGNORECASE),
        TipoProceso.COBRO_CLIENTE,
        '055003730017', True,
    ),

    # --- Pagos SPEI a proveedores (egresos en cuenta cheques) ---
    (
        re.compile(r'[A-Z0-9]{5,}.*SPEI', re.IGNORECASE),
        TipoProceso.PAGO_PROVEEDOR,
        '055003730017', False,
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


_RE_TRASPASO_EGRESO = re.compile(
    r'\(BE\)\s*Traspaso a cuenta:\s*(\d+)', re.IGNORECASE,
)
_RE_RECEPCION = re.compile(
    r'\(NB\)\s*Recepci[oó]n de cuenta:\s*(\d+)', re.IGNORECASE,
)


def _es_cuenta_propia(numero: str) -> bool:
    """Verifica si un numero de cuenta o CLABE corresponde a cuenta propia.

    Soporta numero de cuenta directo (12 digitos) o CLABE (18 digitos).
    En CLABE, la cuenta esta en posiciones 6-17 (11 digitos, sin leading zero).
    """
    if numero in CUENTA_POR_NUMERO:
        return True
    # CLABE: 3 banco + 3 sucursal + 11 cuenta + 1 verificador = 18 digitos
    if len(numero) == 18:
        cuenta_clabe = numero[6:17]
        for cuenta_propia in CUENTA_POR_NUMERO:
            # Cuenta propia sin leading zero == porcion CLABE
            if cuenta_propia.lstrip('0') == cuenta_clabe.lstrip('0'):
                return True
    return False


def _clasificar_uno(mov: MovimientoBancario) -> TipoProceso:
    """Clasifica un solo movimiento."""
    # Caso especial: "(BE) Traspaso a cuenta: XXXXXXXXXXX"
    # Si la cuenta destino es propia → TRASPASO (entre cuentas)
    # Si la cuenta destino es ajena → PAGO_PROVEEDOR (solo conciliar)
    m = _RE_TRASPASO_EGRESO.search(mov.descripcion)
    if m:
        cuenta_destino = m.group(1)
        if _es_cuenta_propia(cuenta_destino):
            return TipoProceso.TRASPASO
        return TipoProceso.PAGO_PROVEEDOR

    # Caso especial: "(NB) Recepcion de cuenta: XXXXXXXXXXX"
    # Si la cuenta origen es propia → TRASPASO_INGRESO
    # Si la cuenta origen es ajena → COBRO_CLIENTE
    m = _RE_RECEPCION.search(mov.descripcion)
    if m:
        cuenta_origen = m.group(1)
        if _es_cuenta_propia(cuenta_origen):
            return TipoProceso.TRASPASO_INGRESO
        return TipoProceso.COBRO_CLIENTE

    for patron, tipo, cuenta_filtro, es_ingreso in PATRONES:
        # Filtrar por cuenta si aplica
        if cuenta_filtro and mov.cuenta_banco != cuenta_filtro:
            continue

        # Filtrar por direccion (ingreso/egreso) si aplica
        if es_ingreso is not None:
            if es_ingreso and not mov.es_ingreso:
                continue
            if not es_ingreso and not mov.es_egreso:
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
