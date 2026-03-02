"""Conexion a SQL Server para el ERP SAV7.

Provee DatabaseConnection con context manager para conexiones
y cursores, soporte de transacciones con rollback automatico,
y fallback multi-driver para compatibilidad Mac/Linux/Windows.
"""

import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import pyodbc
from loguru import logger


# Fix para OpenSSL 3.x en macOS: permite TLS legacy para ODBC Driver 18
if not os.environ.get('OPENSSL_CONF'):
    _openssl_cnf = os.path.join(tempfile.gettempdir(), 'agente5_openssl.cnf')
    if not os.path.exists(_openssl_cnf):
        with open(_openssl_cnf, 'w') as f:
            f.write(
                "openssl_conf = openssl_init\n"
                "[openssl_init]\nssl_conf = ssl_sect\n"
                "[ssl_sect]\nsystem_default = system_default_sect\n"
                "[system_default_sect]\nMinProtocol = TLSv1\n"
                "CipherString = DEFAULT@SECLEVEL=0\n"
            )
    os.environ['OPENSSL_CONF'] = _openssl_cnf


# Drivers en orden de preferencia
DRIVERS_DISPONIBLES = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server Native Client 11.0",
]


@dataclass
class DatabaseConfig:
    """Configuracion de conexion a SQL Server."""
    server: str = 'localhost'
    database: str = 'DBSAV71A'
    username: str = ''
    password: str = ''
    driver: str = '{ODBC Driver 17 for SQL Server}'
    port: int = 1433

    @classmethod
    def from_settings(cls, settings) -> 'DatabaseConfig':
        """Crea configuracion desde un objeto Settings."""
        return cls(
            server=settings.db_server,
            database=settings.db_database,
            username=settings.db_username,
            password=settings.db_password,
            driver=settings.db_driver,
            port=settings.db_port,
        )

    def get_connection_string(self, driver_override: str = None) -> str:
        """Genera la cadena de conexion para pyodbc."""
        driver = driver_override or self.driver
        # Asegurar que el driver tenga llaves
        if not driver.startswith('{'):
            driver = '{' + driver + '}'
        return (
            f"DRIVER={driver};"
            f"SERVER={self.server},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            f"TrustServerCertificate=yes;"
        )


class DatabaseConnection:
    """Administrador de conexiones a SQL Server.

    Uso basico:
        db = DatabaseConnection(config)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")

    Con transaccion explicita:
        with db.get_cursor(transaccion=True) as cursor:
            cursor.execute("INSERT ...")
            cursor.execute("UPDATE ...")
        # Commit automatico al salir sin error, rollback si hay excepcion
    """

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection: Optional[pyodbc.Connection] = None

    def conectar(self) -> pyodbc.Connection:
        """Establece conexion intentando multiples drivers."""
        if self._connection:
            try:
                # Verificar que la conexion sigue viva
                self._connection.cursor().execute("SELECT 1")
                return self._connection
            except Exception:
                self._connection = None

        # Intentar con el driver configurado primero
        drivers_a_probar = [self.config.driver.strip('{}')]

        # Agregar fallbacks
        for d in DRIVERS_DISPONIBLES:
            if d not in drivers_a_probar:
                drivers_a_probar.append(d)

        last_error = None
        for driver in drivers_a_probar:
            try:
                conn_str = self.config.get_connection_string(driver)
                logger.debug("Intentando conexion con driver: {}", driver)
                conn = pyodbc.connect(conn_str, timeout=10)
                # SQL_CHAR (varchar): latin-1 porque la BD tiene datos con
                # acentos/ñ almacenados en Windows-1252 (compatible latin-1)
                conn.setdecoding(pyodbc.SQL_CHAR, encoding='latin-1')
                conn.setdecoding(pyodbc.SQL_WCHAR, encoding='utf-16-le')
                conn.setencoding(encoding='latin-1')
                self._connection = conn
                logger.info(
                    "Conectado a {} con driver '{}'",
                    self.config.database, driver,
                )
                return conn
            except pyodbc.Error as e:
                last_error = e
                logger.debug("Driver '{}' fallo: {}", driver, e)
                continue

        raise ConnectionError(
            f"No se pudo conectar a {self.config.server}. "
            f"Ultimo error: {last_error}"
        )

    def desconectar(self):
        """Cierra la conexion si esta abierta."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
            logger.debug("Conexion cerrada")

    @contextmanager
    def get_connection(self):
        """Context manager para obtener una conexion."""
        conn = self.conectar()
        try:
            yield conn
        finally:
            pass  # No cerramos aqui, reutilizamos la conexion

    @contextmanager
    def get_cursor(self, transaccion: bool = False):
        """Context manager para obtener un cursor.

        Args:
            transaccion: Si True, maneja commit/rollback automaticamente.
                        El autocommit se desactiva y se hace commit al salir
                        sin error, o rollback si hay excepcion.
        """
        conn = self.conectar()

        if transaccion:
            conn.autocommit = False
        cursor = conn.cursor()

        try:
            yield cursor
            if transaccion:
                conn.commit()
                logger.debug("Transaccion committed")
        except Exception:
            if transaccion:
                conn.rollback()
                logger.warning("Transaccion rolled back por excepcion")
            raise
        finally:
            cursor.close()
            if transaccion:
                conn.autocommit = True

    def test_conexion(self) -> bool:
        """Prueba la conexion y retorna True si es exitosa."""
        try:
            conn = self.conectar()
            cursor = conn.cursor()
            cursor.execute("SELECT DB_NAME() AS db, @@SERVERNAME AS server")
            row = cursor.fetchone()
            logger.info("Conexion OK: BD={}, Servidor={}", row.db, row.server)
            cursor.close()
            return True
        except Exception as e:
            logger.error("Error de conexion: {}", e)
            return False
