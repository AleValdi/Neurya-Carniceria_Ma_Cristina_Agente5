# Bugs Detectados — Prueba Ejecucion 24 Feb 2026

Prueba realizada por el usuario final el 24 de febrero de 2026.
Archivos de entrada en `Prueba_Ejecucion_Fallida/` (subcarpetas Entrada, no Procesado).

## Archivos de la Prueba

| Tipo | Archivo |
|------|---------|
| Estado de Cuenta | `Prueba_Ejecucion_Fallida/Estado_de_cuenta/prueba febrero.xlsx` |
| Tesoreria | `Prueba_Ejecucion_Fallida/Tesoreria/FEBRERO INGRESOS 2026 (1).xlsx` |
| Nomina | `Prueba_Ejecucion_Fallida/Nomina/NOMINA 08 CHEQUE.xlsx` |
| Lista de Raya | `Prueba_Ejecucion_Fallida/Lista de Raya/lista de raya 8.pdf` |

Reportes generados (4 ejecuciones):
- `REPORTE_completo_20260224_110545.xlsx` (primera ejecucion, periodo completo)
- `REPORTE_2026-02-23_20260224_111122.xlsx` (solo Feb 23)
- `REPORTE_2026-02-23_20260224_111603.xlsx` (solo Feb 23, re-ejecucion)
- `REPORTE_2026-02-23_20260224_114703.xlsx` (solo Feb 23, re-ejecucion)

**Nota:** Feb 23, 2026 es LUNES (relevante para Bug 4).

---

## Verificacion contra BD (DBSAV71A sandbox + DBSAV71 produccion)

### Registros creados por AGENTE5 en sandbox (12 total)

| Folio | Fecha | Cuenta | Tipo | Concepto | Monto |
|-------|-------|--------|------|----------|-------|
| 127940 | Feb 5 | 038900320016 | 3 | COMISIONES BANCARIAS 05/02/2026 | $2,102.43 |
| 127941 | Feb 6 | 038900320016 | 3 | COMISIONES BANCARIAS 06/02/2026 | $1,764.92 |
| 127942 | Feb 16 | 038900320016 | 3 | COMISIONES BANCARIAS 16/02/2026 | $8,223.76 |
| 127943 | Feb 23 | 055003730017 | 2 | TRASPASO A BANCO (-> GTS 157) | $1,000.00 |
| 127944 | Feb 23 | 055003730157 | 1 | TRASPASO DE BANCO (<- F 017) | $1,000.00 |
| 127945 | Feb 23 | 055003730017 | 2 | TRASPASO A BANCO (-> GTS 157) | $7,500.00 |
| 127946 | Feb 23 | 055003730157 | 1 | TRASPASO DE BANCO (<- F 017) | $7,500.00 |
| 127947 | Feb 23 | 055003730017 | 3 | COMISIONES BANCARIAS 23/02/2026 | $41.76 |
| 127948 | Feb 23 | 038900320016 | 3 | COMISIONES BANCARIAS 23/02/2026 | $8,851.12 |
| 127949 | Feb 23 | 055003730017 | 4 | VENTA DIARIA 20/02/2026 | $233,354.00 |
| 127950 | Feb 23 | 055003730017 | 4 | VENTA DIARIA 19/02/2026 | $300,442.50 |
| 127951 | Feb 23 | 055003730017 | 2 | NOMINA 08 DISPERSION | $117,786.20 |

**Hallazgo clave:** Solo 12 registros para todo el mes. Comisiones en 4 fechas,
ventas EFECTIVO en 2, nomina 1, traspasos 2. **CERO registros TDC** para cuenta 16.

### Facturas de compra (SAVRecC) creadas por AGENTE5

| NumRec | Fecha | Factura | Total | Cuenta vinculada (via SAVCheqPMP) |
|--------|-------|---------|-------|-----------------------------------|
| 900023 | Feb 5 | 05022026 | $2,102.43 | 038900320016 (cuenta 16) |
| 900024 | Feb 6 | 06022026 | $1,764.92 | 038900320016 (cuenta 16) |
| 900025 | Feb 16 | 16022026 | $8,223.76 | 038900320016 (cuenta 16) |
| 900026 | Feb 23 | 23022026 | $41.76 | 055003730017 (cuenta 17) |
| 900027 | Feb 23 | 23022026 | $8,851.12 | 038900320016 (cuenta 16) |

**Hallazgo:** Feb 23 SI tiene compra para cuenta 17 (NumRec 900026, $41.76).
Pero Feb 5, 6, 16 solo tienen compra para cuenta 16. Falta cuenta 17 en esos dias.

---

## Bug 1: Factura de comisiones — NumFactura incorrecto -- CORREGIDO

**Reporte usuario:** "No genera la factura de comisiones correctamente. (Folio)"

**Archivo afectado:** `src/procesadores/comisiones.py:132`

