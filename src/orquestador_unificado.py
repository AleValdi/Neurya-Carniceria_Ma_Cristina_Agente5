"""Orquestador unificado: parsea una vez, clasifica y procesa dia por dia.

A diferencia de las funciones individuales procesar_X() en orquestador.py
(que re-parsean el estado de cuenta cada vez), este modulo parsea y clasifica
UNA sola vez, y retorna un ResultadoLinea por cada linea del estado de cuenta.
"""

import re
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from src.clasificador import clasificar_movimientos, resumen_clasificacion
from src.entrada.estado_cuenta import parsear_estado_cuenta_plano
from src.entrada.tesoreria import parsear_tesoreria
from src.entrada.nomina import parsear_nomina
from src.entrada.ajustes_impuestos import parsear_ajustes_impuestos
from src.entrada.impuestos_pdf import (
    parsear_imss,
    parsear_impuesto_estatal,
    parsear_impuesto_federal,
)
from src.erp.sav7_connector import SAV7Connector
from config.settings import CUENTAS_BANCARIAS, CUENTA_POR_NUMERO, CuentasContables
from src.models import (
    AccionLinea,
    CorteVentaDiaria,
    DatosMovimientoPM,
    DatosNomina,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    ResultadoLinea,
    ResultadoProceso,
    TipoCA,
    TipoProceso,
)
from src.procesadores.comisiones import ProcesadorComisiones
from src.procesadores.nomina_proc import ProcesadorNomina
from src.procesadores.conciliacion_cobros import ProcesadorConciliacionCobros
from src.procesadores.conciliacion_pagos import ProcesadorConciliacionPagos
from src.procesadores.impuestos import ProcesadorImpuestos
from src.procesadores.pago_gastos import ProcesadorPagoGastos
from src.procesadores.traspasos import ProcesadorTraspasos
from src.procesadores.venta_efectivo import ProcesadorVentaEfectivo
from src.procesadores.venta_tdc import ProcesadorVentaTDC
from src.validacion import validar_venta_efectivo, validar_venta_tdc

# Reutilizar helpers existentes del orquestador original
from src.orquestador import (
    _asignar_multi_corte,
    _asignar_secuencial_con_split,
    _buscar_corte_efectivo,
    _buscar_cortes_tdc,
    _ejecutar_cobro_completo,
    _ejecutar_conciliacion,
    _ejecutar_pago_gastos,
    _ejecutar_plan,
    _encontrar_subset_por_suma,
    _obtener_cursor_lectura,
)


# ---------------------------------------------------------------------------
# Helper de idempotencia
# ---------------------------------------------------------------------------


def _ajustar_nota_idempotencia(
    rl: ResultadoLinea,
    resultado: ResultadoProceso,
):
    """Ajusta accion y nota si el movimiento ya existia en BD.

    Llamar despues de cada _ejecutar_plan() exitoso para reflejar
    en la nota si el movimiento fue saltado o conciliado (no insertado).
    """
    total_plan = resultado.movimientos_saltados + resultado.movimientos_conciliados_existentes
    total_folios = len(resultado.folios)

    if resultado.movimientos_saltados > 0 and total_folios == 0:
        # Todos fueron saltados (ya existian y conciliados), ninguno insertado
        rl.accion = AccionLinea.OMITIR
        rl.nota = "Ya registrado y conciliado"
    elif (
        resultado.movimientos_conciliados_existentes > 0
        and total_plan > 0
        and total_plan == resultado.movimientos_conciliados_existentes + resultado.movimientos_saltados
        and total_folios == resultado.movimientos_conciliados_existentes
    ):
        # Todos existian, algunos fueron conciliados ahora
        rl.accion = AccionLinea.CONCILIAR
        folio_txt = ', '.join(str(f) for f in resultado.folios)
        rl.nota = f"Ya registrado, conciliado ahora (Folio {folio_txt})"
        rl.folios = resultado.folios


# ---------------------------------------------------------------------------
# Funcion principal
# ---------------------------------------------------------------------------

