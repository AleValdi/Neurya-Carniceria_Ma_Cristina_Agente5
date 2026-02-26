"""Orquestador central del Agente5.

Coordina el flujo completo: parsear archivos, clasificar movimientos,
despachar a procesadores, validar y ejecutar (o mostrar en dry-run).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from src.clasificador import (
    agrupar_comisiones_por_fecha,
    agrupar_ventas_tdc_por_fecha,
    clasificar_movimientos,
    resumen_clasificacion,
)
from src.entrada.estado_cuenta import parsear_estado_cuenta_plano
from src.entrada.tesoreria import parsear_tesoreria
from src.erp.cobros import (
    actualizar_factura_cobrada,
    insertar_cobro_factcob,
    obtener_siguiente_cobro,
    obtener_siguiente_cobro_multiple,
)
from src.erp.compras import insertar_factura_compra
from src.erp.consecutivos import obtener_siguiente_folio, obtener_siguiente_poliza
from src.erp.facturas_movimiento import insertar_factura_movimiento
from src.erp.pago_factura import insertar_cheq_pmp, insertar_rec_pago
from src.erp.movimientos import (
    actualizar_num_poliza,
    buscar_movimiento_existente,
    conciliar_movimiento,
    insertar_movimiento,
)
from src.erp.poliza import insertar_poliza
from src.erp.sav7_connector import SAV7Connector
from src.models import (
    CorteVentaDiaria,
    DatosCobroCliente,
    DatosMovimientoPM,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    ResultadoProceso,
    TipoCA,
    TipoProceso,
)
from src.entrada.impuestos_pdf import parsear_imss, parsear_impuesto_estatal, parsear_impuesto_federal
from src.entrada.nomina import parsear_nomina
from src.procesadores.comisiones import ProcesadorComisiones
from src.procesadores.conciliacion_cobros import ProcesadorConciliacionCobros
from src.procesadores.conciliacion_pagos import ProcesadorConciliacionPagos
from src.procesadores.impuestos import ProcesadorImpuestos
from src.procesadores.nomina_proc import ProcesadorNomina
from src.procesadores.traspasos import ProcesadorTraspasos
from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo
from src.procesadores.venta_tdc import ProcesadorVentaTDC
from src.validacion import validar_venta_efectivo, validar_venta_tdc


# --- Funciones de orquestacion por tipo de proceso ---


def procesar_ventas_tdc(
    ruta_estado_cuenta: Path,
    ruta_tesoreria: Path,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa ventas TDC completas: parse -> clasifica -> plan -> ejecuta."""
    resultados: List[ResultadoProceso] = []

    # 1. Parsear archivos
    logger.info("=== FASE 1: Parseando archivos ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
    cortes = parsear_tesoreria(ruta_tesoreria)

    # 2. Clasificar
    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)
    resumen = resumen_clasificacion(movimientos)
    logger.info("Resumen clasificacion: {}", resumen)

    # 3. Agrupar TDC por fecha de deposito
    tdc_por_fecha = agrupar_ventas_tdc_por_fecha(movimientos)
    logger.info(
        "Dias con ventas TDC: {} ({} movimientos total)",
        len(tdc_por_fecha),
        sum(len(m) for m in tdc_por_fecha.values()),
    )

    if solo_fecha:
        if solo_fecha in tdc_por_fecha:
            tdc_por_fecha = {solo_fecha: tdc_por_fecha[solo_fecha]}
        else:
            logger.warning("No hay ventas TDC para {}", solo_fecha)
            return resultados

    # 4. Procesar cada dia de deposito
    procesador = ProcesadorVentaTDC()
    db_connector = _preparar_conexion(connector, dry_run)

    fechas_deposito_tdc = sorted(tdc_por_fecha.keys())

    for fecha_deposito in fechas_deposito_tdc:
        movs_dia = tdc_por_fecha[fecha_deposito]
        logger.info(
            "=== Procesando TDC {} ({} abonos) ===",
            fecha_deposito, len(movs_dia),
        )

        # Buscar cortes de venta correspondientes
        # Rango dinamico: cortes entre deposito anterior y actual
        cortes_matching = _buscar_cortes_tdc(
            fecha_deposito, cortes, fechas_deposito_tdc,
        )

        if not cortes_matching:
            logger.warning(
                "Sin corte de tesoreria para deposito TDC del {}",
                fecha_deposito,
            )
            # Procesar sin corte (genera advertencia en plan)
            resultado, confirmar = _procesar_dia_tdc(
                procesador, movs_dia, fecha_deposito, None,
                db_connector, dry_run, confirmar,
            )
            resultados.extend(resultado)
            continue

        if len(cortes_matching) == 1:
            # Caso simple: 1 dia de venta -> depositos del siguiente habil
            corte = cortes_matching[0]
            resultado, confirmar = _procesar_dia_tdc(
                procesador, movs_dia, fecha_deposito, corte,
                db_connector, dry_run, confirmar,
            )
            resultados.extend(resultado)
        else:
            # Caso fin de semana: multiples dias de venta -> 1 dia deposito
            # Asignar depositos a cortes por total TDC
            resultado, confirmar = _procesar_tdc_multiples_cortes(
                procesador, movs_dia, fecha_deposito, cortes_matching,
                db_connector, dry_run, confirmar,
            )
            resultados.extend(resultado)

    return resultados


