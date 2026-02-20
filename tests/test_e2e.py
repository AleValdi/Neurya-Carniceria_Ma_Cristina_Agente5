"""Tests end-to-end contra la BD sandbox (DBSAV71A).

Ejecutan el flujo completo: parsear Excel -> generar plan -> ejecutar
contra BD -> verificar registros -> limpiar.

Requiere:
- Tailscale activo (para 100.73.181.41)
- .env con credenciales (DB_USERNAME, DB_PASSWORD)
- Archivos Excel en data/reportes/
- pyodbc instalado

Uso:
    pytest tests/test_e2e.py -v
    pytest tests/test_e2e.py -v -k traspasos
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import pytest

# Intentar importar pyodbc; si no esta, saltar todos los tests
pyodbc = pytest.importorskip('pyodbc', reason='pyodbc no instalado')

from config.database import DatabaseConfig, DatabaseConnection
from config.settings import Settings, CUENTAS_BANCARIAS
from src.erp.sav7_connector import SAV7Connector
from src.models import PlanEjecucion, ResultadoProceso


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / 'data' / 'reportes'
CONTEXTO_DIR = ROOT / 'contexto' / 'listaRaya'


def _crear_connector() -> SAV7Connector:
    """Crea un connector con settings de .env."""
    settings = Settings.from_env()
    return SAV7Connector(settings)


def _db_disponible() -> bool:
    """Verifica si la BD esta accesible."""
    try:
        conn = _crear_connector()
        ok = conn.test_conexion()
        conn.desconectar()
        return ok
    except Exception:
        return False


# Skip si no hay BD disponible
pytestmark = pytest.mark.skipif(
    not _db_disponible(),
    reason='BD DBSAV71A no disponible (Tailscale apagado o sin credenciales)',
)


@pytest.fixture(scope='module')
def connector():
    """Connector compartido para todo el modulo de tests."""
    conn = _crear_connector()
    yield conn
    conn.desconectar()


@pytest.fixture
def ruta_ec():
    """Ruta al estado de cuenta."""
    ruta = DATA_DIR / 'PRUEBA.xlsx'
    if not ruta.exists():
        pytest.skip(f'Archivo no disponible: {ruta}')
    return ruta


@pytest.fixture
def ruta_tesoreria():
    """Ruta al reporte de tesoreria."""
    ruta = DATA_DIR / 'FEBRERO INGRESOS 2026.xlsx'
    if not ruta.exists():
        pytest.skip(f'Archivo no disponible: {ruta}')
    return ruta


@pytest.fixture
def ruta_nomina():
    """Ruta al archivo de nomina."""
    ruta = CONTEXTO_DIR / 'NOMINA 03 CHEQUE.xlsx'
    if not ruta.exists():
        pytest.skip(f'Archivo no disponible: {ruta}')
    return ruta


# ---------------------------------------------------------------------------
# Cleanup: borrar registros de prueba
# ---------------------------------------------------------------------------

def limpiar_por_folio(cursor, folio: int):
    """Borra movimiento, facturas y poliza vinculados a un folio."""
    # 1. Obtener NumPoliza antes de borrar
    cursor.execute(
        "SELECT NumPoliza FROM SAVCheqPM WHERE Folio = ?", (folio,)
    )
    row = cursor.fetchone()
    num_poliza = row[0] if row else None

    # 2. Borrar facturas vinculadas
    cursor.execute("DELETE FROM SAVCheqPMF WHERE Folio = ?", (folio,))

    # 3. Borrar poliza (por DocFolio = folio)
    cursor.execute(
        "DELETE FROM SAVPoliza WHERE Fuente = 'SAV7-CHEQUES' AND DocFolio = ?",
        (folio,),
    )

    # 4. Borrar movimiento
    cursor.execute("DELETE FROM SAVCheqPM WHERE Folio = ?", (folio,))


def limpiar_compra_por_factura(cursor, factura: str, serie: str = 'F'):
    """Borra factura de compra (SAVRecC + SAVRecD) por referencia."""
    cursor.execute(
        "SELECT NumRec FROM SAVRecC WHERE Serie = ? AND Factura = ?",
        (serie, factura),
    )
    row = cursor.fetchone()
    if row:
        num_rec = row[0]
        cursor.execute(
            "DELETE FROM SAVRecD WHERE Serie = ? AND NumRec = ?",
            (serie, num_rec),
        )
        cursor.execute(
            "DELETE FROM SAVRecC WHERE Serie = ? AND NumRec = ?",
            (serie, num_rec),
        )


def limpiar_resultado(cursor, resultado: ResultadoProceso):
    """Limpia todos los registros creados por un resultado."""
    for folio in resultado.folios:
        limpiar_por_folio(cursor, folio)
    # Limpiar compras si el plan las tiene
    if resultado.plan and resultado.plan.compras:
        for compra in resultado.plan.compras:
            limpiar_compra_por_factura(cursor, compra.factura)


# ---------------------------------------------------------------------------
# Verificacion: queries para validar registros insertados
# ---------------------------------------------------------------------------

def verificar_movimiento(cursor, folio: int) -> dict:
    """Lee un movimiento de SAVCheqPM y retorna como dict."""
    cursor.execute("""
        SELECT Folio, Banco, Cuenta, Age, Mes, Dia, Tipo,
               Ingreso, Egreso, Concepto, Clase, FPago, TipoEgreso,
               Conciliada, Paridad, ParidadDOF, Moneda,
               Cia, Fuente, Oficina, CuentaOficina,
               TipoPoliza, NumPoliza, Capturo, Sucursal
        FROM SAVCheqPM
        WHERE Folio = ?
    """, (folio,))
    row = cursor.fetchone()
    if not row:
        return {}
    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))


def verificar_facturas_pmf(cursor, folio: int) -> List[dict]:
    """Lee facturas vinculadas a un folio."""
    cursor.execute("""
        SELECT Folio, Serie, NumFactura, Ingreso,
               TipoFactura, MontoFactura, SaldoFactura
        FROM SAVCheqPMF
        WHERE Folio = ?
        ORDER BY NumFactura
    """, (folio,))
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def verificar_poliza(cursor, folio: int) -> List[dict]:
    """Lee lineas de poliza por DocFolio (= folio del movimiento)."""
    cursor.execute("""
        SELECT Poliza, Movimiento, Cuenta, SubCuenta,
               TipoCA, Cargo, Abono, Concepto,
               DocTipo, TipoPoliza, DocFolio
        FROM SAVPoliza
        WHERE Fuente = 'SAV7-CHEQUES' AND DocFolio = ?
        ORDER BY Poliza, Movimiento
    """, (folio,))
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def verificar_balance_poliza(cursor, folio: int) -> bool:
    """Verifica que la poliza esta balanceada (cargos = abonos)."""
    lineas = verificar_poliza(cursor, folio)
    if not lineas:
        return False
    total_cargo = sum(Decimal(str(l['Cargo'])) for l in lineas)
    total_abono = sum(Decimal(str(l['Abono'])) for l in lineas)
    return abs(total_cargo - total_abono) < Decimal('0.01')


def comparar_con_produccion(cursor, folio_sandbox: int, tipo: str) -> dict:
    """Busca un movimiento similar en produccion para comparar estructura.

    NO compara montos (son datos distintos), solo formato de campos.
    """
    # Buscar un ejemplo de produccion del mismo tipo
    tipo_bd = {
        'VENTA_TDC': 4, 'VENTA_EFECTIVO': 4,
        'COMISIONES': 3, 'TRASPASOS': 2,
    }.get(tipo)
    if tipo_bd is None:
        return {}

    # Leer el registro de sandbox
    sandbox = verificar_movimiento(cursor, folio_sandbox)
    if not sandbox:
        return {'error': 'Folio no encontrado en sandbox'}

    # Buscar ejemplo en produccion con misma cuenta, tipo Y clase
    clase_sandbox = sandbox.get('Clase', '')
    cursor.execute("""
        SELECT TOP 1
            Folio, Banco, Cuenta, Tipo, Clase, FPago, TipoEgreso,
            Moneda, Cia, Fuente, Oficina, CuentaOficina,
            TipoPoliza, Sucursal
        FROM DBSAV71.dbo.SAVCheqPM
        WHERE Cuenta = ? AND Tipo = ? AND Clase = ?
          AND Age = 2026 AND Mes = 2
        ORDER BY Folio DESC
    """, (sandbox['Cuenta'], tipo_bd, clase_sandbox))

    row = cursor.fetchone()
    if not row:
        return {'error': 'Sin ejemplo en produccion'}

    cols = [desc[0] for desc in cursor.description]
    prod = dict(zip(cols, row))

    # Comparar campos de formato (no montos ni fechas)
    diferencias = {}
    campos_formato = [
        'Banco', 'Moneda', 'Cia', 'Fuente', 'Oficina',
        'CuentaOficina', 'TipoPoliza', 'Sucursal',
        'Clase', 'TipoEgreso',
        # FPago se excluye: varia legitimamente entre registros (Debito vs Credito)
    ]
    for campo in campos_formato:
        val_sandbox = sandbox.get(campo)
        val_prod = prod.get(campo)
        if val_sandbox != val_prod:
            diferencias[campo] = {
                'sandbox': val_sandbox,
                'produccion': val_prod,
            }

    return {
        'folio_sandbox': folio_sandbox,
        'folio_produccion': prod['Folio'],
        'campos_comparados': len(campos_formato),
        'diferencias': diferencias,
        'match_perfecto': len(diferencias) == 0,
    }


def buscar_folio_produccion(
    cursor,
    clase: str,
    egreso: Decimal,
    age: int = 2026,
    mes: int = 2,
    dia: Optional[int] = None,
) -> Optional[int]:
    """Busca folio en produccion por Clase (trimmed) y monto Egreso exacto."""
    params = [clase, float(egreso), age, mes]
    query = """
        SELECT TOP 1 Folio
        FROM DBSAV71.dbo.SAVCheqPM
        WHERE RTRIM(Clase) = ? AND Egreso = ? AND Age = ? AND Mes = ?
    """
    if dia is not None:
        query += " AND Dia = ?"
        params.append(dia)
    query += " ORDER BY Folio DESC"
    cursor.execute(query, params)
    row = cursor.fetchone()
    return row[0] if row else None


def comparar_movimiento_produccion(
    cursor, folio_sandbox: int, folio_produccion: int,
) -> dict:
    """Compara movimiento sandbox vs produccion (formato + montos).

    Similar a comparar_con_produccion pero compara montos y acepta
    folio de produccion directo (en lugar de buscar por tipo).
    """
    sandbox = verificar_movimiento(cursor, folio_sandbox)
    if not sandbox:
        return {'error': f'Folio sandbox {folio_sandbox} no encontrado'}

    cursor.execute("""
        SELECT Folio, Banco, Cuenta, Age, Mes, Dia, Tipo,
               Ingreso, Egreso, Concepto, Clase, FPago, TipoEgreso,
               Conciliada, Paridad, ParidadDOF, Moneda,
               Cia, Fuente, Oficina, CuentaOficina,
               TipoPoliza, NumPoliza, Sucursal
        FROM DBSAV71.dbo.SAVCheqPM
        WHERE Folio = ?
    """, (folio_produccion,))
    row = cursor.fetchone()
    if not row:
        return {'error': f'Folio produccion {folio_produccion} no encontrado'}
    cols = [desc[0] for desc in cursor.description]
    prod = dict(zip(cols, row))

    diferencias = {}

    # Campos de formato (sin Capturo — produccion usa otro usuario)
    campos = [
        'Banco', 'Cuenta', 'Tipo', 'Moneda', 'Cia', 'Fuente',
        'Oficina', 'CuentaOficina', 'TipoPoliza', 'Sucursal',
        'TipoEgreso',
    ]
    for campo in campos:
        if sandbox.get(campo) != prod.get(campo):
            diferencias[campo] = {
                'sandbox': sandbox[campo], 'produccion': prod[campo],
            }

    # Clase (strip trailing spaces de ambos)
    clase_sb = (sandbox.get('Clase') or '').strip()
    clase_pr = (prod.get('Clase') or '').strip()
    if clase_sb != clase_pr:
        diferencias['Clase'] = {'sandbox': clase_sb, 'produccion': clase_pr}

    # Monto Egreso
    egreso_sb = Decimal(str(sandbox['Egreso']))
    egreso_pr = Decimal(str(prod['Egreso']))
    if abs(egreso_sb - egreso_pr) > Decimal('0.01'):
        diferencias['Egreso'] = {
            'sandbox': str(egreso_sb), 'produccion': str(egreso_pr),
        }

    return {
        'folio_sandbox': folio_sandbox,
        'folio_produccion': folio_produccion,
        'diferencias': diferencias,
        'match_perfecto': len(diferencias) == 0,
    }


def comparar_poliza_produccion(
    cursor,
    folio_sandbox: int,
    folio_produccion: int,
    comparar_montos: bool = True,
) -> dict:
    """Compara poliza linea por linea entre sandbox y produccion.

    Verifica: numero de lineas, Cuenta/SubCuenta, TipoCA.
    Si comparar_montos=True, tambien verifica Cargo y Abono.
    """
    lineas_sb = verificar_poliza(cursor, folio_sandbox)

    cursor.execute("""
        SELECT Poliza, Movimiento, Cuenta, SubCuenta,
               TipoCA, Cargo, Abono, Concepto,
               DocTipo, TipoPoliza, DocFolio
        FROM DBSAV71.dbo.SAVPoliza
        WHERE Fuente = 'SAV7-CHEQUES' AND DocFolio = ?
        ORDER BY Poliza, Movimiento
    """, (folio_produccion,))
    cols = [desc[0] for desc in cursor.description]
    lineas_pr = [dict(zip(cols, row)) for row in cursor.fetchall()]

    resultado = {
        'lineas_sandbox': len(lineas_sb),
        'lineas_produccion': len(lineas_pr),
        'diferencias': [],
    }

    if len(lineas_sb) != len(lineas_pr):
        resultado['diferencias'].append(
            f"Diferente numero de lineas: sandbox={len(lineas_sb)}, "
            f"produccion={len(lineas_pr)}"
        )
        resultado['match_perfecto'] = False
        return resultado

    for i, (ls, lp) in enumerate(zip(lineas_sb, lineas_pr)):
        diffs = {}
        if ls['Cuenta'] != lp['Cuenta']:
            diffs['Cuenta'] = {'sb': ls['Cuenta'], 'pr': lp['Cuenta']}
        if ls['SubCuenta'] != lp['SubCuenta']:
            diffs['SubCuenta'] = {'sb': ls['SubCuenta'], 'pr': lp['SubCuenta']}
        if ls['TipoCA'] != lp['TipoCA']:
            diffs['TipoCA'] = {'sb': ls['TipoCA'], 'pr': lp['TipoCA']}

        if comparar_montos:
            cargo_sb = Decimal(str(ls['Cargo']))
            cargo_pr = Decimal(str(lp['Cargo']))
            abono_sb = Decimal(str(ls['Abono']))
            abono_pr = Decimal(str(lp['Abono']))
            if abs(cargo_sb - cargo_pr) > Decimal('0.01'):
                diffs['Cargo'] = {'sb': str(cargo_sb), 'pr': str(cargo_pr)}
            if abs(abono_sb - abono_pr) > Decimal('0.01'):
                diffs['Abono'] = {'sb': str(abono_sb), 'pr': str(abono_pr)}

        if diffs:
            resultado['diferencias'].append(f"Linea {i+1}: {diffs}")

    resultado['match_perfecto'] = len(resultado['diferencias']) == 0
    return resultado


# ---------------------------------------------------------------------------
# Tests E2E
# ---------------------------------------------------------------------------

class TestE2ETraspasos:
    """E2E: Traspasos entre cuentas (proceso mas simple, ideal para probar primero)."""

    def test_traspasos_un_dia(self, connector, ruta_ec):
        """Ejecuta traspasos de 1 dia contra sandbox y verifica."""
        from src.orquestador import procesar_traspasos

        # Elegir un dia especifico con traspasos (dia 3 feb tiene traspasos)
        fecha = date(2026, 2, 3)

        resultados = procesar_traspasos(
            ruta_estado_cuenta=ruta_ec,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            # Debe haber al menos 1 resultado exitoso
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            for resultado in exitosos:
                assert len(resultado.folios) > 0, "Sin folios creados"

                for folio in resultado.folios:
                    # Verificar movimiento insertado
                    mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                    assert mov, f"Folio {folio} no encontrado en BD"
                    assert mov['Cia'] == 'DCM'
                    assert mov['Fuente'] == 'SAV7-CHEQUES'
                    assert mov['Moneda'] == 'PESOS'
                    assert mov['Capturo'] == 'AGENTE5'
                    assert mov['TipoPoliza'] == 'DIARIO'

                    # Egreso (Tipo 2) tiene poliza; Ingreso (Tipo 1) no
                    if mov['Tipo'] == 2:
                        assert mov['NumPoliza'] > 0, "NumPoliza no asignada en egreso"

                        # Verificar poliza balanceada
                        assert verificar_balance_poliza(
                            connector.db.conectar().cursor(), folio
                        ), f"Poliza desbalanceada para folio {folio}"

                        # Verificar poliza tiene DocTipo=TRASPASOS
                        lineas = verificar_poliza(connector.db.conectar().cursor(), folio)
                        assert len(lineas) == 2, f"Traspaso debe tener 2 lineas, tiene {len(lineas)}"
                        for linea in lineas:
                            assert linea['DocTipo'] == 'TRASPASOS'
                    else:
                        # Ingreso de traspaso: sin poliza propia
                        assert mov['NumPoliza'] == 0

                    # Comparar formato con produccion
                    comp = comparar_con_produccion(
                        connector.db.conectar().cursor(), folio, 'TRASPASOS'
                    )
                    if comp.get('diferencias'):
                        pytest.fail(
                            f"Diferencias con produccion: {comp['diferencias']}"
                        )

        finally:
            # CLEANUP: borrar todo lo insertado
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EComisiones:
    """E2E: Comisiones bancarias."""

    def test_comisiones_un_dia(self, connector, ruta_ec):
        """Ejecuta comisiones de 1 dia contra sandbox y verifica."""
        from src.orquestador import procesar_comisiones

        fecha = date(2026, 2, 3)

        resultados = procesar_comisiones(
            ruta_estado_cuenta=ruta_ec,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            for resultado in exitosos:
                for folio in resultado.folios:
                    mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                    assert mov, f"Folio {folio} no encontrado"
                    assert mov['Tipo'] == 3, "Comisiones deben ser Tipo 3"
                    assert mov['Cia'] == 'DCM'
                    assert mov['Fuente'] == 'SAV7-CHEQUES'
                    assert mov['TipoPoliza'] == 'EGRESO'
                    assert mov['NumPoliza'] > 0
                    assert 'COMISIONES' in mov['Concepto'].upper()

                    # Poliza balanceada con 4 lineas
                    assert verificar_balance_poliza(
                        connector.db.conectar().cursor(), folio
                    )
                    lineas = verificar_poliza(connector.db.conectar().cursor(), folio)
                    assert len(lineas) == 4, f"Comision debe tener 4 lineas, tiene {len(lineas)}"

                    # Verificar facturas PMF (comisiones no tienen)
                    facts = verificar_facturas_pmf(connector.db.conectar().cursor(), folio)
                    assert len(facts) == 0, "Comisiones no deben tener SAVCheqPMF"

                    # Comparar formato con produccion
                    comp = comparar_con_produccion(
                        connector.db.conectar().cursor(), folio, 'COMISIONES'
                    )
                    if comp.get('diferencias'):
                        pytest.fail(
                            f"Diferencias con produccion: {comp['diferencias']}"
                        )

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EVentaTDC:
    """E2E: Ventas con tarjeta de credito/debito."""

    def test_venta_tdc_un_dia(self, connector, ruta_ec, ruta_tesoreria):
        """Ejecuta venta TDC de 1 dia contra sandbox y verifica."""
        from src.orquestador import procesar_ventas_tdc

        # Dia 3 feb (depositos del lunes, ventas del sabado)
        fecha = date(2026, 2, 3)

        resultados = procesar_ventas_tdc(
            ruta_estado_cuenta=ruta_ec,
            ruta_tesoreria=ruta_tesoreria,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            for resultado in exitosos:
                for folio in resultado.folios:
                    mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                    assert mov, f"Folio {folio} no encontrado"
                    assert mov['Tipo'] == 4, "Ventas TDC deben ser Tipo 4"
                    assert mov['Clase'] == 'VENTA DIARIA'
                    assert mov['Cia'] == 'DCM'
                    assert mov['NumPoliza'] > 0
                    assert mov['FPago'] in ('Tarjeta Débito', 'Tarjeta Crédito')

                    # Poliza balanceada con 6 lineas
                    assert verificar_balance_poliza(
                        connector.db.conectar().cursor(), folio
                    )
                    lineas = verificar_poliza(connector.db.conectar().cursor(), folio)
                    assert len(lineas) == 6, f"Venta TDC debe tener 6 lineas, tiene {len(lineas)}"

                    # Debe tener exactamente 1 factura GLOBAL
                    facts = verificar_facturas_pmf(connector.db.conectar().cursor(), folio)
                    assert len(facts) == 1, f"TDC debe tener 1 factura, tiene {len(facts)}"
                    assert facts[0]['TipoFactura'] == 'GLOBAL'

                    # Comparar con produccion
                    comp = comparar_con_produccion(
                        connector.db.conectar().cursor(), folio, 'VENTA_TDC'
                    )
                    if comp.get('diferencias'):
                        pytest.fail(
                            f"Diferencias con produccion: {comp['diferencias']}"
                        )

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EVentaEfectivo:
    """E2E: Ventas en efectivo."""

    def test_venta_efectivo_un_dia(self, connector, ruta_ec, ruta_tesoreria):
        """Ejecuta venta efectivo de 1 dia contra sandbox y verifica."""
        from src.orquestador import procesar_ventas_efectivo

        fecha = date(2026, 2, 3)

        resultados = procesar_ventas_efectivo(
            ruta_estado_cuenta=ruta_ec,
            ruta_tesoreria=ruta_tesoreria,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            for resultado in exitosos:
                for folio in resultado.folios:
                    mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                    assert mov, f"Folio {folio} no encontrado"
                    assert mov['Tipo'] == 4
                    assert mov['Clase'] == 'VENTA DIARIA'
                    assert mov['FPago'] == 'Efectivo'
                    assert mov['NumPoliza'] > 0

                    # Poliza balanceada
                    assert verificar_balance_poliza(
                        connector.db.conectar().cursor(), folio
                    )

                    # Debe tener facturas (INDIVIDUAL + GLOBAL)
                    facts = verificar_facturas_pmf(connector.db.conectar().cursor(), folio)
                    assert len(facts) > 0, "Efectivo debe tener facturas"
                    tipos = {f['TipoFactura'] for f in facts}
                    assert 'GLOBAL' in tipos, "Debe incluir factura GLOBAL"

                    # Comparar con produccion
                    comp = comparar_con_produccion(
                        connector.db.conectar().cursor(), folio, 'VENTA_EFECTIVO'
                    )
                    if comp.get('diferencias'):
                        pytest.fail(
                            f"Diferencias con produccion: {comp['diferencias']}"
                        )

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EIdempotencia:
    """E2E: Verificar que ejecutar dos veces no duplica registros."""

    def test_doble_ejecucion_no_duplica(self, connector, ruta_ec):
        """Ejecutar traspasos dos veces: la segunda debe saltar todo."""
        from src.orquestador import procesar_traspasos

        fecha = date(2026, 2, 3)

        # Primera ejecucion: debe insertar
        resultados_1 = procesar_traspasos(
            ruta_estado_cuenta=ruta_ec,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos_1 = [r for r in resultados_1 if r.exito]
            assert len(exitosos_1) > 0
            folios_1 = []
            for r in exitosos_1:
                folios_1.extend(r.folios)
            assert len(folios_1) > 0, "Primera ejecucion debe crear folios"

            # Segunda ejecucion: debe saltar por idempotencia
            resultados_2 = procesar_traspasos(
                ruta_estado_cuenta=ruta_ec,
                dry_run=False,
                solo_fecha=fecha,
                connector=connector,
            )

            exitosos_2 = [r for r in resultados_2 if r.exito]
            folios_2 = []
            for r in exitosos_2:
                folios_2.extend(r.folios)

            # No debe haber creado folios nuevos
            assert len(folios_2) == 0, (
                f"Segunda ejecucion creo {len(folios_2)} folios (deberia 0)"
            )

            # Debe tener advertencia de duplicados
            tiene_advertencia = False
            for r in exitosos_2:
                if r.plan and any('ya existian' in a for a in r.plan.advertencias):
                    tiene_advertencia = True
            assert tiene_advertencia, "Debe advertir sobre movimientos saltados"

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados_1:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EComparacionProduccion:
    """Comparaciones de formato entre sandbox y produccion."""

    def test_formato_poliza_traspaso(self, connector, ruta_ec):
        """Verifica que la poliza de traspaso tenga misma estructura que produccion."""
        from src.orquestador import procesar_traspasos

        fecha = date(2026, 2, 3)

        resultados = procesar_traspasos(
            ruta_estado_cuenta=ruta_ec,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito and r.folios]
            if not exitosos:
                pytest.skip("Sin resultados exitosos")

            folio = exitosos[0].folios[0]
            cursor = connector.db.conectar().cursor()

            # Leer poliza sandbox
            lineas_sandbox = verificar_poliza(cursor, folio)

            # Buscar poliza de traspaso en produccion
            cursor.execute("""
                SELECT TOP 1 p.Poliza
                FROM DBSAV71.dbo.SAVPoliza p
                WHERE p.Fuente = 'SAV7-CHEQUES'
                  AND p.DocTipo = 'TRASPASOS'
                  AND p.Movimiento = 1
                ORDER BY p.Poliza DESC
            """)
            row = cursor.fetchone()
            if not row:
                pytest.skip("Sin poliza de traspaso en produccion")

            poliza_prod = row[0]
            cursor.execute("""
                SELECT Movimiento, Cuenta, SubCuenta, TipoCA,
                       DocTipo, TipoPoliza, Capturo
                FROM DBSAV71.dbo.SAVPoliza
                WHERE Fuente = 'SAV7-CHEQUES' AND Poliza = ?
                ORDER BY Movimiento
            """, (poliza_prod,))
            cols = [desc[0] for desc in cursor.description]
            lineas_prod = [dict(zip(cols, row)) for row in cursor.fetchall()]

            # Comparar estructura (no montos)
            assert len(lineas_sandbox) == len(lineas_prod), (
                f"Sandbox tiene {len(lineas_sandbox)} lineas, "
                f"produccion tiene {len(lineas_prod)}"
            )

            for i, (ls, lp) in enumerate(zip(lineas_sandbox, lineas_prod)):
                assert ls['DocTipo'] == lp['DocTipo'], (
                    f"Linea {i+1}: DocTipo sandbox={ls['DocTipo']} "
                    f"vs prod={lp['DocTipo']}"
                )
                assert ls['TipoPoliza'] == lp['TipoPoliza'], (
                    f"Linea {i+1}: TipoPoliza sandbox={ls['TipoPoliza']} "
                    f"vs prod={lp['TipoPoliza']}"
                )
                assert ls['TipoCA'] == lp['TipoCA'], (
                    f"Linea {i+1}: TipoCA sandbox={ls['TipoCA']} "
                    f"vs prod={lp['TipoCA']}"
                )

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2ENomina:
    """E2E: Nomina (semana 03, feb 2026)."""

    def test_nomina_un_dia(self, connector, ruta_ec, ruta_nomina):
        """Ejecuta nomina de 1 dia contra sandbox y verifica."""
        from src.orquestador import procesar_nomina

        # Dia 3 feb tiene NOMINA - PAGO DE NOMINA ($114,649.60)
        fecha = date(2026, 2, 3)

        resultados = procesar_nomina(
            ruta_estado_cuenta=ruta_ec,
            ruta_nomina=ruta_nomina,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            for resultado in exitosos:
                assert len(resultado.folios) > 0, "Sin folios creados"

                for folio in resultado.folios:
                    mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                    assert mov, f"Folio {folio} no encontrado en BD"
                    assert mov['Tipo'] == 2, "Nomina debe ser Tipo 2"
                    assert mov['Cia'] == 'DCM'
                    assert mov['Fuente'] == 'SAV7-CHEQUES'
                    assert mov['Moneda'] == 'PESOS'
                    assert mov['Capturo'] == 'AGENTE5'
                    assert mov['Clase'] in ('NOMINA', 'FINIQUITO')

                    assert mov['NumPoliza'] > 0, "Nomina debe tener poliza"

                    # Poliza balanceada
                    assert verificar_balance_poliza(
                        connector.db.conectar().cursor(), folio
                    ), f"Poliza desbalanceada para folio {folio}"

                    lineas = verificar_poliza(connector.db.conectar().cursor(), folio)

                    # Movimiento principal (dispersion) = mayor monto, poliza larga
                    if 'DISPERSION' in mov['Concepto'].upper():
                        assert len(lineas) >= 5, (
                            f"Poliza dispersion debe tener >=5 lineas, tiene {len(lineas)}"
                        )
                    else:
                        # Secundarios (cheques, vacaciones, finiquito): 2 lineas
                        assert len(lineas) == 2, (
                            f"Poliza secundaria debe tener 2 lineas, tiene {len(lineas)}"
                        )

            # --- Comparar formato con produccion (sin montos ni poliza) ---
            cursor_lectura = connector.db.conectar().cursor()
            for resultado in exitosos:
                for folio in resultado.folios:
                    mov = verificar_movimiento(cursor_lectura, folio)
                    clase = (mov['Clase'] or '').strip()
                    tipo_egreso = (mov['TipoEgreso'] or '').strip()

                    # Buscar registro de produccion con misma clase y tipo egreso
                    cursor_lectura.execute("""
                        SELECT TOP 1 Folio
                        FROM DBSAV71.dbo.SAVCheqPM
                        WHERE RTRIM(Clase) = ? AND TipoEgreso = ?
                          AND Tipo = 2 AND Age = 2026 AND Mes = 2
                        ORDER BY Folio DESC
                    """, (clase, tipo_egreso))
                    row = cursor_lectura.fetchone()
                    if not row:
                        continue  # Skip si no hay match en produccion

                    folio_prod = row[0]
                    comp = comparar_movimiento_produccion(
                        cursor_lectura, folio, folio_prod
                    )
                    # Solo verificar formato, ignorar Egreso (montos distintos)
                    diffs = comp.get('diferencias', {})
                    diffs_formato = {
                        k: v for k, v in diffs.items() if k != 'Egreso'
                    }
                    assert not diffs_formato, (
                        f"Diferencias formato nomina (folio {folio}) vs "
                        f"produccion (folio {folio_prod}): {diffs_formato}"
                    )

                    # Verificar que cuentas de poliza existen en produccion
                    lineas_sb = verificar_poliza(cursor_lectura, folio)
                    if lineas_sb and tipo_egreso == 'TRANSFERENCIA':
                        cuentas_sb = {
                            (l['Cuenta'], l['SubCuenta']) for l in lineas_sb
                        }
                        cursor_lectura.execute("""
                            SELECT DISTINCT p.Cuenta, p.SubCuenta
                            FROM DBSAV71.dbo.SAVPoliza p
                            JOIN DBSAV71.dbo.SAVCheqPM m
                              ON p.DocFolio = m.Folio
                            WHERE p.Fuente = 'SAV7-CHEQUES'
                              AND RTRIM(m.Clase) = 'NOMINA'
                              AND m.TipoEgreso = 'TRANSFERENCIA'
                              AND m.Age = 2026 AND m.Mes = 2
                        """)
                        cuentas_prod = {
                            (r[0], r[1]) for r in cursor_lectura.fetchall()
                        }
                        cuentas_nuevas = cuentas_sb - cuentas_prod
                        assert not cuentas_nuevas, (
                            f"Cuentas de poliza no encontradas en nomina "
                            f"de produccion: {cuentas_nuevas}"
                        )

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EIMSS:
    """E2E: IMSS solo (enero 2026, pagado feb 9, $93,880.17)."""

    @pytest.fixture
    def ruta_imss_pdf(self):
        """Ruta al PDF Resumen de Liquidacion SUA (solo IMSS)."""
        ruta = ROOT / 'contexto' / 'ConciliacionImssInfonavit' / 'resumen liquidacion_gbl1.pdf'
        if not ruta.exists():
            pytest.skip(f'PDF no disponible: {ruta}')
        return ruta

    def test_imss_un_dia(self, connector, ruta_ec, ruta_imss_pdf):
        """Ejecuta IMSS de feb 9 contra sandbox y verifica."""
        from src.orquestador import procesar_impuestos

        fecha = date(2026, 2, 9)

        resultados = procesar_impuestos(
            ruta_estado_cuenta=ruta_ec,
            ruta_imss=ruta_imss_pdf,
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            folios_todos = []
            for resultado in exitosos:
                folios_todos.extend(resultado.folios)

            # Debe haber exactamente 1 folio (1 movimiento IMSS)
            assert len(folios_todos) == 1, (
                f"IMSS debe generar 1 folio, tiene {len(folios_todos)}"
            )

            folio = folios_todos[0]
            cursor_lectura = connector.db.conectar().cursor()
            mov = verificar_movimiento(cursor_lectura, folio)
            assert mov, f"Folio {folio} no encontrado en BD"

            # Verificar campos del movimiento
            assert mov['Tipo'] == 2, "IMSS debe ser Tipo 2"
            assert mov['Clase'] == 'PAGO IMSS'
            assert mov['TipoEgreso'] == 'TRANSFERENCIA'
            assert mov['TipoPoliza'] == 'EGRESO'
            assert mov['Cia'] == 'DCM'
            assert mov['Fuente'] == 'SAV7-CHEQUES'
            assert mov['Capturo'] == 'AGENTE5'
            assert mov['NumPoliza'] > 0
            assert Decimal(str(mov['Egreso'])) == Decimal('93880.17')

            # Verificar concepto
            assert 'PAGO SUA' in mov['Concepto'].upper()
            assert 'ENERO 2026' in mov['Concepto'].upper()

            # Poliza balanceada con 3 lineas (solo IMSS)
            assert verificar_balance_poliza(cursor_lectura, folio), (
                f"Poliza desbalanceada para folio {folio}"
            )
            lineas = verificar_poliza(cursor_lectura, folio)
            assert len(lineas) == 3, (
                f"IMSS solo debe tener 3 lineas de poliza, tiene {len(lineas)}"
            )

            # Verificar estructura de poliza:
            # Linea 1: Cargo 2140/010000 (Retencion IMSS)
            assert lineas[0]['Cuenta'] == '2140'
            assert lineas[0]['SubCuenta'] == '010000'
            assert lineas[0]['TipoCA'] == 1  # CARGO

            # Linea 2: Cargo 6200/070000 (IMSS Gasto)
            assert lineas[1]['Cuenta'] == '6200'
            assert lineas[1]['SubCuenta'] == '070000'
            assert lineas[1]['TipoCA'] == 1  # CARGO

            # Linea 3: Abono 1120/040000 (Banco)
            assert lineas[2]['Cuenta'] == '1120'
            assert lineas[2]['SubCuenta'] == '040000'
            assert lineas[2]['TipoCA'] == 2  # ABONO
            assert Decimal(str(lineas[2]['Abono'])) == Decimal('93880.17')

            # Retencion + Gasto = Total
            retencion = Decimal(str(lineas[0]['Cargo']))
            gasto = Decimal(str(lineas[1]['Cargo']))
            assert retencion + gasto == Decimal('93880.17'), (
                f"Ret({retencion}) + Gasto({gasto}) != 93880.17"
            )

            # No debe tener facturas PMF
            facts = verificar_facturas_pmf(cursor_lectura, folio)
            assert len(facts) == 0, "IMSS no debe tener SAVCheqPMF"

            # --- Comparar con produccion (montos + poliza) ---
            folio_prod = buscar_folio_produccion(
                cursor_lectura, 'PAGO IMSS', Decimal('93880.17'), 2026, 2, 9
            )
            if folio_prod:
                comp_mov = comparar_movimiento_produccion(
                    cursor_lectura, folio, folio_prod
                )
                assert comp_mov.get('match_perfecto', False), (
                    f"Diferencias movimiento IMSS vs produccion: "
                    f"{comp_mov.get('diferencias')}"
                )
                comp_pol = comparar_poliza_produccion(
                    cursor_lectura, folio, folio_prod
                )
                assert comp_pol.get('match_perfecto', False), (
                    f"Diferencias poliza IMSS vs produccion: "
                    f"{comp_pol.get('diferencias')}"
                )
            else:
                pytest.skip("Sin registro IMSS en produccion para comparar")

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)


class TestE2EImpuestos:
    """E2E: Impuestos federales y estatal (enero 2026, pagados feb 11)."""

    @pytest.fixture
    def rutas_impuestos(self):
        """Rutas a los PDFs de acuses de impuestos."""
        base = ROOT / 'contexto' / 'impuestos'
        rutas = {
            'acuse_federal_1': base / 'ImpuestoFederal' / 'acusePdf-1011.pdf',
            'acuse_federal_2': base / 'ImpuestoFederal' / 'Acuse.DCM02072238A.38.2026.pdf',
            'detalle_ieps': base / 'ImpuestoFederal' / 'Declaracion.Acuse.0.pdf',
            'declaracion_completa': base / 'ImpuestoFederal' / 'DCM02072238A.38.2026.pdf',
            'estatal': base / 'ImpuestoEstatal' / '3% SN Enero 2026.pdf',
        }
        for nombre, ruta in rutas.items():
            if not ruta.exists():
                pytest.skip(f'PDF no disponible: {ruta}')
        return rutas

    def test_impuestos_completo(self, connector, ruta_ec, rutas_impuestos):
        """Ejecuta impuestos de feb 11 contra sandbox y verifica."""
        from src.orquestador import procesar_impuestos

        fecha = date(2026, 2, 11)

        resultados = procesar_impuestos(
            ruta_estado_cuenta=ruta_ec,
            ruta_acuse_federal_1=rutas_impuestos['acuse_federal_1'],
            ruta_acuse_federal_2=rutas_impuestos['acuse_federal_2'],
            ruta_detalle_ieps=rutas_impuestos['detalle_ieps'],
            ruta_declaracion_completa=rutas_impuestos['declaracion_completa'],
            ruta_impuesto_estatal=rutas_impuestos['estatal'],
            dry_run=False,
            solo_fecha=fecha,
            connector=connector,
        )

        try:
            exitosos = [r for r in resultados if r.exito]
            assert len(exitosos) > 0, (
                f"Ningun resultado exitoso. Errores: "
                f"{[r.error for r in resultados if not r.exito]}"
            )

            folios_todos = []
            for resultado in exitosos:
                folios_todos.extend(resultado.folios)

            # Debe haber al menos 3 folios:
            # Federal 1a ($6,822) + Federal 2a principal + retenciones IVA + Estatal
            assert len(folios_todos) >= 3, (
                f"Impuestos deben generar >=3 folios, tiene {len(folios_todos)}"
            )

            for folio in folios_todos:
                mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                assert mov, f"Folio {folio} no encontrado en BD"
                assert mov['Tipo'] == 2, "Impuestos deben ser Tipo 2"
                assert mov['Cia'] == 'DCM'
                assert mov['Fuente'] == 'SAV7-CHEQUES'
                assert mov['Capturo'] == 'AGENTE5'
                assert mov['TipoPoliza'] == 'EGRESO'
                assert mov['TipoEgreso'] == 'TRANSFERENCIA'
                assert mov['NumPoliza'] > 0

                # Poliza balanceada
                assert verificar_balance_poliza(
                    connector.db.conectar().cursor(), folio
                ), f"Poliza desbalanceada para folio {folio}"

                # Verificar concepto contiene IMPUESTO o NOMINA (3%)
                concepto = mov['Concepto'].upper()
                assert 'IMPUESTO' in concepto or 'NOMINA' in concepto, (
                    f"Concepto inesperado: {mov['Concepto']}"
                )

            # Verificar montos especificos en movimientos
            montos = set()
            for folio in folios_todos:
                mov = verificar_movimiento(connector.db.conectar().cursor(), folio)
                egreso = Decimal(str(mov['Egreso']))
                montos.add(egreso)

            # Deben estar los 3 montos principales
            assert Decimal('6822') in montos, f"Falta federal 1a ($6,822). Montos: {montos}"
            assert Decimal('22971') in montos, f"Falta estatal ($22,971). Montos: {montos}"

            # --- Comparar cada folio con produccion (montos + poliza) ---
            cursor_lectura = connector.db.conectar().cursor()
            folios_sin_match = []
            for folio in folios_todos:
                mov = verificar_movimiento(cursor_lectura, folio)
                egreso = Decimal(str(mov['Egreso']))
                clase = (mov['Clase'] or '').strip()

                folio_prod = buscar_folio_produccion(
                    cursor_lectura, clase, egreso, 2026, 2
                )
                if folio_prod:
                    # Comparar movimiento (formato + montos)
                    comp_mov = comparar_movimiento_produccion(
                        cursor_lectura, folio, folio_prod
                    )
                    assert comp_mov.get('match_perfecto', False), (
                        f"Diferencias movimiento (folio {folio}, ${egreso}) vs "
                        f"produccion (folio {folio_prod}): "
                        f"{comp_mov.get('diferencias')}"
                    )
                    # Comparar poliza linea por linea (cuentas + montos)
                    comp_pol = comparar_poliza_produccion(
                        cursor_lectura, folio, folio_prod
                    )
                    assert comp_pol.get('match_perfecto', False), (
                        f"Diferencias poliza (folio {folio}, ${egreso}) vs "
                        f"produccion (folio {folio_prod}): "
                        f"{comp_pol.get('diferencias')}"
                    )
                else:
                    folios_sin_match.append((folio, egreso, clase))

            if folios_sin_match:
                info = [f"Folio {f}: ${e} ({c})" for f, e, c in folios_sin_match]
                pytest.fail(
                    f"Folios sin match en produccion: {', '.join(info)}"
                )

        finally:
            with connector.get_cursor(transaccion=True) as cursor:
                for resultado in resultados:
                    if resultado.exito:
                        limpiar_resultado(cursor, resultado)
