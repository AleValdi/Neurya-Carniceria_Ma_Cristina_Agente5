"""File watcher para monitoreo automatico de archivos de entrada.

Detecta archivos Excel nuevos en la carpeta de entrada, los clasifica
por tipo, agrupa los que van juntos, y despacha al orquestador.
Despues de procesar, mueve los archivos a procesados/ o errores/.
"""

import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from loguru import logger


# Patrones para clasificar archivos por nombre
PATRONES_ESTADO_CUENTA = re.compile(
    r'(PRUEBA|ESTADO|EDO[_ ]?CTA|BANREGIO)',
    re.IGNORECASE,
)
PATRONES_TESORERIA = re.compile(
    r'(INGRESO|TESORERIA)',
    re.IGNORECASE,
)
PATRONES_NOMINA = re.compile(
    r'NOMINA',
    re.IGNORECASE,
)


@dataclass
class LoteArchivos:
    """Grupo de archivos relacionados para procesar juntos."""
    estado_cuenta: Optional[Path] = None
    tesoreria: Optional[Path] = None
    nomina: Optional[Path] = None

    @property
    def es_valido(self) -> bool:
        """Un lote requiere al menos un estado de cuenta."""
        return self.estado_cuenta is not None

    @property
    def archivos(self) -> List[Path]:
        """Lista de todos los archivos del lote."""
        result = []
        if self.estado_cuenta:
            result.append(self.estado_cuenta)
        if self.tesoreria:
            result.append(self.tesoreria)
        if self.nomina:
            result.append(self.nomina)
        return result


def clasificar_archivo(ruta: Path) -> str:
    """Clasifica un archivo Excel por su nombre.

    Returns:
        'estado-cuenta', 'tesoreria', 'nomina' o 'desconocido'
    """
    nombre = ruta.stem  # Nombre sin extension

    if PATRONES_NOMINA.search(nombre):
        return 'nomina'
    if PATRONES_TESORERIA.search(nombre):
        return 'tesoreria'
    if PATRONES_ESTADO_CUENTA.search(nombre):
        return 'estado-cuenta'

    return 'desconocido'


def agrupar_archivos(archivos: List[Path]) -> LoteArchivos:
    """Agrupa archivos detectados en un lote para procesar juntos."""
    lote = LoteArchivos()

    for ruta in archivos:
        tipo = clasificar_archivo(ruta)
        if tipo == 'estado-cuenta' and lote.estado_cuenta is None:
            lote.estado_cuenta = ruta
        elif tipo == 'tesoreria' and lote.tesoreria is None:
            lote.tesoreria = ruta
        elif tipo == 'nomina' and lote.nomina is None:
            lote.nomina = ruta
        elif tipo == 'desconocido':
            logger.warning("Archivo no clasificado: {}", ruta.name)
        else:
            logger.warning(
                "Archivo duplicado de tipo '{}': {} (ya se tiene {})",
                tipo, ruta.name,
                getattr(lote, tipo.replace('-', '_'), 'N/A'),
            )

    return lote


def mover_archivo(ruta: Path, destino_dir: Path) -> Path:
    """Mueve un archivo a un directorio con prefijo de timestamp.

    Args:
        ruta: Archivo a mover.
        destino_dir: Directorio destino (procesados/ o errores/).

    Returns:
        Ruta final del archivo movido.
    """
    destino_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    nombre_destino = f"{timestamp}_{ruta.name}"
    ruta_destino = destino_dir / nombre_destino

    # Evitar colision si ya existe
    if ruta_destino.exists():
        base = ruta_destino.stem
        ext = ruta_destino.suffix
        contador = 1
        while ruta_destino.exists():
            ruta_destino = destino_dir / f"{base}_{contador}{ext}"
            contador += 1

    shutil.move(str(ruta), str(ruta_destino))
    logger.info("Archivo movido: {} → {}", ruta.name, ruta_destino)
    return ruta_destino


def mover_lote(lote: LoteArchivos, exito: bool, procesados_dir: Path, errores_dir: Path):
    """Mueve todos los archivos de un lote a procesados/ o errores/."""
    destino = procesados_dir if exito else errores_dir
    for archivo in lote.archivos:
        if archivo.exists():
            mover_archivo(archivo, destino)


def detectar_archivos(entrada_dir: Path) -> List[Path]:
    """Detecta archivos Excel en la carpeta de entrada."""
    if not entrada_dir.exists():
        return []
    return sorted(entrada_dir.glob('*.xlsx'))


