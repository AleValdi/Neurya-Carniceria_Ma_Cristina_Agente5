# Proyecto: Automatizacion de Movimientos Bancarios en ERP Legacy (Agente5)

## Contexto General
ERP legacy (SAV7 / Asesoft) sin API. La unica forma de automatizar es afectando directamente
la base de datos en SQL Server. Se necesita replicar programaticamente el proceso de captura
de movimientos bancarios que hoy se hace manualmente en el ERP.

**Cliente:** Distribuidora de Carnes Maria Cristina S.A. de C.V.
**RFC:** DCM02072238A
**ERP:** SAV7 (Asesoft)
**Ejecucion:** Diaria — concilia el estado de cuenta del dia actual contra registros en ERP.
Los movimientos que ya existen se marcan como conciliados; los que no, se crean automaticamente.

---

## Estado del Proyecto

| Fase | Estado | Notas |
|------|--------|-------|
| 1. Entender proceso | ✅ Completada | Ver CONTEXTO_PROCESO_ERP.md |
| 2. Exploracion BD | ✅ Completada | Sin sesgo de ContextoComparativo.md |
| 3. Mapa correlacion | ✅ Completada | Ver docs/MAPA_BD.md |
| 3.5 Analisis de archivos de entrada | ✅ Completada | Ver seccion "Archivos de Entrada" |
| 3.6 Verificacion contra produccion | ✅ Completada | Patrones verificados con datos reales feb 2026 |
| 4. Diseno solucion | ✅ Completada | Procesadores + orquestador unificado |
| 5. Implementacion | ✅ Completada | 245 tests, demo funcional Feb 3 |
| 5.5 Comparacion vs produccion | ✅ Completada | Ver "Diferencias Conocidas vs Produccion" |
| 5.6 Bugs prueba usuario (Feb 24) | ✅ Diagnosticado | 2 corregidos, 2 no son bug de codigo. Ver `docs/BUGS_PRUEBA_20260224.md` |
| 6. Hardening / file watcher | ⏳ Pendiente | — |
| 7. Acceso remoto Streamlit | ⏳ Pendiente | Cambio ya aplicado en `iniciar.bat` (`--server.address 0.0.0.0`). Falta abrir puerto 8501 en firewall Windows y probar. |

---

## Archivos de Entrada (Analizados)

### 1. Estado de Cuenta Bancario: `data/reportes/PRUEBA.xlsx`
Archivo de trabajo con estados de cuenta de BANREGIO. Periodo: Febrero 2026.

**Hojas activas:**
| Hoja | Cuenta | Movimientos | Cuenta Contable |
|------|--------|-------------|-----------------|
| `Banregio F` | 055003730017 | 443 | 1120/040000 (efectivo/cheques) |
| `Banregio T ` | 038900320016 | 245 | 1120/060000 (tarjeta) |
| `BANREGIO GTS` | 055003730157 | 19 | 1120/070000 (gastos) |
| `BANREGIO TDC` | 055003730157 | 0 | Plantilla vacia |
| `SALDOS A FAVOR P` | — | 14 | Control saldos a favor proveedores |

**Estructura por hoja bancaria:**
- Filas 1-4: Encabezado (razon social, cuenta, CLABE, RFC, saldo inicial/final)
- Fila 5: Headers → `Fecha | Descripcion/Referencia | Cargos (EGRESOS) | Abonos (DEPOSITOS) | Saldo`
- Fila 6+: Datos (cols A-D son datos reales; col E es formula de saldo acumulado)
- Fin de datos: donde col A (fecha) deja de tener datetime
- Filas finales: SUMA DE SALDOS, SALDO CONTABILIDAD, DIFERENCIA

**Problema de encoding:** Descripciones tienen mojibake (`NOMINA` → `NÃ"MINA`). Normalizar al parsear.

**Patrones de descripcion identificados:**
| Patron | Tipo | Proceso |
|--------|------|---------|
| `ABONO VENTAS TDD_8996711` | Ingreso (cuenta T) | Venta diaria tarjeta debito |
| `ABONO VENTAS TDC_8996711` | Ingreso (cuenta T) | Venta diaria tarjeta credito |
| `Deposito en efectivo_` | Ingreso (cuenta F) | Venta diaria efectivo |
| `(BE) Traspaso a cuenta: {CTA}` | Egreso | Traspaso (incluye cuenta destino) |
| `(NB) Recepcion de cuenta: {CTA}` | Ingreso | Traspaso (incluye cuenta origen) |
| `Comision Transferencia -` | Egreso | Comision SPEI ($6.00 c/u) |
| `IVA de Comision Transfer` | Egreso | IVA comision ($0.96 c/u) |
| `Aplicacion de Tasas de Descuento` | Egreso (cuenta T) | Comision ventas TDC/TDD |
| `IVA Aplicacion de Tasas` | Egreso (cuenta T) | IVA comision ventas |
| `NOMINA - PAGO DE NOMINA` | Egreso | Nomina |
| `VXXX/VWSA... SPEI.` | Egreso | Pago SPEI a proveedor |

