# MAPA_BD.md - Correlacion Pantalla ERP ↔ Base de Datos

Generado en Fase 2-3 del proyecto Agente5. Toda la informacion fue verificada
directamente contra la BD `DBSAV71_TEST` via MCP SQL Server.

---

## 1. Resumen de la BD

- **748 tablas** en DBSAV71_TEST
- **Prefijos**: SAV (734), Imp (14)
- **No hay stored procedures** para procesos de bancos/polizas/pagos
- **No hay triggers** en las tablas relevantes (SAVCheqPM, SAVCheqPMF, SAVPoliza, SAVCheq, SAVCheqP, SAVRecC, SAVRecD, SAVFactCob, SAVContabSaldo)
- **Toda la logica esta en el cliente ERP** — escribir directo a BD es seguro si se respetan PKs y formato

---

## 2. Tablas Core del Modulo de Bancos

### 2.1 SAVCheqPM — Movimientos de Chequera (TABLA PRINCIPAL)

**Pantalla ERP**: "Movimientos en Periodo de Chequera" (pestanas: Todos | Ingresos | Egresos | Fichas de Deposito | Solicitudes de Pago)

**PK**: `Banco + Cuenta + Age + Mes + Dia + Tipo + FechaAlta + HoraAlta` (8 campos — unicidad por timestamp)

| Campo | Tipo SQL | Descripcion | Ejemplo |
|-------|----------|-------------|---------|
| Banco | varchar(20) | Nombre del banco | 'BANREGIO' |
| Cuenta | varchar(20) | Numero de cuenta | '055003730017' |
| Age | int | Ano | 2024 |
| Mes | int | Mes | 11 |
| Dia | int | Dia del movimiento | 28 |
| **Tipo** | smallint | **Tipo de movimiento (ver tabla abajo)** | 1,2,3,4 |
| FechaAlta | datetime | Fecha de captura | 2024-11-28 |
| HoraAlta | datetime | Hora de captura (base 1899-12-30) | 1899-12-30T14:30:00 |
| Ingreso | money | Monto de ingreso (0 si es egreso) | 319052.35 |
| Egreso | money | Monto de egreso (0 si es ingreso) | 6.96 |
| Concepto | varchar(80) | Descripcion del movimiento | 'VENTA DIARIA 27/11/2024' |
| **Clase** | varchar(50) | **Clasificacion del movimiento** | 'VENTA DIARIA' |
| TipoEgreso | varchar(20) | Forma de pago del egreso | 'TRANSFERENCIA', 'CHEQUE', 'NA' |
| FPago | varchar(20) | Forma de pago (ingresos) | 'Efectivo', null |
| **Folio** | int | **Consecutivo GLOBAL** (compartido entre todas las cuentas) | 93305 |
| NumPoliza | int | Numero de poliza contable vinculada | 92316 |
| Conciliada | bit | Conciliado con estado de cuenta | 1/0 |
| ConciliadaCapturo | varchar(20) | Quien concilio | null |
| Proveedor | varchar(6) | Clave proveedor (egresos tipo 3) | '001081' |
| ProveedorNombre | varchar(60) | Nombre proveedor | 'BANCO REGIONAL SA...' |
| PagoAfectado | bit | Si el pago ya afecto contabilidad | 1/0 |
| PagosVarios | bit | Si es egreso varios (manual) | 0 |
| Estatus | varchar(20) | Estado | 'Afectado', 'Traspaso', null |
| Paridad | money | Tipo de cambio | 1.0000 |
| Moneda | varchar(7) | Moneda | 'PESOS' |
| Cia | varchar(6) | Compania | 'DCM' |
| Fuente | varchar(20) | Fuente del sistema | 'SAV7-CHEQUES' |
| Oficina | varchar(6) | Oficina contable | '01' |
| CuentaOficina | varchar(10) | Centro de costo | '01' |
| TipoPoliza | varchar(15) | Tipo de poliza | 'EGRESO', 'INGRESO', 'DIARIO' |
| NumFactura | varchar(50) | Numero de factura (ingresos venta) | 'D-68281', null |
| Capturo | varchar(20) | Usuario que capturo | 'SALMA VAZQUEZ' |
| Referencia | varchar(50) | Referencia adicional | 'TRASPASO AUTOMATICO' |
| TotalLetra | text | Monto en palabras | '(* DIECISIETE PESOS...)' |
| Saldo | money | Saldo (siempre 0 en datos vistos) | 0 |
| FechaMov | datetime | Fecha del movimiento bancario real | 2024-11-28 |
| BancoDestino | varchar(20) | Para traspasos: banco destino | 'BANREGIO' |
| CuentaDestino | varchar(20) | Para traspasos: cuenta destino | '055003730017' |
| ExportaBanco | bit | Si fue exportado a layout bancario | 0 |
| Sucursal | int | Sucursal | 5 |
| LeyendaEspecial | varchar(100) | Leyenda para cheques | 'PARA ABONO EN CUENTA...' |
| RFC | varchar(16) | RFC (poco usado en bancos) | null |
| FacturaElectronica | varchar(50) | UUID CFDI vinculado | null |
| TimbradoFolioFiscal | varchar(50) | UUID timbrado | null |
| ParidadDOF | money | Paridad DOF (traspasos) | 20.0000 |
| Comentario | varchar(150) | Comentario adicional | null |
| PolizaCargos | money | Total cargos de la poliza | 0 |
| PolizaAbonos | money | Total abonos de la poliza | 0 |
| FichaDeposito | int | Numero de ficha deposito | 0 |
| Impresiones | smallint | Numero de impresiones | 0 |
| NumPolizaC | int | Poliza complementaria | 0 |

#### Significado del campo Tipo

| Tipo | Significado | Clases que usa |
|------|------------|----------------|
| **1** | Ingreso general | DEPOSITOS, VENTA DIARIA, TRASPASO (entrada), DEV/PAGO PROVEEDORES, COBRANZAS, AJUSTE BANCARIO, INTERESES, COMISIONES BANREGIO |
| **2** | Egreso varios (manual) | NOMINA, PAGO IMPUESTOS, PAGO IMSS, PAGO 3% NOMINA, ENTRE CUENTAS PROPIA, COMISIONES BANCARIAS, PAGOS A PROVEEDORES, FINIQUITO, AGUINALDO, RENTA, HONORARIOS, NO DEDUCIBLE, etc. |
| **3** | Egreso compra (con proveedor/factura) | PAGOS A PROVEEDORES, COMISIONES BANCARIAS, DEV/TRANSFERENCIAS, TARJETA CREDITO EMPR |
| **4** | Ingreso venta (deposito de venta) | VENTA DIARIA (exclusivamente) |

**Nota**: Tipo 2 y 3 pueden tener la misma Clase (ej: PAGOS A PROVEEDORES aparece en ambos).
- Tipo 2 = movimiento manual sin factura
- Tipo 3 = movimiento con factura/proveedor programado

#### Clases de Movimiento (29 distintas desde 2024)