**Evidencia BD:**
```
AGENTE5:  NumFactura = "23022026"  (fecha como string)
PROD:     NumFactura = NULL         (vacio)
```
En produccion, el campo NumFactura de SAVCheqPM es NULL para comisiones.
AGENTE5 lo llenaba con la fecha en formato DDMMAAAA, lo cual es incorrecto.

**Fix aplicado (24 Feb 2026):**
- `num_factura=factura_ref` -> `num_factura=''` en comisiones.py:132
- SAVRecC.Factura conserva `factura_ref` (DDMMAAAA) — pendiente verificar vs produccion

---

## Bug 2: Conciliaciones con fechas no cargadas -- CORREGIDO (preventivo)

**Reporte usuario:** "Aparecen conciliados movimientos con fechas que no se han cargado"

**Archivos afectados:**
- `src/procesadores/conciliacion_pagos.py:142`
- `src/procesadores/conciliacion_cobros.py:141`

**Verificacion BD:**
- 379 pagos a proveedores en Feb 2026: TODOS ya conciliados -> no habia targets
- 7 cobros de clientes conciliados: ya estaban asi en PRODUCCION (Capturo='SALMA VAZQUEZ')
- **Conclusion:** AGENTE5 no concilio registros ajenos en esta prueba

**Sin embargo, el bug de codigo EXISTIA:**
Las funciones buscaban en rango +-2 dias. Si hubiera pagos no-conciliados de
Feb 21 o Feb 25 con montos coincidentes, los habria conciliado erroneamente.

**Posible explicacion del reporte del usuario:**
El usuario pudo confundir los registros del "completo" (primera ejecucion, que
proceso todas las fechas) con conciliaciones falsas. Las comisiones de Feb 5, 6, 16
fueron creadas por esa ejecucion, y al ver movimientos de esas fechas en una prueba
de "solo Feb 23", penso que eran conciliaciones incorrectas.

**Fix aplicado (24 Feb 2026):**
- `tolerancia_dias: int = 2` -> `tolerancia_dias: int = 0` en ambos procesadores
- Ahora solo matchea por fecha exacta del estado de cuenta

---

## Bug 3: Factura comisiones cuenta 17 no aparece en compras -- NO ES BUG DE CODIGO

**Reporte usuario:** "No aparece en compras la factura de comisiones de la cuenta 17"

**Cuenta 17** = 055003730017 (Banregio F, efectivo/cheques).
Comisiones SPEI: $6 + $0.96 IVA por cada transferencia.

### Diagnostico completo

**Mojibake descartado:** La funcion `fix_mojibake()` en `src/entrada/normalizacion.py`
normaliza correctamente las descripciones. El regex `r'Comisi[oo]n Transferencia'`
matchea despues de normalizacion. Se verifico que las 188 comisiones SPEI + 188 IVA
del archivo se clasifican correctamente.

**Causa raiz: datos no disponibles en el archivo al momento de la ejecucion.**

| Fecha | Razon por la que no se creo |
|-------|------------------------------|
| Feb 5 | No habia comisiones SPEI en el EdoCta (no hubo transferencias SPEI ese dia) |
| Feb 6 | No habia comisiones SPEI en el EdoCta (los pagos usaron "(BE) Traspaso", no SPEI) |
| Feb 10-13 | El archivo del EdoCta estaba incompleto al ejecutar el "completo" (solo cubria hasta Feb 16 parcialmente) |
| Feb 16 | Las lineas de comision SPEI no estaban en el archivo al momento de la ejecucion |
| Feb 23 | SI se creo correctamente ($41.76, NumRec 900026) — el archivo ya tenia datos completos para esta fecha |

**Evidencia del reporte "completo":** Solo 10 fechas procesadas (Feb 3-16), 707 movimientos.
El archivo actual tiene 838 movimientos (datos hasta Feb 23+). Los 131 movimientos
faltantes fueron agregados al archivo DESPUES de la ejecucion.

**Conclusion:** No hay bug de codigo. La clasificacion funciona correctamente.
El problema fue que el EdoCta estaba incompleto. Para la proxima prueba,
asegurar que el archivo del EdoCta tenga todos los datos del periodo.

---

## Bug 4: No registra ingresos cuenta 16 (TDC) -- DOS PROBLEMAS IDENTIFICADOS

**Reporte usuario:** "No registra ingresos de la cuenta 16 cuando corresponde a dias lunes"

**Cuenta 16** = 038900320016 (Banregio T, tarjeta).

### Diagnostico completo

El problema es PEOR que solo lunes: AGENTE5 no creo NINGUN registro TDC.
Se identificaron DOS causas independientes:

### Problema A: Tesoreria no cargada (run "completo")

**Sintoma:** 80 TDC/TDD con `Accion=SIN_PROCESAR`, nota "Sin corte de tesoreria para esta fecha"

