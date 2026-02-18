# Proceso de Conciliacion Bancaria - ERP SAV7

Documento consolidado generado a partir del analisis de:
- Video de demostracion (~46 min) con transcripciones (Whisper + Google Drive)
- Documento formal `proceso_conciliacion_ingresos.pdf`
- 564 capturas de pantalla del ERP

---

## Resumen Ejecutivo

El proceso de **conciliacion bancaria** consiste en verificar que todos los movimientos
del estado de cuenta bancario (egresos e ingresos) esten correctamente registrados en
el ERP SAV7 (modulo de Bancos). Se trabaja con un Excel de conciliacion llamado
"Modulo Poliza [mes]" donde se integra la informacion del banco y se cruza contra el ERP.

**Empresa:** Distribuidora de Carnes Maria Cristina S.A. de C.V.
**RFC:** DCM020722J8A
**ERP:** SAV7 (Asesoft) - modulo SAVCheqPM (Movimientos en Periodo de Chequera)
**Cuentas bancarias activas:** 3

---

## Cuentas Bancarias

| # | Banco | Cuenta | Uso principal |
|---|-------|--------|---------------|
| 1 | BANREGIO | 055003730017 | Cuenta de cheques (principal) - pagos a proveedores, depositos efectivo |
| 2 | BANREGIO | (tarjeta debito) | Tarjeta de debito - traspasos desde cuenta principal |
| 3 | BANREGIO | 038900320016 | Cuenta de tarjetas/terminales - ventas con tarjeta credito/debito |

**Cia en ERP:** DCM
**Moneda:** PESOS
**Estatus tipico:** ABIERTO

---

## Modulos del ERP Involucrados

| Modulo | Ventana/Pantalla | Funcion |
|--------|------------------|---------|
| **Bancos** (SAVCheqPM) | Movimientos en Periodo de Chequera | Modulo central. Registra egresos, ingresos, traspasos |
| **Compras** | Pagos a Proveedores | Programacion de pagos (seleccion de facturas Serie F) |
| **Compras** | Recepciones | Crear facturas de comisiones bancarias |
| **Comercial** | Cobranza > Crea Cobro Multiple | Cobro de facturas a clientes especificos (Serie FC) |

---

## EGRESOS (Salidas de dinero)

### E1. Pagos a Proveedores (Semi-automatico via Programacion)

**Flujo completo - 5 pasos (solo el paso 4 es automatico):**

#### Paso 1: Programacion de Pagos (Modulo Compras) — MANUAL
- Entrar a **Compras > Pagos a Proveedores**
- Filtrar por **Serie F** (facturas)
- Ordenar por **fecha de pago / dias de vencimiento**
- Los rangos de credito vienen del **catalogo de proveedores**: contado, 8, 15, 21 dias
- Seleccionar facturas a pagar con **F8** (una por una)
- Pestanas visibles: Programados | Solicitud Pago | Postfechados | Pagados | Cancelados | N Credito Acreditadas | URL Cambio

**Columnas visibles en la pantalla (frame_00100):**
Serie | # Recep. | Pago | Fatturas Prov | Fecha Factura | Fecha Recep. | V. Venc. | Prov. | Empresa/Nombre Factura | Monto | Moneda | Estatus | Estatus | Saldo. Pag | F Cambio | Tipo | Forma de (pago)

#### Paso 2: Solicitud de Pago (Modulo Bancos) — MANUAL
- Ir a **Bancos > Solicitudes de Pago**
- Aparecen los pagos programados desde Compras
- Confirmar que si se van a pagar (agregar todos)

#### Paso 3: Egresos (Modulo Bancos) — MANUAL
- Ir a **Bancos > Egresos**
- Filtrar por fecha
- Aparecen las facturas programadas y confirmadas
- Verificar que el saldo coincida con la programacion
- Seleccionar las que se van a exportar

#### Paso 4: Generacion de Archivo Bancario — AUTOMATICO
- Se genera automaticamente un **TXT** (layout bancario) para subir al banco
- Se genera un **Excel** con el detalle de los pagos
- Se verifican cantidades y totales

