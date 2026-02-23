"""Dashboard Streamlit para Conciliacion Bancaria — Agente5.

Interfaz visual para gestion de archivos, ejecucion de conciliacion
y visualizacion de resultados. NO modifica logica existente del Agente5,
solo consume la API de procesar_estado_cuenta().

Uso:
    streamlit run app.py
"""

import io
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import streamlit as st
from dotenv import load_dotenv
from loguru import logger

from config.settings import Settings
from src.erp.sav7_connector import SAV7Connector
from src.models import AccionLinea, ResultadoLinea
from src.orquestador_unificado import procesar_estado_cuenta
from src.reports.reporte_demo import generar_reporte_estado_cuenta

load_dotenv()

# ---------------------------------------------------------------------------
# Configuracion de pagina
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Conciliacion Bancaria — Agente5",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Categorias de archivos
# ---------------------------------------------------------------------------

@dataclass
class CategoriaArchivo:
    """Define una categoria de archivo de entrada."""
    id: str
    nombre: str
    extensiones: Tuple[str, ...]
    obligatorio: bool
    multiple: bool
    descripcion: str


CATEGORIAS: List[CategoriaArchivo] = [
    CategoriaArchivo(
        id='01_Estado_de_Cuenta',
        nombre='Estado de Cuenta',
        extensiones=('.xlsx',),
        obligatorio=True,
        multiple=False,
        descripcion='Archivo Excel de BANREGIO con hojas Banregio F, Banregio T, BANREGIO GTS',
    ),
    CategoriaArchivo(
        id='02_Tesoreria',
        nombre='Tesoreria (Ingresos)',
        extensiones=('.xlsx',),
        obligatorio=True,
        multiple=False,
        descripcion='Reporte de ingresos con una hoja por dia (cortes Z, facturas, depositos)',
    ),
    CategoriaArchivo(
        id='03_Nomina',
        nombre='Nomina',
        extensiones=('.xlsx',),
        obligatorio=False,
        multiple=False,
        descripcion='Excel CONTPAQi con hojas NOM, DISPERCION, CHEQUE',
    ),
    CategoriaArchivo(
        id='04_Lista_de_Raya',
        nombre='Lista de Raya',
        extensiones=('.pdf',),
        obligatorio=False,
        multiple=False,
        descripcion='PDF resumen de nomina CONTPAQi',
    ),
    CategoriaArchivo(
        id='05_IMSS',
        nombre='IMSS / INFONAVIT',
        extensiones=('.pdf',),
        obligatorio=False,
        multiple=False,
        descripcion='PDF de resumen de liquidacion IMSS/INFONAVIT',
    ),
    CategoriaArchivo(
        id='06_Impuesto_Federal',
        nombre='Impuesto Federal',
        extensiones=('.pdf',),
        obligatorio=False,
        multiple=True,
        descripcion='PDFs de declaraciones federales (acuses, declaracion completa, detalle IEPS)',
    ),
    CategoriaArchivo(
        id='07_Impuesto_Estatal',
        nombre='Impuesto Estatal',
        extensiones=('.pdf',),
        obligatorio=False,
        multiple=False,
        descripcion='PDF de declaracion 3% sobre nominas',
    ),
]


# ---------------------------------------------------------------------------
# Funciones de infraestructura de archivos
# ---------------------------------------------------------------------------

def obtener_ruta_base() -> Path:
    """Obtiene la ruta base desde variable de entorno."""
    ruta = os.getenv('RUTA_ARCHIVOS', r'\\SERVERMC\Asesoft\ImplementacionIA')
    return Path(ruta)


def obtener_ruta_periodo(periodo: str) -> Path:
    """Retorna la ruta del periodo (ej: 2026-02)."""
    return obtener_ruta_base() / 'data' / periodo


def crear_estructura_periodo(periodo: str) -> Path:
    """Crea todas las carpetas del periodo si no existen."""
    ruta_periodo = obtener_ruta_periodo(periodo)

    for cat in CATEGORIAS:
        (ruta_periodo / cat.id / 'entrada').mkdir(parents=True, exist_ok=True)
        (ruta_periodo / cat.id / 'procesados').mkdir(parents=True, exist_ok=True)

    (ruta_periodo / '09_Reportes').mkdir(parents=True, exist_ok=True)
    (ruta_periodo / 'logs').mkdir(parents=True, exist_ok=True)

    return ruta_periodo