| Clase | Volumen | Tipo(s) | Descripcion |
|-------|---------|---------|-------------|
| DEPOSITOS | 34,137 | 1 | Depositos en efectivo de ventas |
| PAGOS A PROVEEDORES | 13,377 | 2,3 | Pagos a proveedores |
| VENTA DIARIA | 4,527 | 1,4 | Ingresos por ventas (tarjeta/efectivo) |
| TRASPASO | 946 | 1 | Contrapartida de traspaso (entrada) |
| COMISIONES BANCARIAS | 823 | 2,3 | Comisiones cobradas por banco |
| ENTRE CUENTAS PROPIA | 812 | 2 | Traspasos entre cuentas propias (salida) |
| NOMINA | 558 | 2 | Pago de nomina |
| FINIQUITO | 523 | 2 | Finiquitos |
| AJUSTE BANCARIO | 461 | 1 | Ajustes |
| DEV / TRANSFERENCIAS | 238 | 2,3 | Devoluciones de transferencias |
| NO DEDUCIBLE | 224 | 2 | Gastos no deducibles |
| DEV/PAGO PROVEEDORES | 191 | 1 | Devolucion de pagos a proveedores |
| PAGO SERVICIOS | 152 | 2 | Servicios |
| PAGO IMPUESTOS | 90 | 2 | Impuestos |
| AJUSTE | 88 | 2 | Ajustes manuales |
| TARJETA CREDITO EMPR | 74 | 2,3 | Tarjeta empresarial |
| INTERESES | 73 | 1 | Intereses bancarios |
| REPOSICION DE GASTOS | 59 | 2 | Reposicion caja chica |
| COMISIONES BANREGIO | 30 | 1,2 | Comisiones especificas Banregio |
| PAGO IMSS | 25 | 2 | Seguro social |
| PAGO 3% NOMINA | 24 | 2 | Impuesto estatal sobre nomina |
| RENTA | 12 | 2 | Arrendamiento |
| AGUINALDO | 8 | 2 | Aguinaldo |
| COBRANZAS | 4 | 1 | Cobros directos |
| DEVOLUCION HACIENDA | 2 | 1 | Devolucion de IVA |
| SEGUROS Y FIANZAS | 2 | 2 | Seguros |
| CREDITO REVOLVENTE | 1 | 2 | Creditos |
| HONORARIOS | 1 | 2 | Honorarios profesionales |
| TARJETAS | 1 | 1 | Tarjetas (poco usado) |

---

### 2.2 SAVCheqPMF — Facturas Vinculadas a Movimientos

**Pantalla ERP**: Parte inferior del modulo de bancos (grid de facturas en ingresos/egresos)

**PK**: `Banco + Cuenta + Age + Mes + Folio + Sucursal + Serie + NumFactura` (8 campos)

| Campo | Tipo SQL | Descripcion | Ejemplo |
|-------|----------|-------------|---------|
| Banco | varchar(20) | Banco | 'BANREGIO' |
| Cuenta | varchar(20) | Cuenta | '038900320016' |
| Age | int | Ano | 2024 |
| Mes | int | Mes | 11 |
| **Folio** | int | **FK a SAVCheqPM.Folio** | 93239 |
| Sucursal | int | Sucursal | 5 |
| **Serie** | varchar(6) | Serie de la factura | 'FD' |
| **NumFactura** | varchar(50) | Numero de factura | '14763' |
| Ingreso | decimal(18,4) | Monto aplicado de esta factura | 315055.11 |
| FechaFactura | datetime | Fecha de la factura | 2024-11-27 |
| TipoFactura | varchar(20) | 'GLOBAL' o 'INDIVIDUAL' | 'GLOBAL' |
| MontoFactura | decimal(18,4) | Monto total de la factura | 457543.12 |
| SaldoFactura | decimal(18,4) | Saldo pendiente despues del cobro | 0 |
| Dia | int | Dia del movimiento | 29 |
| FechaIngreso | datetime | Fecha en que se registro | 2024-11-29 |
| PosterioralDeposito | bit | Si se registro despues del deposito | 0 |

**Patron observado en venta diaria**:
- Un Folio tiene N facturas: varias INDIVIDUAL + 1 GLOBAL al final
- El Ingreso de cada factura es la parte que se aplica en ESE movimiento
- La suma de Ingresos = Ingreso del movimiento padre en SAVCheqPM

---

### 2.3 SAVCheqP — Periodo de Chequera

**Pantalla ERP**: Cabecera del modulo (Banco, Cuenta, Ano, Mes, Saldo Inicial)

**PK**: `Banco + Cuenta + Age + Mes`

| Campo | Tipo SQL | Descripcion | Ejemplo |
|-------|----------|-------------|---------|
| Banco | varchar(20) | Banco | 'BANREGIO' |
| Cuenta | varchar(20) | Cuenta | '055003730017' |
| Age | int | Ano | 2026 |
| Mes | int | Mes | 1 |
| SaldoInicial | money | Saldo inicio de periodo | 999999999 |
| SaldoFinal | money | Saldo fin de periodo | 999999999 |
| Estatus | varchar(20) | Estado del periodo | 'ABIERTO' |
| SaldoInicialEdoCta | money | Saldo segun estado de cuenta banco | 0 |
| Capturo | varchar(20) | Quien abrio el periodo | 'SALMA VAZQUEZ' |

---

### 2.4 SAVCheq — Catalogo de Cuentas de Chequera

**Pantalla ERP**: Configuracion de cuentas bancarias

**PK**: `Banco + Cuenta`

| Campo | Tipo SQL | Descripcion | Ejemplo |
|-------|----------|-------------|---------|
| Banco | varchar(20) | Banco | 'BANREGIO' |
| Cuenta | varchar(20) | Numero de cuenta | '055003730017' |
| Moneda | varchar(7) | Moneda | 'PESOS' |
| **CuentaC** | varchar(20) | **Cuenta contable vinculada** | '1120' |
| **SubCuentaC** | varchar(20) | **Subcuenta contable** | '040000' |
| Cia | varchar(6) | Compania | 'DCM' |
| Fuente | varchar(20) | Fuente | 'SAV7-CHEQUES' |
| Oficina | varchar(6) | Oficina | '01' |
| CuentaOficina | varchar(10) | Centro de costo | '01' |
| RFC | varchar(16) | RFC de la empresa | 'DCM02072238A' |
| SolicitudPago | bit | Habilita solicitudes de pago | 1 |
| FormatoExportaBanco | int | Formato del layout | 3 |
| LeyendaEspecial | varchar(100) | Leyenda para cheques | 'PARA ABONO EN CUENTA...' |
| Sucursal | int | Sucursal | 5 |
| ValidaSAT | bit | Si valida contra SAT | 1/0 |

#### Cuentas Bancarias Activas (2024+)

| Banco | Cuenta | CuentaC | SubCuentaC | Nombre Contable | Referencia |
|-------|--------|---------|------------|-----------------|------------|
| BANREGIO | 055003730017 | 1120 | 040000 | BANREGIO F | EFECTIVO |
| BANREGIO | 038900320016 | 1120 | 060000 | BANREGIO T | TARJETA |
| BANREGIO | 055003730157 | 1120 | 070000 | BANREGIO GASTOS | TARJETA GASTOS |
| BANREGIO | 4364010637525164 | 1120 | 080000 | BANREGIO EMPRESARIAL 5164 | EMPRESARIAL |
| BANREGIO | 4364010673337565 | — | — | — | — |
| AZTECA | VIRTUAL | — | — | — | Cobros online |
| CAJA CHICA | 00000000000 | — | — | — | Caja chica |
| CAJA ANTICIPO PROVEE | 00000000000 | — | — | — | Anticipo proveedores |

---

## 3. Tablas de Polizas Contables

### 3.1 SAVPoliza — Lineas de Poliza Contable

**PK**: `Cia + Fuente + Poliza + Oficina + DocTipo + Movimiento`

