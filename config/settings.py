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

    # IMSS / INFONAVIT
    IMSS_GASTO = ('6200', '070000')              # I.M.S.S. (gasto patronal)
    APORTACION_2PCT_SAR = ('6200', '028000')     # Aportacion 2% S.A.R. (Retiro)
    CESANTIA_VEJEZ = ('6200', '360000')          # Cesantia y Vejez
    INFONAVIT_5PCT = ('6200', '050000')          # 5% INFONAVIT
