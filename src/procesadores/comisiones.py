"""Procesador E3: Comisiones Bancarias.

Genera movimientos de egreso (SAVCheqPM Tipo 3) para comisiones bancarias.
Tambien crea la factura de compras (SAVRecC/RecD) para el proveedor banco.

Comisiones posibles:
- COMISION_SPEI + IVA: Comisiones por transferencia SPEI ($6 + $0.96 c/u)
  → Cuenta 055003730017 (efectivo)
- COMISION_TDC + IVA: Comisiones por ventas TDC/TDD (% variable)
  → Cuenta 038900320016 (tarjeta)

Caracteristicas:
- Agrupa todas las comisiones del dia por cuenta bancaria
- 1 movimiento SAVCheqPM por grupo (dia + cuenta)
- 1 factura de compras (SAVRecC/RecD) por grupo
- Tipo: 3 (Egreso con Factura)
- Clase: 'COMISIONES BANCARIAS'
- Poliza: 4 lineas:
  1. Cargo Proveedores (2110/010000) = total
  2. Cargo IVA Acreditable Pte Pago (1240/010000) = IVA
  3. Abono IVA Acreditable Pagado (1246/010000) = IVA
  4. Abono Banco (1120/040000 o 060000) = total
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

from loguru import logger

from config.settings import CUENTAS_BANCARIAS, CUENTA_POR_NUMERO, CuentasContables
from src.models import (
    DatosCompraPM,
    DatosMovimientoPM,
    LineaPoliza,
    MovimientoBancario,
    PlanEjecucion,
    TipoCA,
    TipoProceso,
)


CLASE = 'COMISIONES BANCARIAS'
TIPO_MOVIMIENTO = 3  # Egreso con Factura
PROVEEDOR_BANCO = '001081'
NOMBRE_PROVEEDOR = 'BANCO REGIONAL'
TIPO_PROVEEDOR = 'NA'


class ProcesadorComisiones:
    """Procesador para comisiones bancarias (E3)."""

    @property
    def tipos_soportados(self) -> List[TipoProceso]:
        return [
            TipoProceso.COMISION_SPEI,
            TipoProceso.COMISION_SPEI_IVA,
            TipoProceso.COMISION_TDC,
            TipoProceso.COMISION_TDC_IVA,
        ]

    def construir_plan(
        self,
        movimientos: List[MovimientoBancario],
        fecha: date,
        cursor=None,
        **kwargs,
    ) -> PlanEjecucion:
        """Construye plan para comisiones bancarias de un dia.

        Agrupa comisiones por cuenta bancaria. Cada grupo genera:
        - 1 SAVCheqPM (egreso)
        - 1 SAVRecC/RecD (factura de compras)
        - 1 poliza de 4 lineas

        Args:
            movimientos: Comisiones del dia (base + IVA).
            fecha: Fecha del cargo en estado de cuenta.
        """
        plan = PlanEjecucion(
            tipo_proceso='COMISIONES',
            descripcion=f'Comisiones bancarias {fecha}',
            fecha_movimiento=fecha,
        )

        if not movimientos:
            plan.advertencias.append("Sin comisiones para este dia")
            return plan

        # Agrupar comisiones por cuenta bancaria
        por_cuenta = _agrupar_por_cuenta(movimientos)

        for cuenta, movs_cuenta in por_cuenta.items():
            # Separar base e IVA
            subtotal, iva = _sumar_comisiones(movs_cuenta)
            total = subtotal + iva

            if total <= Decimal('0'):
                continue

            # Determinar banco y cuenta contable
            clave_cuenta = CUENTA_POR_NUMERO.get(cuenta)
            if not clave_cuenta:
                plan.advertencias.append(
                    f"Cuenta {cuenta} no reconocida para comisiones"
                )
                continue

            cfg = CUENTAS_BANCARIAS[clave_cuenta]
            cta_banco = (cfg.cuenta_contable, cfg.subcuenta_contable)

            concepto = f"COMISIONES BANCARIAS {fecha.strftime('%d/%m/%Y')}"

            # --- SAVCheqPM (egreso tipo 3) ---
            # NumFactura vacio — en produccion es NULL para comisiones
            factura_ref = fecha.strftime('%d%m%Y')
            datos_pm = DatosMovimientoPM(
                banco=cfg.banco,
                cuenta=cuenta,
                age=fecha.year,
                mes=fecha.month,
                dia=fecha.day,
                tipo=TIPO_MOVIMIENTO,
                ingreso=Decimal('0'),
                egreso=total,
                concepto=concepto,
                clase=CLASE,
                fpago=None,
                tipo_egreso='TRANSFERENCIA',
                conciliada=1,
                paridad=Decimal('1.0000'),
                tipo_poliza='EGRESO',
                num_factura='',
                proveedor=PROVEEDOR_BANCO,
                proveedor_nombre=NOMBRE_PROVEEDOR,
                tipo_proveedor=TIPO_PROVEEDOR,
            )
            plan.movimientos_pm.append(datos_pm)

            # --- SAVRecC/RecD (factura de compras) ---
            datos_compra = DatosCompraPM(
                proveedor=PROVEEDOR_BANCO,
                factura=factura_ref,
                fecha=fecha,
                subtotal=subtotal,
                iva=iva,
                total=total,
            )
            plan.compras.append(datos_compra)

            # --- SAVPoliza (4 lineas) ---
            lineas = _generar_poliza_comisiones(
                total=total,
                iva=iva,
                cta_banco=cta_banco,
                concepto=concepto,
            )
            plan.lineas_poliza.extend(lineas)

            # Tracking: 0 facturas PMF (usa compras), 4 lineas poliza
            plan.facturas_por_movimiento.append(0)
            plan.lineas_por_movimiento.append(4)

        # Validaciones
        total_comisiones = sum(m.monto for m in movimientos)
        n_base = sum(
            1 for m in movimientos
            if m.tipo_proceso in (TipoProceso.COMISION_SPEI, TipoProceso.COMISION_TDC)
        )
        n_iva = sum(
            1 for m in movimientos
            if m.tipo_proceso in (
                TipoProceso.COMISION_SPEI_IVA, TipoProceso.COMISION_TDC_IVA,
            )
        )
        plan.validaciones.append(
            f"Total comisiones dia: ${total_comisiones:,.2f} "
            f"({n_base} base + {n_iva} IVA)"
        )

        return plan


def _agrupar_por_cuenta(
    movimientos: List[MovimientoBancario],
) -> Dict[str, List[MovimientoBancario]]:
    """Agrupa movimientos de comisiones por cuenta bancaria."""
    grupos: Dict[str, List[MovimientoBancario]] = {}
    for mov in movimientos:
        if mov.cuenta_banco not in grupos:
            grupos[mov.cuenta_banco] = []
        grupos[mov.cuenta_banco].append(mov)
    return grupos


def _sumar_comisiones(
    movimientos: List[MovimientoBancario],
) -> Tuple[Decimal, Decimal]:
    """Suma comisiones separando base e IVA.

    El IVA se calcula como 16% del subtotal agregado (no sumando lineas
    IVA individuales del banco). Esto matchea la forma en que el ERP
    lo registra en produccion y evita discrepancias de $0.01 por
    diferencias de redondeo linea-por-linea vs agregado.

    Returns:
        (subtotal_base, total_iva)
    """
    tipos_base = (TipoProceso.COMISION_SPEI, TipoProceso.COMISION_TDC)

    centavos = Decimal('0.01')
    subtotal = sum(
        (m.monto for m in movimientos if m.tipo_proceso in tipos_base),
        Decimal('0'),
    ).quantize(centavos, rounding=ROUND_HALF_UP)

    # IVA calculado sobre subtotal agregado (16%), no sumando lineas IVA
    iva = (subtotal * Decimal('0.16')).quantize(centavos, rounding=ROUND_HALF_UP)

    return (subtotal, iva)


def _generar_poliza_comisiones(
    total: Decimal,
    iva: Decimal,
    cta_banco: Tuple[str, str],
    concepto: str,
) -> List[LineaPoliza]:
    """Genera las 4 lineas de poliza para comisiones bancarias.

    1. Cargo Proveedores (2110/010000) = total
    2. Cargo IVA Acreditable Pte Pago (1240/010000) = IVA
    3. Abono IVA Acreditable Pagado (1246/010000) = IVA
    4. Abono Banco = total
    """
    cta_proveedores = CuentasContables.PROVEEDORES_GLOBAL
    cta_iva_pte = CuentasContables.IVA_ACREDITABLE_PTE_PAGO
    cta_iva_pagado = CuentasContables.IVA_ACREDITABLE_PAGADO

    prefijo = f"Prov:{PROVEEDOR_BANCO} Nombre:{NOMBRE_PROVEEDOR[:10]}"

    return [
        # 1. Cargo Proveedores
        LineaPoliza(
            movimiento=1,
            cuenta=cta_proveedores[0],
            subcuenta=cta_proveedores[1],
            tipo_ca=TipoCA.CARGO,
            cargo=total,
            abono=Decimal('0'),
            concepto=f"{prefijo} Total Pago: {{folio}} Suc.5",
        ),
        # 2. Abono IVA Acreditable Pte Pago (reclasifica IVA)
        LineaPoliza(
            movimiento=2,
            cuenta=cta_iva_pte[0],
            subcuenta=cta_iva_pte[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=iva,
            concepto=f"{prefijo} IVAPP Suc.5",
        ),
        # 3. Cargo IVA Acreditable Pagado (reclasifica IVA)
        LineaPoliza(
            movimiento=3,
            cuenta=cta_iva_pagado[0],
            subcuenta=cta_iva_pagado[1],
            tipo_ca=TipoCA.CARGO,
            cargo=iva,
            abono=Decimal('0'),
            concepto=f"{prefijo} IVAP Suc.5",
        ),
        # 4. Abono Banco
        LineaPoliza(
            movimiento=4,
            cuenta=cta_banco[0],
            subcuenta=cta_banco[1],
            tipo_ca=TipoCA.ABONO,
            cargo=Decimal('0'),
            abono=total,
            concepto="Banco: BANREGIO. Folio Pago: {folio}",
        ),
    ]