| Campo | Tipo SQL | Descripcion | Ejemplo |
|-------|----------|-------------|---------|
| Cia | varchar(6) | Compania | 'DCM' |
| Fuente | varchar(20) | Fuente | 'SAV7-CHEQUES' |
| **Poliza** | int | **Numero de poliza (consecutivo global)** | 92316 |
| Oficina | varchar(6) | Oficina | '01' |
| **DocTipo** | varchar(15) | Tipo documento | 'CHEQUES', 'TRASPASOS', 'CHEQUESCANC' |
| **Movimiento** | int | **Linea de la poliza (1, 2, 3...)** | 1 |
| CuentaOficina | varchar(10) | Centro de costo | '01' |
| **Cuenta** | varchar(20) | **Cuenta contable** | '2110' |
| **SubCuenta** | varchar(20) | **Subcuenta contable** | '010000' |
| Nombre | varchar(50) | Nombre de la cuenta | 'PROVEEDORES GLOBAL' |
| **TipoCA** | int | **1=Cargo, 2=Abono** | 1 |
| **Cargo** | money | Monto cargo | 6.96 |
| **Abono** | money | Monto abono | 0 |
| Concepto | varchar(60) | Descripcion del movimiento | 'Prov:000506...' |
| DocFolio | int | Folio del movimiento bancario | 93305 |
| DocFecha | datetime | Fecha del documento | 2024-11-28 |
| TipoCambio | money | Tipo de cambio | 1 |
| Moneda | varchar(7) | Moneda | 'PESOS' |
| TipoPoliza | varchar(15) | Tipo | 'EGRESO', 'INGRESO', 'DIARIO' |
| Capturo | varchar(20) | Usuario | 'SALMA VAZQUEZ' |
| DocSerie | varchar(6) | Serie del documento | '' |
| Sucursal | int | Sucursal | 5 |

#### DocTipo (Tipos de Documento en Polizas de Bancos)

| DocTipo | Volumen | Uso |
|---------|---------|-----|
| CHEQUES | 200,427 | Movimientos normales (egresos e ingresos) |
| TRASPASOS | 3,000 | Traspasos entre cuentas |
| CHEQUESCANC | 28 | Cancelaciones de cheques |

#### Generacion de NumPoliza

- **Poliza es un consecutivo global**: MAX(Poliza) + 1 de SAVPoliza WHERE Fuente = 'SAV7-CHEQUES'
- **Actual**: MAX = 124,200
- Una poliza tiene N movimientos (lineas) numerados 1, 2, 3...
- El NumPoliza de SAVCheqPM referencia esta poliza

---

### 3.2 SAVContabSaldo — Saldos Contables (Resumen Anual)

**Estructura**: Una fila por cuenta/ano con 24 columnas de saldos (EneCargos, EneAbonos... DicCargos, DicAbonos)

**Nota**: Esta tabla se actualiza como parte de un proceso del ERP. Es un RESUMEN, no transaccional. Probablemente se recalcula con "Regenerar Poliza Contable".

---

## 4. Tablas de Compras (Comisiones Bancarias)

### 4.1 SAVRecC — Recepciones / Facturas de Compras

Las comisiones bancarias se registran como facturas de compras:

| Campo | Valor para comisiones |
|-------|-----------------------|
| Serie | 'F' (factura) |
| NumRec | Consecutivo |
| Proveedor | '001081' |
| ProveedorNombre | 'BANCO REGIONAL' |
| Factura | DDMMAAAA (ej: '12012026' = 12 enero 2026) |
| MetododePago | 'PUE' |
| Estatus | 'Tot.Pagada' (despues de pagar) |
| TimbradoFolioFiscal | null (comisiones no tienen UUID) |
| Total | Suma del dia (ej: 8669.60) |

### 4.2 SAVRecD — Detalle de Recepciones

| Campo | Valor para comisiones |
|-------|-----------------------|
| Producto | '001002002' |
| Nombre | 'COMISION TERMINAL' |
| Cantidad | 1 |
| Costo | Subtotal (ej: 7473.79) |
| PorcIva | 16 |
| Unidad | 'PZA' |

---

## 5. Tablas de Cobranza (Cobros a Clientes)

### 5.1 SAVFactCob — Cobros de Facturas

| Campo | Tipo SQL | Descripcion | Ejemplo |
|-------|----------|-------------|---------|
| Serie | varchar(6) | Serie de la factura | 'FC', 'D' |
| NumFac | int | Numero de factura | 1582 |
| Cobro | int | Numero de cobro | 76648 |
| Cliente | varchar(6) | Clave cliente | '999999' (VENTAS DE CONTADO) |
| Fecha | datetime | Fecha de cobro | 2026-01-24 |
| Monto | money | Monto cobrado | 136.89 |
| FPago | varchar(20) | Forma de pago | 'Efectivo', 'Transferencia' |
| Banco | varchar(20) | Banco referencia | '' |
| BancoDeposito | varchar(20) | Banco de deposito | 'BANREGIO' |
| BancoCuenta | varchar(20) | Cuenta del deposito | '055003730017' |
| Estatus | varchar(20) | Estado | 'Cobrado', 'Cancelado' |
| CobroMultiple | int | ID de cobro multiple | 69461 |
| Parcialidad | int | Numero de parcialidad | 1 |
| SaldoAnterior | money | Saldo antes del cobro | 916.44 |
| SaldoPendiente | money | Saldo despues del cobro | 779.55 |

---

## 6. Catalogos Auxiliares

### 6.1 SAVCheqPMCE — Clases de Egreso (24 entradas)

Cada clase puede tener una cuenta contable predeterminada.

| Nombre | CuentaOficina | Cuenta | SubCuenta | TipoOperacion | GeneraDIOT |
|--------|---------------|--------|-----------|---------------|------------|
| AGUINALDO | 01 | 6200 | 030000 | 00 | false |
| COMISIONES BANCARIAS | 01 | 2120 | 020000 | 85 | true |
| COMISIONES BANREGIO | 01 | 2120 | 020000 | 85 | true |
| DEV / TRANSFERENCIAS | 01 | 2120 | 020000 | 00 | false |
| HONORARIOS | — | — | — | 03 | true |
| NO DEDUCIBLE | 01 | 6300 | 020000 | 00 | false |
| NOMINA | — | — | — | 00 | false |
| PAGO 3% NOMINA | 01 | 6200 | 850000 | 00 | false |
| PAGO IMPUESTOS | — | — | — | 00 | false |
| PAGO IMSS | — | — | — | 00 | false |
| PAGOS A PROVEEDORES | — | — | — | 85 | true |
| PAGO SERVICIOS | — | — | — | 85 | true |
| PENSION ALIMENTICIA | 01 | 2140 | 280000 | 00 | false |
| RENTA | — | — | — | 06 | true |
| REPOSICION DE GASTOS | — | — | — | 85 | true |
| SEGUROS Y FIANZAS | — | — | — | 85 | true |
| TARJETA CREDITO EMPR | — | — | — | — | false |

**Nota**: Las clases sin Cuenta/SubCuenta obtienen su cuenta contable de la poliza directamente.

### 6.2 SAVCheqPMCI — Clases de Ingreso (13 entradas)

| Nombre | CuentaOficina | Cuenta | SubCuenta |
|--------|---------------|--------|-----------|
| AJUSTE BANCARIO | 01 | 1220 | 010002 |
| CAJA CHICA | 01 | 1110 | 010000 |
| COMISIONES BANREGIO | 01 | 2120 | 020000 |
| DEPOSITOS | — | — | — |
| DEV/PAGO PROVEEDORES | 01 | 2120 | 020000 |
| DEVOLUCION HACIENDA | — | — | — |
| ENTRE CUENTAS PROPIA | — | — | — |
| INTERESES | — | — | — |
| IVA COMISIONES BANRE | 01 | 2120 | 020000 |
| TARJETAS | — | — | — |
| TRASPASO | — | — | — |
| **VENTA DIARIA** | **01** | **1210** | **010000** |
| COBRANZAS | — | — | — |

### 6.3 SAVTipoEgreso — Tipos de Egreso (7 valores)

| TipoEgreso | Volumen |
|------------|---------|
| NA | 39,452 (ingresos y movimientos sin tipo) |
| TRANSFERENCIA | 14,580 |
| INTERBANCARIO | 1,747 |
| EFECTIVO | 1,175 |
| TARJETA | 326 |
| CHEQUE | 181 |
| TRANSFERENCIA SPEI | 2 |

### 6.4 SAVBanco — Catalogo de Bancos (25 entradas)

