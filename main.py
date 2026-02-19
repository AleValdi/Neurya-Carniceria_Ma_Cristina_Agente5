"""Agente5 — Automatizacion de Movimientos Bancarios en SAV7.

Punto de entrada CLI para parsear archivos, clasificar movimientos,
y generar/ejecutar planes de insercion en la BD del ERP.

Uso:
    python main.py --test-conexion
    python main.py --parsear estado-cuenta data/reportes/PRUEBA.xlsx
    python main.py --parsear tesoreria "data/reportes/FEBRERO INGRESOS 2026.xlsx"
    python main.py --parsear nomina contexto/listaRaya/NOMINA\ 03\ CHEQUE.xlsx
    python main.py procesar venta-tdc data/reportes/PRUEBA.xlsx "data/reportes/FEBRERO INGRESOS 2026.xlsx"
    python main.py procesar venta-tdc --fecha 2026-02-03 data/reportes/PRUEBA.xlsx "data/reportes/FEBRERO INGRESOS 2026.xlsx"
"""

import argparse
import sys
from pathlib import Path

from loguru import logger


def configurar_logger(nivel: str = 'INFO'):
    """Configura loguru con formato legible."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=nivel,
        format="<green>{time:HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
               "<level>{message}</level>",
    )


def cmd_test_conexion(args):
    """Prueba la conexion a la base de datos."""
    from config.settings import Settings
    from config.database import DatabaseConfig, DatabaseConnection

    settings = Settings.from_env()
    config = DatabaseConfig.from_settings(settings)
    db = DatabaseConnection(config)

    if db.test_conexion():
        print("Conexion exitosa.")
    else:
        print("Error de conexion.")
        sys.exit(1)


def cmd_parsear(args):
    """Parsea un archivo de entrada y muestra resumen."""
    ruta = Path(args.archivo)
    if not ruta.exists():
        logger.error("Archivo no encontrado: {}", ruta)
        sys.exit(1)

    tipo = args.tipo

    if tipo == 'estado-cuenta':
        _parsear_estado_cuenta(ruta)
    elif tipo == 'tesoreria':
        _parsear_tesoreria(ruta)
    elif tipo == 'nomina':
        _parsear_nomina(ruta)
    else:
        logger.error("Tipo no reconocido: {}", tipo)
        sys.exit(1)


def _parsear_estado_cuenta(ruta: Path):
    """Parsea y muestra resumen del estado de cuenta."""
    from src.entrada.estado_cuenta import parsear_estado_cuenta

    resultado = parsear_estado_cuenta(ruta)

    print(f"\n{'='*60}")
    print(f"ESTADO DE CUENTA: {ruta.name}")
    print(f"{'='*60}")

    for hoja, movimientos in resultado.items():
        ingresos = [m for m in movimientos if m.es_ingreso]
        egresos = [m for m in movimientos if m.es_egreso]
        total_ingresos = sum(m.abono for m in ingresos)
        total_egresos = sum(m.cargo for m in egresos)

        print(f"\n  Hoja: {hoja}")
        print(f"  Cuenta: {movimientos[0].cuenta_banco if movimientos else '-'}")
        print(f"  Movimientos: {len(movimientos)}")
        print(f"  Ingresos: {len(ingresos)} (${total_ingresos:,.2f})")
        print(f"  Egresos:  {len(egresos)} (${total_egresos:,.2f})")

        if movimientos:
            print(f"  Rango fechas: {movimientos[0].fecha} → {movimientos[-1].fecha}")

        # Mostrar primeros 5 movimientos como ejemplo
        print(f"  Ejemplo (primeros 5):")
        for m in movimientos[:5]:
            tipo = "+" if m.es_ingreso else "-"
            monto = m.abono if m.es_ingreso else m.cargo
            print(f"    {m.fecha} {tipo}${monto:,.2f} | {m.descripcion[:60]}")

    total_global = sum(len(m) for m in resultado.values())
    print(f"\n  TOTAL: {total_global} movimientos en {len(resultado)} hojas")


def _parsear_tesoreria(ruta: Path):
    """Parsea y muestra resumen del reporte de tesoreria."""
    from src.entrada.tesoreria import parsear_tesoreria

    resultado = parsear_tesoreria(ruta)

    print(f"\n{'='*60}")
    print(f"REPORTE DE TESORERIA: {ruta.name}")
    print(f"{'='*60}")

    for fecha in sorted(resultado.keys()):
        corte = resultado[fecha]
        n_ind = len(corte.facturas_individuales)
        total_ind = corte.total_facturas_individuales

        print(f"\n  Dia {fecha} (hoja '{corte.nombre_hoja}'):")
        print(f"    Factura Global: FD-{corte.factura_global_numero or '-'} "
              f"(${corte.factura_global_importe or 0:,.2f})")
        print(f"    Facturas Individuales: {n_ind} (${total_ind:,.2f})")
        print(f"    Total Efectivo: ${corte.total_efectivo or 0:,.2f}")
        print(f"    Total TDC: ${corte.total_tdc or 0:,.2f}")

        if corte.facturas_individuales:
            print(f"    Detalle facturas:")
            for f in corte.facturas_individuales[:5]:
                print(f"      FD-{f.numero}: ${f.importe:,.2f}")
            if n_ind > 5:
                print(f"      ... y {n_ind - 5} mas")

    print(f"\n  TOTAL: {len(resultado)} dias con datos")


def _parsear_nomina(ruta: Path):
    """Parsea y muestra resumen del archivo de nomina."""
    from src.entrada.nomina import parsear_nomina

    datos = parsear_nomina(ruta)
    if datos is None:
        print("No se pudo parsear el archivo de nomina.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"NOMINA: {ruta.name}")
    print(f"{'='*60}")
    print(f"  Numero: {datos.numero_nomina}")
    print(f"  Dispersion (transferencias): ${datos.total_dispersion:,.2f}")
    print(f"  Cheques (efectivo):          ${datos.total_cheques:,.2f}")
    print(f"  Vacaciones pagadas:          ${datos.total_vacaciones:,.2f}")
    print(f"  Finiquito:                   ${datos.total_finiquito:,.2f}")
    print(f"  {'─'*40}")
    print(f"  TOTAL NETO:                  ${datos.total_neto:,.2f}")

    if datos.percepciones:
        print(f"\n  Percepciones ({len(datos.percepciones)}):")
        for p in datos.percepciones:
            print(f"    {p.concepto}: ${p.monto:,.2f} → {p.cuenta}/{p.subcuenta}")

    if datos.deducciones:
        print(f"\n  Deducciones ({len(datos.deducciones)}):")
        for d in datos.deducciones:
            print(f"    {d.concepto}: ${d.monto:,.2f} → {d.cuenta}/{d.subcuenta}")


def cmd_clasificar(args):
    """Clasifica movimientos del estado de cuenta y muestra resumen."""
    from src.entrada.estado_cuenta import parsear_estado_cuenta_plano
    from src.clasificador import clasificar_movimientos, resumen_clasificacion

    ruta = Path(args.archivo)
    if not ruta.exists():
        logger.error("Archivo no encontrado: {}", ruta)
        sys.exit(1)

    movimientos = parsear_estado_cuenta_plano(ruta)
    clasificar_movimientos(movimientos)
    resumen = resumen_clasificacion(movimientos)

    print(f"\n{'='*60}")
    print(f"CLASIFICACION: {ruta.name}")
    print(f"{'='*60}")

    total = sum(resumen.values())
    for tipo in sorted(resumen.keys()):
        count = resumen[tipo]
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {tipo:25s} → {count:4d} ({pct:5.1f}%)")

    print(f"  {'─'*40}")
    print(f"  {'TOTAL':25s} → {total:4d}")

    # Mostrar desconocidos
    desconocidos = [m for m in movimientos if m.tipo_proceso and m.tipo_proceso.value == 'DESCONOCIDO']
    if desconocidos:
        print(f"\n  MOVIMIENTOS NO CLASIFICADOS ({len(desconocidos)}):")
        for m in desconocidos[:15]:
            signo = "+" if m.es_ingreso else "-"
            print(f"    {m.fecha} {signo}${m.monto:>10,.2f} | {m.descripcion[:55]} [{m.cuenta_banco[-6:]}]")
        if len(desconocidos) > 15:
            print(f"    ... y {len(desconocidos) - 15} mas")


def cmd_watch(args):
    """Monitorea carpeta de entrada por archivos nuevos."""
    from config.settings import Settings
    from src.watcher import FileWatcher

    settings = Settings.from_env()
    dry_run = not args.ejecutar

    watcher = FileWatcher(
        entrada_dir=settings.entrada_dir,
        procesados_dir=settings.procesados_dir,
        errores_dir=settings.errores_dir,
        intervalo=args.intervalo,
    )

    print(f"\n{'='*60}")
    print(f"WATCHER — Monitoreando: {settings.entrada_dir}")
    print(f"Intervalo: {args.intervalo}s")
    print(f"Modo: {'DRY-RUN' if dry_run else 'EJECUCION'}")
    print(f"Procesados: {settings.procesados_dir}")
    print(f"Errores: {settings.errores_dir}")
    print(f"{'='*60}")
    print("Presiona Ctrl+C para detener.\n")

    watcher.iniciar(dry_run=dry_run)


def cmd_procesar(args):
    """Procesa movimientos bancarios."""
    from datetime import date as date_type

    ruta_ec = Path(args.estado_cuenta)
    if not ruta_ec.exists():
        logger.error("Archivo de estado de cuenta no encontrado: {}", ruta_ec)
        sys.exit(1)

    ruta_tes = None
    if args.tesoreria:
        ruta_tes = Path(args.tesoreria)
        if not ruta_tes.exists():
            logger.error("Archivo de tesoreria no encontrado: {}", ruta_tes)
            sys.exit(1)

    # Validar que tesoreria se proporciona cuando es requerido
    if args.tipo in ('venta-tdc', 'venta-efectivo') and ruta_tes is None:
        logger.error("El proceso '{}' requiere el archivo de tesoreria", args.tipo)
        sys.exit(1)

    ruta_nomina = None
    if args.nomina:
        ruta_nomina = Path(args.nomina)
        if not ruta_nomina.exists():
            logger.error("Archivo de nomina no encontrado: {}", ruta_nomina)
            sys.exit(1)

    if args.tipo == 'nomina' and ruta_nomina is None:
        logger.error("El proceso 'nomina' requiere --nomina <archivo>")
        sys.exit(1)

    solo_fecha = None
    if args.fecha:
        try:
            solo_fecha = date_type.fromisoformat(args.fecha)
        except ValueError:
            logger.error("Formato de fecha invalido: {} (usar YYYY-MM-DD)", args.fecha)
            sys.exit(1)

    dry_run = not args.ejecutar
    confirmar = args.confirmar and not dry_run

    if dry_run:
        print("\n  MODO DRY-RUN: solo se muestra el plan, no se ejecuta nada.\n")
    elif confirmar:
        print("\n  MODO CONFIRMAR: se pedira confirmacion para cada plan.\n")
    else:
        respuesta = input(
            "\n  MODO EJECUCION: se escribira en la BD. Continuar? (si/no): "
        )
        if respuesta.lower() not in ('si', 's', 'yes', 'y'):
            print("  Cancelado.")
            sys.exit(0)

    if args.tipo == 'venta-tdc':
        from src.orquestador import procesar_ventas_tdc

        resultados = procesar_ventas_tdc(
            ruta_estado_cuenta=ruta_ec,
            ruta_tesoreria=ruta_tes,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    elif args.tipo == 'venta-efectivo':
        from src.orquestador import procesar_ventas_efectivo

        resultados = procesar_ventas_efectivo(
            ruta_estado_cuenta=ruta_ec,
            ruta_tesoreria=ruta_tes,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    elif args.tipo == 'comisiones':
        from src.orquestador import procesar_comisiones

        resultados = procesar_comisiones(
            ruta_estado_cuenta=ruta_ec,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    elif args.tipo == 'traspasos':
        from src.orquestador import procesar_traspasos

        resultados = procesar_traspasos(
            ruta_estado_cuenta=ruta_ec,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    elif args.tipo == 'conciliaciones':
        from src.orquestador import procesar_conciliaciones

        resultados = procesar_conciliaciones(
            ruta_estado_cuenta=ruta_ec,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    elif args.tipo == 'nomina':
        from src.orquestador import procesar_nomina

        resultados = procesar_nomina(
            ruta_estado_cuenta=ruta_ec,
            ruta_nomina=ruta_nomina,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    elif args.tipo == 'impuestos':
        from src.orquestador import procesar_impuestos

        # Preparar rutas de PDFs opcionales
        ruta_af1 = Path(args.acuse_federal_1) if args.acuse_federal_1 else None
        ruta_af2 = Path(args.acuse_federal_2) if args.acuse_federal_2 else None
        ruta_ieps = Path(args.detalle_ieps) if args.detalle_ieps else None
        ruta_decl = Path(args.declaracion_completa) if args.declaracion_completa else None
        ruta_est = Path(args.impuesto_estatal) if args.impuesto_estatal else None
        ruta_imss = Path(args.imss) if args.imss else None

        # Validar que existen
        for ruta, nombre in [
            (ruta_af1, 'acuse-federal-1'), (ruta_af2, 'acuse-federal-2'),
            (ruta_ieps, 'detalle-ieps'), (ruta_decl, 'declaracion-completa'),
            (ruta_est, 'impuesto-estatal'), (ruta_imss, 'imss'),
        ]:
            if ruta and not ruta.exists():
                logger.error("Archivo no encontrado (--{}): {}", nombre, ruta)
                sys.exit(1)

        resultados = procesar_impuestos(
            ruta_estado_cuenta=ruta_ec,
            ruta_acuse_federal_1=ruta_af1,
            ruta_acuse_federal_2=ruta_af2,
            ruta_detalle_ieps=ruta_ieps,
            ruta_declaracion_completa=ruta_decl,
            ruta_impuesto_estatal=ruta_est,
            ruta_imss=ruta_imss,
            dry_run=dry_run,
            solo_fecha=solo_fecha,
            confirmar=confirmar,
        )
    else:
        logger.error("Tipo de proceso no soportado: {}", args.tipo)
        sys.exit(1)

    # Resumen final
    print(f"\n{'='*60}")
    print(f"RESUMEN FINAL")
    print(f"{'='*60}")
    exitosos = [r for r in resultados if r.exito]
    fallidos = [r for r in resultados if not r.exito]
    print(f"  Exitosos: {len(exitosos)}")
    print(f"  Fallidos: {len(fallidos)}")
    for r in exitosos:
        if r.folios:
            print(f"    {r.descripcion}: Folios={r.folios}, Poliza={r.num_poliza}")
        else:
            print(f"    {r.descripcion}")
    for r in fallidos:
        print(f"    ERROR: {r.descripcion} - {r.error}")


def main():
    """Punto de entrada principal."""
    parser = argparse.ArgumentParser(
        description='Agente5 — Automatizacion de Movimientos Bancarios en SAV7',
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Nivel de logging (default: INFO)',
    )

    subparsers = parser.add_subparsers(dest='comando', help='Comando a ejecutar')

    # Subcomando: test-conexion
    subparsers.add_parser('test-conexion', help='Probar conexion a BD')

    # Subcomando: parsear
    parser_parsear = subparsers.add_parser('parsear', help='Parsear archivo de entrada')
    parser_parsear.add_argument(
        'tipo',
        choices=['estado-cuenta', 'tesoreria', 'nomina'],
        help='Tipo de archivo a parsear',
    )
    parser_parsear.add_argument(
        'archivo',
        help='Ruta al archivo Excel',
    )

    # Subcomando: procesar
    parser_procesar = subparsers.add_parser(
        'procesar', help='Procesar movimientos (dry-run por default)',
    )
    parser_procesar.add_argument(
        'tipo',
        choices=['venta-tdc', 'venta-efectivo', 'comisiones', 'traspasos', 'conciliaciones', 'nomina', 'impuestos'],
        help='Tipo de proceso a ejecutar',
    )
    parser_procesar.add_argument(
        'estado_cuenta',
        help='Ruta al Excel de estado de cuenta',
    )
    parser_procesar.add_argument(
        'tesoreria',
        nargs='?',
        default=None,
        help='Ruta al Excel de tesoreria (requerido para venta-tdc y venta-efectivo)',
    )
    parser_procesar.add_argument(
        '--nomina',
        default=None,
        help='Ruta al Excel de nomina CONTPAQi (requerido para nomina)',
    )
    parser_procesar.add_argument(
        '--acuse-federal-1',
        default=None,
        help='Ruta al acuse PDF de 1a declaracion federal (retenciones + IEPS)',
    )
    parser_procesar.add_argument(
        '--acuse-federal-2',
        default=None,
        help='Ruta al acuse PDF de 2a declaracion federal (ISR + IVA)',
    )
    parser_procesar.add_argument(
        '--detalle-ieps',
        default=None,
        help='Ruta al PDF de detalle IEPS (montos brutos)',
    )
    parser_procesar.add_argument(
        '--declaracion-completa',
        default=None,
        help='Ruta a la declaracion completa (IVA brutos + retenciones por proveedor)',
    )
    parser_procesar.add_argument(
        '--impuesto-estatal',
        default=None,
        help='Ruta al PDF de 3%% nomina estatal',
    )
    parser_procesar.add_argument(
        '--imss',
        default=None,
        help='Ruta al PDF Resumen de Liquidacion SUA (IMSS/INFONAVIT)',
    )
    parser_procesar.add_argument(
        '--fecha',
        help='Solo procesar esta fecha (YYYY-MM-DD)',
        default=None,
    )
    parser_procesar.add_argument(
        '--ejecutar',
        action='store_true',
        help='Ejecutar (NO dry-run). Requiere confirmacion.',
    )
    parser_procesar.add_argument(
        '--confirmar',
        action='store_true',
        help='Confirmar cada plan antes de ejecutar (requiere --ejecutar).',
    )

    # Subcomando: clasificar
    parser_clasificar = subparsers.add_parser(
        'clasificar', help='Clasificar movimientos del estado de cuenta',
    )
    parser_clasificar.add_argument(
        'archivo',
        help='Ruta al Excel de estado de cuenta',
    )

    # Subcomando: watch
    parser_watch = subparsers.add_parser(
        'watch', help='Monitorear carpeta de entrada por archivos nuevos',
    )
    parser_watch.add_argument(
        '--intervalo',
        type=int,
        default=60,
        help='Segundos entre cada revision (default: 60)',
    )
    parser_watch.add_argument(
        '--ejecutar',
        action='store_true',
        help='Ejecutar (NO dry-run). Sin esto, solo muestra planes.',
    )

    args = parser.parse_args()
    configurar_logger(args.log_level)

    if args.comando is None:
        parser.print_help()
        sys.exit(0)

    if args.comando == 'test-conexion':
        cmd_test_conexion(args)
    elif args.comando == 'parsear':
        cmd_parsear(args)
    elif args.comando == 'clasificar':
        cmd_clasificar(args)
    elif args.comando == 'procesar':
        cmd_procesar(args)
    elif args.comando == 'watch':
        cmd_watch(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