def listar_archivos_entrada(periodo: str, categoria: CategoriaArchivo) -> List[Path]:
    """Lista archivos validos en la carpeta entrada/ de una categoria."""
    carpeta = obtener_ruta_periodo(periodo) / categoria.id / 'entrada'
    if not carpeta.exists():
        return []
    archivos = []
    for ext in categoria.extensiones:
        archivos.extend(carpeta.glob(f'*{ext}'))
        archivos.extend(carpeta.glob(f'*{ext.upper()}'))
    # Deduplicar (en caso de sistemas case-insensitive)
    vistos = set()
    unicos = []
    for a in sorted(archivos, key=lambda p: p.name):
        if a.name.lower() not in vistos:
            vistos.add(a.name.lower())
            unicos.append(a)
    return unicos


def guardar_archivo_subido(periodo: str, categoria: CategoriaArchivo,
                           archivo_subido) -> Path:
    """Guarda un archivo subido via Streamlit en la carpeta entrada/."""
    carpeta = obtener_ruta_periodo(periodo) / categoria.id / 'entrada'
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / archivo_subido.name
    with open(destino, 'wb') as f:
        f.write(archivo_subido.getbuffer())
    return destino


def eliminar_archivo(ruta: Path):
    """Elimina un archivo de entrada."""
    if ruta.exists():
        ruta.unlink()


def _buscar_archivo(carpeta: Path, extension: str) -> Optional[Path]:
    """Busca el primer archivo con la extension dada en la carpeta."""
    if not carpeta.exists():
        return None
    archivos = list(carpeta.glob(f'*{extension}'))
    archivos.extend(carpeta.glob(f'*{extension.upper()}'))
    if archivos:
        return sorted(archivos, key=lambda p: p.name)[0]
    return None


# ---------------------------------------------------------------------------
# Ajustes de impuestos (formulario en la app)
# ---------------------------------------------------------------------------

def _ruta_ajustes_json(periodo: str) -> Path:
    """Ruta del JSON de ajustes de impuestos del periodo."""
    return obtener_ruta_periodo(periodo) / 'ajustes_impuestos.json'


def cargar_ajustes(periodo: str) -> Dict:
    """Carga ajustes de impuestos desde JSON del periodo."""
    ruta = _ruta_ajustes_json(periodo)
    if ruta.exists():
        with open(ruta, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def guardar_ajustes(periodo: str, ajustes: Dict):
    """Guarda ajustes de impuestos en JSON del periodo."""
    ruta = _ruta_ajustes_json(periodo)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, 'w', encoding='utf-8') as f:
        json.dump(ajustes, f, indent=2, ensure_ascii=False)