#### Paso 5: Afectar en el Sistema — MANUAL
- Regresar a **Bancos > Egresos**
- **Conciliado y Afectar** en cada pago (uno por uno)
- Se generan **polizas de egreso** automaticamente
- Los registros se marcan como "Afectado" en la columna Estatus

**Nota:** Solo el paso 4 (generacion del layout TXT) es automatico. Los pasos 1, 2, 3 y 5
requieren intervencion manual: seleccionar facturas, confirmar pagos y afectar uno por uno.

**Datos observados en pantalla (frame_00200 - Modulo Bancos):**
- Ventana: "Movimientos en Periodo de Chequera"
- Campos cabecera: Banco, Cuenta, Moneda, Ano, Mes, Saldo Inicial, Estatus, Cia, RFC, Ult.Cheque
- Pestanas: Todos | Ingresos | Egresos | Fichas de Deposito | Solicitudes de Pago
- Sub-pestanas egresos: Detalle de Egresos | Egresos/Tipo Egreso | Egresos/Clase
- Columnas: C.B. | Dia | Ingreso | Egreso | Tipo | Saldo | Concepto | Tipo Egreso | Folio Mov | # Cheque | Estatus | A nombre de (Aplica para Egreso) | Clase | Referencia

---

### E2. Pago de Nomina (Manual)

**Tipo movimiento:** Egresos Varios
**Fuente de datos:** Reporte/lista de raya del sistema de nominas (PDF)

**Proceso:**
1. Modulo de Bancos > Posicionarse en cuenta correcta
2. Icono **[+]** > Tipo de movimiento: **Egresos Varios** > Aceptar
3. Llenar: **Fecha**, **Monto** (debe coincidir con estado de cuenta)
4. Tipo de egreso: **Transferencia**
5. Clase de movimiento: **Nomina**
6. Concepto descriptivo
7. **Guardar** (habilita la parte inferior para movimientos contables)
8. Agregar movimientos contables: cuenta de sueldos, banco, etc.
9. **Conciliado y Guardar**

---

### E3. Comisiones Bancarias (Manual - requiere factura en Compras)

**Frecuencia:** Diaria (el banco emite factura mensual, pero se registran dia a dia)
**Fuente de datos:** Estado de cuenta (comisiones + IVA del dia) + Excel de conciliacion

**Proceso en 2 fases:**

#### Fase A: Crear factura en Compras
1. Modulo **Compras > Recepciones** > Nueva factura de compra
2. Buscar proveedor: banco correspondiente
3. Relacionar factura con el dia
4. Producto: **"comision terminal"**
5. Cantidad: suma de comisiones del dia (calculada desde Excel)
6. Tasa de IVA correspondiente
7. **Guardar**
8. Ir a **Compras > Pagos** > Programar manualmente con **Ctrl+F8**

#### Fase B: Registrar en Bancos
1. Modulo **Bancos** > Icono **[+]**
2. Tipo de movimiento: **Egresos Compra Transferencia**
   (porque tiene factura desde el modulo de compras)
3. Buscar proveedor (banco)
4. Clase de movimiento: **Comisiones Bancarias**
5. **Guardar**
6. Buscar la factura programada en la parte inferior
7. **Conciliado y Afectar**

**Observacion frame_00200:** Se ven registros tipo "COMISIONES BANCARIAS 07/11/2025" en la lista de egresos.

---

### E4. Traspasos entre Cuentas (Manual)

**Proposito:** Mover saldos entre las cuentas bancarias propias.

**Proceso:**
1. Modulo de Bancos > Posicionarse en la **cuenta del cargo** (de donde sale)
2. Presionar **F8**
3. Llenar: **Cantidad**, **Fecha**, **Banco destino**
4. **Aceptar** > Confirmacion
5. Clase de movimiento: **Entre Cuentas Propias**
6. **Conciliado** > **Guardar**

**Nota:** El traspaso genera un movimiento en ambas cuentas (egreso en origen, ingreso en destino).

