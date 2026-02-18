"""Parser de PDFs de acuses de impuestos (SAT + estatal).

Extrae montos de las declaraciones federales y estatal a partir de
acuses PDF del SAT y formato de pago estatal de Nuevo Leon.
"""

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

from src.models import (
    DatosImpuestoEstatal,
    DatosImpuestoFederal,
    RetencionIVAProveedor,
)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None
    logger.warning("pdfplumber no instalado — parser de impuestos deshabilitado")


# --- Utilidades ---


def _extraer_texto_pdf(ruta: Path) -> Optional[str]:
    """Extrae texto completo de un PDF con pdfplumber."""
    if pdfplumber is None:
        logger.error("pdfplumber no disponible")
        return None
    try:
        with pdfplumber.open(ruta) as pdf:
            textos = []
            for page in pdf.pages:
                texto = page.extract_text()
                if texto:
                    textos.append(texto)
            return '\n'.join(textos)
    except Exception as e:
        logger.error("Error leyendo PDF {}: {}", ruta.name, e)
        return None


def _extraer_texto_paginas(ruta: Path) -> Optional[List[str]]:
    """Extrae texto por pagina de un PDF."""
    if pdfplumber is None:
        logger.error("pdfplumber no disponible")
        return None
    try:
        with pdfplumber.open(ruta) as pdf:
            return [page.extract_text() or '' for page in pdf.pages]
    except Exception as e:
        logger.error("Error leyendo PDF {}: {}", ruta.name, e)
        return None


def _parsear_monto(texto: str) -> Optional[Decimal]:
    """Parsea un monto con comas y punto decimal.

    Acepta formatos: '6,822', '$6,822', '6822', '22,971.00', '$22,971.00'
    """
    if not texto:
        return None
    # Limpiar
    limpio = texto.strip().replace('$', '').replace(' ', '')
    # Si tiene punto decimal, preservar; si no, es entero con comas
    if '.' in limpio:
        limpio = limpio.replace(',', '')
    else:
        limpio = limpio.replace(',', '')
    try:
        return Decimal(limpio)
    except (InvalidOperation, ValueError):
        return None


def _buscar_monto_despues(texto: str, patron: str, flags: int = re.IGNORECASE | re.DOTALL) -> Optional[Decimal]:
    """Busca un patron y extrae el primer numero que sigue."""
    match = re.search(patron + r'\s*\$?([\d,]+(?:\.\d{2})?)', texto, flags)
    if match:
        return _parsear_monto(match.group(1))
    return None


def _extraer_periodo(texto: str) -> Optional[str]:
    """Extrae el periodo de la declaracion (ej: 'ENERO 2026')."""
    meses = (
        'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
        'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE',
    )
    # Buscar en texto sin espacios y con espacios
    for mes in meses:
        # Patron: "Periodo...Enero" + "Ejercicio...2026"
        pat_mes = re.search(mes, texto, re.IGNORECASE)
        if pat_mes:
            # Buscar año cercano
            pat_anio = re.search(r'(?:Ejercicio|20\d{2})', texto[pat_mes.start():], re.IGNORECASE)
            if pat_anio:
                anio_match = re.search(r'(20\d{2})', texto[pat_mes.start():])
                if anio_match:
                    return f"{mes} {anio_match.group(1)}"
    return None


# --- Parser 1a Declaracion Federal (Retenciones + IEPS) ---


