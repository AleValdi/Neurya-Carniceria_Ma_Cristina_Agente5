"""Tests para el modulo watcher (Fase 6)."""

import shutil
from pathlib import Path

import pytest

from src.watcher import (
    LoteArchivos,
    agrupar_archivos,
    clasificar_archivo,
    detectar_archivos,
    mover_archivo,
)


class TestClasificarArchivo:
    """Tests para clasificacion de archivos por nombre."""

    def test_estado_cuenta_prueba(self, tmp_path):
        ruta = tmp_path / "PRUEBA.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'estado-cuenta'

    def test_estado_cuenta_edo(self, tmp_path):
        ruta = tmp_path / "EDO CTA FEBRERO.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'estado-cuenta'

    def test_estado_cuenta_banregio(self, tmp_path):
        ruta = tmp_path / "BANREGIO FEBRERO 2026.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'estado-cuenta'

    def test_tesoreria_ingresos(self, tmp_path):
        ruta = tmp_path / "FEBRERO INGRESOS 2026.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'tesoreria'

    def test_tesoreria_nombre_directo(self, tmp_path):
        ruta = tmp_path / "TESORERIA MARZO.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'tesoreria'

    def test_nomina(self, tmp_path):
        ruta = tmp_path / "NOMINA 03 CHEQUE.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'nomina'

    def test_desconocido(self, tmp_path):
        ruta = tmp_path / "REPORTE_VARIOS.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'desconocido'

    def test_nomina_tiene_prioridad_sobre_estado(self, tmp_path):
        """NOMINA en nombre no debe confundirse con estado de cuenta."""
        ruta = tmp_path / "NOMINA 03 CHEQUE.xlsx"
        ruta.touch()
        assert clasificar_archivo(ruta) == 'nomina'


class TestAgruparArchivos:
    """Tests para agrupamiento de archivos en lotes."""

    def test_lote_completo(self, tmp_path):
        ec = tmp_path / "PRUEBA.xlsx"
        tes = tmp_path / "FEBRERO INGRESOS 2026.xlsx"
        nom = tmp_path / "NOMINA 03 CHEQUE.xlsx"
        ec.touch()
        tes.touch()
        nom.touch()

        lote = agrupar_archivos([ec, tes, nom])

        assert lote.estado_cuenta == ec
        assert lote.tesoreria == tes
        assert lote.nomina == nom
        assert lote.es_valido

    def test_solo_estado_cuenta(self, tmp_path):
        ec = tmp_path / "PRUEBA.xlsx"
        ec.touch()

        lote = agrupar_archivos([ec])

        assert lote.estado_cuenta == ec
        assert lote.tesoreria is None
        assert lote.nomina is None
        assert lote.es_valido

    def test_sin_estado_cuenta_invalido(self, tmp_path):
        tes = tmp_path / "FEBRERO INGRESOS 2026.xlsx"
        tes.touch()

        lote = agrupar_archivos([tes])

        assert lote.estado_cuenta is None
        assert not lote.es_valido

    def test_archivos_desconocidos_ignorados(self, tmp_path):
        ec = tmp_path / "PRUEBA.xlsx"
        desc = tmp_path / "RANDOM_FILE.xlsx"
        ec.touch()
        desc.touch()

        lote = agrupar_archivos([ec, desc])

        assert lote.estado_cuenta == ec
        assert lote.es_valido

    def test_lista_archivos(self, tmp_path):
        ec = tmp_path / "PRUEBA.xlsx"
        tes = tmp_path / "FEBRERO INGRESOS 2026.xlsx"
        ec.touch()
        tes.touch()

        lote = agrupar_archivos([ec, tes])

        assert len(lote.archivos) == 2
        assert ec in lote.archivos
        assert tes in lote.archivos


class TestMoverArchivo:
    """Tests para mover archivos procesados."""

    def test_mover_a_procesados(self, tmp_path):
        entrada = tmp_path / "entrada"
        procesados = tmp_path / "procesados"
        entrada.mkdir()

        archivo = entrada / "PRUEBA.xlsx"
        archivo.write_text("contenido")

        resultado = mover_archivo(archivo, procesados)

        assert resultado.exists()
        assert not archivo.exists()
        assert procesados.exists()
        assert "PRUEBA.xlsx" in resultado.name

    def test_crea_directorio_destino(self, tmp_path):
        archivo = tmp_path / "PRUEBA.xlsx"
        archivo.write_text("contenido")
        destino = tmp_path / "procesados" / "sub"

        resultado = mover_archivo(archivo, destino)

        assert resultado.exists()
        assert destino.exists()

    def test_nombre_con_timestamp(self, tmp_path):
        archivo = tmp_path / "PRUEBA.xlsx"
        archivo.write_text("contenido")
        destino = tmp_path / "procesados"

        resultado = mover_archivo(archivo, destino)

        # El nombre debe tener formato YYYYMMDD_HHMMSS_PRUEBA.xlsx
        nombre = resultado.name
        assert nombre.endswith("_PRUEBA.xlsx")
        assert len(nombre) > len("PRUEBA.xlsx")

    def test_evita_colision(self, tmp_path):
        destino = tmp_path / "procesados"
        destino.mkdir()

        # Crear archivo y simulacion de colision
        archivo1 = tmp_path / "A.xlsx"
        archivo1.write_text("v1")
        resultado1 = mover_archivo(archivo1, destino)

        archivo2 = tmp_path / "A.xlsx"
        archivo2.write_text("v2")
        resultado2 = mover_archivo(archivo2, destino)

        # Ambos deben existir con nombres distintos
        assert resultado1.exists()
        assert resultado2.exists()
        assert resultado1 != resultado2


class TestDetectarArchivos:
    """Tests para deteccion de archivos en carpeta."""

    def test_detecta_xlsx(self, tmp_path):
        (tmp_path / "A.xlsx").touch()
        (tmp_path / "B.xlsx").touch()
        (tmp_path / "C.txt").touch()  # No debe detectar

        archivos = detectar_archivos(tmp_path)

        assert len(archivos) == 2
        assert all(a.suffix == '.xlsx' for a in archivos)

    def test_carpeta_vacia(self, tmp_path):
        assert detectar_archivos(tmp_path) == []

    def test_carpeta_no_existe(self, tmp_path):
        no_existe = tmp_path / "no_existe"
        assert detectar_archivos(no_existe) == []