**Observacion frame_00200:** Se ven registros "TRASPASO A BANCO. BANREGIO CUENTA: 055003730017 MONEDA: PESOS INTERBANCARIO"

---

### E5. Pago de Impuestos (Manual)

**Tipo movimiento:** Egresos Varios
**Fuente de datos:** Resumen de liquidacion

**Proceso:**
1. Icono **[+]** > Tipo de movimiento: **Egresos Varios** > Aceptar
2. Llenar: **Monto**, **Fecha**, Concepto: **"Pago de impuestos"**
3. Tipo de egreso: Transferencia
4. **Guardar** (habilita parte inferior)
5. Agregar cuentas contables correspondientes a cada contribucion:
   - ISR retenciones
   - IVA retenido
   - 3% sobre nomina
   - IMSS e Infonavit
   - Contribuciones de seguridad social
6. Verificar que cargos = abonos
7. **Conciliado y Guardar**

**Observacion frame_00300:** Se ven registros:
- "PAGO IMPUESTOS (RETENCIONES) OCTUBRE 2025" - Clase: PAGO IMPUESTOS
- "PAGO IMPUESTOS ISR E IVA OCTUBRE 2025" - Clase: PAGO IMPUESTOS
- "PAGO 3% NOMINA OCTUBRE 2025" - Clase: PAGO 3% NOMINA
- "PAGO IMSS E INFONAVIT OCTUBRE 2025" - Clase: PAGO IMSS

---

## INGRESOS (Entradas de dinero)

### I1. Ventas con Tarjeta (Cuenta de Terminales: 038900320016)

**Fuente de datos:**
- **Reporte de ventas** (compartido por Tesoreria via Google Drive)
- **Estado de cuenta** bancario
- Los depositos se reflejan al **dia habil siguiente** de la venta

**Composicion del reporte de ventas:**
- Factura global (Serie FD)
- Facturas individuales
- Cantidades con tarjeta (credito y debito por separado)

**Proceso:**
1. Identificar en estado de cuenta los abonos de ventas con tarjeta del dia
   (ej: "ABONO VENTAS TDC_8996711" o "ABONO VENTAS TDD_8996711")
2. Clasificar por color en el Excel para identificar ventas del dia
3. Modulo **Bancos** > Cuenta de tarjetas (038900320016)
4. Icono **[+]** > Tipo movimiento: **Ingreso Venta Diaria** > Aceptar
5. Llenar:
   - **Fecha**: fecha del ingreso bancario (dia posterior a la venta)
   - **Clase de movimiento**: Venta Diaria
   - **Concepto**: "Venta diaria del dia [fecha del corte de venta]"
   - **Forma de pago**: segun estado de cuenta (**Tarjeta de Credito** o **Tarjeta de Debito**)
6. **Guardar** (habilita parte inferior)
7. Icono **[+]** en la parte inferior
8. Llenar: **# Factura** (serie FD, numero de factura global del Drive), **Tipo Factura**, **Ingreso** (monto del estado de cuenta)
9. El sistema resta automaticamente del saldo de la factura global
10. **Conciliado** > **Guardar**
11. Ir a **Polizas** > clic derecho > **Regenerar Poliza Contable** > Aceptar > Continuar
12. Repetir para cada abono del dia

**IMPORTANTE:** La poliza contable NO se genera automaticamente para ventas. Hay que regenerarla manualmente.

**Observacion frames_00350/00450:** Se ven en la cuenta de tarjetas:
- Columnas: C.B. | Dia | Ingreso | Egreso | Tipo | Saldo | Concepto
- Registros tipo "VENTA DIARIA 06/11/2025" con ingresos variables
- Tambien "COMISIONES POR VENTAS TDC_8996711" e "IVA COMISION POR VENTAS TDC_8996711"

---

### I2. Ventas en Efectivo (Depositos - Cuenta: 055003730017)

**Fuente de datos:**
- **Reporte de ventas** (Drive - Tesoreria)
- **Estado de cuenta** bancario
- Los depositos en efectivo aparecen **1-2 dias despues** de la venta