def _parsear_acuse_federal_1(texto: str) -> dict:
    """Parsea el acuse de la 1a declaracion federal.

    Extrae: ISR retenciones (honorarios + arrendamiento), IEPS neto, total.

    Nota: pdfplumber extrae el texto con layout desordenado en SAT.
    La descripcion puede venir ANTES o DESPUES de "Conceptodepago".
    Usamos un enfoque por bloques: buscamos cada "Conceptodepago"
    y extraemos el monto del "Cantidadapagar" mas cercano.
    """
    resultado = {
        'isr_ret_honorarios': None,
        'isr_ret_arrendamiento': None,
        'ieps_neto': None,
        'total': None,
        'periodo': None,
    }
    advertencias = []

    # Periodo
    resultado['periodo'] = _extraer_periodo(texto)

    # Texto sin espacios para busqueda de tipo
    texto_limpio = texto.replace(' ', '').upper()

    # Extraer bloques: desde cada Conceptodepago hasta el siguiente
    # Cada bloque tiene: tipo (ISR retenciones, ISR arrendamiento, IEPS) + monto
    bloques = re.split(r'(Conceptodepago\d+:\d+)', texto, flags=re.IGNORECASE)

    for i, bloque in enumerate(bloques):
        if not re.match(r'Conceptodepago\d+:\d+', bloque, re.IGNORECASE):
            continue

        # La descripcion puede estar ANTES (texto_previo) o DESPUES (texto_posterior)
        texto_previo = bloques[i - 1] if i > 0 else ''
        texto_posterior = bloques[i + 1] if i + 1 < len(bloques) else ''

        # Extraer monto SOLO del texto POSTERIOR al marcador (evita montos del bloque previo)
        seccion_monto = bloque + texto_posterior
        match_monto = re.search(
            r'(?:Cantidadapagar|Impuestoacargo)\s*:?\s*([\d,]+(?:\.\d{2})?)',
            seccion_monto, re.IGNORECASE,
        )
        if not match_monto:
            continue
        monto = _parsear_monto(match_monto.group(1))
        if monto is None or monto <= 0:
            continue

        # Identificar tipo usando AMBOS lados (descripcion puede estar antes o despues)
        contexto_tipo = (texto_previo + bloque + texto_posterior).replace(' ', '').upper()
        if 'ISRRETENCIONES' in contexto_tipo and 'SERVICIOSPROFESIONALES' in contexto_tipo:
            resultado['isr_ret_honorarios'] = monto
        elif 'ISRPORPAGOSPORCUENTA' in contexto_tipo or ('ARRENDAMIENTO' in contexto_tipo and 'IEPS' not in (bloque + texto_posterior).replace(' ', '').upper()):
            resultado['isr_ret_arrendamiento'] = monto
        elif 'IEPS' in contexto_tipo and 'ALIMENTOS' in contexto_tipo:
            resultado['ieps_neto'] = monto

    if resultado['isr_ret_honorarios'] is None:
        advertencias.append("No se encontro ISR retenciones por honorarios")
    if resultado['isr_ret_arrendamiento'] is None:
        advertencias.append("No se encontro ISR retenciones por arrendamiento")
    if resultado['ieps_neto'] is None:
        advertencias.append("No se encontro IEPS")

    # Total (linea de captura): "$6,822" en la linea despues de "Importe total"
    match_total = re.search(
        r'\$\s*([\d,]+(?:\.\d{2})?)',
        texto[texto.find('CAPTURA'):] if 'CAPTURA' in texto.upper() else texto,
        re.IGNORECASE,
    )
    if match_total:
        resultado['total'] = _parsear_monto(match_total.group(1))
    else:
        advertencias.append("No se encontro total de 1a declaracion")

    resultado['advertencias'] = advertencias
    return resultado


def _parsear_detalle_ieps(texto: str) -> dict:
    """Parsea el detalle IEPS de la declaracion.

    Extrae: IEPS acumulable (causado), IEPS acreditable.

    Texto real (sin espacios): "TOTALDELIMPUESTOCAUSADODE...DENSIDADCALÓRICA 11,713"
    y "IEPSACREDITABLEPORALIMENTOS...DENSIDADCALÓRICA 10,373"
    """
    resultado = {
        'ieps_acumulable': None,
        'ieps_acreditable': None,
    }
    advertencias = []

    # Buscar lineas que tengan los montos clave
    # El texto real tiene las etiquetas y montos en lineas DISTINTAS:
    #   L86: 'TOTALDELIMPUESTOCAUSADODE'
    #   L87: 'ALIMENTOSNOBÁSICOSCONALTA 11,713'
    # Asi que buscamos la keyword y luego miramos las siguientes lineas para el monto.
    lineas = texto.split('\n')
    for idx, linea in enumerate(lineas):
        limpia = linea.replace(' ', '').upper()

        # IEPS causado (acumulable): keyword en una linea, monto en las siguientes
        if 'TOTALDELIMPUESTOCAUSADO' in limpia and resultado['ieps_acumulable'] is None:
            # Buscar monto en esta linea y las 3 siguientes
            for ahead in range(0, 4):
                if idx + ahead >= len(lineas):
                    break
                match = re.search(r'([\d,]+(?:\.\d{2})?)\s*$', lineas[idx + ahead].strip())
                if match:
                    val = _parsear_monto(match.group(1))
                    if val and val > 0:
                        resultado['ieps_acumulable'] = val
                        break

        # IEPS acreditable: keyword en una linea, monto en las siguientes
        if 'IEPSACREDITABLE' in limpia and 'ALIMENTOS' in limpia and resultado['ieps_acreditable'] is None:
            for ahead in range(0, 4):
                if idx + ahead >= len(lineas):
                    break
                match = re.search(r'([\d,]+(?:\.\d{2})?)\s*$', lineas[idx + ahead].strip())
                if match:
                    val = _parsear_monto(match.group(1))
                    if val and val > 0:
                        resultado['ieps_acreditable'] = val
                        break

    if resultado['ieps_acumulable'] is None:
        advertencias.append("No se encontro IEPS causado (acumulable)")
    if resultado['ieps_acreditable'] is None:
        advertencias.append("No se encontro IEPS acreditable")

    resultado['advertencias'] = advertencias
    return resultado


