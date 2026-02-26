"""Configuracion central del Agente5.

Constantes del ERP SAV7, mapeo de cuentas bancarias y parametros
de ejecucion. Todo se carga desde variables de entorno (.env).
"""

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv


@dataclass
class CuentaBancariaConfig:
    """Configuracion de una cuenta bancaria del ERP."""
    banco: str
    cuenta: str
    cuenta_contable: str
    subcuenta_contable: str
    nombre: str


# Mapeo de cuentas bancarias activas
CUENTAS_BANCARIAS: Dict[str, CuentaBancariaConfig] = {
    'efectivo': CuentaBancariaConfig(
        banco='BANREGIO',
        cuenta='055003730017',
        cuenta_contable='1120',
        subcuenta_contable='040000',
        nombre='BANREGIO F (EFECTIVO)',
    ),
    'tarjeta': CuentaBancariaConfig(
        banco='BANREGIO',
        cuenta='038900320016',
        cuenta_contable='1120',
        subcuenta_contable='060000',
        nombre='BANREGIO T (TARJETA)',
    ),
    'gastos': CuentaBancariaConfig(
        banco='BANREGIO',
        cuenta='055003730157',
        cuenta_contable='1120',
        subcuenta_contable='070000',
        nombre='BANREGIO GASTOS',
    ),
    'caja_chica': CuentaBancariaConfig(
        banco='CAJA CHICA',
        cuenta='00000000000',
        cuenta_contable='1110',
        subcuenta_contable='010000',
        nombre='CAJA CHICA',
    ),
}

# Mapeo inverso: numero de cuenta → clave
CUENTA_POR_NUMERO: Dict[str, str] = {
    cfg.cuenta: clave for clave, cfg in CUENTAS_BANCARIAS.items()
}

# Hojas del estado de cuenta y su cuenta bancaria asociada
HOJAS_ESTADO_CUENTA: Dict[str, str] = {
    'Banregio F': 'efectivo',
    'Banregio T ': 'tarjeta',       # Nota: tiene espacio al final
    'Banregio T': 'tarjeta',        # Sin espacio por si acaso
    'BANREGIO GTS': 'gastos',
}


@dataclass
class Settings:
    """Configuracion principal del Agente5."""

    # --- Base de datos ---
    db_server: str = '100.73.181.41'
    db_database: str = 'DBSAV71A'
    db_username: str = ''
    db_password: str = ''
    db_driver: str = '{ODBC Driver 17 for SQL Server}'
    db_port: int = 1433

    # --- Directorios ---
    proyecto_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    entrada_dir: Path = field(default=None)
    procesados_dir: Path = field(default=None)
    errores_dir: Path = field(default=None)
    logs_dir: Path = field(default=None)

    # --- Constantes ERP ---
    cia: str = 'DCM'
    fuente: str = 'SAV7-CHEQUES'
    oficina: str = '01'
    cuenta_oficina: str = '01'
    sucursal: int = 5
    moneda: str = 'PESOS'
    paridad: Decimal = field(default_factory=lambda: Decimal('1.0000'))
    paridad_dof: Decimal = field(default_factory=lambda: Decimal('20.0000'))
    usuario_sistema: str = 'AGENTE5'
    rfc_empresa: str = 'DCM02072238A'

    # --- Proveedor banco (comisiones) ---
    proveedor_banco: str = '001081'
    producto_comision: str = '001002002'
    nombre_comision: str = 'COMISION TERMINAL'

    # --- Validacion ---
    tolerancia_centavos: Decimal = field(default_factory=lambda: Decimal('0.50'))

    # --- Logging ---
    log_level: str = 'INFO'

    def __post_init__(self):
        """Inicializa directorios derivados si no fueron proporcionados."""
        if self.entrada_dir is None:
            self.entrada_dir = self.proyecto_dir / 'data' / 'entrada'
        if self.procesados_dir is None:
            self.procesados_dir = self.proyecto_dir / 'data' / 'procesados'
        if self.errores_dir is None:
            self.errores_dir = self.proyecto_dir / 'data' / 'errores'
        if self.logs_dir is None:
            self.logs_dir = self.proyecto_dir / 'logs'

    @classmethod
    def from_env(cls, env_path: str = None) -> 'Settings':
        """Carga configuracion desde variables de entorno (.env)."""
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        return cls(
            db_server=os.getenv('DB_SERVER', '100.73.181.41'),
            db_database=os.getenv('DB_DATABASE', 'DBSAV71A'),
            db_username=os.getenv('DB_USERNAME', ''),
            db_password=os.getenv('DB_PASSWORD', ''),
            db_driver=os.getenv('DB_DRIVER', '{ODBC Driver 17 for SQL Server}'),
            db_port=int(os.getenv('DB_PORT', '1433')),
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
        )