Incluye: BANREGIO, AZTECA, BANAMEX, BANCOMER, BANORTE, HSBC, SANTANDER, SCOTIABANK, etc.
Los que se usan activamente: **BANREGIO**, **AZTECA**, **CAJA CHICA**, **CAJA ANTICIPO PROVEE**

---

## 7. Cuentas Contables Relevantes (SAVCuenta)

| Cuenta | SubCuenta | Nombre | Naturaleza | Uso |
|--------|-----------|--------|------------|-----|
| 1110 | 010000 | CAJA CHICA | DEUDORA | Caja chica |
| 1110 | 020000 | CAJA ANTICIPO PROVEEDORES | DEUDORA | Anticipos |
| **1120** | **040000** | **BANREGIO F** | **DEUDORA** | **Banco EFECTIVO (055003730017)** |
| **1120** | **060000** | **BANREGIO T** | **DEUDORA** | **Banco TARJETA (038900320016)** |
| 1120 | 070000 | BANREGIO GASTOS | DEUDORA | Gastos (055003730157) |
| 1120 | 080000 | BANREGIO EMPRESARIAL 5164 | DEUDORA | Empresarial |
| **1210** | **010000** | **CLIENTES GLOBAL** | **DEUDORA** | **Cuentas por cobrar clientes** |
| 1220 | 010002 | AJUSTES BANCARIOS | DEUDORA | Ajustes |
| **1240** | **010000** | **IVA ACREDITABLE AL 16% PTE PAGO** | **DEUDORA** | **IVA pendiente de pago** |
| **1246** | **010000** | **IVA ACREDITABLE PAGADO** | **DEUDORA** | **IVA ya pagado** |
| 1240 | 020000 | IEPS ACREDITABLES 8% PTE PAGO | DEUDORA | IEPS pendiente |
| 1246 | 020000 | IEPS ACREDITABLE PAGADO | DEUDORA | IEPS pagado |
| **2110** | **010000** | **PROVEEDORES GLOBAL** | **ACREEDORA** | **Cuentas por pagar proveedores** |
| **2120** | **020000** | **ACREEDORES DIVERSOS BANREGIO** | **ACREEDORA** | **Comisiones bancarias** |
| **2120** | **040000** | **ACREEDORES DIVERSOS NOMINA** | **ACREEDORA** | **Nomina** |
| 2120 | 050000 | HOMERO GARZA ACREEDORES DIVERSOS | ACREEDORA | Acreedor especifico |
| 2140 | 010000 | RETENCION I.M.S.S. | ACREEDORA | IMSS |
| 2140 | 020000 | RETENCION I.S.P.T. | ACREEDORA | ISR nomina |
| 2140 | 030000 | RVA PARA PAGO DE IMSS | ACREEDORA | Reserva IMSS |
| 2140 | 040000 | RVA. 5% INFONAVIT | ACREEDORA | INFONAVIT |
| 2140 | 070000 | RET ISR HONORARIOS | ACREEDORA | ISR honorarios |
| 2140 | 090000 | RET RESICO | ACREEDORA | RESICO |
| 2140 | 130000 | RVA.P PAGO 2% SAR | ACREEDORA | SAR |
| 2140 | 140000 | RET 10% ISR ARREND PTE PAGO | ACREEDORA | ISR arrendamiento |
| 2140 | 280000 | (PENSION ALIMENTICIA) | ACREEDORA | Pension |
| 2141 | 010000 | IVA ACUMULABRE COBRADO | ACREEDORA | IVA cobrado |
| 2146 | 010000 | IVA ACUMULABLE AL 16% PTE COBRO | ACREEDORA | IVA pte cobro |
| 2141 | 020000 | IEPS ACUMULABLE COBRADO | ACREEDORA | IEPS cobrado |
| 2146 | 020000 | IEPS ACUMULABLE AL 8% PTE COBRO | ACREEDORA | IEPS pte cobro |
| 6200 | 030000 | (AGUINALDO) | DEUDORA | Gasto aguinaldo |
| 6200 | 500000 | MULTAS | DEUDORA | Multas |
| 6200 | 850000 | (3% NOMINA) | DEUDORA | Impuesto estatal |
| 6300 | 020000 | (NO DEDUCIBLE) | DEUDORA | Gastos no deducibles |

---

## 8. Polizas por Tipo de Movimiento (Ejemplos Reales)

### 8.1 Egreso: Comision Bancaria (Tipo 3, Clase: COMISIONES BANCARIAS)

**Movimiento bancario**: Folio 93305, $6.96 egreso
**Poliza 92316** (4 lineas):

| Mov | Cuenta | SubCuenta | Nombre | Cargo | Abono | Concepto |
|-----|--------|-----------|--------|-------|-------|----------|
| 1 | 2110 | 010000 | PROVEEDORES GLOBAL | 6.96 | 0 | Prov:000506... Total Pago: 93305 |
| 2 | 1240 | 010000 | IVA ACREDITABLE AL 16% PTE PAGO | 0 | 0.96 | ...IVAPP... |
| 3 | 1246 | 010000 | IVA ACREDITABLE PAGADO | 0.96 | 0 | ...IVAP... |
| 4 | **1120** | **040000** | **BANREGIO F** | **0** | **6.96** | Banco: BANREGIO. Folio Pago: 93305 |

**Patron**: Cargo Proveedores + reclasificacion IVA + **Abono Banco**

### 8.2 Egreso: Nomina (Tipo 2, Clase: NOMINA)

**Movimiento**: Folio 93301, $25,943.40 egreso
**Poliza 92312** (2 lineas):

| Mov | Cuenta | SubCuenta | Nombre | Cargo | Abono |
|-----|--------|-----------|--------|-------|-------|
| 1 | **1120** | **040000** | **BANREGIO F** | **0** | **25,943.40** |
| 2 | 2120 | 040000 | ACREEDORES DIVERSOS NOMINA | 25,943.40 | 0 |

**Patron**: **Abono Banco** + Cargo Acreedores

### 8.3 Egreso: Pago Impuestos (Tipo 2, Clase: PAGO IMPUESTOS)

**Movimiento**: Folio 93284, $54,285.00 egreso
**Poliza 92295** (2 lineas):

| Mov | Cuenta | SubCuenta | Nombre | Cargo | Abono |
|-----|--------|-----------|--------|-------|-------|
| 1 | **1120** | **040000** | **BANREGIO F** | **0** | **54,285.00** |
| 2 | 6200 | 500000 | MULTAS | 54,285.00 | 0 |

**Patron**: **Abono Banco** + Cargo cuenta de gasto/impuesto

### 8.4 Ingreso: Venta Diaria (Tipo 4, Clase: VENTA DIARIA)

**Movimiento**: Folio 93239, $319,052.35 ingreso (BANREGIO 055003730017)
**Poliza 92252** (18 lineas):

Para la factura GLOBAL (FD-14763, $315,055.11):
| Mov | Cuenta | SubCuenta | Nombre | Cargo | Abono |
|-----|--------|-----------|--------|-------|-------|
| 1 | **1120** | **040000** | **BANREGIO F** | **315,055.11** | **0** |
| 2 | 1210 | 010000 | CLIENTES GLOBAL | 0 | 315,055.11 |
| 3 | 2141 | 010000 | IVA ACUMULABLE COBRADO | 0 | 835.60 |
| 4 | 2146 | 010000 | IVA ACUMULABLE PTE COBRO | 835.60 | 0 |
| 5 | 2141 | 020000 | IEPS ACUMULABLE COBRADO | 0 | 214.45 |
| 6 | 2146 | 020000 | IEPS ACUMULABLE PTE COBRO | 214.45 | 0 |

Para cada factura INDIVIDUAL (FD-14758, FD-14759, etc.):
| Mov | Cuenta | SubCuenta | Nombre | Cargo | Abono |
|-----|--------|-----------|--------|-------|-------|
| N | **1120** | **040000** | **BANREGIO F** | **monto** | **0** |
| N+1 | 1210 | 010000 | CLIENTES GLOBAL | 0 | monto |
| (N+2) | (2141/2146) | (si hay IEPS) | ... | ... | ... |