# --- Parser 2a Declaracion Federal (ISR + IVA) ---


def _parsear_acuse_federal_2(texto: str) -> dict:
    """Parsea el acuse de la 2a declaracion federal.

    Extrae: ISR personas morales, ISR retenciones salarios,
    IVA retenciones total, total.

    Texto real del acuse 2a:
    "Conceptodepago1: ISRpersonasmorales" → "Acargo: 17,060"
    "Conceptodepago2: ISRretencionesporsalarios" → "Acargo: 12,168"
    "Conceptodepago3: ImpuestoalValorAgregado.Personasmorales" → "Afavor: 115,864"
    "Conceptodepago4: IVAretenciones" → "Acargo: 5,780"
    """
    resultado = {
        'isr_personas_morales': None,
        'isr_ret_salarios': None,
        'iva_ret_total': None,
        'total': None,
        'periodo': None,
    }
    advertencias = []

    resultado['periodo'] = _extraer_periodo(texto)

    # Extraer bloques por Conceptodepago
    bloques = re.split(r'(Conceptodepago\d+:)', texto, flags=re.IGNORECASE)

    for i, bloque in enumerate(bloques):
        if not re.match(r'Conceptodepago\d+:', bloque, re.IGNORECASE):
            continue

        # Texto posterior hasta el proximo concepto
        texto_post = bloques[i + 1] if i + 1 < len(bloques) else ''
        contexto = bloque + texto_post
        contexto_limpio = contexto.replace(' ', '').upper()

        # Extraer monto: Acargo o Cantidadapagar
        match_monto = re.search(
            r'(?:Acargo|Cantidadapagar)\s*:?\s*([\d,]+(?:\.\d{2})?)',
            contexto, re.IGNORECASE,
        )
        if not match_monto:
            continue
        monto = _parsear_monto(match_monto.group(1))
        if monto is None:
            continue

        # Identificar tipo
        if 'ISRPERSONASMORALES' in contexto_limpio:
            resultado['isr_personas_morales'] = monto
        elif 'ISRRETENCIONES' in contexto_limpio and 'SALARIOS' in contexto_limpio:
            resultado['isr_ret_salarios'] = monto
        elif 'IVARETENCIONES' in contexto_limpio:
            resultado['iva_ret_total'] = monto

    if resultado['isr_personas_morales'] is None:
        advertencias.append("No se encontro ISR personas morales")
    if resultado['isr_ret_salarios'] is None:
        advertencias.append("No se encontro ISR retenciones salarios")
    if resultado['iva_ret_total'] is None:
        advertencias.append("No se encontro IVA retenciones")

    # Total (linea de captura): "$35,008" en la linea con $
    match_total = re.search(
        r'\$\s*([\d,]+(?:\.\d{2})?)',
        texto[texto.upper().find('CAPTURA'):] if 'CAPTURA' in texto.upper() else texto,
        re.IGNORECASE,
    )
    if match_total:
        resultado['total'] = _parsear_monto(match_total.group(1))
    else:
        advertencias.append("No se encontro total de 2a declaracion")

    resultado['advertencias'] = advertencias
    return resultado


