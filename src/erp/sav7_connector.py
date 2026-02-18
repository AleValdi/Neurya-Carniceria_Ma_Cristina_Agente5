"""Conector principal al ERP SAV7.

Wrapper sobre DatabaseConnection que provee acceso centralizado
a la BD con configuracion del proyecto.
"""

from typing import Optional

from loguru import logger

from config.database import DatabaseConfig, DatabaseConnection
from config.settings import Settings


class SAV7Connector:
    """Acceso centralizado a la base de datos del ERP SAV7."""

    def __init__(self, settings: Optional[Settings] = None):
        if settings is None:
            settings = Settings.from_env()
        self.settings = settings
        self._db_config = DatabaseConfig.from_settings(settings)
        self._db = DatabaseConnection(self._db_config)

    @property
    def db(self) -> DatabaseConnection:
        """Instancia de DatabaseConnection."""
        return self._db

    def test_conexion(self) -> bool:
        """Prueba la conexion a la BD."""
        return self._db.test_conexion()

    def get_cursor(self, transaccion: bool = False):
        """Context manager para obtener un cursor.

        Args:
            transaccion: Si True, commit/rollback automatico.
        """
        return self._db.get_cursor(transaccion=transaccion)

    def desconectar(self):
        """Cierra la conexion."""
        self._db.desconectar()