**Patron**: **Cargo Banco** + Abono Clientes + reclasificacion IVA/IEPS (solo en global)

### 8.5 Traspaso Entre Cuentas (Tipo 2, Clase: ENTRE CUENTAS PROPIA)

**Poliza 124200** (2 lineas) — genera DOS movimientos bancarios:
- SAVCheqPM: Tipo 2 (egreso) en cuenta origen
- SAVCheqPM: Tipo 1 (ingreso) en cuenta destino

| Mov | Cuenta | SubCuenta | Nombre | Cargo | Abono |
|-----|--------|-----------|--------|-------|-------|
| 1 | 1120 | 040000 | BANREGIO F | 1,400,000 | 0 |
| 2 | 1120 | 060000 | BANREGIO T | 0 | 1,400,000 |

**Patron**: Cargo cuenta destino + Abono cuenta origen

---

## 9. Secuencia de Operaciones SQL por Proceso

### 9.1 Para TODOS los movimientos bancarios

```
1. Obtener MAX(Folio) de SAVCheqPM → siguiente folio
2. INSERT en SAVCheqPM con todos los campos
3. Si tiene facturas → INSERT en SAVCheqPMF (una fila por factura)
4. Obtener MAX(Poliza) de SAVPoliza WHERE Fuente='SAV7-CHEQUES' → siguiente poliza
5. INSERT en SAVPoliza (N lineas segun el tipo de movimiento)
6. UPDATE SAVCheqPM SET NumPoliza = poliza_generada WHERE Folio = @folio
7. Si Conciliada = 1 → UPDATE SAVCheqPM SET Conciliada = 1
```

### 9.2 Generacion de Folio

```sql
-- Folio es un consecutivo GLOBAL (todas las cuentas comparten la secuencia)
SELECT ISNULL(MAX(Folio), 0) + 1 FROM SAVCheqPM
-- Actual: 125,753
```

### 9.3 Generacion de NumPoliza

```sql
-- Poliza es un consecutivo GLOBAL para fuente SAV7-CHEQUES
SELECT ISNULL(MAX(Poliza), 0) + 1 FROM SAVPoliza WHERE Fuente = 'SAV7-CHEQUES'
-- Actual: 124,200
```

---

## 10. Constantes y Valores Fijos

| Campo | Valor | Donde |
|-------|-------|-------|
| Cia | 'DCM' | Todas las tablas |
| Fuente | 'SAV7-CHEQUES' | SAVCheqPM, SAVPoliza, SAVCheq |
| Oficina | '01' | SAVCheqPM, SAVPoliza |
| CuentaOficina | '01' | SAVCheqPM, SAVPoliza |
| Moneda | 'PESOS' | Todas |
| Paridad | 1.0000 | SAVCheqPM (movimientos normales) |
| ParidadDOF | 20.0000 | SAVCheqPM (traspasos con afectacion) |
| Sucursal | 5 | Todas las tablas |
| TipoCambio | 1.0000 | SAVPoliza |
| RFC empresa | 'DCM02072238A' | SAVCheq |
| Proveedor banco | '001081' | SAVRecC (comisiones) |

---

## 11. Riesgos y Consideraciones

### 11.1 Riesgos Bajos (sin triggers)
- **No hay triggers**: Podemos escribir directo sin efectos secundarios ocultos
- **No hay stored procedures**: No hay logica de negocio en BD
- **No hay FKs explicitas**: Las relaciones son implicitas (por convencion de nombres)

### 11.2 Riesgos Medios
- **PK de SAVCheqPM usa HoraAlta**: Dos inserts en el mismo segundo/cuenta/dia/tipo colisionarian. Usar timestamps unicos.
- **Folio y Poliza son consecutivos**: Concurrencia podria causar duplicados. Usar transacciones.
- **SAVContabSaldo**: Tabla resumen que el ERP recalcula. Nuestros inserts en SAVPoliza NO la actualizan automaticamente. Puede requerir "Regenerar" desde el ERP.
- **SAVCheqP (periodo)**: Debe existir el periodo abierto antes de insertar movimientos.

### 11.3 Riesgos Altos
- **Saldos**: SAVCheqPM.Saldo siempre es 0 en los datos observados. Podria ser calculado por el ERP al consultar. NO intentar calcular.
- **Facturas en SAVRecC** (comisiones): Requiere que la factura de compras exista ANTES de crear el movimiento bancario tipo 3.
- **Cobros en SAVFactCob** (ventas clientes): Creados por el modulo Comercial. Si automatizamos, hay que respetar la cadena FactCob → CheqPM.

---

## 12. Datos de la Empresa

| Campo | Valor |
|-------|-------|
| Cia | DCM |
| RFC | DCM02072238A |
| Razon Social | DISTRIBUIDORA DE CARNES MARIA CRISTINA S.A. DE C.V. |
| Sucursal | 5 |
| Banco principal | BANREGIO |
| Cuenta efectivo | 055003730017 (CLABE: 058580550037300177) |
| Cuenta tarjeta | 038900320016 (CLABE: 058580389013001​68) |
| Cuenta gastos | 055003730157 |

---

## 13. Polizas Complejas (Observadas en Video, no verificadas en BD)

Fuente: Video de demostracion del ERP. Estas polizas no se verificaron contra BD porque
corresponden a movimientos especificos de noviembre 2025, pero los patrones de cuentas
SI fueron validados contra SAVCuenta.

### 13.1 Pago ISR e IVA (6 lineas)

| Mov | Cargo | Abono | Cuenta | Nombre |
|-----|-------|-------|--------|--------|
| 1 | $16,000.00 | | 1245/010000 | PAGO PROVISIONAL DE I.S.R. |
| 2 | $30,215.00 | | 2140/030000 | RVA PARA PAGO DE IMSS (*) |
| 3 | | $46,215.00 | 1120/040000 | BANREGIO F |
| 4 | $52,795.00 | | 2141/010000 | IVA ACUMULABLE COBRADO |
| 5 | | $232,157.00 | 1246/010000 | IVA ACREDITABLE PAGADO |
| 6 | $179,362.00 | | 1247/010000 | IVA A FAVOR |

(*) Nota: 2140/030000 en BD es "RVA PARA PAGO DE IMSS", en video dice "RETENCION I.S.P.T."
Puede ser uso multiproposito de la misma cuenta.

**Cuentas adicionales**:
- 1245/010000: PAGO PROVISIONAL DE I.S.R. (no explorada en Fase 2)
- 1247/010000: IVA A FAVOR (no explorada en Fase 2)

### 13.2 Pago IMSS e INFONAVIT (7 lineas)

| Mov | Cargo | Abono | Cuenta | Nombre |
|-----|-------|-------|--------|--------|
| 1 | $13,073.54 | | 2140/010000 | RETENCION I.M.S.S. |
| 2 | $79,000.16 | | 6200/070000 | I.M.S.S. |
| 3 | $25,722.43 | | 6200/028000 | APORTACION 2% S.A.R. |
| 4 | $82,681.60 | | 6200/360000 | CESANTIA Y VEJEZ |
| 5 | $64,305.89 | | 6200/050000 | 5% INFONAVIT |
| 6 | $35,837.85 | | 2140/270000 | RETENCION INFONAVIT |
| 7 | | $300,701.47 | 1120/040000 | BANREGIO F |