def _parsear_declaracion_iva(texto: str) -> dict:
    """Parsea la declaracion completa para obtener montos brutos de IVA.

    Extrae: IVA acumulable (a cargo), IVA acreditable, IVA a favor,
    y tabla de retenciones IVA por proveedor.

    Texto real por linea:
    "TOTALDEIVAACARGO 46,399"
    "TOTALDEIVAACREDITABLE 162,263"
    "SALDOAFAVOR 115,864"
    "IMPUESTOAFAVOR 115,864"
    """
    resultado = {
        'iva_acumulable': None,
        'iva_acreditable': None,
        'iva_a_favor': None,
        'retenciones_iva': [],
    }
    advertencias = []

    for linea in texto.split('\n'):
        limpia = linea.replace(' ', '').upper()
        monto_match = re.search(r'([\d,]+(?:\.\d{2})?)\s*$', linea.strip())
        if not monto_match:
            continue
        monto = _parsear_monto(monto_match.group(1))
        if monto is None or monto <= 0:
            continue

        # IVA a cargo (acumulable)
        # "TOTALDEIVAACARGO 46,399" en la seccion DETERMINACION (pagina 17-18)
        if 'TOTALDEIVAACARGO' in limpia and resultado['iva_acumulable'] is None:
            resultado['iva_acumulable'] = monto

        # IVA acreditable
        # "TOTALDEIVAACREDITABLE 162,263" (puede aparecer varias veces, tomar la ultima)
        if 'TOTALDEIVAACREDITABLE' in limpia:
            resultado['iva_acreditable'] = monto

        # Saldo a favor / Impuesto a favor
        if ('SALDOAFAVOR' in limpia or 'IMPUESTOAFAVOR' in limpia) and resultado['iva_a_favor'] is None:
            resultado['iva_a_favor'] = monto

    if resultado['iva_acumulable'] is None:
        advertencias.append("No se encontro IVA a cargo (acumulable)")
    if resultado['iva_acreditable'] is None:
        advertencias.append("No se encontro IVA acreditable")
    if resultado['iva_a_favor'] is None:
        resultado['iva_a_favor'] = Decimal('0')

    # Retenciones IVA por proveedor
    resultado['retenciones_iva'] = _parsear_tabla_retenciones_iva(texto)
    if not resultado['retenciones_iva']:
        advertencias.append("No se encontro tabla de retenciones IVA por proveedor")

    resultado['advertencias'] = advertencias
    return resultado


def _parsear_tabla_retenciones_iva(texto: str) -> List[RetencionIVAProveedor]:
    """Extrae la tabla de retenciones IVA por proveedor del detalle de la declaracion.

    Texto real (pagina 20 del ejemplo):
    "1 SERVICIOSDE 3,861 618 154"
    "  AUTOTRANSPORTE"
    "  TERRESTREDE"
    "  BIENES"
    "2 SERVICIOS 3,147 504 336"
    "  PERSONALES"
    "  INDEPENDIENTES"
    "3 USOOGOCE 49,593 7,935 5,290"
    "  TEMPORALDE"
    "  BIENESOTORGADO"
    "Total 56,601 9,057 5,780"
    """
    retenciones = []

    # Buscar seccion de IVA retenciones
    idx_inicio = texto.upper().find('IVARETENCIONES')
    if idx_inicio == -1:
        idx_inicio = texto.upper().find('IVA RETENCIONES')
    if idx_inicio == -1:
        return retenciones

    seccion = texto[idx_inicio:]

    # Buscar la tabla DETERMINACION dentro de la seccion de IVA retenciones
    idx_det = seccion.upper().find('DETERMINACI')
    if idx_det >= 0:
        seccion = seccion[idx_det:]

    # Mapeo de actividades a proveedores conocidos
    mapeo_proveedores = {
        'AUTOTRANSPORTE': ('001640', 'SERVICIOS AUTOTRANSPORTE'),
        'PERSONALES': ('001352', 'SERVICIOS PERSONALES INDEPENDIENTES'),
        'GOCE': ('001513', 'USO O GOCE TEMPORAL DE BIENES'),
        'TEMPORAL': ('001513', 'USO O GOCE TEMPORAL DE BIENES'),
        'ARRENDAMIENTO': ('001513', 'ARRENDAMIENTO'),
    }

    # Buscar lineas con patron: consecutivo + texto + 3 montos (contraprestacion, IVA trasl, IVA ret)
    # Texto real:
    #   L14: '1 SERVICIOSDE 3,861 618 154'
    #   L15: 'AUTOTRANSPORTE'           ← continuacion de actividad
    #   L16: 'TERRESTREDE'
    #   L17: 'BIENES'
    #   L18: '2 SERVICIOS 3,147 504 336'
    lineas_seccion = seccion.split('\n')
    filas_parseadas = []

    for idx_l, linea_s in enumerate(lineas_seccion):
        match_fila = re.match(
            r'^(\d+)\s+(.+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)',
            linea_s,
        )
        if match_fila:
            consecutivo = match_fila.group(1)
            actividad = match_fila.group(2).strip()
            iva_ret = match_fila.group(5)
            # Juntar lineas de continuacion (lineas sin numero al inicio, hasta la siguiente fila o "Total")
            for ahead_l in range(idx_l + 1, min(idx_l + 6, len(lineas_seccion))):
                linea_cont = lineas_seccion[ahead_l].strip()
                if re.match(r'^(\d+)\s+', linea_cont) or linea_cont.startswith('Total'):
                    break
                actividad += ' ' + linea_cont
            filas_parseadas.append((consecutivo, actividad, iva_ret))

    for consecutivo, actividad, iva_ret in filas_parseadas:
        monto_retenido = _parsear_monto(iva_ret)
        if monto_retenido is None or monto_retenido <= 0:
            continue

        # Identificar proveedor por actividad completa
        proveedor = '000000'
        nombre = actividad.strip()
        actividad_upper = actividad.upper()
        for clave, (prov_code, prov_nombre) in mapeo_proveedores.items():
            if clave in actividad_upper:
                proveedor = prov_code
                nombre = prov_nombre
                break

        retenciones.append(RetencionIVAProveedor(
            proveedor=proveedor,
            nombre=nombre,
            monto=monto_retenido,
        ))

    return retenciones