### 2. Reporte de Tesoreria: `data/reportes/FEBRERO INGRESOS 2026.xlsx`
Reporte diario de ventas. 31 hojas (una por dia), 15 con datos (dias 1-15).

**Estructura de cada hoja diaria:**
| Seccion | Celdas | Dato clave |
|---------|--------|------------|
| Fecha del corte | **J18** (NO E3, E3 tiene fechas erroneas) | Fecha autoritativa |
| Cortes Z (cajas) | C19:D43, total D44 | Total ventas del dia |
| Facturas individuales | **G19:G43** (numero), **H19:H43** (importe) | Serie FD, 9-19 por dia |
| Factura global | **K19 o K20** (numero varia!), **L20** (importe real) | Serie FD |
| Depositos SISSA | J23:L42 | Vacio en todos los dias |
| Efectivo recibido | C50:E62, total **E63** | Total deposito efectivo |
| TDC por terminal | G50:H56, total **H63** | Total TDC (4 terminales) |
| Otro medio pago | L50, total **L55** | Vales ($700-$1000 ocasional) |
| Folio SISSA | **D65** | Numero ficha deposito |

**Anomalias detectadas:**
- Factura global en K19 (hojas 1-13) pero K20 (hojas 14-15). Parser debe checar ambas.
- Fecha E3 incorrecta en varias hojas. Usar J18 siempre.
- Identidad financiera verificada (exacta en 15 dias):
  `Ventas = Factura Global + Facturas Individuales = Efectivo + TDC + Otros`

**Datos ejemplo dia 1:**
- Ventas: $734,540 | TDC: $334,082 | Efectivo: $400,458
- Factura Global: FD-20204 ($725,897.52) | 11 facturas individuales ($8,642)

### 3. Nomina: `contexto/listaRaya/`
| Archivo | Contenido |
|---------|-----------|
| `NOMINA 03 CHEQUE.xlsx` | Excel CONTPAQi con 4 hojas |
| `Lista de raya 03.pdf` | PDF resumen de CONTPAQi |
| `EJEMPLO DE LISTA DE RAYA.pdf` | Ejemplo con reglas contables |

**Estructura del Excel de nomina (NOMINA XX CHEQUE.xlsx):**
| Hoja | Contenido | Dato clave |
|------|-----------|------------|
| `NOM 03` | Nomina completa (67 empleados) | Percepciones, deducciones, neto |
| `DISPERCION` | Transferencias bancarias (53 empleados) | Cuenta bancaria + monto |
| `CHEQUE` | Pagos en cheque (12 empleados) | Solo monto |
| `CHEQUE FINIQUITOS` | Finiquitos por cheque | Vacio en este periodo |

**Totales en NOM 03 (fila 73-78):**
- DISPERSION: $117,992.20 (transferencias)
- CHEQUES: $24,980.60 (efectivo)
- Total Neto: $142,972.80
- Vacaciones pagadas (fila 81): $3,905.20
- Finiquito (fila 84): $3,344.40

**Percepciones (para poliza contable):**
| Concepto | Cuenta | SubCuenta | Monto ejemplo |
|----------|--------|-----------|---------------|
| Sueldo | 6200 | 010000 | $119,737.16 |
| Septimo dia | 6200 | 240000 | $20,473.00 |
| Prima dominical | 6200 | 670000 | $4,923.42 |
| Bono puntualidad | 6200 | 770000 | $300.00 |
| Vacaciones | 6200 | 020000 | $7,690.80 |
| Prima vacacional | 6200 | 060000 | $2,158.98 |
| Aguinaldo | 6200 | 030000 | $191.58 |
| Bono asistencia | 6200 | 780000 | $400.00 |

**Deducciones (para poliza contable):**
| Concepto | Cuenta | SubCuenta | Monto ejemplo |
|----------|--------|-----------|---------------|
| Infonavit vivienda | 2140 | 270000 | $15.00 |
| Infonavit FD | 2140 | 270000 | $380.33 |
| Infonavit CF | 2140 | 270000 | $3,518.28 |
| ISR (mes) | 2140 | 020000 | $2,049.25 |
| IMSS | 2140 | 010000 | $635.78 |

### 4. Liquidacion de Impuestos
**⏳ PENDIENTE** — Lo compartiran mas tarde.

---

## Cobertura de Procesos

### Procesos CUBIERTOS (tienen fuente de datos)