**Cuentas adicionales del grupo 6200 (gastos nomina)**:
- 6200/010000: SUELDOS Y SALARIOS
- 6200/020000: VACACIONES
- 6200/028000: APORTACION 2% S.A.R.
- 6200/030000: AGUINALDOS
- 6200/050000: 5% INFONAVIT / 3% NOMINAS
- 6200/060000: PRIMA VACACIONAL
- 6200/070000: I.M.S.S.
- 6200/240000: SEPTIMO DIA
- 6200/360000: CESANTIA Y VEJEZ
- 6200/670000: PRIMA DOMINICAL
- 6200/770000: BONO DE PUNTUALIDAD
- 6200/780000: BONO DE ASISTENCIA

### 13.3 Pago 3% Nomina (2 lineas)

| Mov | Cargo | Abono | Cuenta | Nombre |
|-----|-------|-------|--------|--------|
| 1 | $21,943.00 | | 6200/050000 | 3% NOMINAS |
| 2 | | $21,943.00 | 1120/040000 | BANREGIO F |

### 13.4 Devolucion IVA por Hacienda (2 lineas)

| Mov | Cargo | Abono | Cuenta | Nombre |
|-----|-------|-------|--------|--------|
| 1 | $120,616.00 | | 1120/040000 | BANREGIO F |
| 2 | | $120,616.00 | 1247/010000 | IVA A FAVOR |

---

## 14. Datos de Entrada del Proceso (Fuentes Externas)

### 14.1 Excel "Modulo Poliza {MES}"

Archivo compartido con el estado de cuenta bancario y anotaciones de conciliacion.

**Pestanas:**
| Pestana | Cuenta | Contenido |
|---------|--------|-----------|
| Banregio F | 055003730017 | Estado de cuenta cheques principal |
| BANREGIO GTS | 055003730157 | Estado de cuenta gastos |
| Banregio T | 038900320016 | Estado de cuenta TDC/Tarjetas |
| REP CAJA CHICA | — | Reporte caja chica |
| FACTURAS CANCELADAS | — | Facturas canceladas |
| SALDO A FAVOR | — | Control IVA a favor |

**Columnas del estado de cuenta (Banregio F):**

| Col | Contenido |
|-----|-----------|
| A | Fecha |
| B | Descripcion/Referencia |
| C | Cargos (EGRESOS) |
| D | Abonos (DEPOSITOS) |
| E | Saldo |
| F | Fecha de venta / Concepto conciliacion |
| G | Monto segun factura |
| H | IVA comision |
| I | Diferencia (falta/sobra saldo) |
| J | Fecha concatenada (DDMMAAAA) |

**Anotaciones de conciliacion:**
- "falta saldo en factura": deposito efectivo > factura global (centavos)
- "sobra saldo en factura": factura global > deposito efectivo
- "comisiones XX": suma de comisiones SPEI del dia XX
- "PTE APLICAR PAGO": pago pendiente de conciliar

### 14.2 Google Sheets "NOVIEMBRE INGRESOS 2025"

Hojas con pestanas por dia (1-27). Estructura por dia:
- **CORTE DEL DIA**: Cortes "Z" de cada caja registradora
- **VENTAS DEL DIA**: Facturas individuales con importes
- **FACTURA GLOBAL**: Numero y monto de factura global
- **DEPOSITOS SISSA**: Concepto, fecha, importe
- **INGRESOS**: Efectivo recibido (desglose por denominacion), importe TDC, folio SISSA

### 14.3 Estado de Cuenta BANREGIO (descarga web banking)

**Patrones de transaccion en el estado de cuenta:**

| Patron | Tipo | Ejemplo |
|--------|------|---------|
| Deposito en efectivo | Ingreso | Venta diaria efectivo |
| (NB) Recepcion de cuenta | Ingreso | Traspaso entre cuentas |
| NOMINA - PAGO DE NOMINA | Egreso | Nomina quincenal |
| (BE) Traspaso a cuenta | Egreso | Pago a proveedor o traspaso |
| TT/TV/TW + NNN SPEI | Egreso | Pago SPEI a proveedor |
| Comision Transferencia | Egreso | Comision SPEI ($6.00) |
| IVA de Comision Transferencia | Egreso | IVA comision ($0.96) |
| ABONO VENTAS TDC/TDD | Ingreso | Liquidacion ventas tarjeta |
| COMISION POR VENTAS TDC/TDD | Egreso | Comision ventas tarjeta |
| Pago Tarjeta Credito | Egreso | Pago TDC corporativa |
| Pago de Servicio | Egreso | Pago servicios (luz, agua) |

### 14.4 CSV Layout BANREGIO (exportacion de pagos)

Archivo generado por SAV7: `EG-BANREGIO-DCM-{AAAAMMDD}-{HHMMSS}.CSV`

| Columna | Contenido |
|---------|-----------|
| Operacion | 0 o S |
| ClaveIDProv | ID proveedor |
| CuentaOrigen | 550037300 (cuenta sin digito verificador) |
| CuentaDestino | CLABE del proveedor |
| Importe | Monto |
| Referencia | Numero factura |
| Descripcion | Texto libre |
| RFCOrdenante | DCM0207223 (truncado) |
| IVA | Monto IVA |
| FechaAplicacion | Fecha |
| Instruccion | — |
| ClaveTipoCambio | — |

---

## 15. Reglas de Matching para Conciliacion

### 15.1 Depositos efectivo vs Venta Diaria
- **Tolerancia**: centavos ($0.01 - $0.50)
- **Desfase temporal**: deposito dia X = venta dia X-1 o X-2
- **Anotacion ERP**: "falta saldo en factura" / "sobra saldo en factura"

### 15.2 Transferencias SPEI vs Pagos a Proveedores
- **Match exacto** por monto
- **Referencia cruzada** con numero de factura en concepto SPEI

### 15.3 Abonos TDC vs Ventas tarjeta
- Se restan comisiones TDC del monto bruto
- Patron: ABONO VENTAS TDC - COMISION POR VENTAS TDC - IVA COMISION

### 15.4 Comisiones SPEI
- $6.00 por transferencia + $0.96 IVA (16%) = $6.96 total
- Se agrupan por dia en una sola factura de compras (SAVRecC)

---

## 16. Usuarios y Modulos del ERP

| Usuario | Modulo | Rol |
|---------|--------|-----|
| hjuarez | SAVBancos | Tesorero/Contabilidad |
| salma | SAVCompras | Compras/Pagos |
| DULCE | SAVComercial | Comercial/Cobranza |

| Modulo SAV7 | Funcion |
|-------------|---------|
| SAVBancos | Gestion cuentas bancarias, chequera, conciliacion |
| SAVCompras | Proveedores, recepciones, pagos |
| SAVComercial | Clientes, facturacion, cobranza |
| SAVConsolida | Consolidacion de remisiones |
| SAVContabilidad | Contabilidad general |

---

## 17. Prioridades de Automatizacion

| # | Proceso | Complejidad | Frecuencia | Prioridad |
|---|---------|-------------|------------|-----------|
| 1 | Captura Venta Diaria (efectivo + TDC) | Media | Diario | ALTA |
| 2 | Captura comisiones bancarias | Baja | Diario | ALTA |
| 3 | Conciliacion banco vs SAV7 | Alta | Diario | MEDIA |
| 4 | Captura poliza NOMINA | Media | Quincenal | MEDIA |
| 5 | Captura impuestos (ISR/IVA/IMSS) | Media | Mensual | BAJA |
| 6 | Traspasos entre cuentas | Baja | Variable | BAJA |

---

## 18. Procedimientos Detallados de Captura (Fuente: PDF proceso_conciliacion_ingresos.pdf)

Documento formal de 19 paginas con capturas de pantalla del ERP (noviembre 2025).
Describe 7 procedimientos de captura de ingresos. Los campos y valores fueron verificados
visualmente contra las pantallas del ERP.

### 18.1 Flujo General (aplica a todos los procedimientos)

```
1. DESCARGA estado de cuenta bancario (Excel desde web banking BANREGIO)
2. INTEGRAR al archivo "MODULO POLIZA _{mes}" (Excel con 3 pestanas por cuenta)
3. PREPARAR informacion: cruzar cortes de venta (Google Sheets) con estado de cuenta
4-7. CAPTURA en SAV7 segun tipo de movimiento (ver abajo)
```

