"""Demo Agente5: procesa estado de cuenta dia por dia y genera reporte Excel.

Ejecuta contra sandbox (DBSAV71A) y reporta cada linea del estado de cuenta
con su clasificacion, accion tomada, y folio creado/conciliado.

Uso:
    python demo.py --solo-fecha 2026-02-03   # Procesar solo un dia
    python demo.py                            # Procesar todo el mes
    python demo.py --limpiar                  # Limpiar previos y ejecutar
    python demo.py --dry-run                  # Solo mostrar que haria
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from loguru import logger

from config.settings import Settings
from src.erp.sav7_connector import SAV7Connector
from src.models import AccionLinea
from src.orquestador_unificado import procesar_estado_cuenta
from src.reports.reporte_demo import generar_reporte_estado_cuenta

# ---------------------------------------------------------------------------
# Rutas de archivos de entrada
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
DATA = ROOT / 'data' / 'reportes'
CONTEXTO = ROOT / 'contexto'

RUTA_EC = DATA / 'PRUEBA.xlsx'
RUTA_TESORERIA = DATA / 'FEBRERO INGRESOS 2026.xlsx'
RUTA_NOMINA = CONTEXTO / 'listaRaya' / 'NOMINA 03 CHEQUE.xlsx'
RUTA_IMSS = CONTEXTO / 'ConciliacionImssInfonavit' / 'resumen liquidacion_gbl1.pdf'

RUTAS_IMPUESTOS = {
    'ruta_acuse_federal_1': CONTEXTO / 'impuestos' / 'ImpuestoFederal' / 'acusePdf-1011.pdf',
    'ruta_acuse_federal_2': CONTEXTO / 'impuestos' / 'ImpuestoFederal' / 'Acuse.DCM02072238A.38.2026.pdf',
    'ruta_detalle_ieps': CONTEXTO / 'impuestos' / 'ImpuestoFederal' / 'Declaracion.Acuse.0.pdf',
    'ruta_declaracion_completa': CONTEXTO / 'impuestos' / 'ImpuestoFederal' / 'DCM02072238A.38.2026.pdf',
    'ruta_impuesto_estatal': CONTEXTO / 'impuestos' / 'ImpuestoEstatal' / '3% SN Enero 2026.pdf',
}

RUTA_SALIDA = DATA / 'DEMO_REPORTE.xlsx'


# ---------------------------------------------------------------------------
# Resumen de resultados
# ---------------------------------------------------------------------------

def _imprimir_resumen(resultados_lineas: list):
    """Imprime resumen de procesamiento."""
    from collections import Counter

    total = len(resultados_lineas)
    acciones = Counter(rl.accion for rl in resultados_lineas)
    tipos = Counter(rl.tipo_clasificado.value for rl in resultados_lineas)

    total_folios = set()
    for rl in resultados_lineas:
        total_folios.update(rl.folios)

    logger.info("=" * 60)
    logger.info("RESUMEN FINAL")
    logger.info("=" * 60)
    logger.info("Total lineas estado de cuenta: {}", total)
    logger.info("")
    logger.info("Por accion:")
    for accion in AccionLinea:
        conteo = acciones.get(accion, 0)
        if conteo > 0:
            logger.info("  {}: {}", accion.value, conteo)
    logger.info("")
    logger.info("Por clasificacion:")
    for tipo, conteo in sorted(tipos.items()):
        logger.info("  {}: {}", tipo, conteo)
    logger.info("")
    logger.info("Folios unicos creados/conciliados: {}", len(total_folios))


# ---------------------------------------------------------------------------
# Modo --limpiar
# ---------------------------------------------------------------------------

def limpiar_registros_previos(connector: SAV7Connector):
    """Borra registros creados por AGENTE5 en sandbox."""
    cursor_lectura = connector.db.conectar().cursor()

    cursor_lectura.execute("""
        SELECT Folio FROM SAVCheqPM
        WHERE Capturo = 'AGENTE5' AND Age = 2026 AND Mes = 2
    """)
    folios = [row[0] for row in cursor_lectura.fetchall()]

    if not folios:
        logger.info("No hay registros previos de AGENTE5 para limpiar")
        return

    logger.info("Limpiando {} registros de AGENTE5...", len(folios))

    with connector.get_cursor(transaccion=True) as cursor:
        for folio in folios:
            cursor.execute("DELETE FROM SAVCheqPMF WHERE Folio = ?", (folio,))
            cursor.execute(
                "DELETE FROM SAVPoliza WHERE Fuente = 'SAV7-CHEQUES' AND DocFolio = ?",
                (folio,),
            )
            cursor.execute("DELETE FROM SAVCheqPM WHERE Folio = ?", (folio,))

    logger.info("Limpieza completada: {} folios eliminados", len(folios))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Demo Agente5: procesa estado de cuenta y genera reporte Excel',
    )
    parser.add_argument(
        '--limpiar', action='store_true',
        help='Limpiar registros previos de AGENTE5 antes de ejecutar',
    )
    parser.add_argument(
        '--solo-fecha', type=str, default=None,
        help='Procesar solo un dia (formato YYYY-MM-DD)',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Solo mostrar clasificacion y planes, sin ejecutar',
    )
    parser.add_argument(
        '--log-level', default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
    )
    args = parser.parse_args()

    # Configurar logging
    logger.remove()
    logger.add(sys.stderr, level=args.log_level)

    # Parsear solo-fecha
    solo_fecha = None
    if args.solo_fecha:
        try:
            partes = args.solo_fecha.split('-')
            solo_fecha = date(int(partes[0]), int(partes[1]), int(partes[2]))
        except (ValueError, IndexError):
            logger.error("Formato de fecha invalido: {}. Usar YYYY-MM-DD", args.solo_fecha)
            sys.exit(1)

    # Verificar archivo principal
    if not RUTA_EC.exists():
        logger.error("Archivo no encontrado: {}", RUTA_EC)
        sys.exit(1)

    # Conectar
    settings = Settings.from_env()
    connector = SAV7Connector(settings)

    if not connector.test_conexion():
        logger.error("No se pudo conectar a la BD")
        sys.exit(1)

    try:
        if args.limpiar:
            limpiar_registros_previos(connector)

        # Ejecutar procesamiento unificado
        resultados_lineas = procesar_estado_cuenta(
            ruta_estado_cuenta=RUTA_EC,
            ruta_tesoreria=RUTA_TESORERIA if RUTA_TESORERIA.exists() else None,
            ruta_nomina=RUTA_NOMINA if RUTA_NOMINA.exists() else None,
            ruta_imss=RUTA_IMSS if RUTA_IMSS.exists() else None,
            rutas_impuestos=RUTAS_IMPUESTOS,
            dry_run=args.dry_run,
            solo_fecha=solo_fecha,
            connector=connector,
        )

        # Filtrar lineas al dia solicitado (si aplica)
        if solo_fecha:
            resultados_lineas = [
                rl for rl in resultados_lineas
                if rl.movimiento.fecha == solo_fecha
            ]

        # Resumen
        _imprimir_resumen(resultados_lineas)

        # Generar reporte Excel
        logger.info("Generando reporte Excel...")
        generar_reporte_estado_cuenta(
            connector, resultados_lineas, RUTA_SALIDA,
        )
        logger.info("Reporte: {}", RUTA_SALIDA)

    finally:
        connector.desconectar()


if __name__ == '__main__':
    main()