def procesar_estado_cuenta(
    ruta_estado_cuenta: Path,
    ruta_tesoreria: Optional[Path] = None,
    ruta_nomina: Optional[Path] = None,
    ruta_lista_raya: Optional[Path] = None,
    ruta_imss: Optional[Path] = None,
    rutas_impuestos: Optional[Dict[str, Path]] = None,
    dry_run: bool = True,
    solo_fecha: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    connector: Optional[SAV7Connector] = None,
) -> List[ResultadoLinea]:
    """Procesa el estado de cuenta completo, linea por linea.

    Parsea UNA vez, clasifica UNA vez, procesa dia por dia.
    Si solo_fecha y fecha_fin se proporcionan, procesa el rango [solo_fecha, fecha_fin].
    Si solo solo_fecha, procesa ese unico dia.
    Retorna un ResultadoLinea por cada linea del estado de cuenta.
    """
    # --- 1. Parsear todo UNA vez ---
    logger.info("Parseando estado de cuenta...")
    movimientos = parsear_estado_cuenta_plano(ruta_estado_cuenta)
    logger.info("  {} movimientos parseados", len(movimientos))

    cortes = {}
    if ruta_tesoreria and ruta_tesoreria.exists():
        cortes = parsear_tesoreria(ruta_tesoreria)
        logger.info("  {} cortes de tesoreria parseados", len(cortes))

    datos_nomina = None
    if ruta_nomina and ruta_nomina.exists():
        datos_nomina = parsear_nomina(ruta_nomina, ruta_lista_raya=ruta_lista_raya)
        logger.info("  Nomina parseada: #{}", datos_nomina.numero_nomina)

    datos_imss = None
    if ruta_imss and ruta_imss.exists():
        datos_imss = parsear_imss(ruta_imss)
        logger.info("  IMSS parseado: {}", datos_imss.periodo)

    datos_federal = None
    datos_estatal = None
    if rutas_impuestos:
        ruta_f1 = rutas_impuestos.get('ruta_acuse_federal_1')
        ruta_f2 = rutas_impuestos.get('ruta_acuse_federal_2')
        ruta_ieps = rutas_impuestos.get('ruta_detalle_ieps')
        ruta_decl = rutas_impuestos.get('ruta_declaracion_completa')
        ruta_est = rutas_impuestos.get('ruta_impuesto_estatal')

        if ruta_f1 and ruta_f2:
            datos_federal = parsear_impuesto_federal(
                ruta_acuse_1=ruta_f1,
                ruta_acuse_2=ruta_f2,
                ruta_detalle_ieps=ruta_ieps,
                ruta_declaracion_completa=ruta_decl,
            )
            logger.info("  Impuesto federal parseado: {}", datos_federal.periodo)

        if ruta_est:
            datos_estatal = parsear_impuesto_estatal(ruta_est)
            logger.info("  Impuesto estatal parseado: {}", datos_estatal.periodo)

    # --- 1.5 Override opcional desde Excel de ajustes ---
    ruta_ajustes = ruta_estado_cuenta.parent / 'AJUSTES_IMPUESTOS.xlsx'
    if ruta_ajustes.exists():
        ajustes = parsear_ajustes_impuestos(ruta_ajustes)
        if ajustes:
            resumen_ajustes = {
                k: f"${v:,.2f}" if isinstance(v, Decimal) else f"{len(v)} retenciones"
                for k, v in ajustes.items()
            }
            logger.info("  Ajustes impuestos desde Excel: {}", resumen_ajustes)
            if 'total_imss' in ajustes and datos_imss:
                datos_imss.total_imss = ajustes['total_imss']
            if 'iva_acumulable' in ajustes and datos_federal:
                datos_federal.iva_acumulable = ajustes['iva_acumulable']
            if 'iva_acreditable' in ajustes and datos_federal:
                datos_federal.iva_acreditable = ajustes['iva_acreditable']
            if 'retenciones_iva' in ajustes and datos_federal:
                datos_federal.retenciones_iva = ajustes['retenciones_iva']
                logger.info("  Retenciones IVA desde Excel: {}",
                            [(r.proveedor, r.nombre, f"${r.monto:,.2f}")
                             for r in ajustes['retenciones_iva']])

    # --- 2. Clasificar UNA vez ---
    logger.info("Clasificando movimientos...")
    clasificar_movimientos(movimientos)
    resumen = resumen_clasificacion(movimientos)
    for tipo, conteo in sorted(resumen.items()):
        logger.info("  {}: {}", tipo, conteo)

    # --- 3. Inicializar ResultadoLinea para CADA movimiento ---
    lineas = []
    indice = {}  # id(mov) -> ResultadoLinea
    for mov in movimientos:
        tipo = mov.tipo_proceso or TipoProceso.DESCONOCIDO
        accion = (
            AccionLinea.DESCONOCIDO
            if tipo == TipoProceso.DESCONOCIDO
            else AccionLinea.SIN_PROCESAR
        )
        rl = ResultadoLinea(
            movimiento=mov,
            tipo_clasificado=tipo,
            accion=accion,
        )
        lineas.append(rl)
        indice[id(mov)] = rl

    # --- 4. Agrupar por fecha ---
    movs_por_fecha = defaultdict(list)
    for mov in movimientos:
        movs_por_fecha[mov.fecha].append(mov)

    fechas = sorted(movs_por_fecha.keys())
    if solo_fecha and fecha_fin:
        # Rango de fechas [solo_fecha, fecha_fin]
        # Incluir TODOS los dias del rango (no solo los que tienen movimientos)
        # para que la conciliacion con retraso de 1 dia funcione incluso
        # cuando el dia siguiente no tiene movimientos propios.
        fechas_con_movs = set(f for f in fechas if solo_fecha <= f <= fecha_fin)
        todas_fechas = []
        d = solo_fecha
        while d <= fecha_fin:
            todas_fechas.append(d)
            d += timedelta(days=1)
        fechas = todas_fechas
        if not fechas_con_movs:
            logger.warning("No hay movimientos en rango {} a {}", solo_fecha, fecha_fin)
            return lineas
    elif solo_fecha:
        if solo_fecha in movs_por_fecha:
            fechas = [solo_fecha]
        else:
            logger.warning("No hay movimientos para {}", solo_fecha)
            return lineas

    # --- 5. Procesar dia por dia ---
    for fecha in fechas:
        movs_dia = movs_por_fecha[fecha]
        logger.info("=" * 60)
        logger.info("PROCESANDO DIA: {} ({} movimientos)", fecha, len(movs_dia))
        logger.info("=" * 60)

        _procesar_dia(
            fecha=fecha,
            movimientos=movs_dia,
            indice=indice,
            cortes=cortes,
            datos_nomina=datos_nomina,
            datos_federal=datos_federal,
            datos_estatal=datos_estatal,
            datos_imss=datos_imss,
            connector=connector,
            dry_run=dry_run,
            movs_por_fecha=movs_por_fecha,
        )

    return lineas


# ---------------------------------------------------------------------------
# Dispatch por dia
# ---------------------------------------------------------------------------

def _procesar_dia(
    fecha: date,
    movimientos: List[MovimientoBancario],
    indice: Dict[int, ResultadoLinea],
    cortes: Dict[date, CorteVentaDiaria],
    datos_nomina: Optional[DatosNomina],
    datos_federal,
    datos_estatal,
    datos_imss,
    connector: Optional[SAV7Connector],
    dry_run: bool,
    movs_por_fecha: Optional[Dict] = None,
):
    """Procesa todos los tipos de movimiento para un dia."""
    # Agrupar por tipo
    por_tipo = defaultdict(list)
    for mov in movimientos:
        tipo = mov.tipo_proceso or TipoProceso.DESCONOCIDO
        por_tipo[tipo].append(mov)

    # 1. Traspasos
    _procesar_traspasos(por_tipo, indice, fecha, connector, dry_run)

    # 2. Comisiones
    _procesar_comisiones(por_tipo, indice, fecha, connector, dry_run)

    # 3. Ventas TDC/TDD
    _procesar_ventas_tdc(
        por_tipo, indice, fecha, cortes, connector, dry_run,
        movs_por_fecha=movs_por_fecha,
    )

    # 4. Ventas Efectivo
    _procesar_ventas_efectivo(por_tipo, indice, fecha, cortes, connector, dry_run)

    # 5. Nomina (solo dispersion)
    _procesar_nomina(por_tipo, indice, fecha, datos_nomina, connector, dry_run)

    # 5.5 Cobros de cheque (secundarios de nomina: cheques, finiquito, etc.)
    _procesar_cobros_cheque(por_tipo, indice, fecha, datos_nomina, connector, dry_run)

    # 5.6 Pagos desde cuenta gastos (crear movimiento, no conciliar)
    _procesar_pago_gastos(
        por_tipo, indice, fecha, connector, dry_run,
        movs_por_fecha=movs_por_fecha,
    )

    # 6. Conciliaciones (pagos + cobros)
    # Pagos a proveedores se afectan con 1 dia de retraso (dia anterior)
    _procesar_conciliaciones(
        por_tipo, indice, fecha, connector, dry_run,
        movs_por_fecha=movs_por_fecha,
    )

    # 7. Impuestos (federal + estatal + IMSS)
    _procesar_impuestos(
        por_tipo, indice, fecha, datos_federal, datos_estatal,
        datos_imss, connector, dry_run,
    )

    # 8. TRASPASO_INGRESO → OMITIR
    for mov in por_tipo.get(TipoProceso.TRASPASO_INGRESO, []):
        rl = indice[id(mov)]
        rl.accion = AccionLinea.OMITIR
        rl.nota = "Ingreso generado automaticamente por el traspaso egreso"


# ---------------------------------------------------------------------------
# Procesadores por tipo
# ---------------------------------------------------------------------------