### 18.2 Procedimiento 4: Ventas con Tarjeta (cuenta 038900320016)

**Contexto**: Los abonos TDC/TDD aparecen en el estado de cuenta de la cuenta tarjeta.
Cada abono es un movimiento separado en SAV7.

**Campos SAVCheqPM (constantes en negrita):**
| Campo | Valor | Notas |
|-------|-------|-------|
| Banco | **BANREGIO** | Constante |
| Cuenta | **038900320016** | Constante: cuenta tarjeta |
| Tipo | **4** | Constante: Ingreso/Venta Diaria |
| Dia | (variable) | Dia del ESTADO DE CUENTA, NO dia de corte venta |
| Clase | **VENTA DIARIA** | Constante |
| Concepto | **"VENTA DIARIA {DD/MM/AAAA}"** | Patron fijo. La fecha es del CORTE de venta |
| FPago | **Tarjeta Credito** | Constante para esta cuenta |
| Ingreso | (variable) | Monto del abono TDC |
| Conciliada | **1** | Constante: se marca conciliado antes de guardar |
| Paridad | **1.0000** | Constante |
| Suc | **5** | Constante |

**Facturas (SAVCheqPMF) — constantes en negrita:**
- Se registran TODAS las facturas individuales del dia + la factura global
- Serie = **"FD"** (constante)
- TipoFactura = **"INDIVIDUAL"** (facturas individuales) o **"GLOBAL"** (factura global)
- Suc = **5** (constante)
- El campo Ingreso en cada factura individual = monto de esa factura (variable)
- El campo Ingreso en la factura global = suma de ventas con tarjeta del dia (variable)
- Fecha Factura = fecha del corte de venta (variable)
- Monto Factura = monto total de la factura (variable, puede ser mayor que Ingreso)
- Saldo Factura = $0.00 (factura queda saldada)

**Origen de datos para facturas (confirmado con usuario):**
- El **# Factura** NO viene del estado de cuenta bancario, viene del **reporte de ventas de Tesoreria** (Google Sheets)
- Es un numero de factura global que se aplica a TODOS los movimientos TDC del dia
- Las facturas individuales se identifican cruzando estado de cuenta con reporte de Tesoreria

**Regla de validacion TDC:**
La suma de todos los abonos TDC del dia en el estado de cuenta
debe ser IGUAL al total de ventas con tarjeta del corte de venta de Tesoreria.

**Estructura de facturas por movimiento:**
```
N facturas INDIVIDUAL (una por ticket/factura del corte de venta)
1 factura GLOBAL (la factura global del dia)
Todas las facturas se repiten en cada movimiento TDC del dia.
```

**Poliza generada (6 lineas por cada abono TDC) — VERIFICADO EN BD:**
| Mov | Cargo/Abono | Cuenta | SubCuenta | Concepto poliza | Descripcion |
|-----|-------------|--------|-----------|-----------------|-------------|
| 1 | **Cargo** = monto abono | **1120** | **060000** | "Banco: BANREGIO. FactG: FD-{NUM} FolioI: {FOLIO}" | Banco tarjeta |
| 2 | **Abono** = monto abono | **1210** | **010000** | "Clase:VENTA DIARIA Cob.FactG: FD-{NUM}..." | Clientes generales |
| 3 | **Abono** = IVA factura | **2141** | **010000** | "Clase:VENTA DIARIA Iva.FactG: FD-{NUM}..." | IVA trasladado |
| 4 | **Cargo** = IVA factura | **2146** | **010000** | "Clase:VENTA DIARIA Iva.FactG: FD-{NUM}..." | IVA por cobrar |
| 5 | **Abono** = IEPS factura | **2141** | **020000** | "Clase:VENTA DIARIA Ieps.FactG: FD-{NUM}..." | IEPS trasladado |
| 6 | **Cargo** = IEPS factura | **2146** | **020000** | "Clase:VENTA DIARIA Ieps.FactG: FD-{NUM}..." | IEPS por cobrar |

**NOTA**: Los movs 3-6 son IVA e IEPS de la factura global (NO comisiones bancarias como
sugeria el PDF visualmente). Los montos de IVA/IEPS pueden ser $0.00 si no aplica.

**Resultado**: Se crea 1 movimiento tipo 4 POR CADA abono TDC del dia en el estado de cuenta.

### 18.3 Procedimiento 5: Ventas Efectivo + Global (cuenta 055003730017)

**Contexto**: Deposito en efectivo de la venta diaria. El deposito aparece dias despues
en el estado de cuenta.

**Campos SAVCheqPM (constantes en negrita):**
| Campo | Valor | Notas |
|-------|-------|-------|
| Banco | **BANREGIO** | Constante |
| Cuenta | **055003730017** | Constante: cuenta cheques principal |
| Tipo | **4** | Constante: Ingreso/Venta Diaria |
| Dia | (variable) | Dia del DEPOSITO en estado de cuenta |
| Clase | **VENTA DIARIA** | Constante |
| Concepto | **"VENTA DIARIA {DD/MM/AAAA}"** | Patron fijo. La fecha es del CORTE de venta |
| FPago | **Efectivo** | Constante para esta cuenta |
| Ingreso | (variable) | Monto del deposito en efectivo |
| Conciliada | **1** | Constante |
| Paridad | **1.0000** | Constante |

**Facturas (SAVCheqPMF):**
- Misma estructura que Proc.4: facturas individuales (INDIVIDUAL) + global (GLOBAL)
- Serie = **"FD"**, Suc = **5** (constantes)
- Se registran las MISMAS facturas del corte de venta (misma factura global)
- Ingreso de cada factura = monto de esa venta (variable)

**Poliza generada — ESTRUCTURA VARIABLE (verificado en BD):**

A diferencia de la cuenta tarjeta (siempre 6 lineas), la poliza de efectivo tiene
numero variable de lineas segun la cantidad de facturas:

```
Estructura de poliza para venta diaria en cuenta cheques:
  Movs 1-6: Factura GLOBAL (misma estructura que tarjeta)
    1: Cargo monto_global → 1120/040000 (banco cheques)
    2: Abono monto_global → 1210/010000 (clientes)
    3: Abono IVA_global   → 2141/010000
    4: Cargo IVA_global   → 2146/010000
    5: Abono IEPS_global  → 2141/020000
    6: Cargo IEPS_global  → 2146/020000
  Movs 7+: Facturas INDIVIDUALES (2 lineas c/u, algunas con IVA/IEPS extra)
    N:   Cargo monto_fact → 1120/040000 (banco cheques)
    N+1: Abono monto_fact → 1210/010000 (clientes)
    (opcionales si la factura individual tiene IVA/IEPS desglosado:)
    N+2: Abono IVA_fact   → 2141/010000
    N+3: Cargo IVA_fact   → 2146/010000
```

Datos verificados: 85 polizas de efectivo analizadas, rango 6-68 lineas.
Cuenta tarjeta: 143 polizas, TODAS con exactamente 6 lineas.

**REGLA DE VALIDACION**: La suma de Ventas del Dia + Global debe ser igual al deposito en efectivo.

**NOTA IMPORTANTE PARA AUTOMATIZACION**: La poliza la genera el ERP al hacer
"Regenerar Poliza Contable". Si la automatizacion escribe directamente a SAVPoliza,
debe replicar esta logica de N lineas por factura. Alternativa: insertar solo SAVCheqPM
+ SAVCheqPMF y encontrar forma de disparar la regeneracion de poliza.

### 18.4 Procedimiento 6: Cobros de Clientes por Transferencia (cuenta 055003730017)

**Contexto**: Clientes que pagan por transferencia bancaria. El proceso se inicia desde
el modulo SAV Comercial (Cobranza), NO desde Bancos.