def procesar_ventas_efectivo(
    ruta_estado_cuenta: Path,
    ruta_tesoreria: Path,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa ventas en efectivo: parse -> clasifica -> plan -> ejecuta."""
    resultados: List[ResultadoProceso] = []

    logger.info("=== FASE 1: Parseando archivos ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
    cortes = parsear_tesoreria(ruta_tesoreria)

    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)

    # Agrupar depositos de efectivo por fecha
    efectivo_por_fecha: Dict[date, List[MovimientoBancario]] = {}
    for mov in movimientos:
        if mov.tipo_proceso == TipoProceso.VENTA_EFECTIVO:
            if mov.fecha not in efectivo_por_fecha:
                efectivo_por_fecha[mov.fecha] = []
            efectivo_por_fecha[mov.fecha].append(mov)

    logger.info(
        "Dias con depositos efectivo: {} ({} depositos total)",
        len(efectivo_por_fecha),
        sum(len(m) for m in efectivo_por_fecha.values()),
    )

    if solo_fecha:
        if solo_fecha in efectivo_por_fecha:
            efectivo_por_fecha = {solo_fecha: efectivo_por_fecha[solo_fecha]}
        else:
            logger.warning("No hay depositos de efectivo para {}", solo_fecha)
            return resultados

    procesador = ProcesadorVentaEfectivo()
    db_connector = _preparar_conexion(connector, dry_run)

    for fecha_deposito in sorted(efectivo_por_fecha.keys()):
        movs_dia = efectivo_por_fecha[fecha_deposito]
        logger.info(
            "=== Procesando Efectivo {} ({} depositos) ===",
            fecha_deposito, len(movs_dia),
        )

        # Para cada deposito de efectivo, buscar el corte de venta
        # cuyo total_efectivo coincida con el monto del deposito
        for mov in movs_dia:
            corte = _buscar_corte_efectivo(mov.monto, cortes)

            if corte is None:
                logger.warning(
                    "No se encontro corte para deposito efectivo "
                    "${:,.2f} del {}",
                    mov.monto, fecha_deposito,
                )

            # Validar
            if corte:
                errores_val = validar_venta_efectivo([mov], corte)
                for err in errores_val:
                    logger.warning("Validacion: {}", err)

            # Construir plan
            cursor_lectura = _obtener_cursor_lectura(db_connector)

            plan = procesador.construir_plan(
                movimientos=[mov],
                fecha=fecha_deposito,
                cursor=cursor_lectura,
                corte_venta=corte,
            )

            if cursor_lectura:
                cursor_lectura.close()

            _mostrar_plan(plan)

            if dry_run:
                resultados.append(ResultadoProceso(
                    exito=True,
                    tipo_proceso='VENTA_EFECTIVO',
                    descripcion=f'DRY-RUN: {plan.descripcion}',
                    plan=plan,
                ))
            else:
                if confirmar:
                    resp = _confirmar_ejecucion(plan)
                    if resp == 'cancelar':
                        break
                    if resp == 'no':
                        continue
                    if resp == 'todos':
                        confirmar = False
                resultado = _ejecutar_plan(plan, db_connector)
                resultados.append(resultado)

    return resultados


def procesar_comisiones(
    ruta_estado_cuenta: Path,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa comisiones bancarias: parse -> clasifica -> plan -> ejecuta."""
    resultados: List[ResultadoProceso] = []

    logger.info("=== FASE 1: Parseando estado de cuenta ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)

    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)

    # Filtrar comisiones
    tipos_comision = (
        TipoProceso.COMISION_SPEI, TipoProceso.COMISION_SPEI_IVA,
        TipoProceso.COMISION_TDC, TipoProceso.COMISION_TDC_IVA,
    )
    comisiones = [m for m in movimientos if m.tipo_proceso in tipos_comision]

    if not comisiones:
        logger.info("No se encontraron comisiones")
        return resultados

    # Agrupar por fecha
    por_fecha: Dict[date, List[MovimientoBancario]] = {}
    for mov in comisiones:
        if mov.fecha not in por_fecha:
            por_fecha[mov.fecha] = []
        por_fecha[mov.fecha].append(mov)

    logger.info(
        "Dias con comisiones: {} ({} movimientos total)",
        len(por_fecha), len(comisiones),
    )

    if solo_fecha:
        if solo_fecha in por_fecha:
            por_fecha = {solo_fecha: por_fecha[solo_fecha]}
        else:
            logger.warning("No hay comisiones para {}", solo_fecha)
            return resultados

    procesador = ProcesadorComisiones()
    db_connector = _preparar_conexion(connector, dry_run)

    for fecha in sorted(por_fecha.keys()):
        movs_dia = por_fecha[fecha]
        logger.info(
            "=== Procesando Comisiones {} ({} movimientos) ===",
            fecha, len(movs_dia),
        )

        plan = procesador.construir_plan(
            movimientos=movs_dia,
            fecha=fecha,
        )

        _mostrar_plan(plan)

        if dry_run:
            resultados.append(ResultadoProceso(
                exito=True,
                tipo_proceso='COMISIONES',
                descripcion=f'DRY-RUN: {plan.descripcion}',
                plan=plan,
            ))
        else:
            if confirmar:
                resp = _confirmar_ejecucion(plan)
                if resp == 'cancelar':
                    break
                if resp == 'no':
                    continue
                if resp == 'todos':
                    confirmar = False
            resultado = _ejecutar_plan(plan, db_connector)
            resultados.append(resultado)

    return resultados


def procesar_traspasos(
    ruta_estado_cuenta: Path,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa traspasos entre cuentas: parse -> clasifica -> plan -> ejecuta."""
    resultados: List[ResultadoProceso] = []

    logger.info("=== FASE 1: Parseando estado de cuenta ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)

    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)

    # Filtrar traspasos egreso (los ingresos se generan automaticamente)
    traspasos = [
        m for m in movimientos if m.tipo_proceso == TipoProceso.TRASPASO
    ]

    if not traspasos:
        logger.info("No se encontraron traspasos")
        return resultados

    # Agrupar por fecha
    por_fecha: Dict[date, List[MovimientoBancario]] = {}
    for mov in traspasos:
        if mov.fecha not in por_fecha:
            por_fecha[mov.fecha] = []
        por_fecha[mov.fecha].append(mov)

    logger.info(
        "Dias con traspasos: {} ({} movimientos total)",
        len(por_fecha), len(traspasos),
    )

    if solo_fecha:
        if solo_fecha in por_fecha:
            por_fecha = {solo_fecha: por_fecha[solo_fecha]}
        else:
            logger.warning("No hay traspasos para {}", solo_fecha)
            return resultados

    procesador = ProcesadorTraspasos()
    db_connector = _preparar_conexion(connector, dry_run)

    for fecha in sorted(por_fecha.keys()):
        movs_dia = por_fecha[fecha]
        logger.info(
            "=== Procesando Traspasos {} ({} movimientos) ===",
            fecha, len(movs_dia),
        )

        plan = procesador.construir_plan(
            movimientos=movs_dia,
            fecha=fecha,
        )

        _mostrar_plan(plan)

        if dry_run:
            resultados.append(ResultadoProceso(
                exito=True,
                tipo_proceso='TRASPASOS',
                descripcion=f'DRY-RUN: {plan.descripcion}',
                plan=plan,
            ))
        else:
            if confirmar:
                resp = _confirmar_ejecucion(plan)
                if resp == 'cancelar':
                    break
                if resp == 'no':
                    continue
                if resp == 'todos':
                    confirmar = False
            resultado = _ejecutar_plan(plan, db_connector)
            resultados.append(resultado)

    return resultados


def procesar_conciliaciones(
    ruta_estado_cuenta: Path,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa conciliaciones (pagos + cobros): parse -> clasifica -> concilia."""
    resultados: List[ResultadoProceso] = []

    logger.info("=== FASE 1: Parseando estado de cuenta ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)

    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)

    db_connector = _preparar_conexion(connector, dry_run)
    cursor = _obtener_cursor_lectura(db_connector)

    # --- Pagos a proveedores (E1) ---
    pagos = [
        m for m in movimientos if m.tipo_proceso == TipoProceso.PAGO_PROVEEDOR
    ]

    if pagos:
        pagos_por_fecha: Dict[date, List[MovimientoBancario]] = {}
        for mov in pagos:
            if mov.fecha not in pagos_por_fecha:
                pagos_por_fecha[mov.fecha] = []
            pagos_por_fecha[mov.fecha].append(mov)

        if solo_fecha:
            pagos_por_fecha = {
                f: m for f, m in pagos_por_fecha.items() if f == solo_fecha
            }

        procesador_pagos = ProcesadorConciliacionPagos()
        for fecha in sorted(pagos_por_fecha.keys()):
            movs_dia = pagos_por_fecha[fecha]
            logger.info(
                "=== Conciliando Pagos {} ({} movimientos) ===",
                fecha, len(movs_dia),
            )

            plan = procesador_pagos.construir_plan(
                movimientos=movs_dia,
                fecha=fecha,
                cursor=cursor,
            )

            _mostrar_plan(plan)

            if dry_run:
                resultados.append(ResultadoProceso(
                    exito=True,
                    tipo_proceso='CONCILIACION_PAGOS',
                    descripcion=f'DRY-RUN: {plan.descripcion}',
                    plan=plan,
                ))
            else:
                if confirmar:
                    resp = _confirmar_ejecucion(plan)
                    if resp == 'cancelar':
                        break
                    if resp == 'no':
                        continue
                    if resp == 'todos':
                        confirmar = False
                resultado = _ejecutar_conciliacion(plan, db_connector)
                resultados.append(resultado)

    # --- Cobros a clientes (I3) ---
    cobros = [
        m for m in movimientos if m.tipo_proceso == TipoProceso.COBRO_CLIENTE
    ]

    if cobros:
        cobros_por_fecha: Dict[date, List[MovimientoBancario]] = {}
        for mov in cobros:
            if mov.fecha not in cobros_por_fecha:
                cobros_por_fecha[mov.fecha] = []
            cobros_por_fecha[mov.fecha].append(mov)

        if solo_fecha:
            cobros_por_fecha = {
                f: m for f, m in cobros_por_fecha.items() if f == solo_fecha
            }

        procesador_cobros = ProcesadorConciliacionCobros()
        for fecha in sorted(cobros_por_fecha.keys()):
            movs_dia = cobros_por_fecha[fecha]
            logger.info(
                "=== Conciliando Cobros {} ({} movimientos) ===",
                fecha, len(movs_dia),
            )

            plan = procesador_cobros.construir_plan(
                movimientos=movs_dia,
                fecha=fecha,
                cursor=cursor,
            )

            _mostrar_plan(plan)

            if dry_run:
                tipo_dr = 'CONCILIACION_COBROS'
                if plan.cobros_cliente:
                    tipo_dr = 'COBRO_CLIENTE'
                resultados.append(ResultadoProceso(
                    exito=True,
                    tipo_proceso=tipo_dr,
                    descripcion=f'DRY-RUN: {plan.descripcion}',
                    plan=plan,
                ))
            else:
                if confirmar:
                    resp = _confirmar_ejecucion(plan)
                    if resp == 'cancelar':
                        break
                    if resp == 'no':
                        continue
                    if resp == 'todos':
                        confirmar = False
                # Ejecutar conciliaciones (Fase B) y/o cobros (Fase A)
                if plan.conciliaciones:
                    resultado = _ejecutar_conciliacion(plan, db_connector)
                    resultados.append(resultado)
                if plan.cobros_cliente:
                    resultado = _ejecutar_cobro_completo(plan, db_connector)
                    resultados.append(resultado)

    if cursor:
        cursor.close()

    if not pagos and not cobros:
        logger.info("No se encontraron pagos ni cobros para conciliar")

    return resultados


def procesar_nomina(
    ruta_estado_cuenta: Path,
    ruta_nomina: Path,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa nomina: parse -> clasifica -> plan -> ejecuta."""
    resultados: List[ResultadoProceso] = []

    logger.info("=== FASE 1: Parseando archivos ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
    datos_nomina = parsear_nomina(ruta_nomina)

    if datos_nomina is None:
        logger.error("No se pudo parsear archivo de nomina")
        return [ResultadoProceso(
            exito=False,
            tipo_proceso='NOMINA',
            descripcion='Error parseando archivo de nomina',
            error='No se pudo parsear el archivo',
        )]

    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)

    # Filtrar movimientos de nomina
    nominas = [
        m for m in movimientos if m.tipo_proceso == TipoProceso.NOMINA
    ]

    if not nominas:
        logger.info("No se encontraron movimientos de nomina")
        return resultados

    # Agrupar por fecha
    por_fecha: Dict[date, List[MovimientoBancario]] = {}
    for mov in nominas:
        if mov.fecha not in por_fecha:
            por_fecha[mov.fecha] = []
        por_fecha[mov.fecha].append(mov)

    if solo_fecha:
        if solo_fecha in por_fecha:
            por_fecha = {solo_fecha: por_fecha[solo_fecha]}
        else:
            logger.warning("No hay nomina para {}", solo_fecha)
            return resultados

    procesador = ProcesadorNomina()
    db_connector = _preparar_conexion(connector, dry_run)

    for fecha in sorted(por_fecha.keys()):
        movs_dia = por_fecha[fecha]
        logger.info(
            "=== Procesando Nomina {} ({} movimientos) ===",
            fecha, len(movs_dia),
        )

        plan = procesador.construir_plan(
            movimientos=movs_dia,
            fecha=fecha,
            datos_nomina=datos_nomina,
        )

        _mostrar_plan(plan)

        if dry_run:
            resultados.append(ResultadoProceso(
                exito=True,
                tipo_proceso='NOMINA',
                descripcion=f'DRY-RUN: {plan.descripcion}',
                plan=plan,
            ))
        else:
            if confirmar:
                resp = _confirmar_ejecucion(plan)
                if resp == 'cancelar':
                    break
                if resp == 'no':
                    continue
                if resp == 'todos':
                    confirmar = False
            resultado = _ejecutar_plan(plan, db_connector)
            resultados.append(resultado)

    return resultados


def procesar_impuestos(
    ruta_estado_cuenta: Path,
    ruta_acuse_federal_1: Optional[Path] = None,
    ruta_acuse_federal_2: Optional[Path] = None,
    ruta_detalle_ieps: Optional[Path] = None,
    ruta_declaracion_completa: Optional[Path] = None,
    ruta_impuesto_estatal: Optional[Path] = None,
    ruta_imss: Optional[Path] = None,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
    confirmar: bool = False,
) -> List[ResultadoProceso]:
    """Procesa impuestos federales, estatal e IMSS: parse -> clasifica -> plan -> ejecuta."""
    resultados: List[ResultadoProceso] = []

    # 1. Parsear estado de cuenta
    logger.info("=== FASE 1: Parseando archivos ===")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)

    # 2. Parsear PDFs de impuestos
    datos_federal = None
    datos_estatal = None

    if ruta_acuse_federal_1 and ruta_acuse_federal_2:
        datos_federal = parsear_impuesto_federal(
            ruta_acuse_1=ruta_acuse_federal_1,
            ruta_acuse_2=ruta_acuse_federal_2,
            ruta_detalle_ieps=ruta_detalle_ieps,
            ruta_declaracion_completa=ruta_declaracion_completa,
        )
        if datos_federal:
            logger.info(
                "Federal parseado: periodo={}, 1a=${:,.0f}, 2a=${:,.0f}, confianza={}",
                datos_federal.periodo, datos_federal.total_primera,
                datos_federal.total_segunda, datos_federal.confianza_100,
            )
    else:
        logger.info("Sin acuses federales — solo se procesara estatal")

    if ruta_impuesto_estatal:
        datos_estatal = parsear_impuesto_estatal(ruta_impuesto_estatal)
        if datos_estatal:
            logger.info(
                "Estatal parseado: periodo={}, monto=${:,.2f}, confianza={}",
                datos_estatal.periodo, datos_estatal.monto, datos_estatal.confianza_100,
            )
    else:
        logger.info("Sin formato estatal — solo se procesara federal")

    datos_imss = None
    if ruta_imss:
        datos_imss = parsear_imss(ruta_imss)
        if datos_imss:
            logger.info(
                "IMSS parseado: periodo={}, total=${:,.2f}, infonavit={}, confianza={}",
                datos_imss.periodo, datos_imss.total_a_pagar,
                datos_imss.incluye_infonavit, datos_imss.confianza_100,
            )

    # 3. Clasificar movimientos
    logger.info("=== FASE 2: Clasificando movimientos ===")
    clasificar_movimientos(movimientos)

    # 4. Filtrar impuestos
    tipos_impuesto = (
        TipoProceso.IMPUESTO_FEDERAL,
        TipoProceso.IMPUESTO_ESTATAL,
        TipoProceso.IMPUESTO_IMSS,
    )
    impuestos = [m for m in movimientos if m.tipo_proceso in tipos_impuesto]

    if not impuestos:
        logger.info("No se encontraron movimientos de impuestos")
        return resultados

    # Agrupar por fecha
    por_fecha: Dict[date, List[MovimientoBancario]] = {}
    for mov in impuestos:
        if mov.fecha not in por_fecha:
            por_fecha[mov.fecha] = []
        por_fecha[mov.fecha].append(mov)

    logger.info(
        "Dias con impuestos: {} ({} movimientos total)",
        len(por_fecha), len(impuestos),
    )

    if solo_fecha:
        if solo_fecha in por_fecha:
            por_fecha = {solo_fecha: por_fecha[solo_fecha]}
        else:
            logger.warning("No hay impuestos para {}", solo_fecha)
            return resultados

    procesador = ProcesadorImpuestos()
    db_connector = _preparar_conexion(connector, dry_run)

    for fecha in sorted(por_fecha.keys()):
        movs_dia = por_fecha[fecha]
        logger.info(
            "=== Procesando Impuestos {} ({} movimientos) ===",
            fecha, len(movs_dia),
        )

        # Obtener cursor de lectura para consultas de balanza (IMSS)
        cursor_lectura = None
        if datos_imss:
            cursor_lectura = _obtener_cursor_lectura(db_connector)

        plan = procesador.construir_plan(
            movimientos=movs_dia,
            fecha=fecha,
            datos_federal=datos_federal,
            datos_estatal=datos_estatal,
            datos_imss=datos_imss,
            cursor=cursor_lectura,
        )

        _mostrar_plan(plan)

        if dry_run:
            resultados.append(ResultadoProceso(
                exito=True,
                tipo_proceso='IMPUESTOS',
                descripcion=f'DRY-RUN: {plan.descripcion}',
                plan=plan,
            ))
        else:
            if confirmar:
                resp = _confirmar_ejecucion(plan)
                if resp == 'cancelar':
                    break
                if resp == 'no':
                    continue
                if resp == 'todos':
                    confirmar = False
            resultado = _ejecutar_plan(plan, db_connector)
            resultados.append(resultado)

    return resultados


# --- Funciones auxiliares de matching ---


def _buscar_cortes_tdc(
    fecha_deposito: date,
    cortes: Dict[date, CorteVentaDiaria],
    fechas_deposito_tdc: Optional[List[date]] = None,
) -> List[CorteVentaDiaria]:
    """Busca cortes de venta correspondientes a un dia de deposito TDC.

    Las ventas TDC se depositan el siguiente dia habil bancario.
    Para determinar que cortes corresponden a un deposito, se usa el rango
    entre la fecha del deposito TDC anterior y la fecha actual:

        cortes para D[i] = tesoreria con fechas en [D[i-1], D[i])

    Esto maneja automaticamente fines de semana Y dias inhabiles bancarios
    sin necesidad de un calendario de festivos.

    Args:
        fecha_deposito: Fecha del deposito en el estado de cuenta.
        cortes: Dict fecha → CorteVentaDiaria de tesoreria.
        fechas_deposito_tdc: Lista ordenada de TODAS las fechas con depositos
            TDC/TDD en el estado de cuenta. Si no se proporciona, usa
            fallback por dia de la semana (solo maneja fines de semana).
    """
    if fechas_deposito_tdc:
        return _buscar_cortes_por_rango(fecha_deposito, cortes, fechas_deposito_tdc)

    # Fallback: logica simple por dia de la semana (sin soporte festivos)
    dia_semana = fecha_deposito.weekday()
    if dia_semana == 0:
        # Lunes: viernes + sabado + domingo
        resultado = []
        for delta in (3, 2, 1):
            candidata = fecha_deposito - timedelta(days=delta)
            if candidata in cortes:
                resultado.append(cortes[candidata])
        return resultado
    else:
        # Otros dias: dia anterior
        candidata = fecha_deposito - timedelta(days=1)
        if candidata in cortes:
            return [cortes[candidata]]
        return []


def _buscar_cortes_por_rango(
    fecha_deposito: date,
    cortes: Dict[date, CorteVentaDiaria],
    fechas_deposito_tdc: List[date],
) -> List[CorteVentaDiaria]:
    """Busca cortes en el rango [deposito_anterior, deposito_actual).

    Para deposito D[i], los cortes son todas las fechas de tesoreria
    entre D[i-1] (inclusive) y D[i] (exclusive). Esto cubre:
    - Dias normales: 1 corte (dia anterior)
    - Fines de semana: 2-3 cortes
    - Festivos bancarios: N cortes acumulados
    """
    fechas_sorted = sorted(fechas_deposito_tdc)

    try:
        idx = fechas_sorted.index(fecha_deposito)
    except ValueError:
        return []

    if idx == 0:
        # Primer deposito del periodo: buscar hasta 7 dias atras
        inicio = fecha_deposito - timedelta(days=7)
    else:
        inicio = fechas_sorted[idx - 1]

    fin = fecha_deposito - timedelta(days=1)

    resultado = []
    current = inicio
    while current <= fin:
        if current in cortes:
            resultado.append(cortes[current])
        current += timedelta(days=1)

    return sorted(resultado, key=lambda c: c.fecha_corte)


def _buscar_corte_efectivo(
    monto_deposito: Decimal,
    cortes: Dict[date, CorteVentaDiaria],
    tolerancia: Decimal = Decimal('2.00'),
) -> Optional[CorteVentaDiaria]:
    """Busca el corte cuyo total_efectivo coincide con el monto del deposito.

    El deposito de efectivo puede aparecer dias despues en el banco.
    La suma de facturas individuales + parte global = monto del deposito.
    Usamos total_efectivo de tesoreria como referencia.
    """
    mejor_corte = None
    mejor_dif = None

    for corte in cortes.values():
        if corte.total_efectivo is None or corte.total_efectivo <= 0:
            continue

        dif = abs(monto_deposito - corte.total_efectivo)
        if dif <= tolerancia:
            if mejor_dif is None or dif < mejor_dif:
                mejor_dif = dif
                mejor_corte = corte

    if mejor_corte:
        logger.debug(
            "Match efectivo: deposito ${:,.2f} -> corte {} (${:,.2f}, dif=${:,.2f})",
            monto_deposito, mejor_corte.fecha_corte,
            mejor_corte.total_efectivo, mejor_dif,
        )

    return mejor_corte


def _procesar_dia_tdc(
    procesador: ProcesadorVentaTDC,
    movimientos: List[MovimientoBancario],
    fecha_deposito: date,
    corte: Optional[CorteVentaDiaria],
    db_connector: Optional[SAV7Connector],
    dry_run: bool,
    confirmar: bool = False,
) -> Tuple[List[ResultadoProceso], bool]:
    """Procesa un dia de TDC con un solo corte de venta.

    Returns:
        Tupla (resultados, confirmar_actualizado).
    """
    # Validar
    if corte:
        errores_val = validar_venta_tdc(movimientos, corte)
        for err in errores_val:
            logger.warning("Validacion: {}", err)

    cursor_lectura = _obtener_cursor_lectura(db_connector)

    plan = procesador.construir_plan(
        movimientos=movimientos,
        fecha=fecha_deposito,
        cursor=cursor_lectura,
        corte_venta=corte,
    )

    if cursor_lectura:
        cursor_lectura.close()

    _mostrar_plan(plan)

    if dry_run:
        return [ResultadoProceso(
            exito=True,
            tipo_proceso='VENTA_TDC',
            descripcion=f'DRY-RUN: {plan.descripcion}',
            plan=plan,
        )], confirmar

    # Confirmacion interactiva
    if confirmar:
        resp = _confirmar_ejecucion(plan)
        if resp == 'cancelar':
            return [], False
        if resp == 'no':
            return [ResultadoProceso(
                exito=True,
                tipo_proceso='VENTA_TDC',
                descripcion=f'SALTADO: {plan.descripcion}',
                plan=plan,
            )], confirmar
        if resp == 'todos':
            confirmar = False  # No preguntar mas

    return [_ejecutar_plan(plan, db_connector)], confirmar


def _procesar_tdc_multiples_cortes(
    procesador: ProcesadorVentaTDC,
    movimientos: List[MovimientoBancario],
    fecha_deposito: date,
    cortes: List[CorteVentaDiaria],
    db_connector: Optional[SAV7Connector],
    dry_run: bool,
    confirmar: bool = False,
) -> Tuple[List[ResultadoProceso], bool]:
    """Procesa TDC cuando multiples dias de venta depositan en la misma fecha.

    Algoritmo: asignar depositos a cortes usando el total TDC de cada corte.
    Ordena cortes por fecha. Para cada corte, busca el subconjunto de
    depositos cuya suma sea igual (+-tolerancia) al total TDC del corte.

    Returns:
        Tupla (resultados, confirmar_actualizado).
    """
    resultados = []
    depositos_disponibles = list(movimientos)

    for corte in sorted(cortes, key=lambda c: c.fecha_corte):
        if not depositos_disponibles:
            break

        target = corte.total_tdc
        if target is None or target <= 0:
            logger.warning(
                "Corte {} sin total TDC, saltando",
                corte.fecha_corte,
            )
            continue

        # Buscar subconjunto de depositos que sume al target
        subset = _encontrar_subset_por_suma(
            depositos_disponibles, target, tolerancia=Decimal('1.00'),
        )

        if subset:
            # Remover depositos asignados
            for mov in subset:
                depositos_disponibles.remove(mov)

            logger.info(
                "Corte {}: {} depositos asignados (${:,.2f} TDC)",
                corte.fecha_corte, len(subset), target,
            )

            resultado, confirmar = _procesar_dia_tdc(
                procesador, subset, fecha_deposito, corte,
                db_connector, dry_run, confirmar,
            )
            resultados.extend(resultado)
        else:
            logger.warning(
                "No se pudo encontrar subconjunto de depositos "
                "para corte {} (target=${:,.2f})",
                corte.fecha_corte, target,
            )

    # Depositos no asignados
    if depositos_disponibles:
        suma_rest = sum(m.monto for m in depositos_disponibles)
        logger.warning(
            "{} depositos sin asignar (${:,.2f}) en {}",
            len(depositos_disponibles), suma_rest, fecha_deposito,
        )

    return resultados, confirmar


def _encontrar_subset_por_suma(
    movimientos: List[MovimientoBancario],
    target: Decimal,
    tolerancia: Decimal = Decimal('1.00'),
) -> Optional[List[MovimientoBancario]]:
    """Encuentra un subconjunto de movimientos cuya suma sea ~target.

    Usa busqueda exhaustiva para conjuntos pequenos (tipicamente <20 items).
    Retorna el primer subconjunto encontrado, o None.
    """
    n = len(movimientos)

    # Optimizacion: si la suma total coincide, retornar todos
    suma_total = sum(m.monto for m in movimientos)
    if abs(suma_total - target) <= tolerancia:
        return list(movimientos)

    # Para conjuntos pequenos, busqueda de subconjuntos por tamanio
    # Empezar por subconjuntos chicos para dejar mas items a cortes siguientes
    for size in range(1, n):
        resultado = _buscar_combinacion(movimientos, target, tolerancia, size)
        if resultado is not None:
            return resultado

    return None


def _asignar_multi_corte(
    depositos: List[MovimientoBancario],
    targets: List[Decimal],
    tolerancia: Decimal = Decimal('1.00'),
    tolerancias: Optional[List[Decimal]] = None,
) -> Optional[List[List[MovimientoBancario]]]:
    """Asigna depositos a multiples cortes simultaneamente.

    A diferencia de _encontrar_subset_por_suma (greedy secuencial),
    esta funcion usa backtracking: para cada target prueba todos los
    subsets validos y verifica que los targets restantes se puedan
    satisfacer con el remanente.

    Args:
        depositos: Lista de movimientos disponibles.
        targets: Lista de montos objetivo (uno por corte, en orden).
        tolerancia: Tolerancia uniforme en pesos para cada target.
        tolerancias: Lista de tolerancias individuales (una por target).
            Si se proporciona, tiene prioridad sobre tolerancia.

    Returns:
        Lista de subsets (uno por target) si existe solucion, None si no.
    """
    if not targets:
        return []

    from itertools import combinations

    # Determinar tolerancia para este nivel y para los siguientes
    if tolerancias:
        tol_actual = tolerancias[0]
        tols_restantes = tolerancias[1:] if len(tolerancias) > 1 else None
    else:
        tol_actual = tolerancia
        tols_restantes = None

    target = targets[0]
    remaining_targets = targets[1:]
    n = len(depositos)

    # Optimizacion: si suma total coincide y es el ultimo target
    suma_total = sum(m.monto for m in depositos)
    if abs(suma_total - target) <= tol_actual and not remaining_targets:
        return [list(depositos)]

    logger.debug(
        "Multi-corte: {} depositos, target=${:,.2f}, tol=${:,.2f}, "
        "{} targets restantes",
        n, target, tol_actual, len(remaining_targets),
    )

    hits = 0
    for size in range(1, n):
        for combo_idx in combinations(range(n), size):
            suma = sum(depositos[i].monto for i in combo_idx)
            if abs(suma - target) <= tol_actual:
                hits += 1
                subset = [depositos[i] for i in combo_idx]

                if not remaining_targets:
                    return [subset]

                idx_set = set(combo_idx)
                remainder = [
                    depositos[i] for i in range(n) if i not in idx_set
                ]

                sub_result = _asignar_multi_corte(
                    remainder, remaining_targets,
                    tolerancia=tolerancia,
                    tolerancias=tols_restantes,
                )
                if sub_result is not None:
                    return [subset] + sub_result

    logger.debug(
        "Multi-corte: sin solucion (n={}, target=${:,.2f}, hits={})",
        n, target, hits,
    )
    return None


def _buscar_combinacion(
    movimientos: List[MovimientoBancario],
    target: Decimal,
    tolerancia: Decimal,
    size: int,
) -> Optional[List[MovimientoBancario]]:
    """Busca una combinacion de `size` elementos que sume ~target."""
    from itertools import combinations

    # Limitar combinaciones para evitar explosion combinatoria
    max_combos = 500000
    count = 0

    for combo in combinations(movimientos, size):
        count += 1
        if count > max_combos:
            break
        suma = sum(m.monto for m in combo)
        if abs(suma - target) <= tolerancia:
            return list(combo)

    return None


def _asignar_secuencial_con_split(
    depositos: List[MovimientoBancario],
    cortes_con_target: List['CorteVentaDiaria'],
    tolerancia: Decimal = Decimal('2.00'),
) -> Tuple[
    List[Tuple['CorteVentaDiaria', List[MovimientoBancario]]],
    List[MovimientoBancario],
    Dict[int, int],
]:
    """Asigna depositos a cortes secuencialmente, partiendo depositos si necesario.

    Replica la logica manual de las capturistas:
    1. Tomar depositos en orden de aparicion en el estado de cuenta
    2. Acumular hacia el target TDC del primer corte
    3. Si un deposito excede el target restante, partirlo en dos
    4. Pasar al siguiente corte con el remanente

    Se usa como fallback cuando el backtracking exacto (_asignar_multi_corte)
    no encuentra solucion — tipicamente cuando el banco combina depositos de
    multiples cortes en una sola linea.

    Args:
        depositos: Depositos TDC/TDD en orden de aparicion.
        cortes_con_target: Cortes con total_tdc > 0, ordenados por fecha.
        tolerancia: Tolerancia en pesos para considerar un corte completado.

    Returns:
        Tupla (asignaciones, sobrantes, mapa_virtual):
        - asignaciones: Lista de (corte, depositos_asignados).
        - sobrantes: Depositos no asignados a ningun corte.
        - mapa_virtual: Dict id(virtual) → id(original) para depositos split.
    """
    asignaciones = []
    cola = list(depositos)
    mapa_virtual: Dict[int, int] = {}

    for corte in cortes_con_target:
        target = corte.total_tdc
        if target is None or target <= 0:
            continue

        asignados: List[MovimientoBancario] = []
        acumulado = Decimal('0')

        while cola and (target - acumulado) > tolerancia:
            dep = cola.pop(0)
            faltante = target - acumulado

            if dep.monto <= faltante + tolerancia:
                # Deposito cabe completo en este corte
                asignados.append(dep)
                acumulado += dep.monto
            else:
                # Deposito excede: partir
                parte_1 = _clonar_deposito(dep, faltante)
                asignados.append(parte_1)
                acumulado += faltante

                remanente = dep.monto - faltante
                parte_2 = _clonar_deposito(dep, remanente)

                # Rastrear origen real (encadenar si dep ya es virtual)
                original_id = mapa_virtual.get(id(dep), id(dep))
                mapa_virtual[id(parte_1)] = original_id
                mapa_virtual[id(parte_2)] = original_id

                cola.insert(0, parte_2)

        if asignados:
            asignaciones.append((corte, asignados))
            logger.debug(
                "Split secuencial: corte {} → {} deps (${:,.2f}), "
                "target=${:,.2f}, diff=${:,.2f}",
                corte.fecha_corte, len(asignados), acumulado,
                target, abs(acumulado - target),
            )

    return asignaciones, cola, mapa_virtual


def _clonar_deposito(
    original: MovimientoBancario,
    nuevo_monto: Decimal,
) -> MovimientoBancario:
    """Crea clon de un deposito con monto diferente (para split secuencial)."""
    return MovimientoBancario(
        fecha=original.fecha,
        descripcion=original.descripcion,
        cargo=nuevo_monto if original.es_egreso else None,
        abono=nuevo_monto if original.es_ingreso else None,
        cuenta_banco=original.cuenta_banco,
        nombre_hoja=original.nombre_hoja,
        tipo_proceso=original.tipo_proceso,
    )


# --- Helpers ---


def _preparar_conexion(
    connector: Optional[SAV7Connector],
    dry_run: bool,
) -> Optional[SAV7Connector]:
    """Prepara conexion a BD si es necesario."""
    if connector is not None:
        return connector
    if not dry_run:
        return SAV7Connector()
    return None


def _obtener_cursor_lectura(
    db_connector: Optional[SAV7Connector],
):
    """Obtiene cursor de lectura si hay conexion."""
    if db_connector is None:
        return None
    try:
        return db_connector.db.conectar().cursor()
    except Exception as e:
        logger.warning("Sin conexion a BD para consultas: {}", e)
        return None


# --- Visualizacion ---


def _mostrar_plan(plan: PlanEjecucion):
    """Muestra un plan de ejecucion de forma legible."""
    print(f"\n{'─'*60}")
    print(f"PLAN: {plan.descripcion}")
    print(f"Fecha movimiento: {plan.fecha_movimiento}")
    print(f"Inserts: {plan.total_inserts} | Updates: {plan.total_updates}")
    print(f"{'─'*60}")

    if plan.advertencias:
        print("\n  ADVERTENCIAS:")
        for adv in plan.advertencias:
            print(f"    ! {adv}")

    if plan.validaciones:
        print("\n  VALIDACIONES:")
        for val in plan.validaciones:
            print(f"    v {val}")

    if plan.movimientos_pm:
        print(f"\n  SAVCheqPM ({len(plan.movimientos_pm)} movimientos):")
        for i, pm in enumerate(plan.movimientos_pm):
            monto = pm.ingreso if pm.ingreso > 0 else pm.egreso
            signo = '+' if pm.ingreso > 0 else '-'
            print(
                f"    {i+1}. Tipo={pm.tipo} | {pm.fpago} | "
                f"{signo}${monto:,.2f} | '{pm.concepto}'"
            )

    if plan.facturas_pmf:
        print(f"\n  SAVCheqPMF ({len(plan.facturas_pmf)} facturas):")
        for i, pmf in enumerate(plan.facturas_pmf):
            print(
                f"    {i+1}. {pmf.serie}-{pmf.num_factura} ({pmf.tipo_factura}) "
                f"| Aplicado=${pmf.ingreso:,.2f} | Total=${pmf.monto_factura:,.2f}"
            )
            if i >= 14:  # Limitar display
                print(f"    ... y {len(plan.facturas_pmf) - 15} mas")
                break

    if plan.compras:
        print(f"\n  SAVRecC/RecD ({len(plan.compras)} facturas compra):")
        for i, compra in enumerate(plan.compras):
            print(
                f"    {i+1}. Prov={compra.proveedor} | Fact={compra.factura} "
                f"| Sub=${compra.subtotal:,.2f} + IVA=${compra.iva:,.2f} "
                f"= ${compra.total:,.2f}"
            )

    if plan.cobros_cliente:
        print(f"\n  COBROS A CREAR ({len(plan.cobros_cliente)}):")
        for cobro in plan.cobros_cliente:
            print(
                f"    {cobro.serie}-{cobro.num_fac} | "
                f"${cobro.monto:,.2f} | Cliente {cobro.cliente} "
                f"({cobro.cliente_nombre[:25]})"
            )

    if plan.conciliaciones:
        print(f"\n  CONCILIACIONES ({len(plan.conciliaciones)}):")
        for conc in plan.conciliaciones:
            print(f"    UPDATE {conc['tabla']} SET {conc['campo']}={conc['valor_nuevo']}")
            print(f"      {conc['descripcion']}")

    if plan.lineas_poliza:
        print(f"\n  SAVPoliza ({len(plan.lineas_poliza)} lineas):")
        # Mostrar las primeras lineas como ejemplo
        for linea in plan.lineas_poliza[:8]:
            tipo_str = "CARGO" if linea.tipo_ca.value == 1 else "ABONO"
            monto = linea.cargo if linea.cargo > 0 else linea.abono
            doc_tipo = f" [{linea.doc_tipo}]" if linea.doc_tipo != 'CHEQUES' else ''
            print(
                f"    Mov {linea.movimiento}: {tipo_str:5} ${monto:>12,.2f} -> "
                f"{linea.cuenta}/{linea.subcuenta}{doc_tipo} | {linea.concepto[:50]}"
            )
        if len(plan.lineas_poliza) > 8:
            print(f"    ... y {len(plan.lineas_poliza) - 8} lineas mas")


# --- Confirmacion interactiva ---


def _confirmar_ejecucion(plan: PlanEjecucion) -> str:
    """Pide confirmacion al usuario para ejecutar un plan.

    Returns:
        'si', 'no', 'todos' o 'cancelar'
    """
    while True:
        resp = input(
            "\n  Ejecutar este plan? (s)i / (n)o / (t)odos / (c)ancelar: "
        ).strip().lower()
        if resp in ('s', 'si', 'yes', 'y'):
            return 'si'
        if resp in ('n', 'no'):
            return 'no'
        if resp in ('t', 'todos', 'all', 'a'):
            return 'todos'
        if resp in ('c', 'cancelar', 'cancel'):
            return 'cancelar'
        print("  Opcion no valida. Usa: s/n/t/c")


# --- Ejecucion ---


def _ejecutar_plan(
    plan: PlanEjecucion,
    connector: SAV7Connector,
) -> ResultadoProceso:
    """Ejecuta un plan dentro de una transaccion.

    Secuencia por movimiento:
    1. Obtener siguiente Folio (con lock)
    2. INSERT SAVCheqPM
    3. INSERT SAVCheqPMF (N facturas segun facturas_por_movimiento)
    4. INSERT SAVRecC/RecD (si hay compras)
    5. Obtener siguiente Poliza (con lock)
    6. INSERT SAVPoliza (N lineas segun lineas_por_movimiento)
    7. UPDATE SAVCheqPM SET NumPoliza
    """
    folios_creados = []
    num_poliza = None
    movimientos_saltados = 0
    movimientos_conciliados = 0

    try:
        with connector.get_cursor(transaccion=True) as cursor:
            factura_idx = 0
            linea_idx = 0
            compra_idx = 0

            for i, datos_pm in enumerate(plan.movimientos_pm):
                # Cuantas facturas y lineas corresponden a este movimiento
                n_facturas = (
                    plan.facturas_por_movimiento[i]
                    if i < len(plan.facturas_por_movimiento)
                    else 1
                )
                n_lineas = (
                    plan.lineas_por_movimiento[i]
                    if i < len(plan.lineas_por_movimiento)
                    else 6
                )

                # CHECK: verificar si el movimiento ya existe en BD
                monto_check = (
                    datos_pm.ingreso if datos_pm.ingreso > 0
                    else datos_pm.egreso
                )
                es_ingreso = datos_pm.ingreso > 0
                existente = buscar_movimiento_existente(
                    cursor, datos_pm.banco, datos_pm.cuenta,
                    datos_pm.dia, datos_pm.mes, datos_pm.age,
                    monto_check, es_ingreso,
                )

                if existente:
                    folio_existente, ya_conciliado = existente
                    if not ya_conciliado:
                        # Registro existente (manual o de otro origen) → conciliar
                        conciliar_movimiento(cursor, folio_existente)
                        movimientos_conciliados += 1
                        folios_creados.append(folio_existente)
                        logger.info(
                            "Movimiento existente conciliado: Folio={}, {} ${:,.2f}",
                            folio_existente, datos_pm.concepto[:50], monto_check,
                        )
                    else:
                        # Ya conciliado (duplicado de ejecucion anterior)
                        movimientos_saltados += 1
                        logger.warning(
                            "Movimiento ya existe y conciliado (Folio={}), "
                            "saltando: {} ${:,.2f}",
                            folio_existente, datos_pm.concepto[:50], monto_check,
                        )
                    # Avanzar indices sin insertar
                    factura_idx += n_facturas
                    linea_idx += n_lineas
                    if compra_idx < len(plan.compras):
                        compra_idx += 1
                    continue

                # 1. Siguiente Folio
                folio = obtener_siguiente_folio(cursor)
                folios_creados.append(folio)

                # 2. INSERT SAVCheqPM
                insertar_movimiento(cursor, datos_pm, folio)

                # 3. INSERT SAVCheqPMF (N facturas)
                for j in range(n_facturas):
                    if factura_idx < len(plan.facturas_pmf):
                        datos_pmf = plan.facturas_pmf[factura_idx]
                        insertar_factura_movimiento(
                            cursor, datos_pmf,
                            banco=datos_pm.banco,
                            cuenta=datos_pm.cuenta,
                            age=datos_pm.age,
                            mes=datos_pm.mes,
                            folio=folio,
                            dia=datos_pm.dia,
                        )
                        factura_idx += 1

                # 4. INSERT SAVRecC/RecD (compras, si aplica)
                #    + SAVRecPago + SAVCheqPMP (vinculo movimiento ↔ factura)
                if compra_idx < len(plan.compras):
                    datos_compra = plan.compras[compra_idx]
                    try:
                        num_rec = insertar_factura_compra(cursor, datos_compra)
                    except Exception as e:
                        raise RuntimeError(
                            f"Error INSERT SAVRecC/RecD: {e}"
                        ) from e

                    # Crear pago y detalle si tiene proveedor
                    if datos_pm.proveedor:
                        try:
                            pago = insertar_rec_pago(
                                cursor,
                                serie='F',
                                num_rec=num_rec,
                                proveedor=datos_pm.proveedor,
                                proveedor_nombre=datos_pm.proveedor_nombre,
                                fecha=datetime(
                                    datos_pm.age, datos_pm.mes, datos_pm.dia,
                                ),
                                monto=datos_compra.total,
                                banco=datos_pm.banco,
                                cuenta=datos_pm.cuenta,
                                folio=folio,
                                factura=datos_compra.factura,
                            )
                        except Exception as e:
                            raise RuntimeError(
                                f"Error INSERT SAVRecPago: {e}"
                            ) from e
                        try:
                            insertar_cheq_pmp(
                                cursor,
                                banco=datos_pm.banco,
                                cuenta=datos_pm.cuenta,
                                age=datos_pm.age,
                                mes=datos_pm.mes,
                                folio=folio,
                                num_rec=num_rec,
                                pago=pago,
                                fecha=datetime(
                                    datos_pm.age, datos_pm.mes, datos_pm.dia,
                                ),
                                monto=datos_compra.total,
                                iva=datos_compra.iva,
                                factura=datos_compra.factura,
                                proveedor=datos_pm.proveedor,
                            )
                        except Exception as e:
                            raise RuntimeError(
                                f"Error INSERT SAVCheqPMP: {e}"
                            ) from e

                    compra_idx += 1

                # 5-7. Poliza (solo si hay lineas para este movimiento)
                lineas_mov = plan.lineas_poliza[linea_idx:linea_idx + n_lineas]
                linea_idx += n_lineas

                # Resolver placeholders {folio} en conceptos de poliza
                for linea in lineas_mov:
                    if '{folio}' in linea.concepto:
                        linea.concepto = linea.concepto.format(folio=folio)

                num_poliza = 0
                if lineas_mov:
                    num_poliza = obtener_siguiente_poliza(cursor)

                    insertar_poliza(
                        cursor,
                        num_poliza=num_poliza,
                        lineas=lineas_mov,
                        folio=folio,
                        fecha=datetime(
                            datos_pm.age, datos_pm.mes, datos_pm.dia,
                        ),
                        tipo_poliza=datos_pm.tipo_poliza,
                        concepto_encabezado=datos_pm.concepto,
                    )

                    poliza_cargos = sum(l.cargo for l in lineas_mov)
                    poliza_abonos = sum(l.abono for l in lineas_mov)
                    actualizar_num_poliza(
                        cursor, folio, num_poliza,
                        poliza_cargos, poliza_abonos,
                    )

                monto = datos_pm.ingreso if datos_pm.ingreso > 0 else datos_pm.egreso
                logger.info(
                    "Movimiento {}/{}: Folio={}, Poliza={}, ${:,.2f}",
                    i + 1, len(plan.movimientos_pm),
                    folio, num_poliza, monto,
                )

        # Commit exitoso
        if movimientos_conciliados > 0:
            logger.info(
                "{} movimientos existentes fueron conciliados",
                movimientos_conciliados,
            )
            plan.validaciones.append(
                f"{movimientos_conciliados} movimientos existentes conciliados"
            )
        if movimientos_saltados > 0:
            logger.info(
                "{} movimientos ya existian y fueron saltados",
                movimientos_saltados,
            )
            plan.advertencias.append(
                f"{movimientos_saltados} movimientos ya existian (saltados)"
            )

        return ResultadoProceso(
            exito=True,
            tipo_proceso=plan.tipo_proceso,
            descripcion=plan.descripcion,
            folios=folios_creados,
            num_poliza=num_poliza,
            plan=plan,
        )

    except Exception as e:
        logger.error("Error ejecutando plan: {}", e)
        return ResultadoProceso(
            exito=False,
            tipo_proceso=plan.tipo_proceso,
            descripcion=plan.descripcion,
            error=str(e),
            plan=plan,
        )


def _ejecutar_conciliacion(
    plan: PlanEjecucion,
    connector: SAV7Connector,
) -> ResultadoProceso:
    """Ejecuta conciliaciones dentro de una transaccion.

    Para cada conciliacion:
    1. UPDATE SAVCheqPM SET Conciliada = 1
    2. Si tiene lineas de poliza (pagos a proveedores):
       INSERT SAVPoliza usando el NumPoliza pre-asignado del movimiento
    3. Si tiene factura vinculada (SAVCheqPMP):
       UPDATE SAVRecPago SET Estatus='Pagado', FPago, Banco, Referencia...
       UPDATE SAVRecC SET Saldo=0, Estatus='Tot.Pagada'
    """
    # Mapeo TipoEgreso (SAVCheqPM) → FPago (SAVRecPago varchar(15))
    MAPA_FPAGO = {
        'TRANSFERENCIA': 'Transferencia',
        'TRANSFERENCIA SPEI': 'Transferencia',
        'TARJETA': 'TARJETA',
        'TARJETA DE CREDITO': 'TARJETA',
        'INTERBANCARIO': 'Transferencia',
        'EFECTIVO': 'Efectivo',
        'CHEQUE': 'Cheque',
        'NA': 'NA',
    }

    try:
        poliza_offset = 0
        with connector.get_cursor(transaccion=True) as cursor:
            for conc in plan.conciliaciones:
                folio = conc['folio']
                cursor.execute("""
                    UPDATE SAVCheqPM
                    SET Conciliada = 1
                    WHERE Folio = ?
                """, (folio,))
                logger.info(
                    "Conciliado: Folio {} → Conciliada=1",
                    folio,
                )

                # Insertar poliza si hay lineas (pagos a proveedores)
                num_poliza = conc.get('num_poliza')
                num_lineas = conc.get('lineas_poliza', 0)
                if num_poliza and num_lineas > 0:
                    lineas = plan.lineas_poliza[
                        poliza_offset:poliza_offset + num_lineas
                    ]
                    fecha_mov = datetime(
                        plan.fecha_movimiento.year,
                        plan.fecha_movimiento.month,
                        plan.fecha_movimiento.day,
                    )
                    insertar_poliza(
                        cursor,
                        num_poliza,
                        lineas,
                        folio,
                        fecha_mov,
                        tipo_poliza='EGRESO',
                        concepto_encabezado=conc.get('descripcion', ''),
                    )
                    # Actualizar PolizaCargos/PolizaAbonos
                    poliza_cargos = sum(l.cargo for l in lineas)
                    poliza_abonos = sum(l.abono for l in lineas)
                    cursor.execute("""
                        UPDATE SAVCheqPM
                        SET PolizaCargos = ?, PolizaAbonos = ?
                        WHERE Folio = ?
                    """, (poliza_cargos, poliza_abonos, folio))
                    logger.info(
                        "Poliza pago: Folio {} → Poliza {} ({} lineas, "
                        "Cargos=${:,.2f})",
                        folio, num_poliza, num_lineas, poliza_cargos,
                    )
                    poliza_offset += num_lineas

                # Actualizar factura de compras y registro de pago
                num_rec = conc.get('factura_num_rec')
                serie = conc.get('factura_serie', 'F')
                if num_rec:
                    cuenta_banco = conc.get('cuenta_banco', '')
                    tipo_egreso = conc.get('tipo_egreso', '')
                    banco_nombre = conc.get('banco_nombre', 'BANREGIO')
                    fpago = MAPA_FPAGO.get(tipo_egreso, tipo_egreso[:15])
                    referencia = f"{cuenta_banco}F: {folio}"
                    fecha_pago = plan.fecha_movimiento

                    # UPDATE SAVRecPago: Programado → Pagado
                    cursor.execute("""
                        UPDATE SAVRecPago
                        SET Estatus = 'Pagado',
                            FPago = ?,
                            Banco = ?,
                            Referencia = ?,
                            SolicitudPago = 1,
                            Paridad = 1,
                            Fecha = ?,
                            UltimoCambio = CAST(GETDATE() AS DATE),
                            UltimoCambioHora = CAST(CAST(GETDATE() AS FLOAT)
                                - FLOOR(CAST(GETDATE() AS FLOAT)) AS DATETIME)
                        WHERE Serie = ? AND NumRec = ?
                    """, (
                        fpago, banco_nombre, referencia,
                        fecha_pago.isoformat(),
                        serie, num_rec,
                    ))
                    logger.info(
                        "RecPago: {}-{} → Estatus='Pagado', FPago='{}', "
                        "Ref='{}'",
                        serie, num_rec, fpago, referencia,
                    )

                    # UPDATE SAVRecC: Saldo=0, Estatus='Tot.Pagada'
                    cursor.execute("""
                        UPDATE SAVRecC
                        SET Saldo = 0,
                            Estatus = 'Tot.Pagada'
                        WHERE Serie = ? AND NumRec = ?
                    """, (serie, num_rec))
                    logger.info(
                        "RecC: {}-{} → Saldo=0, Estatus='Tot.Pagada'",
                        serie, num_rec,
                    )

        return ResultadoProceso(
            exito=True,
            tipo_proceso=plan.tipo_proceso,
            descripcion=plan.descripcion,
            plan=plan,
        )

    except Exception as e:
        logger.error("Error ejecutando conciliacion: {}", e)
        return ResultadoProceso(
            exito=False,
            tipo_proceso=plan.tipo_proceso,
            descripcion=plan.descripcion,
            error=str(e),
            plan=plan,
        )


def _ejecutar_cobro_completo(
    plan: PlanEjecucion,
    connector: SAV7Connector,
) -> ResultadoProceso:
    """Ejecuta creacion de cobro completo (Fase A).

    Afecta 4 tablas en una sola transaccion:
    1. INSERT SAVFactCob (cobro del modulo Comercial)
    2. UPDATE SAVFactC (Saldo, Estatus)
    3. INSERT SAVCheqPM (movimiento bancario Tipo=1, Conciliada=1)
    4. INSERT SAVPoliza (SAV7-COMERCIAL, 2 lineas, DocTipo='COBRO MULTIPLE')
    5. UPDATE SAVCheqPM NumPoliza
    """
    folios = []

    try:
        with connector.get_cursor(transaccion=True) as cursor:
            for cobro_data in plan.cobros_cliente:
                # 1. Consecutivos
                num_cobro = obtener_siguiente_cobro(cursor)
                num_cobro_multiple = obtener_siguiente_cobro_multiple(cursor)
                folio = obtener_siguiente_folio(cursor)
                num_poliza = obtener_siguiente_poliza(
                    cursor, fuente='SAV7-COMERCIAL',
                )

                # 2. INSERT SAVFactCob
                insertar_cobro_factcob(
                    cursor, cobro_data, num_cobro, num_cobro_multiple,
                )

                # 3. UPDATE SAVFactC (Saldo, Estatus)
                actualizar_factura_cobrada(
                    cursor, cobro_data.serie,
                    cobro_data.num_fac, cobro_data.monto,
                )

                # 4. INSERT SAVCheqPM (Tipo=1, Clase=DEPOSITOS, Conciliada=1)
                concepto = (
                    f"CLIENTE: {cobro_data.cliente}-"
                    f"{cobro_data.cliente_nombre[:15]} "
                    f"CM: {num_cobro_multiple} "
                    f"FACT: {cobro_data.serie}-{cobro_data.num_fac},"
                )
                referencia_cobro = (
                    f"CLIENTE: {cobro_data.cliente}-"
                    f"{cobro_data.cliente_nombre}"
                )[:40]
                datos_pm = DatosMovimientoPM(
                    banco=cobro_data.banco,
                    cuenta=cobro_data.cuenta_banco,
                    age=cobro_data.fecha_cobro.year,
                    mes=cobro_data.fecha_cobro.month,
                    dia=cobro_data.fecha_cobro.day,
                    tipo=1,
                    ingreso=cobro_data.monto,
                    egreso=Decimal('0'),
                    concepto=concepto,
                    clase='DEPOSITOS',
                    fpago='Transferencia',
                    tipo_egreso='NA',
                    conciliada=1,
                    paridad=Decimal('1.0000'),
                    tipo_poliza='INGRESO',
                    num_factura=f'CM: {num_cobro_multiple}',
                    referencia=referencia_cobro,
                    referencia2=f"FP: Transferencia B: {cobro_data.banco} Ref: ",
                )
                insertar_movimiento(cursor, datos_pm, folio)
                folios.append(folio)

                # 5. INSERT SAVPoliza (SAV7-COMERCIAL, 2 lineas)
                lineas = [
                    LineaPoliza(
                        movimiento=1,
                        cuenta=cobro_data.cuenta_contable,
                        subcuenta=cobro_data.subcuenta_contable,
                        tipo_ca=TipoCA.CARGO,
                        cargo=cobro_data.monto,
                        abono=Decimal('0'),
                        concepto=(
                            f"Banco: {cobro_data.banco}. "
                            f"CM: {num_cobro_multiple} "
                            f"Fact: {cobro_data.serie}-{cobro_data.num_fac} "
                            f"FPago: Transferenci"
                        ),
                        doc_tipo='COBRO MULTIPLE',
                    ),
                    LineaPoliza(
                        movimiento=2,
                        cuenta='1210',
                        subcuenta='010000',
                        tipo_ca=TipoCA.ABONO,
                        cargo=Decimal('0'),
                        abono=cobro_data.monto,
                        concepto=(
                            f"Cliente:{cobro_data.cliente} "
                            f"Nombre:{cobro_data.cliente_nombre[:15]}  "
                            f"CM: {num_cobro_multiple}"
                        ),
                        doc_tipo='COBRO MULTIPLE',
                    ),
                ]
                insertar_poliza(
                    cursor,
                    num_poliza=num_poliza,
                    lineas=lineas,
                    folio=folio,
                    fecha=datetime.combine(
                        cobro_data.fecha_cobro, datetime.min.time(),
                    ),
                    tipo_poliza='INGRESO',
                    concepto_encabezado=concepto,
                    fuente='SAV7-COMERCIAL',
                )

                # 6. UPDATE SAVCheqPM NumPoliza
                actualizar_num_poliza(cursor, folio, num_poliza)

                logger.info(
                    "Cobro creado: {}-{} ${:,.2f} | Cobro={}, CM={}, "
                    "Folio={}, Poliza={}",
                    cobro_data.serie, cobro_data.num_fac, cobro_data.monto,
                    num_cobro, num_cobro_multiple, folio, num_poliza,
                )

        return ResultadoProceso(
            exito=True,
            tipo_proceso=plan.tipo_proceso,
            descripcion=plan.descripcion,
            folios=folios,
            plan=plan,
        )

    except Exception as e:
        logger.error("Error creando cobro: {}", e)
        return ResultadoProceso(
            exito=False,
            tipo_proceso=plan.tipo_proceso,
            descripcion=plan.descripcion,
            error=str(e),
            plan=plan,
        )