| # | Proceso | Accion | Fuente datos | Tipo ERP | Complejidad |
|---|---------|--------|-------------|----------|-------------|
| 1 | **I1. Ventas TDC** | Crear movimiento + facturas + poliza | EdoCta (Banregio T) + Ingresos | Tipo 4, VENTA DIARIA | Alta |
| 2 | **I2. Ventas Efectivo** | Crear movimiento + facturas + poliza | EdoCta (Banregio F) + Ingresos | Tipo 4, VENTA DIARIA | Alta |
| 3 | **E3. Comisiones bancarias** | Crear factura compras + movimiento + poliza | EdoCta (ambas cuentas) | Tipo 3, COMISIONES BANCARIAS | Media |
| 4 | **E4. Traspasos** | Crear egreso + ingreso + poliza | EdoCta (patron BE/NB incluye cuenta destino) | Tipo 2, ENTRE CUENTAS PROPIA | Media |
| 5 | **E1. Pagos a proveedores** | Solo CONCILIAR (match SPEI ↔ pago existente) | EdoCta + BD (pagos ya existen en SAVCheqPM) | Tipo 3, PAGOS A PROVEEDORES | Baja |
| 6 | **I3. Cobros clientes** | Solo CONCILIAR (match transferencia ↔ cobro existente) | EdoCta + BD (cobros ya generados por modulo Comercial) | Tipo 1, DEPOSITOS | Baja |
| 7 | **E2. Nomina** | Crear movimiento(s) + poliza compleja | Excel CONTPAQi + EdoCta | Tipo 2, NOMINA/FINIQUITO | Alta |

### Procesos PENDIENTES (falta fuente de datos)

| # | Proceso | Que falta |
|---|---------|-----------|
| 8 | **E5. Impuestos** | Liquidacion de impuestos (ISR, IVA, IMSS, 3% nomina) — lo compartiran despues |

---

## Verificacion contra Produccion (Feb 2026) — Resultados

### Venta TDC (Folio 126931 — cuenta tarjeta 038900320016)
- Concepto: `"VENTA DIARIA 01/02/2026"` (fecha = corte venta, NO fecha deposito)
- FPago: `"Tarjeta Debito"` o `"Tarjeta Credito"` (diferencia TDD/TDC)
- **Facturas en SAVCheqPMF: SOLO la GLOBAL** (FD-20204, $215,370.52)
- **NO lleva facturas individuales** en cuenta tarjeta
- Poliza: **6 lineas fijas** (Cargo Banco 1120/060000 + Abono Clientes 1210/010000 + IVA + IEPS)
- 4 movimientos para corte dia 1: TDD $215,370.52 + TDC $88,643.24 + TDD $6,560.71 + TDC $23,508.01 = $334,082.48
- Los montos vienen del **estado de cuenta** (abonos individuales), NO de terminales de Tesoreria
- La suma TDC de Tesoreria ($334,082.48) sirve como **validacion**, no como fuente de montos

### Venta Efectivo (Folio 127155 — cuenta cheques 055003730017)
- FPago: `"Efectivo"`
- Ingreso: $400,457.39 (EdoCta dice $400,457.50 — diferencia de centavos es normal)
- **Facturas en SAVCheqPMF: 11 INDIVIDUAL + 1 GLOBAL = 12** (match exacto con hoja "1" de Ingresos)
- Poliza: **46 lineas** (variable segun facturas):
  - 6 lineas para factura GLOBAL (Cargo Banco + Abono Clientes + IVA + IEPS)
  - 2-6 lineas por cada factura INDIVIDUAL (con IVA/IEPS variable por factura)
- **IVA/IEPS por factura se obtiene de SAVFactC** (tabla de ventas, Serie='D', campo Iva e IEPS)
  - IMPORTANTE: en SAVCheqPMF la Serie es "FD" pero en SAVFactC la Serie es "D" (mismos NumFac)

### Comisiones Bancarias (Folio 126963)
- Concepto: `"COMISIONES BANCARIAS 03/02/2026"`
- Cuenta: 038900320016 (tarjeta) — las comisiones TDC van en cuenta tarjeta
- Proveedor: 001081 (BANCO REGIONAL)
- Poliza: 4 lineas (Cargo Proveedores 2110/010000 + IVA reclasificacion + Abono Banco 1120/060000)

### Traspasos (Folio 126791)
- Concepto: `"TRASPASO A BANCO: BANREGIO CUENTA: 038900320016 MONEDA: PESOS"`
- TipoPoliza: `"DIARIO"` (no EGRESO ni INGRESO)
- DocTipo en poliza: `"TRASPASOS"` (no CHEQUES)
- Poliza: 2 lineas (Cargo cuenta destino + Abono cuenta origen)
- ParidadDOF: 20.0000

### Pagos a Proveedores (Tipo 3)
- 248 pagos en feb 2026, **todos ya afectados y conciliados**
- **NO usan SAVCheqPMF** — no tienen facturas vinculadas en esa tabla
- Numero de factura va en campo `Concepto` (ej: "F06489826") o dice "PAGO DE FACTURAS DE COMPRAS"
- Poliza: 4 lineas (Cargo Proveedores + IVA/IEPS reclasificacion + Abono Banco)

