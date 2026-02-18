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
class DatosNomina:
    """Datos parseados del Excel de nomina CONTPAQi."""
    numero_nomina: int          # Ej: 3 (de "NOMINA 03 CHEQUE.xlsx")
    total_dispersion: Decimal   # Transferencias
    total_cheques: Decimal      # Pagos en efectivo/cheque
    total_vacaciones: Decimal   # Vacaciones pagadas
    total_finiquito: Decimal    # Finiquitos

    percepciones: List[LineaContable] = field(default_factory=list)
    deducciones: List[LineaContable] = field(default_factory=list)

    @property
    def total_neto(self) -> Decimal:
        """Total neto de la nomina."""
        return (
            self.total_dispersion
            + self.total_cheques
            + self.total_vacaciones
            + self.total_finiquito
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


@dataclass
class DatosCompraPM:
    """Datos para INSERT en SAVRecC/SAVRecD (facturas de compra, comisiones)."""
    proveedor: str
    factura: str                # DDMMAAAA
    fecha: date
    subtotal: Decimal
    iva: Decimal
    total: Decimal


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