# --- Parser Estatal ---


def _parsear_estatal_3pct(texto: str) -> dict:
    """Parsea el formato de pago estatal 3% sobre nominas."""
    resultado = {
        'monto': None,
        'periodo': None,
    }
    advertencias = []

    # Monto a pagar
    # Texto: "Montoapagar: $22,971.00"
    match_monto = re.search(
        r'Monto\s*a\s*pagar\s*:?\s*\$?([\d,]+(?:\.\d{2})?)',
        texto, re.IGNORECASE,
    )
    if match_monto:
        resultado['monto'] = _parsear_monto(match_monto.group(1))
    else:
        advertencias.append("No se encontro monto a pagar en formato estatal")

    # Periodo
    resultado['periodo'] = _extraer_periodo(texto)

    # Verificar que es impuesto sobre nomina
    if 'ImpuestosobreNomina' not in texto.replace(' ', '') and \
       'Impuesto sobre Nomina' not in texto:
        advertencias.append("No se confirma que sea Impuesto sobre Nomina")

    resultado['advertencias'] = advertencias
    return resultado


# --- Funciones publicas ---


def parsear_impuesto_federal(
    ruta_acuse_1: Path,
    ruta_acuse_2: Path,
    ruta_detalle_ieps: Optional[Path] = None,
    ruta_declaracion_completa: Optional[Path] = None,
) -> Optional[DatosImpuestoFederal]:
    """Parsea los PDFs de declaraciones federales y retorna datos estructurados.

    Args:
        ruta_acuse_1: Acuse de 1a declaracion (retenciones + IEPS).
        ruta_acuse_2: Acuse de 2a declaracion (ISR + IVA).
        ruta_detalle_ieps: Detalle de la 1a declaracion (montos brutos IEPS).
        ruta_declaracion_completa: Declaracion completa 2a (IVA brutos + retenciones por proveedor).

    Returns:
        DatosImpuestoFederal o None si no se pudo parsear nada.
    """
    advertencias = []

    # --- 1a Declaracion ---
    texto_1 = _extraer_texto_pdf(ruta_acuse_1)
    if texto_1 is None:
        logger.error("No se pudo leer acuse 1a declaracion: {}", ruta_acuse_1)
        return None

    datos_1 = _parsear_acuse_federal_1(texto_1)
    advertencias.extend(datos_1.get('advertencias', []))

    # Detalle IEPS (opcional)
    ieps_acumulable = Decimal('0')
    ieps_acreditable = Decimal('0')
    if ruta_detalle_ieps and ruta_detalle_ieps.exists():
        texto_ieps = _extraer_texto_pdf(ruta_detalle_ieps)
        if texto_ieps:
            datos_ieps = _parsear_detalle_ieps(texto_ieps)
            advertencias.extend(datos_ieps.get('advertencias', []))
            ieps_acumulable = datos_ieps.get('ieps_acumulable') or Decimal('0')
            ieps_acreditable = datos_ieps.get('ieps_acreditable') or Decimal('0')
        else:
            advertencias.append("No se pudo leer detalle IEPS")
    else:
        advertencias.append("Sin detalle IEPS — montos brutos no disponibles")

    # --- 2a Declaracion ---
    texto_2 = _extraer_texto_pdf(ruta_acuse_2)
    if texto_2 is None:
        logger.error("No se pudo leer acuse 2a declaracion: {}", ruta_acuse_2)
        return None

    datos_2 = _parsear_acuse_federal_2(texto_2)
    advertencias.extend(datos_2.get('advertencias', []))

    # Declaracion completa (IVA brutos + retenciones por proveedor)
    iva_acumulable = Decimal('0')
    iva_acreditable = Decimal('0')
    iva_a_favor = Decimal('0')
    retenciones_iva: List[RetencionIVAProveedor] = []

    if ruta_declaracion_completa and ruta_declaracion_completa.exists():
        texto_decl = _extraer_texto_pdf(ruta_declaracion_completa)
        if texto_decl:
            datos_iva = _parsear_declaracion_iva(texto_decl)
            advertencias.extend(datos_iva.get('advertencias', []))
            iva_acumulable = datos_iva.get('iva_acumulable') or Decimal('0')
            iva_acreditable = datos_iva.get('iva_acreditable') or Decimal('0')
            iva_a_favor = datos_iva.get('iva_a_favor') or Decimal('0')
            retenciones_iva = datos_iva.get('retenciones_iva', [])
        else:
            advertencias.append("No se pudo leer declaracion completa")
    else:
        advertencias.append("Sin declaracion completa — montos IVA brutos y retenciones por proveedor no disponibles")

    # --- Construir resultado ---
    isr_ret_honorarios = datos_1.get('isr_ret_honorarios') or Decimal('0')
    isr_ret_arrendamiento = datos_1.get('isr_ret_arrendamiento') or Decimal('0')
    ieps_neto = datos_1.get('ieps_neto') or Decimal('0')
    total_primera = datos_1.get('total') or Decimal('0')

    isr_personas_morales = datos_2.get('isr_personas_morales') or Decimal('0')
    isr_ret_salarios = datos_2.get('isr_ret_salarios') or Decimal('0')
    iva_ret_total = datos_2.get('iva_ret_total') or Decimal('0')
    total_segunda = datos_2.get('total') or Decimal('0')

    periodo = datos_1.get('periodo') or datos_2.get('periodo') or 'DESCONOCIDO'

    # --- Validacion cruzada ---
    confianza_100 = True

    # Validar total 1a
    suma_1a = isr_ret_honorarios + isr_ret_arrendamiento + ieps_neto
    if total_primera > 0 and suma_1a != total_primera:
        advertencias.append(
            f"Suma conceptos 1a ({suma_1a}) != total acuse ({total_primera})"
        )
        confianza_100 = False

    # Validar total 2a
    suma_2a = isr_personas_morales + isr_ret_salarios + iva_ret_total
    if total_segunda > 0 and suma_2a != total_segunda:
        advertencias.append(
            f"Suma conceptos 2a ({suma_2a}) != total acuse ({total_segunda})"
        )
        confianza_100 = False

    # Validar retenciones IVA total vs acuse
    if retenciones_iva and iva_ret_total > 0:
        suma_ret = sum(r.monto for r in retenciones_iva)
        if suma_ret != iva_ret_total:
            advertencias.append(
                f"Suma retenciones IVA ({suma_ret}) != total acuse ({iva_ret_total})"
            )
            confianza_100 = False

    # Validar IEPS brutos
    if ieps_acumulable > 0 and ieps_acreditable > 0:
        if ieps_acumulable - ieps_acreditable != ieps_neto:
            advertencias.append(
                f"IEPS acumulable ({ieps_acumulable}) - acreditable ({ieps_acreditable}) "
                f"!= neto ({ieps_neto})"
            )
            confianza_100 = False
    elif ieps_neto > 0:
        # Sin detalle IEPS no podemos generar reclasificacion
        confianza_100 = False

    # Validar IVA brutos
    if iva_acumulable > 0 and iva_acreditable > 0:
        diferencia_iva = iva_acreditable - iva_acumulable
        if iva_a_favor > 0 and diferencia_iva != iva_a_favor:
            advertencias.append(
                f"IVA acreditable ({iva_acreditable}) - acumulable ({iva_acumulable}) "
                f"!= a favor ({iva_a_favor})"
            )
            confianza_100 = False
    elif isr_personas_morales > 0:
        # Sin declaracion completa no podemos generar reclasificacion IVA
        confianza_100 = False

    # Campos obligatorios
    campos_requeridos = [
        ('isr_ret_honorarios', isr_ret_honorarios),
        ('isr_ret_arrendamiento', isr_ret_arrendamiento),
        ('isr_personas_morales', isr_personas_morales),
        ('isr_ret_salarios', isr_ret_salarios),
        ('total_primera', total_primera),
        ('total_segunda', total_segunda),
    ]
    for nombre, valor in campos_requeridos:
        if valor <= 0:
            advertencias.append(f"Campo {nombre} no parseado o es 0")
            confianza_100 = False

    resultado = DatosImpuestoFederal(
        periodo=periodo,
        isr_ret_honorarios=isr_ret_honorarios,
        isr_ret_arrendamiento=isr_ret_arrendamiento,
        ieps_neto=ieps_neto,
        ieps_acumulable=ieps_acumulable,
        ieps_acreditable=ieps_acreditable,
        total_primera=total_primera,
        isr_personas_morales=isr_personas_morales,
        isr_ret_salarios=isr_ret_salarios,
        iva_acumulable=iva_acumulable,
        iva_acreditable=iva_acreditable,
        iva_a_favor=iva_a_favor,
        retenciones_iva=retenciones_iva,
        total_segunda=total_segunda,
        confianza_100=confianza_100,
        advertencias=advertencias,
    )

    logger.info(
        "Impuesto federal parseado: periodo={}, total_1a=${:,.0f}, total_2a=${:,.0f}, confianza={}",
        periodo, total_primera, total_segunda, confianza_100,
    )
    if advertencias:
        for adv in advertencias:
            logger.warning("  Advertencia: {}", adv)

    return resultado