### Cobros a Clientes (Tipo 1, Clase DEPOSITOS)
- 5 cobros en feb 2026, todos ya conciliados
- Generados automaticamente por modulo Comercial
- Concepto: `"CLIENTE: 000059-EDDY ALBERTO MO CM: 70162 FACT: FC-1622,"`
- Solo requieren marcar `Conciliada = 1`

### Nomina (verificado con documentacion, no con BD aun)
- Una nomina genera **hasta 4 movimientos bancarios**:
  1. Dispersion (transferencias): Tipo 2, NOMINA, TipoEgreso=TRANSFERENCIA
  2. Cheques (efectivo): Tipo 2, NOMINA, TipoEgreso=CHEQUE
  3. Vacaciones pagadas: Tipo 2, NOMINA, TipoEgreso=TRANSFERENCIA
  4. Finiquitos: Tipo 2, FINIQUITO, TipoEgreso=TRANSFERENCIA
- Poliza del movimiento principal (dispersion): **~19 lineas**
  - Cargos: percepciones (cuentas 6200/XXXXXX)
  - Abonos: deducciones (cuentas 2140/XXXXXX) + Banco (1120/040000) + Acreedores Nomina (2120/040000)
  - Los montos de cheques, vacaciones y finiquito se abonan a 2120/040000 en la poliza principal
- Polizas de movimientos secundarios (cheques, vacaciones, finiquito): **2 lineas** c/u
  - Cargo 2120/040000 (Acreedores Diversos Nomina) + Abono 1120/040000 (Banco)

---

## Cruce de Fuentes de Datos por Proceso

### I1. Ventas TDC (cuenta tarjeta)
```
Estado de Cuenta (Banregio T)     →  Montos individuales de cada abono TDC/TDD
    + patron ABONO VENTAS TDC/TDD →  Distingue FPago: "Tarjeta Credito" o "Tarjeta Debito"
    + fecha del abono             →  Dia del movimiento en SAVCheqPM

Reporte Tesoreria (hoja del dia)  →  Numero de factura GLOBAL (K19 o K20)
    + importe factura global (L20)→  MontoFactura en SAVCheqPMF
    + total TDC (H63)             →  VALIDACION: debe = suma abonos TDC del dia en EdoCta
    + fecha del corte (J18)       →  Fecha para Concepto: "VENTA DIARIA DD/MM/AAAA"

BD: SAVFactC (Serie D)            →  IVA e IEPS de la factura global
                                  →  Para generar lineas 3-6 de la poliza
```

### I2. Ventas Efectivo (cuenta cheques)
```
Estado de Cuenta (Banregio F)     →  Monto del deposito en efectivo
    + patron "Deposito en efectivo" → Identificar el movimiento
    + fecha del deposito          →  Dia del movimiento en SAVCheqPM

Reporte Tesoreria (hoja del dia)  →  Factura GLOBAL: numero (K19/K20) + importe (L20)
    + Facturas INDIVIDUALES       →  Numeros (G19:G43) + importes (H19:H43)
    + total efectivo (E63)        →  VALIDACION: debe ≈ deposito en EdoCta (tolerancia centavos)
    + fecha del corte (J18)       →  Fecha para Concepto: "VENTA DIARIA DD/MM/AAAA"

BD: SAVFactC (Serie D)            →  IVA e IEPS POR CADA factura (global + individuales)
                                  →  Para generar lineas de poliza (variable: 6-46+ lineas)
```

### E3. Comisiones Bancarias
```
Estado de Cuenta                  →  Suma de comisiones + IVA del dia
    + patron "Comision Transferencia" ($6.00 c/u) + "IVA de Comision" ($0.96 c/u)
    + patron "Aplicacion de Tasas" (cuenta tarjeta)
    → Agrupar por dia para generar 1 movimiento diario

BD: SAVRecC/SAVRecD               →  Crear factura de compras (Proveedor 001081, Producto COMISION TERMINAL)
BD: SAVCheqPM                     →  Crear movimiento tipo 3
BD: SAVPoliza                     →  4 lineas: Proveedores + IVA reclasificacion + Banco
```

### E4. Traspasos
```
Estado de Cuenta                  →  Patron "(BE) Traspaso a cuenta: {CTA_DESTINO}"
    + monto y fecha               →  Movimiento de egreso
    + cuenta destino en texto     →  Para identificar BancoDestino y CuentaDestino

BD: SAVCheq                       →  Obtener CuentaC/SubCuentaC de ambas cuentas → poliza
BD: SAVCheqPM                     →  INSERT egreso (Tipo 2) + INSERT ingreso (Tipo 1)
BD: SAVPoliza                     →  2 lineas, DocTipo=TRASPASOS, TipoPoliza=DIARIO
```

### E1. Pagos a Proveedores (solo conciliar)
```
Estado de Cuenta (Banregio F)     →  SPEIs con monto y fecha
BD: SAVCheqPM                     →  Pagos ya existentes (Tipo 3, Clase PAGOS A PROVEEDORES)
    → Match por monto + fecha     →  UPDATE Conciliada = 1
```