# Cuentas contables usadas en polizas
class CuentasContables:
    """Catalogo de cuentas contables frecuentes."""

    # Bancos
    BANCO_EFECTIVO = ('1120', '040000')
    BANCO_TARJETA = ('1120', '060000')
    BANCO_GASTOS = ('1120', '070000')

    # Clientes
    CLIENTES_GLOBAL = ('1210', '010000')

    # IVA
    IVA_ACUMULABLE_COBRADO = ('2141', '010000')
    IVA_ACUMULABLE_PTE_COBRO = ('2146', '010000')
    IVA_ACREDITABLE_PTE_PAGO = ('1240', '010000')
    IVA_ACREDITABLE_PAGADO = ('1246', '010000')

    # IEPS
    IEPS_ACUMULABLE_COBRADO = ('2141', '020000')
    IEPS_ACUMULABLE_PTE_COBRO = ('2146', '020000')

    # Proveedores
    PROVEEDORES_GLOBAL = ('2110', '010000')

    # Acreedores
    ACREEDORES_BANREGIO = ('2120', '020000')
    ACREEDORES_NOMINA = ('2120', '040000')
    ACREEDORES_CLIENTES = ('2120', '070000')

    # Retenciones
    RETENCION_IMSS = ('2140', '010000')
    RETENCION_ISR = ('2140', '020000')
    RETENCION_INFONAVIT = ('2140', '270000')

    # Impuestos federales
    ISR_PROVISIONAL = ('1245', '010000')
    ISR_RET_HONORARIOS = ('2140', '070000')
    ISR_RET_ARRENDAMIENTO = ('2140', '320000')
    IVA_RETENIDO_PAGADO = ('2140', '290000')
    IVA_A_FAVOR = ('1247', '010000')
    IEPS_ACREDITABLE_PAGADO = ('1246', '020000')

    # Impuesto estatal
    NOMINAS_3_PCT = ('6200', '850000')
    RVA_3_NOMINAS = ('2140', '220000')

    # Retenciones federales pagadas
    ISR_RET_HONORARIOS_PAGADO = ('2140', '330000')
    ISR_RET_ARRENDAMIENTO_PAGADO = ('2140', '320000')
    IVA_RETENIDO_PTE_PAGO = ('2140', '260000')

    # IMSS / INFONAVIT
    IMSS_GASTO = ('6200', '070000')              # I.M.S.S. (gasto patronal)
    APORTACION_2PCT_SAR = ('6200', '028000')     # Aportacion 2% S.A.R. (Retiro)
    CESANTIA_VEJEZ = ('6200', '360000')          # Cesantia y Vejez
    INFONAVIT_5PCT = ('6200', '050000')          # 5% INFONAVIT