**Proceso en SAV Comercial:**
1. Ingresar a Cobranza → Crea Cobro Multiple
2. Seleccionar cliente por clave
3. Identificar folio de factura pendiente
4. Doble clic en la factura → habilitar proceso de cobro
5. Clic en [Procesar]
6. Llenar campos de cobro:
   - Fecha de Cobro = fecha del ingreso en estado de cuenta
   - Forma de Pago = **Transferencia**
   - Banco = **BANREGIO**
   - Deposito a = **BANREGIO**
   - Cuenta Deposito = **055003730017**
   - Serie Cobro = **CP**
7. [Aceptar] → [Aceptar] → [Continuar]

**Resultado en SAVCheqPM (generado automaticamente por el modulo Comercial):**
| Campo | Valor | Notas |
|-------|-------|-------|
| Tipo | **1** | Constante: Ingreso/Deposito (NO tipo 4) |
| Clase | **DEPOSITOS** | Constante |
| Concepto | "CLIENTE: {CLAVE}-{NOMBRE} CM: {COBRO} FACT: {SERIE}-{NUM};" | Patron generado |
| Referencia | "NTE: {CLAVE}-{NOMBRE}" | Patron generado |
| Referencia2 | "FP: Transferencia B: BANREGIO Ref:" | Patron generado |
| #Factura | "CM: {COBRO}" | Patron generado |
| Ingreso | (variable) | Monto de la factura cobrada |
| Folio | (autogenerado) | Consecutivo global |
| NumPoliza | (autogenerado) | Consecutivo global |

**Para conciliar**: Doble clic en registro → Clic en [Conciliar] → [Guardar]
El campo C.B. cambia de vacio a "Si".

**NOTA CRITICA**: Este proceso afecta TAMBIEN la tabla SAVFactCob (cobros de facturas).
El modulo Comercial hace todos los INSERTs automaticamente. Para automatizar esto
via SQL directo habria que replicar lo que hace el modulo Comercial (mas complejo).

### 18.5 Procedimiento 7: Traspasos entre Cuentas

**Contexto**: Movimiento de dinero entre cuentas propias (ej: de tarjeta a cheques).
Genera un egreso en cuenta origen y un ingreso en cuenta destino.

**Proceso en SAV7:**
1. Posicionarse en la CUENTA DEL CARGO (origen del dinero)
2. Abrir periodo correspondiente
3. Tecla **F8** → ventana "Traspaso de Movimiento entre Cuentas"
4. Llenar:
   - Cantidad = monto del traspaso (variable)
   - Fecha = fecha del movimiento en estado de cuenta (variable)
   - Tipo = **Electronico** (constante)
   - Seleccionar chequera destino
5. [Aceptar] → [Aceptar] → [Continuar]

**Resultado — Registro de EGRESO (cuenta origen):**
| Campo | Valor | Notas |
|-------|-------|-------|
| Tipo | **2** | Constante: Egreso manual |
| Dia | (variable) | Dia del estado de cuenta |
| Egreso | (variable) | Monto del traspaso |
| TipoEgreso | **INTERBANCARIO** | Constante |
| Clase | **ENTRE CUENTAS PROPIAS** | Constante |
| Concepto | **"TRASPASO A BANCO: {BANCO} CUENTA: {CTA_DESTINO} MONEDA: PESOS"** | Patron fijo |
| Estatus | **Traspaso** | Constante |
| Conciliada | **1** | Constante |
| TipoPoliza | **DIARIO** | Constante |

**Resultado — Registro de INGRESO (cuenta destino):**
- Generado automaticamente como contrapartida
- Tipo = **1** (Ingreso), Clase = **TRASPASO**
- Concepto con patron similar referenciando la cuenta origen

**Poliza generada (2 lineas) — cuentas contables constantes:**
| Mov | Cargo/Abono | Cuenta | SubCuenta | Nombre | Notas |
|-----|-------------|--------|-----------|--------|-------|
| 1 | **Cargo** = monto | **1120** | **040000** | BANREGIO F | Cuenta contable de cta cheques |
| 2 | **Abono** = monto | **1120** | **060000** | BANREGIO T | Cuenta contable de cta tarjeta |

**NOTA**: Las cuentas contables en la poliza dependen de las cuentas bancarias involucradas.
Los valores 040000/060000 son para el traspaso tipico tarjeta→cheques.
Se obtienen de SAVCheq.CuentaC/SubCuentaC de cada cuenta bancaria.

### 18.6 Tipos de Movimiento del ERP (confirmado por PDF)

El dialogo "Tipo de Movimiento" del ERP muestra estas opciones:

| Opcion ERP | Tipo en BD | Descripcion |
|------------|-----------|-------------|
| Ingreso/Deposito | 1 | Ingreso general (cobros clientes, devoluciones, traspasos) |
| **Ingreso/Venta Diaria** | **4** | **Ventas (efectivo + TDC)** |
| Ingreso/Varios | 1 | Otros ingresos |
| Egreso/Cheque | 2 | Pago con cheque |
| Egreso/Varios | 2 | Egreso manual |
| Egreso/Compras/Cheque | 3 | Pago proveedor con cheque |
| Egreso/Compras/Transferencia | 3 | Pago proveedor transferencia |
| Egreso/Transferencia | 2 | Transferencia sin factura |

### 18.7 Campos de Referencia en SAVCheqPMF (detalle de PDF)

Campos visibles en la pestana "Facturas" del movimiento:

| Campo | Descripcion | Constante/Variable |
|-------|-------------|-------------------|
| Suc | Sucursal | **Constante: 5** |
| Serie | Serie de factura | **Constante: FD** |
| # Factura | Numero de factura | Variable |
| TipoFactura | Tipo: INDIVIDUAL o GLOBAL | **Constante por tipo** |
| Ingreso | Monto aplicado a este movimiento | Variable |
| Fecha Factura | Fecha de emision de la factura | Variable |
| Monto Factura | Monto total de la factura | Variable |
| Saldo Factura | Saldo restante despues de aplicar | Variable (normalmente $0.00) |
| PostDep | Post deposito | Normalmente vacio |
| Observac | Observaciones | Normalmente vacio |

### 18.8 Notas Importantes del PDF

1. **Dia del movimiento**: Siempre es el dia que aparece en el ESTADO DE CUENTA bancario,
   NO la fecha del corte de venta.

2. **Desfase TDC**: Las ventas con tarjeta se reflejan en el estado de cuenta de la
   cuenta tarjeta (038900320016) al **dia habil siguiente** del corte de venta.
   Ej: corte del dia 6 → abono en estado de cuenta dia 7.
   IMPORTANTE PARA MATCHING: para vincular un abono TDC con su corte de venta,
   buscar el corte de 1 dia habil antes de la fecha del abono.

3. **Concepto**: Siempre incluye la fecha del CORTE DE VENTA (dia anterior o 2 dias antes).

3. **Factura global**: Puede tener monto mayor al ingreso porque se aplica parcialmente
   en cada movimiento (parte TDC + parte efectivo).

4. **Cuenta 038900320016**: Solo recibe ingresos por ventas con tarjeta.
   Excepcion: devoluciones de comisiones bancarias (ocasional).

5. **Regenerar Poliza Contable**: Despues de habilitar "Conciliado", se va a pestana Poliza,
   clic derecho → "Regenerar Poliza Contable" → Aceptar → Continuar.
   Esto genera automaticamente las lineas de poliza (SAVPoliza).

6. **Traspasos**: Se usa F8, NO captura manual. El ERP genera ambos movimientos
   (egreso+ingreso) y la poliza de 2 lineas automaticamente.

7. **Cobros de clientes**: Se procesan desde modulo Comercial (Cobranza), NO desde Bancos.
   El movimiento bancario se genera automaticamente al procesar el cobro.