### I3. Cobros a Clientes (solo conciliar)
```
Estado de Cuenta (Banregio F)     →  Transferencias recibidas
BD: SAVCheqPM                     →  Movimientos ya creados por modulo Comercial (Tipo 1, Concepto LIKE '%CLIENTE%')
    → Match por monto + fecha     →  UPDATE Conciliada = 1
```

### E2. Nomina → TRASPASO a CAJA CHICA
```
IMPORTANTE: En el estado de cuenta, el movimiento "NOMINA" es realmente una
transferencia de fondos de BANREGIO F a CAJA CHICA. La nomina real (percepciones,
deducciones, poliza de 19 lineas) se procesa en AZTECA/VIRTUAL, que esta FUERA
del estado de cuenta bancario.

Estado de Cuenta                  →  Monto de la transferencia
    + patron "NOMINA - PAGO DE NOMINA" → Identificar
    + dia del movimiento          →  Dia en SAVCheqPM

→ Genera TRASPASO a CAJA CHICA (egreso BANREGIO F + ingreso CAJA CHICA):
    - Tipo 2 (egreso) + Tipo 1 (ingreso)
    - Clase: 'ENTRE CUENTAS PROPIA' (egreso) / 'TRASPASO' (ingreso)
    - TipoEgreso: 'INTERBANCARIO'
    - TipoPoliza: 'DIARIO', DocTipo: 'TRASPASOS'
    - Poliza: 2 lineas (Cargo CAJA CHICA 1110/010000, Abono Banco 1120/040000)
    - Egreso conciliada=1, ingreso conciliada=0

El archivo Excel CONTPAQi (NOMINA XX CHEQUE.xlsx) NO se usa para este proceso.
```

---

## Fuente de Informacion Original
Se analizo un video de demostracion donde un usuario ejecuta el proceso completo en el ERP.
El analisis visual + audio genero material en `contexto/`:
- `contexto/frames/` — capturas de pantalla del video (~564 frames, 1 cada 5s)
- `contexto/transcripcion.srt` — transcripcion por Whisper (con timestamps)
- `contexto/transcripcion_gdrive.txt` — transcripcion generada por Google Drive
- `contexto/proceso_conciliacion_ingresos.pdf` — documento formal del proceso (19 paginas)
- `contexto/listaRaya/` — archivos de nomina CONTPAQi (Excel + PDFs)

Hay dos transcripciones del mismo audio generadas por herramientas distintas.
Cuando haya discrepancias entre ambas, usa el contexto visual de los frames
para determinar cual es correcta.

## Documentos Generados

| Documento | Fase | Contenido |
|-----------|------|-----------|
| `contexto/CONTEXTO_PROCESO_ERP.md` | 1 | Proceso del video: egresos (E1-E5), ingresos (I1-I5), prioridades |
| `contexto/ContextoComparativo.md` | — | Documento preexistente. Info ya extraida a MAPA_BD.md |
| **`docs/MAPA_BD.md`** | **2-3** | **Correlacion BD completa: tablas, PKs, polizas, ejemplos reales, secuencia SQL** |

---

## Referencia Rapida BD (detalle completo en docs/MAPA_BD.md)

### Tablas Core
| Tabla | Funcion | PK |
|-------|---------|-----|
| SAVCheqPM | Movimientos bancarios | Banco+Cuenta+Age+Mes+Dia+Tipo+FechaAlta+HoraAlta |
| SAVCheqPMF | Facturas vinculadas a movimientos | Banco+Cuenta+Age+Mes+Folio+Sucursal+Serie+NumFactura |
| SAVPoliza | Lineas de poliza contable | Cia+Fuente+Poliza+Oficina+DocTipo+Movimiento |
| SAVCheqP | Periodos de chequera | Banco+Cuenta+Age+Mes |
| SAVCheq | Catalogo de cuentas bancarias | Banco+Cuenta |
| SAVRecC / SAVRecD | Facturas de compras (comisiones) | Serie+NumRec |
| SAVFactCob | Cobros de clientes | Serie+NumFac+Cobro |
| SAVFactC / SAVFactD | Facturas de venta (tickets Serie D) | Serie+NumFac |