**Causa:** El archivo de tesoreria no fue encontrado/pasado durante la primera ejecucion.
En `orquestador_unificado.py:90-93`:
```python
cortes = {}
if ruta_tesoreria and ruta_tesoreria.exists():
    cortes = parsear_tesoreria(ruta_tesoreria)
```
Si `ruta_tesoreria` es None o el path no existe, `cortes = {}` y todos los TDC
quedan sin procesar. El archivo de tesoreria en si es correcto — tiene 23 hojas
con datos (Feb 1-23), todas parseando correctamente.

**Posible causa:** El archivo no estaba subido al directorio correcto del servidor
(`02_Tesoreria/entrada/`) cuando se lanzo el primer run.

### Problema B: Multi-corte falla para lunes (runs "solo Feb 23")

**Sintoma:** 15 TDC/TDD con `Accion=REQUIERE_REVISION`, nota "Posible deposito combinado"

**Causa:** Feb 23 es lunes. `_buscar_cortes_tdc()` busca 3 cortes (Vie 20, Sab 21, Dom 22):

| Corte | Target TDC |
|-------|-----------|
| Feb 20 (Vie) | $263,562.17 |
| Feb 21 (Sab) | $315,611.61 |
| Feb 22 (Dom) | $373,293.87 |
| **Total** | **$952,467.65** |

Los 15 depositos bancarios de Feb 23 suman **$952,669.54** (diferencia $201.89).
El banco combina multiples abonos TDC/TDD en una sola linea del estado de cuenta.
`_asignar_multi_corte()` intenta encontrar subconjuntos exactos (tolerancia $0.01)
y falla porque no hay combinacion de depositos que sume exactamente a cada corte target.

**Este es un problema conocido** (documentado en CLAUDE.md bajo "Diferencias Conocidas
vs Produccion"). La tolerancia escalonada ($1 -> $500) no es suficiente para el gap
de $201.89. El operador manual en produccion resuelve esto con informacion adicional
del banco (lotes de liquidacion) que AGENTE5 no tiene.

### Resumen por ejecucion

| Run | Tesoreria? | TDC Resultado | Causa |
|-----|-----------|---------------|-------|
| Completo (11:05) | NO (cortes vacio) | SIN_PROCESAR x 80 | Archivo no encontrado |
| Solo Feb 23 (11:11) | SI (23 cortes) | REQUIERE_REVISION x 15 | Multi-corte falla ($201.89 gap) |
| Solo Feb 23 (11:16) | SI (23 cortes) | REQUIERE_REVISION x 15 | Idem |
| Solo Feb 23 (11:47) | SI (23 cortes) | REQUIERE_REVISION x 15 | Idem |

### Posibles mejoras futuras

Para Problema A: Validar que todos los archivos requeridos existen antes de ejecutar.
Mostrar advertencia clara en la UI de Streamlit si falta la tesoreria.

Para Problema B: Opciones:
1. Aumentar tolerancia global del multi-corte
2. Si total depositos ≈ total cortes, asignar proporcionalmente
3. Procesar al menos los cortes que SI matchean exactamente
4. Solicitar al usuario separacion manual via interfaz

---

## Prioridad de Correccion (actualizada post-diagnostico)

| Bug | Estado | Severidad | Fix |
|-----|--------|-----------|-----|
| Bug 1 (NumFactura) | CORREGIDO | **MEDIA** | `num_factura=''` en comisiones.py |
| Bug 2 (conciliaciones +-2 dias) | CORREGIDO | **MEDIA** | `tolerancia_dias=0` en ambos procesadores |
| Bug 3 (compra cuenta 17) | NO ES BUG DE CODIGO | **N/A** | Datos incompletos en el archivo de entrada |
| Bug 4a (TDC sin tesoreria) | PENDIENTE MEJORA UI | **ALTA** | Validar archivos antes de ejecutar |
| Bug 4b (TDC multi-corte lunes) | LIMITACION CONOCIDA | **MEDIA** | Mejorar algoritmo o UI de separacion manual |

**Tests:** 266 unitarios pasan. 1 test e2e falla por estado del sandbox (pre-existente).

---

## Notas de Contexto

- Los archivos de entrada estaban en la carpeta `entrada/`, no en `procesados/`
  (confirmado por el usuario).
- El dia de tesoreria viene indicado por el numero de hoja (Dia 1 = Hoja 1),
  no por el nombre de la hoja.
- El archivo de tesoreria para la prueba es version diferente del original:
  `FEBRERO INGRESOS 2026 (1).xlsx` vs `FEBRERO INGRESOS 2026.xlsx`.
- El "completo" fue la primera ejecucion y creo la mayoria de los registros.
  Las 3 ejecuciones "solo-fecha Feb 23" posteriores encontraron movimientos
  existentes y los saltaron o conciliaron.
- La normalizacion de mojibake (`fix_mojibake()` en `src/entrada/normalizacion.py`)
  funciona correctamente para todas las descripciones del EdoCta.