def _procesar_traspasos(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Procesa traspasos del dia."""
    movs = por_tipo.get(TipoProceso.TRASPASO, [])
    if not movs:
        return

    logger.info("  Traspasos: {} movimientos", len(movs))
    procesador = ProcesadorTraspasos()
    cursor = _obtener_cursor_lectura(connector)

    try:
        plan = procesador.construir_plan(
            movimientos=movs, fecha=fecha, cursor=cursor,
        )

        if not plan.movimientos_pm:
            for mov in movs:
                indice[id(mov)].nota = "Sin movimientos generados"
            return

        if dry_run:
            # Cada traspaso genera 2 movimientos_pm (egreso + ingreso)
            for mov in movs:
                indice[id(mov)].accion = AccionLinea.INSERT
                indice[id(mov)].nota = "DRY-RUN"
            return

        resultado = _ejecutar_plan(plan, connector)
        # Mapear: cada traspaso egreso genera 2 folios (egreso + ingreso)
        for i, mov in enumerate(movs):
            rl = indice[id(mov)]
            if resultado.exito:
                rl.accion = AccionLinea.INSERT
                rl.resultado = resultado
                idx = i * 2
                if idx < len(resultado.folios):
                    rl.folios = resultado.folios[idx:idx + 2]
                _ajustar_nota_idempotencia(rl, resultado)
            else:
                rl.accion = AccionLinea.ERROR
                rl.nota = resultado.error
    finally:
        if cursor:
            cursor.close()


def _procesar_comisiones(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Procesa comisiones bancarias del dia."""
    tipos_comision = (
        TipoProceso.COMISION_SPEI,
        TipoProceso.COMISION_SPEI_IVA,
        TipoProceso.COMISION_TDC,
        TipoProceso.COMISION_TDC_IVA,
    )
    movs = []
    for t in tipos_comision:
        movs.extend(por_tipo.get(t, []))

    if not movs:
        return

    logger.info("  Comisiones: {} movimientos", len(movs))
    procesador = ProcesadorComisiones()

    plan = procesador.construir_plan(movimientos=movs, fecha=fecha)

    if not plan.movimientos_pm:
        for mov in movs:
            indice[id(mov)].nota = "Sin movimientos generados"
        return

    if dry_run:
        for mov in movs:
            indice[id(mov)].accion = AccionLinea.INSERT
            indice[id(mov)].nota = "DRY-RUN"
        return

    resultado = _ejecutar_plan(plan, connector)

    # Comisiones agrupan por cuenta: 1 folio por cuenta bancaria
    # Todas las lineas de la misma cuenta comparten folio
    por_cuenta = defaultdict(list)
    for mov in movs:
        por_cuenta[mov.cuenta_banco].append(mov)

    for cuenta_idx, (cuenta, movs_cuenta) in enumerate(sorted(por_cuenta.items())):
        folio = (
            resultado.folios[cuenta_idx]
            if resultado.exito and cuenta_idx < len(resultado.folios)
            else None
        )
        for mov in movs_cuenta:
            rl = indice[id(mov)]
            if resultado.exito:
                rl.accion = AccionLinea.INSERT
                rl.resultado = resultado
                if folio:
                    rl.folios = [folio]
                _ajustar_nota_idempotencia(rl, resultado)
            else:
                rl.accion = AccionLinea.ERROR
                rl.nota = resultado.error


def _procesar_ventas_tdc(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    cortes: Dict[date, CorteVentaDiaria],
    connector: Optional[SAV7Connector],
    dry_run: bool,
    movs_por_fecha: Optional[Dict] = None,
):
    """Procesa ventas TDC/TDD del dia."""
    movs_tdc = por_tipo.get(TipoProceso.VENTA_TDC, [])
    movs_tdd = por_tipo.get(TipoProceso.VENTA_TDD, [])
    movs = movs_tdc + movs_tdd

    if not movs:
        return

    logger.info("  Ventas TDC/TDD: {} movimientos", len(movs))

    # Calcular todas las fechas con depositos TDC para rango dinamico
    fechas_deposito_tdc = None
    if movs_por_fecha:
        fechas_deposito_tdc = sorted([
            f for f, movs_f in movs_por_fecha.items()
            if any(
                m.tipo_proceso in (TipoProceso.VENTA_TDC, TipoProceso.VENTA_TDD)
                for m in movs_f
            )
        ])

    cortes_matching = _buscar_cortes_tdc(fecha, cortes, fechas_deposito_tdc)
    if not cortes_matching:
        logger.warning("  Sin cortes de venta para depositos del {}", fecha)
        for mov in movs:
            rl = indice[id(mov)]
            rl.accion = AccionLinea.SIN_PROCESAR
            rl.nota = "Sin corte de tesoreria para esta fecha"
        return

    procesador = ProcesadorVentaTDC()
    cursor = _obtener_cursor_lectura(connector)

    try:
        if len(cortes_matching) == 1:
            _procesar_tdc_un_corte(
                procesador, movs, fecha, cortes_matching[0],
                indice, connector, cursor, dry_run,
            )
        else:
            _procesar_tdc_multi_corte(
                procesador, movs, fecha, cortes_matching,
                indice, connector, cursor, dry_run,
            )
    finally:
        if cursor:
            cursor.close()


def _procesar_tdc_un_corte(
    procesador: ProcesadorVentaTDC,
    movimientos: List[MovimientoBancario],
    fecha: date,
    corte: CorteVentaDiaria,
    indice: Dict[int, ResultadoLinea],
    connector: Optional[SAV7Connector],
    cursor,
    dry_run: bool,
):
    """Procesa TDC con un solo corte de venta."""
    errores_val = validar_venta_tdc(movimientos, corte)
    for err in errores_val:
        logger.warning("  Validacion TDC: {}", err)

    plan = procesador.construir_plan(
        movimientos=movimientos, fecha=fecha,
        cursor=cursor, corte_venta=corte,
    )

    if dry_run:
        for mov in movimientos:
            indice[id(mov)].accion = AccionLinea.INSERT
            indice[id(mov)].nota = f"DRY-RUN | Corte {corte.fecha_corte}"
        return

    resultado = _ejecutar_plan(plan, connector)
    # 1:1 mapping: plan.movimientos_pm[i] corresponde a movimientos[i]
    for i, mov in enumerate(movimientos):
        rl = indice[id(mov)]
        if resultado.exito:
            rl.accion = AccionLinea.INSERT
            rl.resultado = resultado
            if i < len(resultado.folios):
                rl.folios = [resultado.folios[i]]
            _ajustar_nota_idempotencia(rl, resultado)
        else:
            rl.accion = AccionLinea.ERROR
            rl.nota = resultado.error


def _construir_plan_traspaso_caja_chica(
    mov: MovimientoBancario,
    fecha: date,
    desde_caja_chica: bool = False,
) -> PlanEjecucion:
    """Construye plan de TRASPASO entre cuenta bancaria y CAJA CHICA.

    Args:
        mov: Movimiento del estado de cuenta.
        fecha: Fecha del movimiento.
        desde_caja_chica: Si True, CAJA CHICA es ORIGEN (egreso CAJA CHICA,
            ingreso en cuenta banco). Si False, cuenta banco es ORIGEN
            (egreso banco, ingreso CAJA CHICA).

    Usos:
    - TDC sobrantes: desde_caja_chica=True (CAJA CHICA → tarjeta)
    - Nomina: desde_caja_chica=False (BANREGIO F → CAJA CHICA)
    """
    cfg_caja = CUENTAS_BANCARIAS['caja_chica']
    clave_banco = CUENTA_POR_NUMERO.get(mov.cuenta_banco)
    cfg_banco = CUENTAS_BANCARIAS.get(clave_banco) if clave_banco else None

    if not cfg_banco:
        plan = PlanEjecucion(
            tipo_proceso='TRASPASOS',
            descripcion=f'Traspaso CAJA CHICA {fecha}',
            fecha_movimiento=fecha,
        )
        plan.advertencias.append(
            f"Cuenta {mov.cuenta_banco} no reconocida"
        )
        return plan

    monto = mov.monto

    if desde_caja_chica:
        # CAJA CHICA (egreso) → cuenta banco (ingreso)
        cfg_egreso, cta_egreso = cfg_caja, cfg_caja.cuenta
        cfg_ingreso, cta_ingreso = cfg_banco, mov.cuenta_banco
    else:
        # Cuenta banco (egreso) → CAJA CHICA (ingreso)
        cfg_egreso, cta_egreso = cfg_banco, mov.cuenta_banco
        cfg_ingreso, cta_ingreso = cfg_caja, cfg_caja.cuenta

    concepto_egreso = (
        f"TRASPASO A BANCO: {cfg_ingreso.banco} "
        f"CUENTA: {cta_ingreso} MONEDA: PESOS"
    )
    concepto_ingreso = (
        f"TRASPASO DE BANCO: {cfg_egreso.banco} "
        f"CUENTA: {cta_egreso} MONEDA: PESOS"
    )

    plan = PlanEjecucion(
        tipo_proceso='TRASPASOS',
        descripcion=f'Traspaso CAJA CHICA ${monto:,.2f} {fecha}',
        fecha_movimiento=fecha,
    )

    # Movimiento egreso
    plan.movimientos_pm.append(DatosMovimientoPM(
        banco=cfg_egreso.banco,
        cuenta=cta_egreso,
        age=fecha.year,
        mes=fecha.month,
        dia=fecha.day,
        tipo=2,  # Egreso manual
        ingreso=Decimal('0'),
        egreso=monto,
        concepto=concepto_egreso,
        clase='ENTRE CUENTAS PROPIA',
        fpago=None,
        tipo_egreso='INTERBANCARIO',
        conciliada=1 if not desde_caja_chica else 0,
        paridad=Decimal('1.0000'),
        tipo_poliza='DIARIO',
        num_factura='',
        paridad_dof=Decimal('20.0000'),
        referencia='TRASPASO AUTOMATICO',
    ))

    # Movimiento ingreso
    plan.movimientos_pm.append(DatosMovimientoPM(
        banco=cfg_ingreso.banco,
        cuenta=cta_ingreso,
        age=fecha.year,
        mes=fecha.month,
        dia=fecha.day,
        tipo=1,  # Ingreso general
        ingreso=monto,
        egreso=Decimal('0'),
        concepto=concepto_ingreso,
        clase='TRASPASO',
        fpago=None,
        tipo_egreso='INTERBANCARIO',
        conciliada=1 if desde_caja_chica else 0,
        paridad=Decimal('1.0000'),
        tipo_poliza='DIARIO',
        num_factura='',
        paridad_dof=Decimal('20.0000'),
        referencia='TRASPASO AUTOMATICO',
    ))

    # Poliza: 2 lineas (concepto corto para varchar(60))
    cta_egreso_corta = cta_egreso[:6]
    concepto_poliza_cargo = (
        f"TRASPASO de {cfg_egreso.banco}-{cta_egreso_corta} "
        f"a {cfg_ingreso.banco}"
    )
    concepto_poliza_abono = f"TRASPASO de Banco: {cfg_egreso.banco}"

    plan.lineas_poliza = [
        # 1. Cargo cuenta destino (ingreso)
        LineaPoliza(
            movimiento=1,
            cuenta=cfg_ingreso.cuenta_contable,
            subcuenta=cfg_ingreso.subcuenta_contable,
            tipo_ca=TipoCA.CARGO,
            cargo=monto,
            abono=Decimal('0'),
            concepto=concepto_poliza_cargo,
            doc_tipo='TRASPASOS',
        ),
        # 2. Abono cuenta origen (egreso)
        LineaPoliza(
            movimiento=2,
            cuenta=cfg_egreso.cuenta_contable,
            subcuenta=cfg_egreso.subcuenta_contable,
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=monto,
            concepto=concepto_poliza_abono,
            doc_tipo='TRASPASOS',
        ),
    ]

    plan.facturas_por_movimiento = [0, 0]
    plan.lineas_por_movimiento = [2, 0]

    return plan


def _construir_plan_ajuste_bancario(
    mov: MovimientoBancario,
    fecha: date,
) -> PlanEjecucion:
    """Construye plan de AJUSTE BANCARIO para sobrante TDC.

    Patron PROD: 1 ingreso simple con Clase='AJUSTE BANCARIO'
    + 2 lineas poliza (Cargo Banco + Abono Acreedores Clientes 2120/070000).
    """
    clave_cuenta = CUENTA_POR_NUMERO.get(mov.cuenta_banco)
    cfg = CUENTAS_BANCARIAS.get(clave_cuenta) if clave_cuenta else None

    plan = PlanEjecucion(
        tipo_proceso='AJUSTE_BANCARIO',
        descripcion=f'Ajuste bancario sobrante TDC {fecha}',
        fecha_movimiento=fecha,
    )

    if not cfg:
        plan.advertencias.append(f"Cuenta {mov.cuenta_banco} no reconocida")
        return plan

    concepto = 'AJUSTES DE INGRESOS PENDIENTES DE FACTURA'

    datos_pm = DatosMovimientoPM(
        banco=cfg.banco,
        cuenta=mov.cuenta_banco,
        age=fecha.year,
        mes=fecha.month,
        dia=fecha.day,
        tipo=1,
        ingreso=mov.monto,
        egreso=Decimal('0'),
        concepto=concepto,
        clase='AJUSTE BANCARIO',
        fpago='Tarjeta Credito',
        tipo_egreso='NA',
        conciliada=1,
        paridad=Decimal('1.0000'),
        tipo_poliza='INGRESO',
        num_factura=None,
    )
    plan.movimientos_pm.append(datos_pm)

    cta_banco = (cfg.cuenta_contable, cfg.subcuenta_contable)
    cta_acreedores = CuentasContables.ACREEDORES_CLIENTES

    plan.lineas_poliza.extend([
        LineaPoliza(
            movimiento=1,
            cuenta=cta_banco[0],
            subcuenta=cta_banco[1],
            tipo_ca=TipoCA.CARGO,
            cargo=mov.monto,
            abono=Decimal('0'),
            concepto=concepto,
        ),
        LineaPoliza(
            movimiento=2,
            cuenta=cta_acreedores[0],
            subcuenta=cta_acreedores[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=mov.monto,
            concepto=concepto,
        ),
    ])

    plan.facturas_por_movimiento.append(0)
    plan.lineas_por_movimiento.append(2)

    return plan


def _procesar_tdc_multi_corte(
    procesador: ProcesadorVentaTDC,
    movimientos: List[MovimientoBancario],
    fecha: date,
    cortes_list: List[CorteVentaDiaria],
    indice: Dict[int, ResultadoLinea],
    connector: Optional[SAV7Connector],
    cursor,
    dry_run: bool,
):
    """Procesa TDC cuando hay multiples cortes (ej: lunes con vie/sab/dom).

    Fase 1: Backtracking exacto — busca subconjuntos de depositos que
    sumen exactamente al target TDC de cada corte.

    Fase 2 (fallback): Asignacion secuencial con split — replica la logica
    manual de las capturistas: consume depositos en orden, y cuando un
    deposito excede el target de un corte, lo parte en dos.
    """
    cortes_sorted = sorted(cortes_list, key=lambda c: c.fecha_corte)

    # Construir lista de cortes con target valido
    cortes_con_target = [
        c for c in cortes_sorted
        if c.total_tdc and c.total_tdc > 0
    ]
    targets = [c.total_tdc for c in cortes_con_target]

    # --- Fase 1: Backtracking exacto ---
    TOL_EXACTA = Decimal('0.01')
    asignacion = _asignar_multi_corte(
        movimientos, targets, tolerancia=TOL_EXACTA,
    )

    if asignacion:
        logger.info(
            "  Asignacion multi-corte OK: {}",
            " + ".join(
                f"{len(s)} deps (${sum(m.monto for m in s):,.2f})"
                for s in asignacion
            ),
        )

        depositos_asignados = set()
        for corte, subset in zip(cortes_con_target, asignacion):
            for m in subset:
                depositos_asignados.add(id(m))
            _procesar_tdc_un_corte(
                procesador, subset, fecha, corte,
                indice, connector, cursor, dry_run,
            )

        # Sobrantes de Fase 1 → AJUSTE BANCARIO
        disponibles = [
            m for m in movimientos if id(m) not in depositos_asignados
        ]
        for mov in disponibles:
            _tdc_sobrante_a_ajuste_bancario(
                mov, fecha, indice, connector, dry_run,
            )
        return

    # --- Fase 2: Asignacion secuencial con split ---
    logger.warning(
        "  Multi-corte: sin match exacto, usando asignacion secuencial con split",
    )

    asignacion_seq, sobrantes, mapa_virtual = _asignar_secuencial_con_split(
        movimientos, cortes_con_target,
    )

    if not asignacion_seq:
        for mov in movimientos:
            rl = indice[id(mov)]
            rl.accion = AccionLinea.REQUIERE_REVISION
            rl.nota = "No se pudo asignar a ningun corte de tesoreria"
        return

    # Log resumen
    total_asignado = sum(
        sum(d.monto for d in deps) for _, deps in asignacion_seq
    )
    n_originales_split = len(set(mapa_virtual.values()))
    logger.info(
        "  Asignacion secuencial: {} cortes, ${:,.2f} asignado, "
        "{} depositos partidos, {} sobrantes",
        len(asignacion_seq), total_asignado,
        n_originales_split, len(sobrantes),
    )

    originales_procesados = set()

    for corte, deps in asignacion_seq:
        logger.info(
            "  Corte {}: {} deps (${:,.2f})",
            corte.fecha_corte, len(deps), sum(d.monto for d in deps),
        )

        plan = procesador.construir_plan(
            movimientos=deps, fecha=fecha,
            cursor=cursor, corte_venta=corte,
        )

        if dry_run:
            for dep in deps:
                oid = mapa_virtual.get(id(dep), id(dep))
                if oid in indice:
                    rl = indice[oid]
                    rl.accion = AccionLinea.INSERT
                    nota = (
                        f"DRY-RUN | Corte {corte.fecha_corte} "
                        f"(${dep.monto:,.2f})"
                    )
                    rl.nota = f"{rl.nota} + {nota}" if rl.nota else nota
                originales_procesados.add(oid)
            continue

        resultado = _ejecutar_plan(plan, connector)

        for i, dep in enumerate(deps):
            oid = mapa_virtual.get(id(dep), id(dep))
            if oid not in indice:
                continue
            rl = indice[oid]
            if resultado.exito:
                rl.accion = AccionLinea.INSERT
                rl.resultado = resultado
                if i < len(resultado.folios):
                    rl.folios.append(resultado.folios[i])
                nota = f"Corte {corte.fecha_corte} ${dep.monto:,.2f}"
                rl.nota = f"{rl.nota} + {nota}" if rl.nota else nota
                _ajustar_nota_idempotencia(rl, resultado)
            else:
                rl.accion = AccionLinea.ERROR
                rl.nota = resultado.error
            originales_procesados.add(oid)

    # Sobrantes del split secuencial → AJUSTE BANCARIO
    for dep in sobrantes:
        oid = mapa_virtual.get(id(dep), id(dep))
        if oid not in indice:
            continue
        rl = indice[oid]

        logger.info(
            "  TDC sobrante (split) → AJUSTE BANCARIO: ${:,.2f}",
            dep.monto,
        )

        if dry_run:
            nota = f"DRY-RUN | AJUSTE BANCARIO (${dep.monto:,.2f})"
            rl.nota = f"{rl.nota} + {nota}" if rl.nota else nota
            rl.accion = AccionLinea.INSERT
            originales_procesados.add(oid)
            continue

        plan = _construir_plan_ajuste_bancario(dep, fecha)
        if plan.advertencias:
            rl.accion = AccionLinea.ERROR
            rl.nota = (
                f"{rl.nota} | {plan.advertencias[0]}"
                if rl.nota else plan.advertencias[0]
            )
            originales_procesados.add(oid)
            continue

        resultado = _ejecutar_plan(plan, connector)
        if resultado.exito:
            if rl.accion != AccionLinea.INSERT:
                rl.accion = AccionLinea.INSERT
            rl.folios.extend(resultado.folios)
            nota = f"AJUSTE BANCARIO ${dep.monto:,.2f}"
            rl.nota = f"{rl.nota} + {nota}" if rl.nota else nota
            _ajustar_nota_idempotencia(rl, resultado)
        else:
            rl.accion = AccionLinea.ERROR
            rl.nota = (
                f"{rl.nota} | ERROR: {resultado.error}"
                if rl.nota else resultado.error
            )
        originales_procesados.add(oid)

    # Depositos originales que no fueron asignados (no deberia ocurrir)
    for mov in movimientos:
        if id(mov) not in originales_procesados:
            rl = indice[id(mov)]
            rl.accion = AccionLinea.REQUIERE_REVISION
            rl.nota = "No asignado en split secuencial"


def _tdc_sobrante_a_ajuste_bancario(
    mov: MovimientoBancario,
    fecha: date,
    indice: Dict[int, ResultadoLinea],
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Registra un deposito TDC sobrante como AJUSTE BANCARIO.

    Patron PROD: 1 ingreso simple + 2 lineas poliza
    (Cargo Banco + Abono Acreedores Clientes).
    """
    rl = indice[id(mov)]
    logger.info(
        "  TDC sobrante → AJUSTE BANCARIO: ${:,.2f}",
        mov.monto,
    )

    if dry_run:
        rl.accion = AccionLinea.INSERT
        rl.nota = "DRY-RUN | AJUSTE BANCARIO (sobrante TDC)"
        return

    plan = _construir_plan_ajuste_bancario(mov, fecha)
    if plan.advertencias:
        rl.accion = AccionLinea.ERROR
        rl.nota = plan.advertencias[0]
        return

    resultado = _ejecutar_plan(plan, connector)
    if resultado.exito:
        rl.accion = AccionLinea.INSERT
        rl.resultado = resultado
        rl.folios = resultado.folios
        rl.nota = "AJUSTE BANCARIO (sobrante TDC)"
        _ajustar_nota_idempotencia(rl, resultado)
    else:
        rl.accion = AccionLinea.ERROR
        rl.nota = resultado.error


def _en_borde_de_mes(fecha: date, margen: int = 4) -> bool:
    """True si la fecha cae en los primeros o ultimos N dias del mes."""
    import calendar
    _, ultimo_dia = calendar.monthrange(fecha.year, fecha.month)
    return fecha.day <= margen or fecha.day > (ultimo_dia - margen)


def _procesar_ventas_efectivo(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    cortes: Dict[date, CorteVentaDiaria],
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Procesa ventas en efectivo del dia."""
    movs = por_tipo.get(TipoProceso.VENTA_EFECTIVO, [])
    if not movs:
        return

    # Primeros/ultimos 4 dias del mes: omitir (proceso manual por desfase deposito/venta)
    if _en_borde_de_mes(fecha):
        logger.info(
            "  Ventas Efectivo: {} depositos OMITIDOS (borde de mes, dia {})",
            len(movs), fecha.day,
        )
        for mov in movs:
            indice[id(mov)].accion = AccionLinea.OMITIR
            indice[id(mov)].nota = (
                f"Borde de mes (dia {fecha.day}): proceso manual"
            )
        return

    logger.info("  Ventas Efectivo: {} depositos", len(movs))
    procesador = ProcesadorVentaEfectivo()
    cursor = _obtener_cursor_lectura(connector)

    try:
        for mov in movs:
            corte = _buscar_corte_efectivo(mov.monto, cortes)
            if not corte:
                indice[id(mov)].accion = AccionLinea.SIN_PROCESAR
                indice[id(mov)].nota = (
                    f"Sin corte de tesoreria para deposito ${mov.monto:,.2f}"
                )
                continue

            errores_val = validar_venta_efectivo([mov], corte)
            for err in errores_val:
                logger.warning("  Validacion Efectivo: {}", err)

            plan = procesador.construir_plan(
                movimientos=[mov], fecha=fecha,
                cursor=cursor, corte_venta=corte,
            )

            if dry_run:
                indice[id(mov)].accion = AccionLinea.INSERT
                indice[id(mov)].nota = f"DRY-RUN | Corte {corte.fecha_corte}"
                continue

            resultado = _ejecutar_plan(plan, connector)
            rl = indice[id(mov)]
            if resultado.exito:
                rl.accion = AccionLinea.INSERT
                rl.resultado = resultado
                rl.folios = resultado.folios
                _ajustar_nota_idempotencia(rl, resultado)
            else:
                rl.accion = AccionLinea.ERROR
                rl.nota = resultado.error
    finally:
        if cursor:
            cursor.close()


def _procesar_nomina(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    datos_nomina: Optional[DatosNomina],
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Procesa nomina del dia con percepciones/deducciones del Excel CONTPAQi.

    Solo crea el movimiento de DISPERSION (es_principal=True) con poliza ~17
    lineas que provisiona acreedores (2120/040000) para secundarios.
    Los secundarios (cheques, finiquito, vacaciones) se crean en
    _procesar_cobros_cheque() cuando aparecen en el estado de cuenta.
    """
    movs = por_tipo.get(TipoProceso.NOMINA, [])
    if not movs:
        return

    logger.info("  Nomina: {} movimientos", len(movs))

    # Sin datos CONTPAQi no podemos generar la poliza correcta
    if datos_nomina is None:
        for mov in movs:
            rl = indice[id(mov)]
            rl.accion = AccionLinea.ERROR
            rl.nota = "Sin archivo de nomina CONTPAQi — no se puede registrar"
        return

    procesador = ProcesadorNomina()
    plan = procesador.construir_plan(
        movimientos=movs, fecha=fecha, datos_nomina=datos_nomina,
    )

    if plan.advertencias and not plan.movimientos_pm:
        for mov in movs:
            rl = indice[id(mov)]
            rl.accion = AccionLinea.ERROR
            rl.nota = plan.advertencias[0]
        return

    if dry_run:
        for mov in movs:
            rl = indice[id(mov)]
            rl.accion = AccionLinea.INSERT
            rl.nota = "DRY-RUN | NOMINA DISPERSION"
        return

    resultado = _ejecutar_plan(plan, connector)

    for mov in movs:
        rl = indice[id(mov)]
        if resultado.exito:
            rl.accion = AccionLinea.INSERT
            rl.resultado = resultado
            rl.folios = resultado.folios
            rl.nota = "NOMINA DISPERSION"
            _ajustar_nota_idempotencia(rl, resultado)
        else:
            rl.accion = AccionLinea.ERROR
            rl.nota = resultado.error


_RE_NUM_CHEQUE = re.compile(r'cheque:0*(\d+)', re.IGNORECASE)


def _procesar_cobros_cheque(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    datos_nomina: Optional[DatosNomina],
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Procesa cobros de cheque del dia.

    Si hay datos de nomina CONTPAQi, intenta matchear el monto del cobro
    contra movimientos secundarios del Excel (CHEQUES, FINIQUITO, VACACIONES).
    Los que matchean se registran como movimientos de nomina con poliza 2 lineas
    (cancela acreedores 2120/040000).
    Los que NO matchean quedan como DESCONOCIDO (no son de nomina).
    """
    movs = por_tipo.get(TipoProceso.COBRO_CHEQUE, [])
    if not movs:
        return

    logger.info("  Cobros de cheque: {} movimientos", len(movs))

    procesador = ProcesadorNomina()

    for mov in movs:
        rl = indice[id(mov)]

        # Extraer numero de cheque de la descripcion
        m = _RE_NUM_CHEQUE.search(mov.descripcion)
        num_cheque = m.group(1) if m else ''

        # Sin datos de nomina → no podemos determinar si es de nomina
        if datos_nomina is None:
            rl.accion = AccionLinea.DESCONOCIDO
            rl.nota = "Sin archivo de nomina — no se puede clasificar cobro de cheque"
            continue

        plan = procesador.construir_plan_cheque(
            fecha=fecha,
            datos_nomina=datos_nomina,
            monto_banco=mov.monto,
            num_cheque=num_cheque,
        )

        if plan is None:
            # Monto no matchea ningun secundario de nomina
            rl.accion = AccionLinea.DESCONOCIDO
            rl.nota = (
                f"Cobro cheque #{num_cheque} ${mov.monto:,.2f} "
                f"sin match en Excel nomina"
            )
            continue

        if dry_run:
            rl.accion = AccionLinea.INSERT
            rl.nota = (
                f"DRY-RUN | {plan.descripcion} "
                f"(cheque #{num_cheque})"
            )
            continue

        resultado = _ejecutar_plan(plan, connector)
        if resultado.exito:
            rl.accion = AccionLinea.INSERT
            rl.resultado = resultado
            rl.folios = resultado.folios
            rl.nota = f"{plan.descripcion} (cheque #{num_cheque})"
            _ajustar_nota_idempotencia(rl, resultado)
        else:
            rl.accion = AccionLinea.ERROR
            rl.nota = resultado.error


def _procesar_conciliaciones(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    connector: Optional[SAV7Connector],
    dry_run: bool,
    movs_por_fecha: Optional[Dict] = None,
):
    """Procesa conciliaciones (pagos a proveedores + cobros a clientes).

    Pagos a proveedores se afectan con 1 dia de retraso: al procesar dia X,
    se concilian los pagos del dia X-1 (el usuario espera un dia para
    confirmar que el pago no fue devuelto o rechazado).
    """
    # --- Pagos a proveedores (con retraso de 1 dia) ---
    # Marcar pagos del dia actual como pendientes
    movs_pagos_hoy = por_tipo.get(TipoProceso.PAGO_PROVEEDOR, [])
    for mov in movs_pagos_hoy:
        rl = indice[id(mov)]
        rl.accion = AccionLinea.SIN_PROCESAR
        rl.nota = "Pendiente: se afecta al dia siguiente"

    # Conciliar pagos del dia anterior
    fecha_ayer = fecha - timedelta(days=1)
    movs_pagos_ayer = []
    if movs_por_fecha:
        for mov in movs_por_fecha.get(fecha_ayer, []):
            if mov.tipo_proceso == TipoProceso.PAGO_PROVEEDOR:
                movs_pagos_ayer.append(mov)

    if movs_pagos_ayer:
        logger.info(
            "  Pagos Proveedor: {} movimientos (del {}, afectados hoy {})",
            len(movs_pagos_ayer), fecha_ayer, fecha,
        )
        procesador_pagos = ProcesadorConciliacionPagos()
        cursor = _obtener_cursor_lectura(connector)

        try:
            for mov in movs_pagos_ayer:
                plan = procesador_pagos.construir_plan(
                    movimientos=[mov], fecha=fecha_ayer, cursor=cursor,
                )
                rl = indice[id(mov)]

                if plan.conciliaciones:
                    folio = plan.conciliaciones[0]['folio']
                    if dry_run:
                        rl.accion = AccionLinea.CONCILIAR
                        rl.folios = [folio]
                        rl.nota = f"DRY-RUN | Folio {folio} (afectado dia {fecha})"
                    else:
                        resultado = _ejecutar_conciliacion(plan, connector)
                        if resultado.exito:
                            rl.accion = AccionLinea.CONCILIAR
                            rl.folios = [folio]
                            rl.resultado = resultado
                        else:
                            rl.accion = AccionLinea.ERROR
                            rl.nota = resultado.error
                elif plan.ya_conciliados:
                    ya = plan.ya_conciliados[0]
                    rl.accion = AccionLinea.OMITIR
                    rl.folios = [ya['folio']]
                    rl.nota = ya['descripcion']
                else:
                    rl.accion = AccionLinea.SIN_PROCESAR
                    rl.nota = (
                        plan.advertencias[0]
                        if plan.advertencias
                        else "Sin match en BD"
                    )
        finally:
            if cursor:
                cursor.close()
    elif movs_pagos_hoy:
        logger.info(
            "  Pagos Proveedor: {} del dia {} pendientes (se afectan mañana)",
            len(movs_pagos_hoy), fecha,
        )

    # --- Cobros a clientes ---
    movs_cobros = por_tipo.get(TipoProceso.COBRO_CLIENTE, [])
    if movs_cobros:
        logger.info("  Cobros Cliente: {} movimientos", len(movs_cobros))
        procesador_cobros = ProcesadorConciliacionCobros()
        cursor = _obtener_cursor_lectura(connector)

        try:
            for mov in movs_cobros:
                plan = procesador_cobros.construir_plan(
                    movimientos=[mov], fecha=fecha, cursor=cursor,
                )
                rl = indice[id(mov)]

                if plan.conciliaciones:
                    # Fase B: cobro ya existe → conciliar
                    folio = plan.conciliaciones[0]['folio']
                    if dry_run:
                        rl.accion = AccionLinea.CONCILIAR
                        rl.folios = [folio]
                        rl.nota = f"DRY-RUN | Folio {folio}"
                    else:
                        resultado = _ejecutar_conciliacion(plan, connector)
                        if resultado.exito:
                            rl.accion = AccionLinea.CONCILIAR
                            rl.folios = [folio]
                            rl.resultado = resultado
                        else:
                            rl.accion = AccionLinea.ERROR
                            rl.nota = resultado.error
                elif plan.cobros_cliente:
                    # Fase A: cobro NO existe → crear completo
                    cobro = plan.cobros_cliente[0]
                    if dry_run:
                        rl.accion = AccionLinea.INSERT
                        rl.nota = (
                            f"DRY-RUN | Crear cobro "
                            f"{cobro.serie}-{cobro.num_fac} "
                            f"${cobro.monto:,.2f}"
                        )
                    else:
                        resultado = _ejecutar_cobro_completo(plan, connector)
                        if resultado.exito:
                            rl.accion = AccionLinea.INSERT
                            rl.resultado = resultado
                            rl.folios = resultado.folios
                            rl.nota = (
                                f"Cobro creado: "
                                f"{cobro.serie}-{cobro.num_fac}"
                            )
                        else:
                            rl.accion = AccionLinea.ERROR
                            rl.nota = resultado.error
                elif plan.ya_conciliados:
                    ya = plan.ya_conciliados[0]
                    rl.accion = AccionLinea.OMITIR
                    rl.folios = [ya['folio']]
                    rl.nota = ya['descripcion']
                else:
                    rl.accion = AccionLinea.SIN_PROCESAR
                    rl.nota = (
                        plan.advertencias[0]
                        if plan.advertencias
                        else "Sin match en BD"
                    )
        finally:
            if cursor:
                cursor.close()


def _procesar_pago_gastos(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    connector: Optional[SAV7Connector],
    dry_run: bool,
    movs_por_fecha: Optional[Dict] = None,
):
    """Procesa pagos a proveedores desde cuenta de gastos.

    A diferencia de la conciliacion normal (E1), estos pagos NO pre-existen
    en SAVCheqPM. Se busca la factura correspondiente en SAVRecC y se CREA
    el movimiento bancario + poliza + vinculacion.

    Usa retraso de 1 dia igual que pagos normales.
    """
    # --- Marcar pagos del dia actual como pendientes ---
    movs_gastos_hoy = por_tipo.get(TipoProceso.PAGO_GASTOS, [])
    for mov in movs_gastos_hoy:
        rl = indice[id(mov)]
        rl.accion = AccionLinea.SIN_PROCESAR
        rl.nota = "Pendiente: se afecta al dia siguiente"

    # --- Procesar pagos del dia anterior (retraso 1 dia) ---
    fecha_ayer = fecha - timedelta(days=1)
    movs_gastos_ayer = []
    if movs_por_fecha:
        for mov in movs_por_fecha.get(fecha_ayer, []):
            if mov.tipo_proceso == TipoProceso.PAGO_GASTOS:
                movs_gastos_ayer.append(mov)

    if movs_gastos_ayer:
        logger.info(
            "  Pagos Gastos: {} movimientos (del {}, afectados hoy {})",
            len(movs_gastos_ayer), fecha_ayer, fecha,
        )
        procesador = ProcesadorPagoGastos()
        cursor = _obtener_cursor_lectura(connector)

        try:
            for idx_gasto, mov in enumerate(movs_gastos_ayer):
                # PK de SAVCheqPM incluye HoraAlta (precision 1s).
                # Esperar entre inserts para evitar colision.
                if idx_gasto > 0:
                    time.sleep(1.1)

                plan = procesador.construir_plan(
                    movimientos=[mov], fecha=fecha_ayer, cursor=cursor,
                )
                rl = indice[id(mov)]

                if plan.movimientos_pm:
                    if dry_run:
                        match_data = plan.pagos_factura_existente[0]
                        rl.accion = AccionLinea.INSERT
                        rl.nota = (
                            f"DRY-RUN | Crear pago -> "
                            f"{match_data['serie']}-{match_data['num_rec']} "
                            f"${mov.monto:,.2f} (afectado dia {fecha})"
                        )
                    else:
                        resultado = _ejecutar_pago_gastos(plan, connector)
                        if resultado.exito:
                            rl.accion = AccionLinea.INSERT
                            rl.resultado = resultado
                            rl.folios = resultado.folios
                            _ajustar_nota_idempotencia(rl, resultado)
                        else:
                            rl.accion = AccionLinea.ERROR
                            rl.nota = resultado.error
                elif plan.ya_conciliados:
                    ya = plan.ya_conciliados[0]
                    rl.accion = AccionLinea.OMITIR
                    rl.nota = ya['descripcion']
                else:
                    rl.accion = AccionLinea.SIN_PROCESAR
                    rl.nota = (
                        plan.advertencias[0]
                        if plan.advertencias
                        else "Sin factura en BD"
                    )
        finally:
            if cursor:
                cursor.close()
    elif movs_gastos_hoy:
        logger.info(
            "  Pagos Gastos: {} del dia {} pendientes (se afectan manana)",
            len(movs_gastos_hoy), fecha,
        )


def _procesar_impuestos(
    por_tipo: Dict[TipoProceso, List[MovimientoBancario]],
    indice: Dict[int, ResultadoLinea],
    fecha: date,
    datos_federal,
    datos_estatal,
    datos_imss,
    connector: Optional[SAV7Connector],
    dry_run: bool,
):
    """Procesa impuestos (federal + estatal + IMSS) del dia."""
    tipos_impuesto = (
        TipoProceso.IMPUESTO_FEDERAL,
        TipoProceso.IMPUESTO_ESTATAL,
        TipoProceso.IMPUESTO_IMSS,
    )
    movs = []
    for t in tipos_impuesto:
        movs.extend(por_tipo.get(t, []))

    if not movs:
        return

    logger.info("  Impuestos: {} movimientos", len(movs))

    # Verificar que tengamos datos para al menos un tipo
    tiene_datos = datos_federal or datos_estatal or datos_imss
    if not tiene_datos:
        for mov in movs:
            indice[id(mov)].accion = AccionLinea.SIN_PROCESAR
            indice[id(mov)].nota = "Sin archivos de impuestos proporcionados"
        return

    procesador = ProcesadorImpuestos()
    cursor = _obtener_cursor_lectura(connector)

    try:
        plan = procesador.construir_plan(
            movimientos=movs,
            fecha=fecha,
            cursor=cursor,
            datos_federal=datos_federal,
            datos_estatal=datos_estatal,
            datos_imss=datos_imss,
        )

        if not plan.movimientos_pm:
            for mov in movs:
                rl = indice[id(mov)]
                if rl.accion == AccionLinea.SIN_PROCESAR:
                    rl.nota = (
                        plan.advertencias[0]
                        if plan.advertencias
                        else "Sin movimientos generados"
                    )
            return

        if dry_run:
            for mov in movs:
                indice[id(mov)].accion = AccionLinea.INSERT
                indice[id(mov)].nota = "DRY-RUN"
            return

        resultado = _ejecutar_plan(plan, connector)

        if not resultado.exito:
            for mov in movs:
                indice[id(mov)].accion = AccionLinea.ERROR
                indice[id(mov)].nota = resultado.error
            return

        # Mapear folios a lineas del banco por monto
        # plan.movimientos_pm[i].egreso debe coincidir con algún mov.monto
        folios_asignados = set()  # id(mov) de lineas banco ya asignadas
        folios_pm_asignados = set()  # indices PM ya asignados
        for pm_idx, datos_pm in enumerate(plan.movimientos_pm):
            if pm_idx >= len(resultado.folios):
                break
            folio = resultado.folios[pm_idx]
            monto_pm = datos_pm.egreso

            # Buscar linea del banco con mismo monto (no asignada aun)
            for mov in movs:
                if id(mov) in folios_asignados:
                    continue
                if abs(mov.monto - monto_pm) <= Decimal('0.01'):
                    rl = indice[id(mov)]
                    rl.accion = AccionLinea.INSERT
                    rl.resultado = resultado
                    rl.folios.append(folio)
                    _ajustar_nota_idempotencia(rl, resultado)
                    folios_asignados.add(id(mov))
                    folios_pm_asignados.add(pm_idx)
                    break

        # Fallback: PMs sin match individual pueden ser sub-movimientos
        # de una sola linea bancaria (ej: retenciones IVA dentro de total_segunda)
        folios_sin_match = []
        suma_sin_match = Decimal('0')
        for pm_idx, datos_pm in enumerate(plan.movimientos_pm):
            if pm_idx in folios_pm_asignados or pm_idx >= len(resultado.folios):
                continue
            folios_sin_match.append(resultado.folios[pm_idx])
            suma_sin_match += datos_pm.egreso

        if folios_sin_match:
            for mov in movs:
                if id(mov) in folios_asignados:
                    continue
                if abs(mov.monto - suma_sin_match) <= Decimal('0.01'):
                    rl = indice[id(mov)]
                    rl.accion = AccionLinea.INSERT
                    rl.resultado = resultado
                    rl.folios.extend(folios_sin_match)
                    _ajustar_nota_idempotencia(rl, resultado)
                    folios_asignados.add(id(mov))
                    break

        # Marcar las que no se pudieron mapear
        for mov in movs:
            if id(mov) not in folios_asignados:
                rl = indice[id(mov)]
                if rl.accion == AccionLinea.SIN_PROCESAR:
                    rl.nota = "Sin match de monto en plan de impuestos"
    finally:
        if cursor:
            cursor.close()