# Mapeo Cuenta+SubCuenta → Nombre (de SAVCuenta en produccion)
# Usado por poliza.py para poblar el campo Nombre en SAVPoliza.
NOMBRES_CUENTAS: Dict[tuple, str] = {
    # Caja
    ('1110', '010000'): 'CAJA CHICA',
    # Bancos
    ('1120', '040000'): 'BANREGIO F',
    ('1120', '060000'): 'BANREGIO T',
    ('1120', '070000'): 'BANREGIO GASTOS',
    ('1120', '080000'): 'BANREGIO EMPRESARIAL 5164',
    # Clientes
    ('1210', '010000'): 'CLIENTES GLOBAL',
    # IVA acreditable
    ('1240', '010000'): 'IVA ACREDITABLE AL 16% PTE PAGO',
    ('1246', '010000'): 'IVA ACREDITABLE PAGADO',
    ('1246', '020000'): 'IEPS ACREDITABLE PAGADO',
    # ISR provisional / IVA a favor
    ('1245', '010000'): 'PAGO PROVISIONAL DE I.S.R.',
    ('1247', '010000'): 'IVA A FAVOR',
    # Proveedores
    ('2110', '010000'): 'PROVEEDORES GLOBAL',
    # Acreedores
    ('2120', '020000'): 'ACREEDORES DIVERSOS BANREGIO',
    ('2120', '040000'): 'ACREEDORES DIVERSOS NOMINA',
    ('2120', '070000'): 'ACREEDORES CLIENTES',
    # Retenciones / impuestos por pagar
    ('2140', '010000'): 'RETENCION I.M.S.S.',
    ('2140', '020000'): 'RETENCION I.S.P.T.',
    ('2140', '030000'): 'RVA PARA PAGO DE IMSS',
    ('2140', '040000'): 'RVA. 5% INFONAVIT',
    ('2140', '070000'): 'RET ISR HONORARIOS',
    ('2140', '130000'): 'RVA.P PAGO 2% SAR',
    ('2140', '140000'): 'RET 10% ISR ARREND PTE PAGO',
    ('2140', '200000'): 'RVA.PARA CESANTIA Y VEJEZ',
    ('2140', '220000'): 'RVA. 3% S/NOMINAS',
    ('2140', '230000'): 'RVA. P. IVA PAGADO',
    ('2140', '260000'): 'IVA RETENIDO PTE PAGO',
    ('2140', '270000'): 'RETENCION INFONAVIT',
    ('2140', '290000'): 'IVA RETENIDO PAGADO',
    ('2140', '320000'): 'RET 10% ISR ARREND PAGADO',
    ('2140', '330000'): 'RET ISR HONORARIOS PAGADO',
    # IVA/IEPS trasladados (ventas)
    ('2141', '010000'): 'IVA ACUMULABRE COBRADO',
    ('2141', '020000'): 'IEPS ACUMULABLE COBRADO',
    ('2146', '010000'): 'IVA ACUMULABLE AL 16% PTE COBRO',
    ('2146', '020000'): 'IEPS ACUMULABLE AL 8% PTE COBRO',
    # Gastos de venta (nomina, impuestos)
    ('6200', '010000'): 'SUELDOS Y SALARIOS',
    ('6200', '020000'): 'VACACIONES',
    ('6200', '028000'): 'APORTACION 2% S.A.R.',
    ('6200', '030000'): 'AGUINALDOS',
    ('6200', '050000'): '5% INFONAVIT',
    ('6200', '060000'): 'PRIMA VACACIONAL',
    ('6200', '070000'): 'I.M.S.S.',
    ('6200', '240000'): 'SEPTIMO DIA',
    ('6200', '260000'): 'GRATIFICACIONES',
    ('6200', '270000'): 'INDEMNIZACIONES',
    ('6200', '300000'): 'RECARGOS',
    ('6200', '360000'): 'CESANTIA Y VEJEZ',
    ('6200', '370000'): 'COMISIONES BANCARIAS',
    ('6200', '670000'): 'PRIMA DOMINICAL',
    ('6200', '770000'): 'BONO DE PUNTUALIDAD',
    ('6200', '780000'): 'BONO DE ASISTENCIA',
    ('6200', '850000'): '3% NOMINAS',
}