**Composicion de la poliza de venta en efectivo:**
La poliza se compone de:
1. **Facturas individuales** del dia (una por una) - TipoFactura: Individual
2. **Factura global** con el remanente - TipoFactura: Global
La suma de ventas individuales + global = deposito en efectivo del estado de cuenta

**Proceso:**
1. Verificar en Drive que el deposito reportado por Tesoreria coincida con estado de cuenta
2. Modulo **Bancos** > Cuenta principal (055003730017) > Mes correspondiente
3. **[+]** > Tipo movimiento: **Ingreso Venta Diaria** > Aceptar
4. Llenar:
   - **Fecha**: fecha del ingreso bancario
   - **Clase de movimiento**: Venta Diaria
   - **Concepto**: "Venta diaria del dia [fecha del corte]"
   - **Forma de pago**: **Efectivo** (porque son depositos en efectivo)
5. **Guardar** > habilita parte inferior
6. Agregar cada factura individual del dia:
   - **[+]** > # Factura, Tipo Factura: **Individual**, Importe
7. Al final agregar la **factura global** con el remanente que queda
   - Tipo Factura: **Global**
   - El sistema calcula automaticamente el saldo restante
8. **Conciliado** > **Guardar**
9. **Polizas** > clic derecho > **Regenerar Poliza Contable**

---

### I3. Cobros a Clientes Especificos (Transferencias - Cuenta: 055003730017)

**Contexto:** Clientes que requieren factura con RFC especifico. Las facturas se
generan en el **Modulo Comercial** (no en Ventas). Serie: **FC** (facturas timbradas).

**Proceso:**
1. Identificar el ingreso por transferencia en el estado de cuenta
2. Modulo **Comercial** > **Cobranza** > **Crea Cobro Multiple**
3. Seleccionar el cliente del listado
4. Identificar folio de factura serie **FC** (las timbradas)
5. Doble clic en la factura > **Procesar**
6. Llenar campos de cobro:
   - **Fecha de cobro**: segun estado de cuenta
   - **Forma de pago**: Transferencia
   - **Banco**: BANREGIO
   - **Cuenta de deposito**: segun estado de cuenta (ej: 055003730017)
7. **Afectar**
8. El registro aparece automaticamente en **Bancos > Ingresos**
9. Ir a Bancos > buscar el registro > **Conciliado y Guardar**
10. Verificar que aparezca "Si" en columna C.B. (Conciliacion Bancaria)

---

### I4. Ingresos Varios (Devoluciones, etc.)

**Frecuencia:** Rara vez. Ejemplo: devolucion de IVA por Hacienda.

**Proceso:**
1. Posicionarse en la cuenta correcta
2. Icono **[+]** > **Ingresos Varios**
3. Llenar: **Fecha**, Concepto (ej: "Devoluciones Hacienda"), Forma de pago: Transferencia
4. **Guardar** > habilita parte inferior para cuentas contables
5. Agregar cuentas contables:
   - Si NO tiene intereses: poner cantidad total
   - Si tiene intereses: usar cuenta de intereses por devoluciones de IVA
6. Verificar cargos = abonos
7. **Conciliado y Guardar**

---

### I5. Traspasos entre Cuentas (Ingreso automatico)

Los traspasos generados desde el egreso (E4) aparecen automaticamente como ingreso
en la cuenta destino. Solo requieren conciliacion.

---

## Clases de Movimiento Identificadas

### Egresos
| Clase | Tipo Movimiento | Proceso |
|-------|----------------|---------|
| PAGOS A PROVEEDORES | Egreso Compra Transferencia | Automatico (programacion) |
| NOMINA | Egresos Varios | Manual |
| PAGO 3% NOMINA | Egresos Varios | Manual |
| PAGO IMPUESTOS | Egresos Varios | Manual |
| PAGO IMSS | Egresos Varios | Manual |
| COMISIONES BANCARIAS | Egresos Compra Transferencia | Manual (con factura en Compras) |
| DEV / TRANSFERENCIAS | Egresos Varios | Manual (pagos rechazados) |
| ENTRE CUENTAS PROPIAS | Traspaso (F8) | Manual |