def _extraer_periodo_de_nombre(nombre_archivo: str) -> Optional[str]:
    """Intenta extraer el periodo del nombre del archivo.

    Ej: '3% SN Enero 2026.pdf' → 'ENERO 2026'
    """
    meses = (
        'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
        'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE',
    )
    nombre_upper = nombre_archivo.upper()
    for mes in meses:
        if mes in nombre_upper:
            anio_match = re.search(r'(20\d{2})', nombre_archivo)
            if anio_match:
                return f"{mes} {anio_match.group(1)}"
    return None


def parsear_impuesto_estatal(ruta_pdf: Path) -> Optional[DatosImpuestoEstatal]:
    """Parsea el formato de pago estatal 3% sobre nominas.

    Args:
        ruta_pdf: Ruta al PDF del formato de pago estatal.

    Returns:
        DatosImpuestoEstatal o None si no se pudo parsear.
    """
    texto = _extraer_texto_pdf(ruta_pdf)
    if texto is None:
        logger.error("No se pudo leer PDF estatal: {}", ruta_pdf)
        return None

    datos = _parsear_estatal_3pct(texto)
    advertencias = datos.get('advertencias', [])

    monto = datos.get('monto')
    if monto is None or monto <= 0:
        advertencias.append("Monto estatal no parseado o es 0")
        return DatosImpuestoEstatal(
            periodo=datos.get('periodo') or 'DESCONOCIDO',
            monto=Decimal('0'),
            confianza_100=False,
            advertencias=advertencias,
        )

    # Periodo: intentar del texto, luego del nombre del archivo
    periodo = datos.get('periodo')
    if not periodo:
        periodo = _extraer_periodo_de_nombre(ruta_pdf.name)
    if not periodo:
        periodo = 'DESCONOCIDO'
        advertencias.append("No se pudo determinar el periodo")

    confianza_100 = len(advertencias) == 0

    resultado = DatosImpuestoEstatal(
        periodo=periodo,
        monto=monto,
        confianza_100=confianza_100,
        advertencias=advertencias,
    )

    logger.info(
        "Impuesto estatal parseado: periodo={}, monto=${:,.2f}, confianza={}",
        periodo, monto, confianza_100,
    )

    return resultado