def _generar_excel_ajustes(ajustes: Dict, destino: Path):
    """Genera AJUSTES_IMPUESTOS.xlsx desde los valores del formulario.

    Formato compatible con src/entrada/ajustes_impuestos.py:
    B2=IMSS, B3=IVA Acumulable, B4=IVA Acreditable, filas 7+=retenciones.
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active

    # Headers
    ws.cell(row=1, column=1, value='Concepto')
    ws.cell(row=1, column=2, value='Importe')

    # Montos fijos
    ws.cell(row=2, column=1, value='IMSS')
    if ajustes.get('total_imss'):
        ws.cell(row=2, column=2, value=float(ajustes['total_imss']))

    ws.cell(row=3, column=1, value='IVA Acumulable')
    if ajustes.get('iva_acumulable'):
        ws.cell(row=3, column=2, value=float(ajustes['iva_acumulable']))

    ws.cell(row=4, column=1, value='IVA Acreditable')
    if ajustes.get('iva_acreditable'):
        ws.cell(row=4, column=2, value=float(ajustes['iva_acreditable']))

    # Retenciones IVA
    retenciones = ajustes.get('retenciones_iva', [])
    if retenciones:
        ws.cell(row=6, column=1, value='Proveedor')
        ws.cell(row=6, column=2, value='Nombre')
        ws.cell(row=6, column=3, value='Monto')
        for i, ret in enumerate(retenciones):
            fila = 7 + i
            ws.cell(row=fila, column=1, value=ret.get('proveedor', ''))
            ws.cell(row=fila, column=2, value=ret.get('nombre', ''))
            if ret.get('monto'):
                ws.cell(row=fila, column=3, value=float(ret['monto']))

    destino.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(destino))


# ---------------------------------------------------------------------------
# Mapeo de archivos a parametros de la API
# ---------------------------------------------------------------------------

def construir_parametros_api(periodo: str) -> Dict:
    """Mapea archivos en carpetas a parametros de procesar_estado_cuenta()."""
    ruta = obtener_ruta_periodo(periodo)
    params = {}

    # Estado de cuenta (obligatorio)
    ec = _buscar_archivo(ruta / '01_Estado_de_Cuenta' / 'entrada', '.xlsx')
    if ec:
        params['ruta_estado_cuenta'] = ec

    # Tesoreria
    tes = _buscar_archivo(ruta / '02_Tesoreria' / 'entrada', '.xlsx')
    if tes:
        params['ruta_tesoreria'] = tes

    # Nomina
    nom = _buscar_archivo(ruta / '03_Nomina' / 'entrada', '.xlsx')
    if nom:
        params['ruta_nomina'] = nom

    # Lista de raya
    lr = _buscar_archivo(ruta / '04_Lista_de_Raya' / 'entrada', '.pdf')
    if lr:
        params['ruta_lista_raya'] = lr

    # IMSS
    imss = _buscar_archivo(ruta / '05_IMSS' / 'entrada', '.pdf')
    if imss:
        params['ruta_imss'] = imss

    # Impuestos federales (multiples PDFs)
    rutas_imp = {}
    federal_dir = ruta / '06_Impuesto_Federal' / 'entrada'
    if federal_dir.exists():
        pdfs = sorted(federal_dir.glob('*.pdf')) + sorted(federal_dir.glob('*.PDF'))
        for pdf in pdfs:
            nombre = pdf.stem.lower()
            if 'acusepdf' in nombre or 'acuse-1' in nombre:
                rutas_imp['ruta_acuse_federal_1'] = pdf
            elif 'acuse.dcm' in nombre or 'acuse-2' in nombre:
                rutas_imp['ruta_acuse_federal_2'] = pdf
            elif 'declaracion.acuse' in nombre or 'ieps' in nombre:
                rutas_imp['ruta_detalle_ieps'] = pdf
            elif nombre.startswith('dcm') and not 'acuse' in nombre:
                rutas_imp['ruta_declaracion_completa'] = pdf

    # Impuesto estatal
    estatal_dir = ruta / '07_Impuesto_Estatal' / 'entrada'
    if estatal_dir.exists():
        est = _buscar_archivo(estatal_dir, '.pdf')
        if est:
            rutas_imp['ruta_impuesto_estatal'] = est

    if rutas_imp:
        params['rutas_impuestos'] = rutas_imp

    # Ajustes: generar Excel desde formulario para que orquestador lo detecte
    ajustes = cargar_ajustes(periodo)
    if ajustes and 'ruta_estado_cuenta' in params:
        destino = params['ruta_estado_cuenta'].parent / 'AJUSTES_IMPUESTOS.xlsx'
        _generar_excel_ajustes(ajustes, destino)

    return params


# ---------------------------------------------------------------------------
# Conexion a BD (cached)
# ---------------------------------------------------------------------------

@st.cache_resource
def obtener_connector() -> SAV7Connector:
    """Crea y cachea la conexion a BD."""
    settings = Settings.from_env()
    return SAV7Connector(settings)


def verificar_conexion() -> bool:
    """Verifica si la conexion a BD esta activa."""
    try:
        connector = obtener_connector()
        return connector.test_conexion()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Ejecucion de conciliacion
# ---------------------------------------------------------------------------

def ejecutar_conciliacion(
    periodo: str,
    solo_fecha: Optional[date] = None,
    dry_run: bool = True,
) -> Tuple[List[ResultadoLinea], Optional[Path]]:
    """Ejecuta la conciliacion y retorna resultados + ruta de reporte."""
    params = construir_parametros_api(periodo)

    if 'ruta_estado_cuenta' not in params:
        st.error("No se encontro el archivo de Estado de Cuenta en la carpeta de entrada.")
        return [], None

    connector = obtener_connector()

    resultados = procesar_estado_cuenta(
        **params,
        dry_run=dry_run,
        solo_fecha=solo_fecha,
        connector=connector,
    )

    # Filtrar por fecha si aplica
    if solo_fecha:
        resultados = [
            rl for rl in resultados
            if rl.movimiento.fecha == solo_fecha
        ]

    # Generar reporte si no es dry-run
    ruta_reporte = None
    if not dry_run and resultados:
        ruta_periodo = obtener_ruta_periodo(periodo)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        fecha_str = solo_fecha.strftime('%Y-%m-%d') if solo_fecha else 'completo'
        nombre = f'REPORTE_{fecha_str}_{timestamp}.xlsx'
        ruta_reporte = ruta_periodo / '09_Reportes' / nombre
        ruta_reporte.parent.mkdir(parents=True, exist_ok=True)
        generar_reporte_estado_cuenta(connector, resultados, ruta_reporte)

    # Guardar log de ejecucion
    _guardar_log_ejecucion(periodo, solo_fecha, dry_run, resultados, ruta_reporte)

    return resultados, ruta_reporte


def _guardar_log_ejecucion(
    periodo: str,
    solo_fecha: Optional[date],
    dry_run: bool,
    resultados: List[ResultadoLinea],
    ruta_reporte: Optional[Path],
):
    """Guarda un log JSON de la ejecucion."""
    ruta_periodo = obtener_ruta_periodo(periodo)
    logs_dir = ruta_periodo / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    from collections import Counter
    acciones = Counter(rl.accion.value for rl in resultados)

    log = {
        'timestamp': datetime.now().isoformat(),
        'periodo': periodo,
        'solo_fecha': solo_fecha.isoformat() if solo_fecha else None,
        'dry_run': dry_run,
        'total_lineas': len(resultados),
        'acciones': dict(acciones),
        'reporte': str(ruta_reporte) if ruta_reporte else None,
    }

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    modo = 'dryrun' if dry_run else 'ejecucion'
    ruta_log = logs_dir / f'{modo}_{timestamp}.json'
    with open(ruta_log, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Funciones de visualizacion
# ---------------------------------------------------------------------------

def mostrar_resumen_resultados(resultados: List[ResultadoLinea]):
    """Muestra metricas y tabla de resultados."""
    from collections import Counter

    if not resultados:
        st.warning("No hay resultados para mostrar.")
        return

    acciones = Counter(rl.accion for rl in resultados)
    folios = set()
    for rl in resultados:
        folios.update(rl.folios)

    # Metricas principales
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total movimientos", len(resultados))
    col2.metric("INSERT", acciones.get(AccionLinea.INSERT, 0))
    col3.metric("CONCILIAR", acciones.get(AccionLinea.CONCILIAR, 0))
    col4.metric("Folios", len(folios))
    col5.metric("Errores", acciones.get(AccionLinea.ERROR, 0))

    # Tabla detallada
    filas = []
    for rl in resultados:
        mov = rl.movimiento
        filas.append({
            'Fecha': mov.fecha.strftime('%d/%m/%Y'),
            'Cuenta': mov.cuenta_banco,
            'Descripcion': mov.descripcion[:60],
            'Cargo': float(mov.cargo) if mov.cargo else None,
            'Abono': float(mov.abono) if mov.abono else None,
            'Clasificacion': rl.tipo_clasificado.value,
            'Accion': rl.accion.value,
            'Folios': ', '.join(str(f) for f in rl.folios),
            'Nota': rl.nota or '',
        })

    st.dataframe(filas, use_container_width=True, height=400)


# ---------------------------------------------------------------------------
# UI: Sidebar
# ---------------------------------------------------------------------------

def render_sidebar():
    """Renderiza el sidebar con selector de periodo y estado de conexion."""
    with st.sidebar:
        st.title("Agente5")
        st.caption("Conciliacion Bancaria")

        st.divider()

        # Selector de periodo
        hoy = date.today()
        anio = st.number_input("Año", min_value=2024, max_value=2030, value=hoy.year)
        mes = st.number_input("Mes", min_value=1, max_value=12, value=hoy.month)
        periodo = f"{anio}-{mes:02d}"

        st.session_state['periodo'] = periodo

        # Crear estructura automaticamente al seleccionar periodo
        crear_estructura_periodo(periodo)

        st.divider()

        # Estado de conexion
        st.subheader("Base de datos")
        if verificar_conexion():
            st.success("Conectado")
        else:
            st.error("Sin conexion")
            if st.button("Reintentar conexion"):
                st.cache_resource.clear()
                st.rerun()

        st.divider()

        # Info de ruta
        st.caption(f"Ruta base: {obtener_ruta_base()}")
        st.caption(f"Periodo: {periodo}")


# ---------------------------------------------------------------------------
# UI: Tab Archivos
# ---------------------------------------------------------------------------

def render_tab_archivos():
    """Tab de gestion de archivos."""
    periodo = st.session_state.get('periodo', '')
    if not periodo:
        st.info("Seleccione un periodo en el sidebar.")
        return

    st.subheader(f"Archivos del periodo {periodo}")

    for cat in CATEGORIAS:
        archivos = listar_archivos_entrada(periodo, cat)
        tiene_archivo = len(archivos) > 0

        # Icono de estado
        if tiene_archivo:
            icono = "✅"
        elif cat.obligatorio:
            icono = "❌"
        else:
            icono = "⬜"

        with st.expander(f"{icono} {cat.nombre}", expanded=not tiene_archivo and cat.obligatorio):
            st.caption(cat.descripcion)

            # Archivos existentes
            if archivos:
                for arch in archivos:
                    col_nombre, col_accion = st.columns([4, 1])
                    col_nombre.text(f"📄 {arch.name}")
                    if col_accion.button("Eliminar", key=f"del_{cat.id}_{arch.name}"):
                        eliminar_archivo(arch)
                        st.rerun()

            # Uploader
            tipo_ext = cat.extensiones[0].replace('.', '').upper()
            accept = list(cat.extensiones)
            uploaded = st.file_uploader(
                f"Subir {tipo_ext}",
                type=[e.replace('.', '') for e in cat.extensiones],
                accept_multiple_files=cat.multiple,
                key=f"upload_{cat.id}",
            )

            if uploaded:
                archivos_subidos = uploaded if isinstance(uploaded, list) else [uploaded]
                for archivo in archivos_subidos:
                    ruta = guardar_archivo_subido(periodo, cat, archivo)
                    st.success(f"Guardado: {ruta.name}")

    # --- Ajustes de impuestos (formulario) ---
    st.divider()
    _render_formulario_ajustes(periodo)

    # Resumen
    st.divider()
    total = len(CATEGORIAS)
    con_archivo = sum(
        1 for cat in CATEGORIAS
        if listar_archivos_entrada(periodo, cat)
    )
    obligatorios_ok = all(
        listar_archivos_entrada(periodo, cat)
        for cat in CATEGORIAS if cat.obligatorio
    )

    col1, col2 = st.columns(2)
    col1.metric("Archivos cargados", f"{con_archivo} / {total}")
    if obligatorios_ok:
        col2.success("Archivos obligatorios completos")
    else:
        col2.warning("Faltan archivos obligatorios")


def _render_formulario_ajustes(periodo: str):
    """Renderiza formulario de ajustes de impuestos."""
    ajustes = cargar_ajustes(periodo)
    tiene_datos = bool(ajustes.get('total_imss') or ajustes.get('iva_acumulable')
                       or ajustes.get('iva_acreditable') or ajustes.get('retenciones_iva'))
    icono = "✅" if tiene_datos else "⬜"

    with st.expander(f"{icono} Ajustes de Impuestos (opcional)", expanded=False):
        st.caption(
            "Override manual de valores extraidos de los PDFs. "
            "Deje en blanco (0) para usar el valor del PDF."
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            total_imss = st.number_input(
                "IMSS",
                min_value=0.0,
                value=float(ajustes.get('total_imss', 0)),
                step=0.01,
                format="%.2f",
                key="ajuste_imss",
                help="Monto total IMSS/INFONAVIT",
            )
        with col2:
            iva_acumulable = st.number_input(
                "IVA Acumulable",
                min_value=0.0,
                value=float(ajustes.get('iva_acumulable', 0)),
                step=0.01,
                format="%.2f",
                key="ajuste_iva_acum",
                help="IVA acumulable de declaracion federal",
            )
        with col3:
            iva_acreditable = st.number_input(
                "IVA Acreditable",
                min_value=0.0,
                value=float(ajustes.get('iva_acreditable', 0)),
                step=0.01,
                format="%.2f",
                key="ajuste_iva_acred",
                help="IVA acreditable de declaracion federal",
            )

        # Retenciones IVA
        st.markdown("**Retenciones IVA por proveedor**")
        retenciones = ajustes.get('retenciones_iva', [])

        # Inicializar session_state para retenciones
        if 'retenciones_lista' not in st.session_state:
            st.session_state['retenciones_lista'] = retenciones if retenciones else []

        retenciones_editadas = []
        for i, ret in enumerate(st.session_state['retenciones_lista']):
            c1, c2, c3, c4 = st.columns([2, 3, 2, 1])
            with c1:
                prov = st.text_input(
                    "Clave", value=ret.get('proveedor', ''),
                    key=f"ret_prov_{i}", label_visibility="collapsed",
                    placeholder="Clave proveedor",
                )
            with c2:
                nombre = st.text_input(
                    "Nombre", value=ret.get('nombre', ''),
                    key=f"ret_nom_{i}", label_visibility="collapsed",
                    placeholder="Nombre",
                )
            with c3:
                monto = st.number_input(
                    "Monto", value=float(ret.get('monto', 0)),
                    min_value=0.0, step=0.01, format="%.2f",
                    key=f"ret_monto_{i}", label_visibility="collapsed",
                )
            with c4:
                if st.button("🗑️", key=f"ret_del_{i}"):
                    st.session_state['retenciones_lista'].pop(i)
                    st.rerun()
            retenciones_editadas.append({
                'proveedor': prov, 'nombre': nombre, 'monto': monto,
            })

        if st.button("+ Agregar retencion", key="agregar_retencion"):
            st.session_state['retenciones_lista'].append(
                {'proveedor': '', 'nombre': '', 'monto': 0}
            )
            st.rerun()

        # Boton guardar
        if st.button("Guardar ajustes", type="primary", key="guardar_ajustes"):
            nuevos_ajustes = {}
            if total_imss > 0:
                nuevos_ajustes['total_imss'] = total_imss
            if iva_acumulable > 0:
                nuevos_ajustes['iva_acumulable'] = iva_acumulable
            if iva_acreditable > 0:
                nuevos_ajustes['iva_acreditable'] = iva_acreditable
            # Filtrar retenciones con datos
            rets_validas = [
                r for r in retenciones_editadas
                if r.get('proveedor') and r.get('monto', 0) > 0
            ]
            if rets_validas:
                nuevos_ajustes['retenciones_iva'] = rets_validas

            guardar_ajustes(periodo, nuevos_ajustes)
            st.session_state['retenciones_lista'] = rets_validas
            st.success("Ajustes guardados")


# ---------------------------------------------------------------------------
# UI: Tab Procesar
# ---------------------------------------------------------------------------

def render_tab_procesar():
    """Tab de ejecucion de conciliacion."""
    periodo = st.session_state.get('periodo', '')
    if not periodo:
        st.info("Seleccione un periodo en el sidebar.")
        return

    # Verificar archivos obligatorios
    params = construir_parametros_api(periodo)
    if 'ruta_estado_cuenta' not in params:
        st.warning(
            "No se encontro el Estado de Cuenta. "
            "Suba el archivo en la pestana Archivos."
        )
        return

    st.subheader("Archivos detectados")
    nombres_params = {
        'ruta_estado_cuenta': 'Estado de Cuenta',
        'ruta_tesoreria': 'Tesoreria',
        'ruta_nomina': 'Nomina',
        'ruta_lista_raya': 'Lista de Raya',
        'ruta_imss': 'IMSS',
        'rutas_impuestos': 'Impuestos',
    }
    for clave, nombre in nombres_params.items():
        if clave in params:
            valor = params[clave]
            if isinstance(valor, dict):
                st.text(f"  ✅ {nombre}: {len(valor)} archivos")
            else:
                st.text(f"  ✅ {nombre}: {valor.name}")
        else:
            st.text(f"  ⬜ {nombre}: no proporcionado")

    st.divider()

    # Selector de fecha
    st.subheader("Configuracion")
    partes = periodo.split('-')
    anio, mes = int(partes[0]), int(partes[1])

    modo_fecha = st.radio(
        "Procesar",
        ["Dia especifico", "Todo el periodo"],
        horizontal=True,
    )

    solo_fecha = None
    if modo_fecha == "Dia especifico":
        dia = st.number_input("Dia", min_value=1, max_value=31, value=1)
        try:
            solo_fecha = date(anio, mes, dia)
        except ValueError:
            st.error("Fecha invalida")
            return

    st.divider()

    # Botones de ejecucion
    col_preview, col_ejecutar = st.columns(2)

    with col_preview:
        if st.button("Vista previa (dry-run)", type="secondary", use_container_width=True):
            with st.spinner("Ejecutando vista previa..."):
                resultados, _ = ejecutar_conciliacion(
                    periodo, solo_fecha=solo_fecha, dry_run=True,
                )
            st.session_state['ultimos_resultados'] = resultados
            st.session_state['ultimo_modo'] = 'dry-run'

    with col_ejecutar:
        if st.button("Ejecutar conciliacion", type="primary", use_container_width=True):
            if not verificar_conexion():
                st.error("No hay conexion a la base de datos.")
                return
            with st.spinner("Ejecutando conciliacion..."):
                resultados, ruta_reporte = ejecutar_conciliacion(
                    periodo, solo_fecha=solo_fecha, dry_run=False,
                )
            st.session_state['ultimos_resultados'] = resultados
            st.session_state['ultimo_modo'] = 'ejecucion'
            if ruta_reporte:
                st.session_state['ultimo_reporte'] = str(ruta_reporte)

    # Mostrar resultados
    if 'ultimos_resultados' in st.session_state:
        modo = st.session_state.get('ultimo_modo', '')
        st.divider()
        st.subheader(f"Resultados ({modo})")
        mostrar_resumen_resultados(st.session_state['ultimos_resultados'])

        # Descargar reporte
        if 'ultimo_reporte' in st.session_state and modo == 'ejecucion':
            ruta_rep = Path(st.session_state['ultimo_reporte'])
            if ruta_rep.exists():
                with open(ruta_rep, 'rb') as f:
                    st.download_button(
                        "Descargar reporte Excel",
                        data=f.read(),
                        file_name=ruta_rep.name,
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    )


# ---------------------------------------------------------------------------
# UI: Tab Historial
# ---------------------------------------------------------------------------

def render_tab_historial():
    """Tab de historial de ejecuciones."""
    periodo = st.session_state.get('periodo', '')
    if not periodo:
        st.info("Seleccione un periodo en el sidebar.")
        return

    ruta_periodo = obtener_ruta_periodo(periodo)

    # Logs
    st.subheader("Ejecuciones")
    logs_dir = ruta_periodo / 'logs'
    if logs_dir.exists():
        logs = sorted(logs_dir.glob('*.json'), reverse=True)
        if logs:
            for log_file in logs[:20]:  # Ultimos 20
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        log = json.load(f)
                    modo = "DRY-RUN" if log.get('dry_run') else "EJECUCION"
                    ts = log.get('timestamp', '')[:19]
                    fecha = log.get('solo_fecha', 'completo')
                    total = log.get('total_lineas', 0)
                    acciones = log.get('acciones', {})

                    with st.expander(f"{modo} | {ts} | Fecha: {fecha} | {total} lineas"):
                        for accion, conteo in acciones.items():
                            st.text(f"  {accion}: {conteo}")
                        if log.get('reporte'):
                            st.text(f"  Reporte: {log['reporte']}")
                except Exception:
                    continue
        else:
            st.info("No hay ejecuciones registradas.")
    else:
        st.info("No hay ejecuciones registradas.")

    # Reportes
    st.divider()
    st.subheader("Reportes generados")
    reportes_dir = ruta_periodo / '09_Reportes'
    if reportes_dir.exists():
        reportes = sorted(reportes_dir.glob('*.xlsx'), reverse=True)
        if reportes:
            for rep in reportes[:10]:
                col_nombre, col_descarga = st.columns([3, 1])
                col_nombre.text(f"📊 {rep.name}")
                with open(rep, 'rb') as f:
                    col_descarga.download_button(
                        "Descargar",
                        data=f.read(),
                        file_name=rep.name,
                        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        key=f"dl_{rep.name}",
                    )
        else:
            st.info("No hay reportes generados.")
    else:
        st.info("No hay reportes generados.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    render_sidebar()

    tab_archivos, tab_procesar, tab_historial = st.tabs([
        "📁 Archivos",
        "⚙️ Procesar",
        "📋 Historial",
    ])

    with tab_archivos:
        render_tab_archivos()

    with tab_procesar:
        render_tab_procesar()

    with tab_historial:
        render_tab_historial()


if __name__ == '__main__':
    main()
