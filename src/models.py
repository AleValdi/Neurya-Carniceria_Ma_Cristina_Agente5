"""Modelos de datos del Agente5.

Todos los dataclasses compartidos entre modulos: movimientos bancarios,
facturas, polizas contables, planes de ejecucion y resultados.
Compatible con Python 3.9 (usa typing.List, typing.Optional).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional


# --- Enumeraciones ---

class TipoProceso(str, Enum):
    """Tipos de proceso identificados por el clasificador."""
    VENTA_TDC = 'VENTA_TDC'
    VENTA_TDD = 'VENTA_TDD'
    VENTA_EFECTIVO = 'VENTA_EFECTIVO'
    TRASPASO = 'TRASPASO'
    TRASPASO_INGRESO = 'TRASPASO_INGRESO'
    COMISION_SPEI = 'COMISION_SPEI'
    COMISION_SPEI_IVA = 'COMISION_SPEI_IVA'
    COMISION_TDC = 'COMISION_TDC'
    COMISION_TDC_IVA = 'COMISION_TDC_IVA'
    NOMINA = 'NOMINA'
    COBRO_CHEQUE = 'COBRO_CHEQUE'
    PAGO_PROVEEDOR = 'PAGO_PROVEEDOR'
    COBRO_CLIENTE = 'COBRO_CLIENTE'
    IMPUESTO_FEDERAL = 'IMPUESTO_FEDERAL'
    IMPUESTO_ESTATAL = 'IMPUESTO_ESTATAL'
    IMPUESTO_IMSS = 'IMPUESTO_IMSS'
    DESCONOCIDO = 'DESCONOCIDO'


class TipoMovimiento(int, Enum):
    """Tipo de movimiento en SAVCheqPM."""
    INGRESO_GENERAL = 1
    EGRESO_MANUAL = 2
    EGRESO_CON_FACTURA = 3
    INGRESO_VENTA = 4


class TipoCA(int, Enum):
    """Tipo de movimiento contable en SAVPoliza."""
    CARGO = 1
    ABONO = 2


# --- Modelos de entrada (parsers) ---

@dataclass
class MovimientoBancario:
    """Fila parseada del estado de cuenta bancario."""
    fecha: date
    descripcion: str
    cargo: Optional[Decimal]    # Egreso (None si es ingreso)
    abono: Optional[Decimal]    # Ingreso (None si es egreso)
    cuenta_banco: str           # Numero de cuenta (ej: '055003730017')
    nombre_hoja: str            # Hoja de origen en el Excel

    # Asignado por el clasificador
    tipo_proceso: Optional[TipoProceso] = None

    @property
    def monto(self) -> Decimal:
        """Monto del movimiento (siempre positivo)."""
        if self.abono and self.abono > 0:
            return self.abono
        if self.cargo and self.cargo > 0:
            return self.cargo
        return Decimal('0')

    @property
    def es_ingreso(self) -> bool:
        """True si es un abono/ingreso."""
        return self.abono is not None and self.abono > 0

    @property
    def es_egreso(self) -> bool:
        """True si es un cargo/egreso."""
        return self.cargo is not None and self.cargo > 0


@dataclass
class FacturaVenta:
    """Factura individual del reporte de tesoreria."""
    serie: str          # 'FD'
    numero: str         # ej: '20204'
    importe: Decimal


@dataclass
class CorteVentaDiaria:
    """Datos de un dia del reporte de tesoreria."""
    fecha_corte: date
    nombre_hoja: str

    # Facturas
    facturas_individuales: List[FacturaVenta] = field(default_factory=list)
    factura_global_numero: Optional[str] = None
    factura_global_importe: Optional[Decimal] = None

    # Totales
    total_ventas: Optional[Decimal] = None      # D44
    total_efectivo: Optional[Decimal] = None     # E63
    total_tdc: Optional[Decimal] = None          # H63
    total_otros: Optional[Decimal] = None        # L55
    folio_sissa: Optional[str] = None            # D65

    @property
    def total_facturas_individuales(self) -> Decimal:
        """Suma de importes de facturas individuales."""
        return sum((f.importe for f in self.facturas_individuales), Decimal('0'))


@dataclass
class LineaContable:
    """Linea de percepcion o deduccion de nomina."""
    concepto: str
    cuenta: str
    subcuenta: str
    monto: Decimal


@dataclass
class MovimientoNomina:
    """Un movimiento individual de nomina (dispersion, cheques, vacaciones, etc.)."""
    tipo: str               # 'DISPERSION', 'CHEQUES', 'VAC PAGADAS', 'FINIQUITO PAGADO', etc.
    monto: Decimal
    clase: str = 'NOMINA'               # 'NOMINA' o 'FINIQUITO'
    tipo_egreso: str = 'TRANSFERENCIA'  # 'TRANSFERENCIA' o 'CHEQUE'
    es_principal: bool = False          # True solo para DISPERSION (recibe poliza completa)
    matched: bool = False               # True cuando ya se creo el movimiento secundario


@dataclass
class DatosNomina:
    """Datos parseados del Excel de nomina CONTPAQi."""
    numero_nomina: int          # Ej: 3 (de "NOMINA 03 CHEQUE.xlsx")

    movimientos: List[MovimientoNomina] = field(default_factory=list)
    percepciones: List[LineaContable] = field(default_factory=list)
    deducciones: List[LineaContable] = field(default_factory=list)

    @property
    def total_neto(self) -> Decimal:
        """Total neto de la nomina (suma de todos los movimientos)."""
        return sum((m.monto for m in self.movimientos), Decimal('0'))

    @property
    def total_dispersion(self) -> Decimal:
        """Monto del movimiento principal (dispersion)."""
        return sum(
            (m.monto for m in self.movimientos if m.es_principal), Decimal('0'),
        )

    @property
    def total_secundarios(self) -> Decimal:
        """Suma de movimientos secundarios (cheques, vacaciones, finiquito, etc.)."""
        return sum(
            (m.monto for m in self.movimientos if not m.es_principal), Decimal('0'),
        )


@dataclass
class RetencionIVAProveedor:
    """Retencion IVA por proveedor (de la DIOT en acuse SAT)."""
    proveedor: str          # Clave SAV7 (ej: '001640')
    nombre: str
    monto: Decimal


@dataclass
class DatosImpuestoFederal:
    """Datos parseados de las declaraciones federales mensuales."""
    periodo: str            # 'ENERO 2026'
    # 1a Declaracion (Retenciones + IEPS)
    isr_ret_honorarios: Decimal
    isr_ret_arrendamiento: Decimal
    ieps_neto: Decimal               # Monto a pagar (linea de captura)
    ieps_acumulable: Decimal          # Bruto (de detalle IEPS)
    ieps_acreditable: Decimal         # acumulable - neto
    total_primera: Decimal            # Suma 1a declaracion
    # 2a Declaracion (ISR + IVA)
    isr_personas_morales: Decimal
    isr_ret_salarios: Decimal
    iva_acumulable: Decimal           # Bruto IVA trasladado
    iva_acreditable: Decimal          # IVA acreditable
    iva_a_favor: Decimal              # iva_acreditable - iva_acumulable (si > 0)
    retenciones_iva: List[RetencionIVAProveedor] = field(default_factory=list)
    total_segunda: Decimal = field(default_factory=lambda: Decimal('0'))
    # Metadata de confianza
    confianza_100: bool = False
    advertencias: List[str] = field(default_factory=list)


@dataclass
class DatosImpuestoEstatal:
    """Datos parseados del impuesto estatal 3% sobre nominas."""
    periodo: str
    monto: Decimal
    confianza_100: bool = False
    advertencias: List[str] = field(default_factory=list)


@dataclass
class DatosIMSS:
    """Datos parseados del Resumen de Liquidacion SUA (IMSS/INFONAVIT)."""
    periodo: str                    # 'ENERO 2026' o 'OCTUBRE 2025'
    folio_sua: str                  # '659522'

    # IMSS (siempre presente)
    total_imss: Decimal             # Subtotal seccion "Para abono en cuenta del IMSS"

    # Cuenta Individual (solo bimestral)
    retiro: Decimal = field(default_factory=lambda: Decimal('0'))
    cesantia_vejez: Decimal = field(default_factory=lambda: Decimal('0'))
    total_cuenta_individual: Decimal = field(default_factory=lambda: Decimal('0'))

    # INFONAVIT (solo bimestral)
    aportacion_sin_credito: Decimal = field(default_factory=lambda: Decimal('0'))
    aportacion_con_credito: Decimal = field(default_factory=lambda: Decimal('0'))
    amortizacion: Decimal = field(default_factory=lambda: Decimal('0'))
    total_infonavit: Decimal = field(default_factory=lambda: Decimal('0'))

    # Total
    total_a_pagar: Decimal = field(default_factory=lambda: Decimal('0'))

    # Flags
    incluye_infonavit: bool = False
    confianza_100: bool = False
    advertencias: List[str] = field(default_factory=list)

    @property
    def infonavit_5pct(self) -> Decimal:
        """5% INFONAVIT = Ap. sin credito + Ap. con credito."""
        return self.aportacion_sin_credito + self.aportacion_con_credito


# --- Modelos de salida (procesadores → BD) ---

@dataclass
class DatosMovimientoPM:
    """Datos para INSERT en SAVCheqPM."""
    banco: str
    cuenta: str
    age: int                    # Anio
    mes: int
    dia: int
    tipo: int                   # 1, 2, 3, o 4
    ingreso: Decimal
    egreso: Decimal
    concepto: str
    clase: str
    fpago: Optional[str]        # 'Efectivo', 'Tarjeta Débito', 'Tarjeta Crédito', None
    tipo_egreso: str            # 'TRANSFERENCIA', 'CHEQUE', 'NA'
    conciliada: int             # 0 o 1
    paridad: Decimal
    tipo_poliza: str            # 'INGRESO', 'EGRESO', 'DIARIO'
    num_factura: str            # Referencia factura (ej: 'D-20204')
    paridad_dof: Optional[Decimal] = None  # 20.0000 para traspasos, None para otros
    referencia: str = ''                    # 'TRASPASO AUTOMATICO' para traspasos, '' para otros
    referencia2: Optional[str] = None       # Solo cobros: 'FP: ... B: ... Ref: '
    total_letra: str = ''                   # '( MONTO PESOS XX/100 M.N. )' — se genera automaticamente
    proveedor: str = ''                      # Clave proveedor (ej: '001081' para comisiones)
    proveedor_nombre: str = ''               # 'BANCO REGIONAL'
    tipo_proveedor: str = ''                 # 'NA'
    num_cheque: str = ''                     # Numero de cheque (ej: '7632')
    # Campos adicionales (comisiones / pagos afectados)
    cheque_para: str = ''                    # 'BANCO REGIONAL' (nombre beneficiario)
    pago_afectado: bool = False              # true cuando el pago ya fue procesado
    num_pagos: int = 0                       # Numero de pagos vinculados
    fecha_cheque_cobrado: Optional[date] = None  # Fecha del movimiento (para comisiones = fecha)
    valor_pagado_tasa15: Decimal = Decimal('0')  # Subtotal (sin IVA)
    valor_pagado_imp_tasa15: Decimal = Decimal('0')  # IVA
    estatus: str = ''                        # 'Afectado' para comisiones
    rfc: str = ''                            # RFC del proveedor/beneficiario
    # Campos que se asignan al ejecutar
    folio: Optional[int] = None
    num_poliza: Optional[int] = None


@dataclass
class DatosFacturaPMF:
    """Datos para INSERT en SAVCheqPMF."""
    serie: str                  # 'FD'
    num_factura: str            # ej: '20204'
    ingreso: Decimal            # Monto aplicado en este movimiento
    fecha_factura: date
    tipo_factura: str           # 'GLOBAL' o 'INDIVIDUAL'
    monto_factura: Decimal      # Total de la factura
    saldo_factura: Decimal      # Saldo restante (normalmente 0)


@dataclass
class LineaPoliza:
    """Una linea de poliza contable (SAVPoliza)."""
    movimiento: int             # Numero de linea (1, 2, 3...)
    cuenta: str                 # Ej: '1120'
    subcuenta: str              # Ej: '040000'
    tipo_ca: TipoCA            # CARGO o ABONO
    cargo: Decimal
    abono: Decimal
    concepto: str
    doc_tipo: str = 'CHEQUES'   # 'CHEQUES' o 'TRASPASOS'
    nombre: str = ''            # Nombre de la cuenta contable (de SAVCuenta)


@dataclass
class DatosCompraPM:
    """Datos para INSERT en SAVRecC/SAVRecD (facturas de compra, comisiones)."""
    proveedor: str
    factura: str                # DDMMAAAA
    fecha: date
    subtotal: Decimal
    iva: Decimal
    total: Decimal


@dataclass
class DatosCobroCliente:
    """Datos para crear un cobro completo (SAVFactCob + SAVFactC + SAVCheqPM + SAVPoliza)."""
    # Factura
    serie: str                  # 'FC'
    num_fac: int                # Numero de factura
    cliente: str                # '000671'
    cliente_nombre: str         # 'ALEJANDRO HURTADO TREVIÑO'
    fecha_cobro: date           # Fecha del EdoCta
    fecha_factura: date         # Fecha de la factura (de SAVFactC)
    monto: Decimal              # Monto del cobro
    vendedor: str               # Vendedor de la factura
    # Banco
    banco: str                  # 'BANREGIO'
    cuenta_banco: str           # '055003730017'
    cuenta_contable: str        # '1120'
    subcuenta_contable: str     # '040000'
    # Desglose fiscal (copiado de SAVFactC)
    subtotal_iva0: Decimal = field(default_factory=lambda: Decimal('0'))
    subtotal_iva16: Decimal = field(default_factory=lambda: Decimal('0'))
    iva: Decimal = field(default_factory=lambda: Decimal('0'))
    ieps: Decimal = field(default_factory=lambda: Decimal('0'))


# --- Plan de ejecucion ---

@dataclass
class PlanEjecucion:
    """Plan completo de lo que un procesador intenta hacer.

    En modo dry-run se muestra sin ejecutar.
    En modo live se ejecuta dentro de una transaccion.
    """
    tipo_proceso: str
    descripcion: str
    fecha_movimiento: date

    movimientos_pm: List[DatosMovimientoPM] = field(default_factory=list)
    facturas_pmf: List[DatosFacturaPMF] = field(default_factory=list)
    lineas_poliza: List[LineaPoliza] = field(default_factory=list)
    compras: List[DatosCompraPM] = field(default_factory=list)
    cobros_cliente: List[DatosCobroCliente] = field(default_factory=list)
    conciliaciones: List[Dict] = field(default_factory=list)

    # Mapeo: cuantas facturas_pmf y lineas_poliza pertenecen a cada movimiento_pm.
    # Si vacio, se asume 1 factura y 6 lineas por movimiento (patron TDC).
    facturas_por_movimiento: List[int] = field(default_factory=list)
    lineas_por_movimiento: List[int] = field(default_factory=list)

    validaciones: List[str] = field(default_factory=list)
    advertencias: List[str] = field(default_factory=list)

    @property
    def total_inserts(self) -> int:
        """Total de filas que se insertarian."""
        return (
            len(self.movimientos_pm)
            + len(self.facturas_pmf)
            + len(self.lineas_poliza)
            + len(self.compras)
        )

    @property
    def total_updates(self) -> int:
        """Total de filas que se actualizarian."""
        return len(self.conciliaciones)


@dataclass
class ResultadoProceso:
    """Resultado de la ejecucion de un plan."""
    exito: bool
    tipo_proceso: str
    descripcion: str
    folios: List[int] = field(default_factory=list)
    num_poliza: Optional[int] = None
    plan: Optional[PlanEjecucion] = None
    error: Optional[str] = None


# --- Resultado por linea de estado de cuenta ---

class AccionLinea(str, Enum):
    """Accion tomada sobre una linea del estado de cuenta."""
    INSERT = 'INSERT'
    CONCILIAR = 'CONCILIAR'
    OMITIR = 'OMITIR'
    SIN_PROCESAR = 'SIN_PROCESAR'
    REQUIERE_REVISION = 'REQUIERE_REVISION'
    ERROR = 'ERROR'
    DESCONOCIDO = 'DESCONOCIDO'


@dataclass
class ResultadoLinea:
    """Vinculo entre una linea del estado de cuenta y su resultado."""
    movimiento: MovimientoBancario
    tipo_clasificado: TipoProceso
    accion: AccionLinea
    folios: List[int] = field(default_factory=list)
    resultado: Optional[ResultadoProceso] = None
    nota: Optional[str] = None
