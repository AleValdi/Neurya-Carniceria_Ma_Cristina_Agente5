# 🎬 Pipeline: Video de ERP → Contexto para Claude Code

Convierte automáticamente un video de demostración de un ERP en documentación técnica estructurada, lista para usarse como contexto en Claude Code.

## Qué hace

```
Video en Google Drive (1 hora de demo del ERP)
    │
    ├── 1. Descarga automática de Google Drive
    ├── 2. Recorta al segmento relevante (ej: desde min 24:30)
    ├── 3. Extrae frames cada 5 segundos
    ├── 4. Transcribe el audio con Whisper
    ├── 5. Sincroniza frames + transcripción en bloques de 30s
    └── 6. Genera archivos listos para análisis con Claude
```

## Instalación

```bash
# Dependencias de Python
pip install gdown openai-whisper srt Pillow anthropic

# ffmpeg (necesario)
brew install ffmpeg        # macOS
sudo apt install ffmpeg    # Ubuntu/Debian
choco install ffmpeg       # Windows
```

## Uso rápido

### Tu caso: video en Drive, proceso empieza en minuto 24:30

```bash
python video_to_context.py "https://drive.google.com/file/d/TU_ID_AQUI/view" --start 00:24:30
```

### Si sabes dónde termina la explicación

```bash
python video_to_context.py "https://drive.google.com/file/d/TU_ID_AQUI/view" --start 00:24:30 --end 00:52:00
```

### Si ya descargaste el video

```bash
python video_to_context.py --local ~/Downloads/video_proceso.mp4 --start 00:24:30
```

### Si tu máquina es lenta (usar modelo Whisper más rápido)

```bash
python video_to_context.py "URL" --start 00:24:30 --whisper-model medium
```

### Si quieres menos frames (1 cada 10 segundos)

```bash
python video_to_context.py "URL" --start 00:24:30 --fps 0.1
```

## Archivos generados

Después de correr el script, en `./output_pipeline/` encontrarás:

| Archivo | Para qué sirve |
|---------|----------------|
| `frames/` | Capturas de pantalla del video |
| `transcripcion.srt` | Transcripción con timestamps |
| `transcripcion.txt` | Transcripción en texto plano |
| `bloques_sincronizados.json` | Bloques frames+texto (para programar) |
| `bloques_para_claude.md` | Bloques legibles (para revisar) |
| `enviar_a_claude_api.py` | Script que envía todo a Claude API |
| `prompt_consolidacion.md` | Prompt para el análisis final |

## Paso 2: Análisis con Claude

### Opción A: Vía API (automático, recomendado)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python output_pipeline/enviar_a_claude_api.py
```

Esto envía cada bloque (frames + transcripción) a Claude con vision y genera:
- `analisis_por_bloque.json` — análisis detallado de cada bloque
- `CONTEXTO_PROCESO_ERP.md` — documento consolidado

### Opción B: Vía Claude.ai (manual, sin costo de API)

1. Crea un **Proyecto** en claude.ai
2. Sube los frames más relevantes como archivos del proyecto
3. Sube `transcripcion.txt` 
4. Usa el prompt de `prompt_consolidacion.md` para pedir el análisis

### Opción C: Vía Claude Code (interactivo)

1. Copia `CONTEXTO_PROCESO_ERP.md` a tu proyecto
2. Renómbralo o inclúyelo en tu `CLAUDE.md`
3. Claude Code lo leerá automáticamente

## Paso 3: Claude Code correlaciona automáticamente con la BD

Como Claude Code tiene el **MCP de SQL Server** conectado, no necesitas extraer el schema manualmente. El `CLAUDE.md` ya incluye instrucciones para que Claude Code:

1. Lea el análisis del video (`CONTEXTO_PROCESO_ERP.md`)
2. Explore autónomamente la BD buscando las tablas que corresponden a cada pantalla
3. Revise triggers, stored procedures, constraints y relaciones
4. Genere el mapa completo: Pantalla ERP → Tabla(s) → Campos → Secuencia SQL
5. Proponga la arquitectura de automatización
6. Implemente (con tu aprobación antes de cualquier escritura)

### Workflow en Claude Code

```bash
# 1. Copia los archivos a tu proyecto
cp output_pipeline/CONTEXTO_PROCESO_ERP.md ./tu_proyecto/
cp CLAUDE_md_template.md ./tu_proyecto/CLAUDE.md

# 2. Edita CLAUDE.md y pega el contenido de CONTEXTO_PROCESO_ERP.md
#    en la sección correspondiente

# 3. Abre Claude Code en tu proyecto
cd tu_proyecto
claude

# 4. Tu primer prompt:
# "Lee CLAUDE.md. Empieza con la Fase 1 y Fase 2: entiende el proceso
#  del video y explora la BD para correlacionar las pantallas con las tablas.
#  No hagas ningún cambio en la BD, solo explora y documenta."
```

Claude Code hará todo el detective work: buscar tablas por nombre, revisar los datos,
encontrar los stored procedures que usa el ERP, identificar triggers que ejecutan
lógica de negocio, y armar el mapa completo. Tú solo validas y apruebas.

## Parámetros

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `--start` | inicio | Tiempo de inicio (HH:MM:SS) |
| `--end` | final | Tiempo de fin (HH:MM:SS) |
| `--fps` | 0.2 | Frames/segundo (0.2 = cada 5s, 0.1 = cada 10s) |
| `--block-seconds` | 30 | Duración de cada bloque en segundos |
| `--whisper-model` | large-v3 | Modelo Whisper (tiny/base/small/medium/large-v3) |
| `--output` | ./output_pipeline | Directorio de salida |
| `--skip-transcribe` | false | Omitir transcripción si ya existe |
| `--local` | - | Usar video local en vez de descargar |