### Ingresos
| Clase | Tipo Movimiento | Proceso |
|-------|----------------|---------|
| VENTA DIARIA | Ingreso Venta Diaria | Manual (tarjeta y efectivo) |
| (cobro cliente) | Desde Modulo Comercial | Semi-automatico |
| (ingresos varios) | Ingresos Varios | Manual (raro) |
| (traspaso) | Automatico desde egreso | Solo conciliar |

---

## Tipos de Movimiento del Sistema

Observados en los frames del ERP:

| Tipo Movimiento | Tipo Egreso | Uso |
|-----------------|-------------|-----|
| Ingreso Venta Diaria | - | Ventas con tarjeta y efectivo |
| Egresos Varios | Transferencia | Nomina, impuestos, otros sin factura |
| Egresos Compra Transferencia | Transferencia | Pagos con factura (proveedores, comisiones) |
| Ingresos Varios | - | Devoluciones, otros ingresos sin factura |
| Traspaso (F8) | - | Entre cuentas propias |

---

## Series de Documentos

| Serie | Tipo | Donde se usa |
|-------|------|-------------|
| **F** | Factura consolidada (proveedor) | Compras > Programacion de pagos |
| **R** | Remision | Compras > Recepciones |
| **FD** | Factura global de venta diaria | Bancos > Ingresos venta diaria |
| **FC** | Factura comercial (cliente especifico) | Comercial > Cobranza |

---

## Herramientas Externas al ERP

| Herramienta | Uso |
|-------------|-----|
| **Excel "Modulo Poliza [mes]"** | Archivo base de conciliacion. Integra estado de cuenta bancario. Tiene hojas por cuenta. Formulado para llevar saldos. |
| **Google Drive** | Tesoreria comparte reportes de ventas (cortes diarios) |
| **Estado de cuenta bancario** | Se descarga de la banca en formato Excel |
| **Sistema de nominas** (externo) | Genera lista de raya / reporte de nomina |
| **Layout TXT bancario** | Generado automaticamente por SAV7 para subir pagos al banco |

---

## Flujo General de Conciliacion

```
Estado de Cuenta (Excel)
        |
        v
Excel "Modulo Poliza"  <--->  ERP SAV7 (Bancos)
        |                           |
        |-- Egresos                 |-- Pagos a proveedores (automatico)
        |   |-- Pagos proveedores   |-- Nomina (manual)
        |   |-- Nomina              |-- Comisiones (manual + compras)
        |   |-- Comisiones          |-- Impuestos (manual)
        |   |-- Impuestos           |-- Traspasos (F8)
        |   |-- Traspasos           |
        |                           |
        |-- Ingresos                |-- Venta diaria tarjeta (manual)
            |-- Ventas tarjeta      |-- Venta diaria efectivo (manual)
            |-- Depositos efectivo  |-- Cobro clientes (semi-auto)
            |-- Pagos clientes      |-- Ingresos varios (manual)
            |-- Devoluciones        |
            |-- Traspasos           |-- Conciliado + Guardar
```

---

## Reglas de Negocio Clave

1. **Ventas con tarjeta se reflejan al dia habil siguiente** de la venta en el estado de cuenta.
2. **Depositos en efectivo** aparecen 1-2 dias despues.
3. **La factura global (FD)** se compone de ventas con tarjeta + ventas individuales + efectivo.
4. **Polizas de venta requieren regeneracion manual** (clic derecho > Regenerar Poliza Contable).
5. **Comisiones bancarias** se registran diariamente aunque el banco factura mensualmente.
6. **Todo movimiento debe coincidir con el estado de cuenta** antes de marcar "Conciliado".
7. **C.B. = "Si"** indica que el movimiento esta conciliado con el banco.
8. **Folio Mov** es un consecutivo interno del ERP para cada movimiento bancario.
9. **Egresos de pago a proveedores** fluyen via programacion integrada en el ERP, pero la seleccion de facturas (paso 1) y la afectacion final (paso 5) son manuales. Solo la generacion del layout TXT (paso 4) es automatica.
10. **Todos los movimientos requieren intervencion manual** en mayor o menor grado.