class FileWatcher:
    """Monitorea carpeta de entrada y procesa archivos automaticamente."""

    def __init__(
        self,
        entrada_dir: Path,
        procesados_dir: Path,
        errores_dir: Path,
        intervalo: int = 60,
    ):
        self.entrada_dir = entrada_dir
        self.procesados_dir = procesados_dir
        self.errores_dir = errores_dir
        self.intervalo = intervalo
        self._archivos_procesados: Set[str] = set()

    def iniciar(self, dry_run: bool = True):
        """Inicia el loop de polling.

        Args:
            dry_run: Si True, solo muestra planes sin ejecutar.
        """
        logger.info(
            "Watcher iniciado. Monitoreando: {}",
            self.entrada_dir,
        )
        logger.info(
            "Intervalo: {}s | Modo: {}",
            self.intervalo,
            'DRY-RUN' if dry_run else 'EJECUCION',
        )

        # Crear directorio de entrada si no existe
        self.entrada_dir.mkdir(parents=True, exist_ok=True)

        try:
            while True:
                self._ciclo(dry_run)
                time.sleep(self.intervalo)
        except KeyboardInterrupt:
            logger.info("Watcher detenido por el usuario")

    def _ciclo(self, dry_run: bool):
        """Un ciclo de deteccion y procesamiento."""
        archivos = detectar_archivos(self.entrada_dir)

        # Filtrar archivos ya procesados en este ciclo (por si aun no se movieron)
        archivos_nuevos = [
            a for a in archivos
            if str(a) not in self._archivos_procesados
        ]

        if not archivos_nuevos:
            return

        logger.info(
            "Detectados {} archivos nuevos en {}",
            len(archivos_nuevos), self.entrada_dir,
        )

        lote = agrupar_archivos(archivos_nuevos)

        if not lote.es_valido:
            logger.warning(
                "No se encontro estado de cuenta en los archivos detectados"
            )
            return

        # Marcar como en proceso
        for a in lote.archivos:
            self._archivos_procesados.add(str(a))

        exito = self.procesar_lote(lote, dry_run)

        # Mover archivos
        if not dry_run:
            mover_lote(lote, exito, self.procesados_dir, self.errores_dir)

    def procesar_lote(self, lote: LoteArchivos, dry_run: bool) -> bool:
        """Procesa un lote de archivos ejecutando todos los tipos aplicables.

        Returns:
            True si al menos un proceso fue exitoso, False si todo fallo.
        """
        from src.orquestador import (
            procesar_comisiones,
            procesar_conciliaciones,
            procesar_nomina,
            procesar_traspasos,
            procesar_ventas_efectivo,
            procesar_ventas_tdc,
        )

        algun_exito = False
        ruta_ec = lote.estado_cuenta

        # 1. Ventas TDC (requiere tesoreria)
        if lote.tesoreria:
            try:
                logger.info("--- Procesando VENTAS TDC ---")
                resultados = procesar_ventas_tdc(
                    ruta_estado_cuenta=ruta_ec,
                    ruta_tesoreria=lote.tesoreria,
                    dry_run=dry_run,
                )
                if any(r.exito for r in resultados):
                    algun_exito = True
            except Exception as e:
                logger.error("Error en ventas TDC: {}", e)

        # 2. Ventas Efectivo (requiere tesoreria)
        if lote.tesoreria:
            try:
                logger.info("--- Procesando VENTAS EFECTIVO ---")
                resultados = procesar_ventas_efectivo(
                    ruta_estado_cuenta=ruta_ec,
                    ruta_tesoreria=lote.tesoreria,
                    dry_run=dry_run,
                )
                if any(r.exito for r in resultados):
                    algun_exito = True
            except Exception as e:
                logger.error("Error en ventas efectivo: {}", e)

        # 3. Comisiones
        try:
            logger.info("--- Procesando COMISIONES ---")
            resultados = procesar_comisiones(
                ruta_estado_cuenta=ruta_ec,
                dry_run=dry_run,
            )
            if any(r.exito for r in resultados):
                algun_exito = True
        except Exception as e:
            logger.error("Error en comisiones: {}", e)

        # 4. Traspasos
        try:
            logger.info("--- Procesando TRASPASOS ---")
            resultados = procesar_traspasos(
                ruta_estado_cuenta=ruta_ec,
                dry_run=dry_run,
            )
            if any(r.exito for r in resultados):
                algun_exito = True
        except Exception as e:
            logger.error("Error en traspasos: {}", e)

        # 5. Conciliaciones
        try:
            logger.info("--- Procesando CONCILIACIONES ---")
            resultados = procesar_conciliaciones(
                ruta_estado_cuenta=ruta_ec,
                dry_run=dry_run,
            )
            if any(r.exito for r in resultados):
                algun_exito = True
        except Exception as e:
            logger.error("Error en conciliaciones: {}", e)

        # 6. Nomina (requiere archivo nomina)
        if lote.nomina:
            try:
                logger.info("--- Procesando NOMINA ---")
                resultados = procesar_nomina(
                    ruta_estado_cuenta=ruta_ec,
                    ruta_nomina=lote.nomina,
                    dry_run=dry_run,
                )
                if any(r.exito for r in resultados):
                    algun_exito = True
            except Exception as e:
                logger.error("Error en nomina: {}", e)

        return algun_exito