### Hallazgos Criticos
- **NO hay triggers** en ninguna tabla relevante
- **NO hay stored procedures** para bancos/polizas/pagos
- **Folio** (SAVCheqPM) y **Poliza** (SAVPoliza): consecutivos globales con MAX+1
- **Tipo**: 1=ingreso general, 2=egreso manual, 3=egreso con factura, 4=ingreso venta
- **29 clases** de movimiento, **8 cuentas bancarias** activas
- **Dia del movimiento**: siempre = dia del ESTADO DE CUENTA, NO dia del corte de venta
- **Concepto venta diaria**: "VENTA DIARIA {DD/MM/AAAA}" usa fecha del CORTE (dia anterior)
- **Facturas TDC (SAVCheqPMF)**: Solo GLOBAL. Serie=FD, TipoFactura=GLOBAL, Suc=5
- **Facturas Efectivo (SAVCheqPMF)**: INDIVIDUAL + GLOBAL. Serie=FD, TipoFactura=INDIVIDUAL/GLOBAL
- **Serie FD en SAVCheqPMF = Serie D en SAVFactC** (mismo NumFac, distinta serie)
- **IVA/IEPS por factura**: Consultar SAVFactC WHERE Serie='D' AND NumFac=X → campos Iva, IEPS
- **Cobros de clientes**: Se procesan desde modulo Comercial (Cobranza), NO desde Bancos
- **Traspasos**: DocTipo=TRASPASOS en poliza (no CHEQUES), TipoPoliza=DIARIO
- **Pagos a proveedores**: NO usan SAVCheqPMF, factura va en campo Concepto
- **Nomina**: En el estado de cuenta, "NOMINA" es realmente TRASPASO a CAJA CHICA (ver abajo)
- **CAJA CHICA**: Cuenta interna intermediaria (Banco='CAJA CHICA', Cuenta='00000000000', CuentaC='1110/010000')
- **SAVPoliza.Concepto**: varchar(60) — SIEMPRE truncar a 60 chars
- **TipoEgreso traspasos**: 'INTERBANCARIO' (NO 'TRANSFERENCIA')
- **COBRO_CLIENTE vs PAGO_PROVEEDOR**: Misma regex SPEI, se distinguen por direccion (ingreso vs egreso)

### Constantes
| Campo | Valor |
|-------|-------|
| Cia | DCM |
| Fuente | SAV7-CHEQUES |
| Oficina / CuentaOficina | 01 |
| Sucursal | 5 |
| RFC | DCM02072238A |
| Moneda | PESOS |
| Paridad | 1.0000 (movimientos normales) |
| ParidadDOF | 20.0000 (traspasos) |
| Proveedor banco | 001081 (BANCO REGIONAL) |
| Cuenta banco efectivo | BANREGIO / 055003730017 → contable 1120/040000 |
| Cuenta banco tarjeta | BANREGIO / 038900320016 → contable 1120/060000 |
| Cuenta banco gastos | BANREGIO / 055003730157 → contable 1120/070000 |
| Cuenta CAJA CHICA | CAJA CHICA / 00000000000 → contable 1110/010000 |

### Secuencia SQL para insertar movimiento
```
1. MAX(Folio) + 1 → nuevo folio
2. INSERT SAVCheqPM
3. INSERT SAVCheqPMF (si tiene facturas — ventas y comisiones)
4. MAX(Poliza) + 1 → nueva poliza (WHERE Fuente='SAV7-CHEQUES')
5. INSERT SAVPoliza (N lineas segun tipo)
6. UPDATE SAVCheqPM SET NumPoliza = @poliza
```

---

## Entorno de Desarrollo

- **Virtual environment**: `venv/` en la raiz del proyecto
- Activar: `source venv/bin/activate`
- Instalar dependencias: `pip install -r requirements.txt`
- **Siempre usar el venv** para ejecutar tests y scripts (el Python del sistema no tiene las dependencias)
- Driver ODBC en Mac: `msodbcsql17` instalado via `brew install microsoft/mssql-release/msodbcsql17`

### Servidor Windows (SERVERMC)