---

## Campos Clave Observados en Pantalla

### Cabecera del Modulo de Bancos
- **Banco**: BANREGIO
- **Cuenta**: 055003730017 / 038900320016
- **Moneda**: PESOS
- **Ano**: 2025
- **Mes**: Noviembre
- **Saldo Inicial**: $999,999,999.00 (placeholder?)
- **Estatus**: ABIERTO
- **Cia**: DCM
- **RFC**: DCM020722J8A
- **Ult.Cheque**: 20 / 0

### Columnas de Movimientos
- **C.B.**: Conciliacion Bancaria (Si/vacio)
- **Dia**: Dia del mes
- **Ingreso**: Monto de ingreso
- **Egreso**: Monto de egreso
- **Tipo**: Numero tipo (1, 2, 3...)
- **Saldo**: Saldo acumulado
- **Concepto**: Descripcion del movimiento
- **Tipo Egreso**: TRANSFERENCIA, INTERBANCARIO, NA
- **Folio Mov**: Consecutivo interno (ej: 121731, 122004, etc.)
- **# Cheque**: Numero de cheque (si aplica)
- **Estatus**: Afectado / Traspaso / (vacio)
- **A nombre de / Aplica para Egreso**: Beneficiario
- **Clase**: PAGOS A PROVEEDORES, PAGO IMPUESTOS, NOMINA, etc.
- **Referencia**: Referencia adicional

---

## Prioridad de Automatizacion Sugerida

Basado en volumen y repetitividad:

| Prioridad | Proceso | Razon |
|-----------|---------|-------|
| **ALTA** | Pagos a proveedores (E1) | Diario/semanal, alto volumen de facturas, seleccion y afectacion manual uno por uno |
| **ALTA** | Ventas con tarjeta (I1) | Diario, 4-5 movimientos por dia, muy repetitivo |
| **ALTA** | Ventas en efectivo (I2) | Diario, multiples facturas individuales + global |
| **ALTA** | Comisiones bancarias (E3) | Diario, proceso largo (Compras + Bancos) |
| **MEDIA** | Pago de impuestos (E5) | Mensual, pero multiples cuentas contables |
| **MEDIA** | Pago de nomina (E2) | Quincenal/mensual |
| **BAJA** | Traspasos (E4) | Simple, poco frecuente |
| **BAJA** | Cobros clientes (I3) | Poco frecuente, semi-automatico ya |
| **BAJA** | Ingresos varios (I4) | Rara vez |

---

## Dudas y Ambiguedades Pendientes

1. **Tabla exacta de movimientos bancarios**: La ventana se llama "SAVCheqPM" - necesito identificar la tabla real en la BD (posiblemente SAVCheq, SAVMovBan, o similar).
2. **Como se genera el Folio Mov**: Parece ser un consecutivo global. Necesito verificar si hay trigger o se calcula.
3. **Relacion factura global FD con el modulo de Bancos**: Como se vincula el # Factura que se captura en la parte inferior con las tablas de facturas.
4. **Polizas contables**: Que tablas se afectan al "Regenerar Poliza Contable" y si es necesario replicar eso.
5. **Tipo (1, 2, 3...)**: Que significa el campo numerico "Tipo" en los movimientos.
6. **Campo Saldo Inicial en cabecera**: Muestra $999,999,999.00 - verificar si es un placeholder o valor real.
7. **Consecutivos de Folio Mov**: Rango observado 121731-122127 para noviembre 2025. Como se asigna.
8. **Diferencia entre "Afectar" y "Conciliado"**: Pagos a proveedores usan "Conciliado y Afectar". Otros solo "Conciliado y Guardar". Verificar diferencia en BD.