El servidor tiene Python MSYS2 (`C:\msys64\mingw64\`) que usan los otros agentes. **No usar MSYS2 para Agente5** porque:
- No tiene wheels binarios en PyPI (numpy, pandas, pyodbc fallan)
- SSL roto impide compilar desde source (cmake no descarga)
- `--trusted-host` y `--only-binary` no resuelven

**Solucion**: Instalar Python 3.12 de python.org en paralelo, **sin agregar al PATH**:
1. Descargar `python-3.12.10-amd64.exe` de python.org (ultima version con instalador Windows)
2. Instalar sin "Add to PATH" ni "Use admin privileges"
3. Se instala en `C:\Users\Administrador\AppData\Local\Programs\Python\Python312\`
4. Crear venv con ruta completa:
```powershell
cd C:\Tools\Agente5
& "C:\Users\Administrador\AppData\Local\Programs\Python\Python312\python.exe" -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```
5. Los otros agentes siguen usando MSYS2 sin afectarse
6. El venv usa `Scripts\` (no `bin\` como MSYS2)

**App Streamlit**: `.\venv\Scripts\activate` y luego `streamlit run app.py`, o doble clic en `iniciar.bat`

## Conexion a Base de Datos

| Contexto | Host | Driver |
|----------|------|--------|
| Produccion (servidor) | `localhost` | SQL Server Native Client 11.0 |
| Desarrollo (laptop/MCP) | `100.73.181.41` | ODBC Driver 17 for SQL Server (via Tailscale) |

- **Credenciales**: `devsav7` / `devsav7`
- **BD Sandbox/Pruebas**: `DBSAV71A` (default del MCP — lectura y escritura)
- **BD Produccion**: `DBSAV71` (usar nombre calificado: `DBSAV71.dbo.Tabla` — solo lectura)
- **BD Legacy pruebas**: `DBSAV71_TEST` (ya no usar, reemplazada por DBSAV71A)

## Herramientas Disponibles
- **MCP de SQL Server**: Conectado a `DBSAV71A` (sandbox). Puede consultar produccion con `DBSAV71.dbo.Tabla`.
  Requiere Tailscale activo para conectar a `100.73.181.41`.
- **MCP de Plane**: Gestion de tareas. Workspace: `neurya`.

---

## Reglas Importantes
- **NUNCA hagas INSERT/UPDATE/DELETE sin confirmacion explicita**. Primero muestra
  el SQL que ejecutarias y espera aprobacion.
- Siempre trabaja primero en DBSAV71A (sandbox).
- Los ERPs legacy tienen logica oculta. Explora TODO antes de escribir.
- Documenta cada hallazgo en el proceso de exploracion.
- Codigo en Python 3.9+, variables en espanol, docstrings en espanol.

---

## Estructura del Proyecto
```
Agente5/
├── CLAUDE.md                    # Este archivo — instrucciones para Claude Code
├── README.md                    # Docs del pipeline video→contexto
├── .env / .env.example          # Credenciales BD
├── requirements.txt             # Dependencias Python
├── contexto/                    # Material de referencia
│   ├── frames/                  # Capturas del video (~564 JPGs)
│   ├── transcripcion.srt        # Transcripcion Whisper
│   ├── transcripcion_gdrive.txt # Transcripcion Google Drive
│   ├── proceso_conciliacion_ingresos.pdf  # Documento formal (19 pags)
│   ├── CONTEXTO_PROCESO_ERP.md  # Analisis consolidado del proceso
│   ├── ContextoComparativo.md   # Documento legacy (info ya en MAPA_BD)
│   └── listaRaya/               # Archivos nomina CONTPAQi
│       ├── NOMINA 03 CHEQUE.xlsx
│       ├── Lista de raya 03.pdf
│       └── EJEMPLO DE LISTA DE RAYA.pdf
├── docs/                        # Documentacion generada
│   └── MAPA_BD.md               # Correlacion pantalla↔BD (Fases 2-3)
├── config/
│   └── settings.py              # Cuentas bancarias, constantes ERP, cuentas contables
├── src/
│   ├── clasificador.py          # Patrones regex para clasificar movimientos
│   ├── orquestador.py           # Funciones standalone + helpers (subset matching)
│   ├── orquestador_unificado.py # Orquestacion principal (usa demo.py)
│   ├── models.py                # Dataclasses: MovimientoBancario, PlanEjecucion, etc.
│   ├── validacion.py            # Validaciones TDC/efectivo vs tesoreria
│   ├── erp/                     # Escritura al ERP (INSERT/UPDATE)
│   │   ├── consecutivos.py      # MAX(Folio)+1, MAX(Poliza)+1
│   │   ├── movimientos.py       # INSERT/UPDATE SAVCheqPM
│   │   ├── facturas_movimiento.py # INSERT SAVCheqPMF
│   │   ├── compras.py           # INSERT SAVRecC/RecD (comisiones)
│   │   └── poliza.py            # INSERT SAVPoliza (trunca concepto a 60 chars)
│   ├── entrada/                 # Lectura de datos de entrada
│   │   ├── estado_cuenta.py     # Parser estado de cuenta BANREGIO
│   │   ├── tesoreria.py         # Parser reporte tesoreria (cortes de venta)
│   │   ├── nomina.py            # Parser Excel CONTPAQi
│   │   └── impuestos_pdf.py     # Parser PDFs impuestos
│   ├── procesadores/            # Constructores de PlanEjecucion por tipo
│   │   ├── venta_tdc.py         # I1: Venta TDC/TDD
│   │   ├── venta_efectivo.py    # I2: Venta efectivo
│   │   ├── comisiones.py        # E3: Comisiones bancarias
│   │   ├── traspasos.py         # E4: Traspasos entre cuentas
│   │   ├── conciliacion_pagos.py # E1: Conciliacion pagos proveedor
│   │   ├── conciliacion_cobros.py # I3: Conciliacion cobros clientes
│   │   ├── nomina_proc.py       # E2: Nomina (dispersion + cobros cheque)
│   │   └── impuestos.py         # E5: Impuestos federales/estatales/IMSS
│   └── reports/
│       └── reporte_demo.py      # Genera Excel con comparacion vs produccion
├── demo.py                      # Entry point: --limpiar, --solo-fecha, --dry-run
├── tests/                       # 266 tests unitarios + 2 e2e
├── data/
│   └── reportes/
│       ├── PRUEBA.xlsx          # Estado de cuenta bancario (Feb 2026)
│       ├── FEBRERO INGRESOS 2026.xlsx  # Reporte tesoreria (Feb 2026)
│       └── DEMO_REPORTE.xlsx    # Reporte generado (comparacion vs PROD)
└── logs/
```

---

## Bugs Corregidos y Lecciones (Prueba Feb 24 2026)

Detalle completo en `docs/BUGS_PRUEBA_20260224.md`.

### Corregidos en codigo

1. **SAVCheqPM.NumFactura en comisiones** — Produccion usa NULL, AGENTE5 ponia la fecha
   como string (ej: "23022026"). Corregido: `num_factura=''` en `comisiones.py:132`.

2. **Conciliacion con tolerancia ±2 dias** — `_buscar_pago_en_bd()` y `_buscar_cobro_en_bd()`
   buscaban movimientos ±2 dias, pudiendo conciliar registros de fechas no procesadas.
   Corregido: `tolerancia_dias=0` (match por fecha exacta).

### No son bug de codigo (datos de entrada)

3. **Comisiones cuenta 17 faltantes** — El archivo del EdoCta estaba incompleto al ejecutar
   el run "completo" (solo tenia datos parciales hasta Feb 16). La normalizacion de mojibake
   (`fix_mojibake()` en `normalizacion.py`) funciona correctamente — el regex matchea bien.
   **Leccion:** Verificar que el archivo del EdoCta tiene TODOS los datos antes de ejecutar.

4. **TDC cero registros** — Dos causas:
   - Run "completo": tesoreria no fue encontrada (`cortes = {}`). El archivo existia pero
     no estaba en el path esperado (`02_Tesoreria/entrada/`).
   - Runs "solo Feb 23" (lunes): Multi-corte falla porque los depositos combinados del banco
     no suman exactamente a los cortes de tesoreria ($201.89 gap en $952K totales).
   **Leccion:** La UI debe validar que todos los archivos requeridos existen antes de ejecutar.
   El algoritmo multi-corte para lunes con depositos combinados es una limitacion conocida.

---

## Diferencias Conocidas vs Produccion (Feb 3 2026)

### TDC: Depositos combinados del banco
El banco BANREGIO a veces combina multiples abonos TDC/TDD en una sola linea
del estado de cuenta. En produccion, el operador los separa manualmente usando
info adicional del banco (lotes de liquidacion).

Ejemplo Feb 3 (19 depositos en cuenta tarjeta):
| Deposito banco | PROD lo separa en |
|---|---|
| $56,137.11 | $23,508.01 (VENTA corte 1) + $32,629.10 (VENTA corte 2) |
| $229,526.52 | $215,370.52 (VENTA corte 1) + $14,156.00 (TRASPASO) |
| $277,313.16 | $271,901.87 (TRASPASO) + $5,411.29 (TRASPASO) |

**Algoritmo**: `_asignar_multi_corte()` usa backtracking simultaneo con tolerancia
escalonada en 3 niveles:
1. Exacto para todos ($1)
2. Exacto para corte 1, relajado ($500) para los demas
3. Relajado para todos ($500)

**Resultado Feb 3**: Corte 1 = 4 deps EXACTO ($334,082.48), Corte 2 = 3 deps
($238,868.78, diff $245.69). Total: 7 VENTA + 12 TRASPASO (PROD: 10 + 12).
TRASPASO count matchea PROD exactamente. VENTA diff = 3 items (depositos combinados).

### Comisiones: IVA agregado vs por linea (RESUELTO)
El banco calcula IVA por linea individual y suma, pero el ERP calcula IVA como
16% del subtotal agregado. Para matchear PROD, usamos IVA calculado:
`iva = (subtotal * 0.16).quantize('0.01', ROUND_HALF_UP)`
**Resultado**: $11,236.83 = PROD exacto (antes $11,236.82, diff $0.01).

### Direccion de traspasos CAJA CHICA
| Caso | Direccion | Egreso conciliada | Ingreso conciliada |
|------|-----------|-------------------|-------------------|
| TDC sobrantes | CAJA CHICA → tarjeta (`desde_caja_chica=True`) | 0 | 1 |
| Nomina | BANREGIO F → CAJA CHICA (`desde_caja_chica=False`) | 1 | 0 |

### Tesoreria: detalle TDC por terminal
Cada celda TDC en tesoreria (H50:H62) es formula `=X+Y` con 2 sumandos literales.
Dia 1 tiene 7 filas, Dia 2 tiene 4 filas. Los sumandos NO mapean 1:1 a depositos
bancarios (el banco agrega por lote de liquidacion, no por terminal).

### Demo: como ejecutar y comparar
```bash
# Limpiar sandbox y procesar solo Feb 3
source venv/bin/activate
python demo.py --limpiar --solo-fecha 2026-02-03

# Resultado: genera DEMO_REPORTE.xlsx
# Feb 3: 63 INSERT, 37 folios, 0 errores
# Comisiones: $11,236.83 (match PROD exacto)
# TDC: 7 VENTA + 12 TRASPASO (PROD: 10 + 12)
```
