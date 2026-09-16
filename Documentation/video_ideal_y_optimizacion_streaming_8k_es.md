# Vídeo ideal y optimización del streaming 8K

Actualizado: 2026-09-15

Este documento resume, de forma práctica, cómo preparar un vídeo para esta aplicación y qué se ha hecho para conseguir un streaming NDI 8K más estable.

## 1. Receta rápida: el vídeo recomendado

Para producir material nuevo, utilizar este perfil:

| Parámetro | Recomendación |
|---|---|
| Resolución | `7680 × 4320` (8K UHD, relación 16:9) |
| Escaneo | Progresivo |
| Cadencia | `24 fps` constantes; `23.976 fps` también es válido |
| Códec de vídeo | `VP9` |
| Contenedor | `WebM` |
| Bitrate orientativo | `30–40 Mbit/s` para una calidad similar al clip probado |
| Audio | `Opus`, estéreo, `48 kHz` |
| Duración | Preferiblemente clips cortos o medios; hasta unos 11 minutos de audio estéreo puede precargarse con el límite actual de 256 MiB |
| Timeline | PTS continuos y crecientes, sin saltos, duplicados ni tramos dañados |

La prioridad es combinar una cadencia constante con timestamps correctos. Un archivo con más bitrate no será mejor si obliga al equipo a decodificar tarde o contiene un timeline irregular.

Para la validación de máxima calidad actual se ha creado además un máster HEVC
Main `7680 × 4320`, `24000/1001 fps`, AAC 48 kHz y aproximadamente 151,9 Mbit/s.
Se decodifica mediante VideoToolbox y se entrega a NDI como NV12 4:2:0 sin la
conversión intermedia a UYVY. Esta es la candidata de aceptación, no todavía un
perfil promovido: debe pasar de nuevo en Device tras eliminar el receptor 8K
duplicado que agotaba la memoria del AVP.

### Referencia validada

El archivo de referencia usado durante las pruebas es:

`Videos/Ghost_Towns_in_8K_GoPro_be_Hero.webm`

Sus características reales son:

- vídeo VP9, `7680 × 4320`, progresivo;
- cadencia media `5959/250`, aproximadamente `23.836 fps`;
- audio Opus;
- duración `128.321 s`;
- bitrate total aproximado `33.7 Mbit/s`;
- tamaño `540,585,327 bytes`;
- SHA-1 `fddad96b46d200f79d827c226913539b32307191`.

Este archivo está probado y funciona, pero para nuevas exportaciones es preferible fijar exactamente `24 fps` constantes. La cadencia de `23.836 fps` es una propiedad del original, no un requisito de la aplicación.

## 2. Qué debe evitarse

- Resolución superior a `7680 × 4320`: aumenta coste sin aportar resolución útil al perfil 8K.
- `50/60 fps` en 8K salvo necesidad real: duplica aproximadamente la carga frente a 24/30 fps.
- Framerate variable: complica el pacing y hace más difícil diagnosticar sincronía.
- Vídeo entrelazado.
- Audio con frecuencia distinta de `48 kHz`, múltiples pistas innecesarias o más de dos canales.
- Timestamps rotos, huecos A/V, frames corruptos o padding excesivo al final del audio.
- Códecs extremadamente pesados de decodificar o bitrates desproporcionados para el disco y la máquina emisora.
- BGRA como modo de salida para 8K. El candidato HEVC debe conservar NV12; UYVY
  queda como fallback 4:2:2 y comparación controlada.

Otros formatos que PyAV pueda abrir, como H.264/AAC en MP4, son compatibles, pero deben validarse en la máquina final. El perfil VP9/Opus en WebM es el perfil 8K con evidencia directa en este proyecto.

## 3. Comprobación mínima antes de usar un vídeo

Confirmar siempre:

1. resolución `7680 × 4320`;
2. vídeo progresivo y framerate constante de 24, 23.976 o, como máximo recomendado, 30 fps;
3. una única pista de audio estéreo a 48 kHz;
4. reproducción completa sin errores de decode;
5. timestamps y duración coherentes entre audio y vídeo;
6. prueba primero en NDI Monitor y después en Unity/Apple Vision Pro;
7. prueba larga si el vídeo se utilizará en producción o en loop continuo.

Para Ghost Towns, iniciar con:

```bash
Launchers/Stream_NDI_Ghost_Towns_8K24.command
```

El lanzador usa NV12, cuatro frames de prefetch, precarga de audio cuando es posible y diagnósticos. I420 queda como override de diagnóstico.

Los lanzadores 8K usan por defecto `single-tcp` aislado. En la comparación
controlada del 15/09/2026, con una build ya corregida a un solo receptor visual,
`auto/RUDP` alcanzó el límite de 5120 MB de visionOS en unos 22 segundos bajo
saturación; la misma señal con TCP permaneció viva más de tres minutos y mantuvo
el audio estable. `NDI_TRANSPORT=auto` queda como override diagnóstico. TCP evita
la acumulación mediante backpressure, pero no inventa ancho de banda: la ruta
Wi-Fi actual sólo entregó aproximadamente 2–5 fps y mostró 3–593 ms de ping
(102 ms de media). La aceptación 8K exige primero corregir la infraestructura de
red o aprobar un transporte comprimido distinto de NDI full-bandwidth.

## 4. Qué se ha optimizado en el streaming

### Timeline y sincronía A/V

- Se sustituyó el modelo anterior, que calculaba audio a partir del índice de frame, por un timeline único con PyAV.
- Audio y vídeo conservan los tiempos del mismo contenedor.
- El audio se emite en bloques continuos de 1024 muestras que atraviesan el loop sin paquetes finales cortos ni rebases acumulativos.
- Exactamente un sender —la fuente combinada recomendada— activa el reloj nativo de audio NDI. Python no vuelve a dormir ese mismo PCM con un segundo reloj.
- Audio y vídeo arrancan en la misma posición de contenido; el sender no introduce preroll específico de receptor.
- Ambos llevan timecodes NDI explícitos de 100 ns calculados desde esa misma timeline continua. ACCESS calcula su reserva desde esos relojes y la cola real de vídeo; un preroll manual queda sólo como override diagnóstico porque un monitor genérico puede reproducir inmediatamente el audio anticipado.
- El vídeo nunca reajusta su reloj de forma independiente: si llega tarde se descarta el frame vencido, protegiendo la continuidad del audio y evitando deriva permanente.
- El padding de audio al final del clip se recorta respecto a la duración marcada por el vídeo para que el loop no acumule huecos.

### Sender NDI

- Se reemplazó `cyndilib` como sender principal por llamadas directas a `libndi` mediante `ctypes`.
- Esto elimina buffering y comportamiento implícito del wrapper y da control directo sobre frames, clocks y ciclo de vida del sender.
- Audio y vídeo se publican, por defecto, en una sola fuente `StreamNDI`. La fuente de audio separada queda sólo como herramienta de diagnóstico.
- El loop reutiliza y hace flush/seek de los decoders PyAV persistentes; reabrirlos queda como fallback de compatibilidad.
- Los diagnósticos se escriben desde una cola asíncrona para que un flush de disco no bloquee el hilo emisor de audio.
- La metadata de Unity sigue llegando por el backchannel NDI sin crear un transporte paralelo.

### Rendimiento específico de 8K

- I420 queda disponible como prueba explícita con ROI OFF: reduce un frame 8192x4320 de 67,5 MiB en UYVY a 50,625 MiB, pero la prueba de 45 minutos empeoró el envío nativo NDI y la cadencia efectiva. No es el default de producción.
- El envío de vídeo es asíncrono y se conserva el buffer hasta que NDI deja de utilizarlo.
- El decode de vídeo dispone de una cola limitada de prefetch; el perfil actual usa cuatro frames. Esto absorbe variaciones breves del decode sin permitir crecimiento ilimitado de memoria.
- Audio y vídeo se decodifican por rutas separadas. El audio se envía desde un hilo dedicado para que un frame 8K costoso no vacíe la cola de audio del receptor.
- El launcher 8K principal arranca con ROI OFF/NV12: no inicia el backchannel ni dibuja overlays, y anuncia `roi_feedback="0"` para que Unity tampoco calcule ni envíe viewport/gaze.
- La variante `Stream_NDI_Default_8K_ROI.command`/`.app` activa ROI ON. En ese modo, el ROI y el marcador de gaze se dibujan directamente sobre los planos Y/UV de NV12, sin volver a BGRA. UYVY conserva su renderer directo de fallback y el overlay `--dual`/cuadrado sigue limitado a BGRA.
- Se añadieron recuperación de errores de decode y descarte controlado de frames de vídeo tardíos.
- El avance de vídeo queda limitado por el final del último bloque PCM cuya llamada
  nativa ya terminó. Si `libndi` bloquea momentáneamente el hilo de audio, el vídeo
  espera y desplaza sus deadlines futuros: no consume el preroll ni reinicia el
  timeline al cruzar un loop.

### Audio

- La salida se normaliza a PCM `float32` planar, estéreo y `48 kHz`, que es el formato entregado a NDI.
- Se envían siempre bloques de 1024 muestras; `audio_short_blocks`, `audio_output_gaps` y `audio_output_bursts` deben permanecer a cero.
- La fuente combinada debe reportar `audio_native_clock=true`; la fuente separada de diagnóstico mantiene ese clock desactivado.
- El lanzador 8K solicita precargar el audio mediante PyAV para aislarlo de bloqueos del disco o del decode de vídeo, sin depender de un ejecutable `ffmpeg` externo.
- La precarga se decide por memoria, no por una duración arbitraria: presupuesto máximo de `256 MiB` de PCM decodificado.
- Ghost Towns necesita aproximadamente 47 MiB de PCM, por lo que entra holgadamente en ese presupuesto cuando FFmpeg está disponible.
- Si la precarga no puede realizarse, existe un fallback de decode continuo con PyAV; funciona, pero la precarga sigue siendo preferible para una sesión 8K de producción.

### Diagnóstico y operación

- Se añadieron logs JSONL y resúmenes por segundo de retrasos, gaps, tiempos de decode/empaquetado/envío, descartes, errores y diferencia A/V.
- El esquema de diagnóstico 4 identifica el codec/formato fuente y el formato NDI efectivo; separa decode comprimido (`video_decode_ms_max`), empaquetado/conversión (`video_pack_ms_max`), lectura total y envío nativo. También identifica el contrato de timecodes explícitos y separa el adelanto de contenido
  (`av_content_lead_ms`) del retraso real de entrega nativa de cada stream
  (`video_clock_lag_ms`, `audio_clock_lag_ms` y `av_submission_drift_ms`). También
  escribe un `av_sync_checkpoint` y una línea `[sync]` por vuelta del vídeo.
- Los eventos lentos detallados se limitan tras las cinco primeras muestras; los contadores acumulados siguen siendo exactos. Así un formato persistentemente lento no genera decenas de miles de líneas ni altera la propia medición.
- `NDI_VIDEO_HWACCEL=videotoolbox` habilita decode hardware con fallback explícito y registrado. El H.264 8192x4320 no es aceptado, mientras que el master derivado HEVC 7680x4320 sí activa VideoToolbox sin fallback.
- La candidata de producción se prueba sin cambios en NDI Monitor, Unity SIM y
  AVP mediante `Stream_NDI_Default_8K.command`. No existe una segunda emisión
  específica por receptor.
- Se añadió captura correlacionable de logs de Apple Vision Pro/Xcode.
- Se crearon lanzadores específicos para perfiles 8K y un lanzador `.app` para doble clic.
- Los lanzadores ya no dependen del Python activo en Terminal: crean y reutilizan `.venv`, instalan las dependencias fijadas y comprueban `libndi`.
- Se añadió detección de `libndi` en la ruta oficial del NDI SDK para Apple y selección de vídeo mediante Finder si falta el configurado.
- El launcher 8K apunta al candidato local HEVC 7680x4320/23,976 y avisa antes de arrancar si la
  instalación opcional NDI HX Driver y PyAV van a cargar dos `libavdevice` con las
  mismas clases AVFoundation. Corregirlo exige retirar HX Driver del host cuando no
  se necesite; el repositorio no modifica componentes del sistema.
- Ghost Towns se conserva con Git LFS para que Git sólo almacene el puntero y el binario grande se gestione fuera del historial normal.

## 5. Resultado actual

El camino recomendado es:

```text
Master HEVC 7680x4320/23,976 a unos 151,9 Mbit/s con audio original
  -> PyAV + VideoToolbox real: decode y timestamps del contenedor
  -> prefetch de vídeo + PCM fijo de 1024 muestras en hilo dedicado
  -> un único reloj nativo NDI para audio + preroll de sender cero
  -> ROI OFF: NV12 4:2:0 directo; I420 queda como comparativa diagnóstica
  -> ROI ON: NV12 4:2:0 con dibujo directo en planos Y/UV
  -> UYVY 4:2:2 queda como fallback medido
  -> libndi directo
  -> fuente NDI combinada StreamNDI
```

La arquitectura base fue validada en NDI Monitor, Unity Editor, Unity AVP Simulator y Apple Vision Pro. Una regresión posterior demostró que el sender y el receptor activos ya no coincidían con esa implementación: el sender perdió bloques fijos/clock nativo y Unity usaba un clip circular alimentado desde `Update`. Ambos núcleos históricos se restauraron el 8 de septiembre de 2026 conservando ROI ON/OFF y UYVY. Un log de unas 4,5 horas del 14 de septiembre confirmó además que stalls nativos de audio consumían gradualmente el preroll mientras el vídeo seguía su reloj, acumulando aproximadamente 4,75 s de diferencia. El sender actual corrige ese mecanismo haciendo autoritativo el progreso PCM aceptado. El 15 de septiembre, una prueba de 59 minutos con preroll cero en NDI Monitor terminó con cero gaps PCM, 6,353 ms de delta de contenido y 7,448 ms de deriva relativa de envío. La prueba posterior demostró que NDI Monitor reproduce inmediatamente un preroll de 2500 ms aunque reciba timecodes explícitos; por ello el perfil universal elimina todo preroll del sender. ACCESS mantiene 10 s de capacidad, pero calcula el arranque desde los timecodes y la cola de vídeo realmente observados. El esquema 4 registra además decode, empaquetado y envío por separado. La prueba I420 de 45 minutos entregó 25,74 fps y descartó 14,12 % frente a 26,99 fps y 9,95 % del UYVY comparable; el coste se concentra en la llamada nativa NDI. El H.264 8192x4320 tampoco puede usar VideoToolbox en este Mac. El master derivado conserva toda la imagen mediante 7680x4050 + bandas de 134/136 px alineadas a croma, mantiene intactos los paquetes/timestamps AAC y produce HEVC Main 7680x4320/23,976 a unos 151,9 Mbit/s. Con VideoToolbox y UYVY, el gate local ROI OFF entregó 1.705 frames sin descartes en 71,6 s; ROI ON, después de llenar el prefetch antes de iniciar la timeline, entregó 704 sin descartes en 30 s. En ambos casos hubo cero gaps PCM, bloques cortos o audio tardío. Las pruebas actuales cubren los tres receptores con el mismo contrato; lo pendiente es la fluidez 8K sostenida en AVP bajo una ruta LAN aceptable.

El 16 de septiembre se encontró una última divergencia operativa: el launcher
1080p `Stream_NDI_Default.command` seguía usando la ruta antigua SDK auto/RUDP,
BGRA, decode/downmix AAC 5.1 en vivo y sin precarga, prefetch ni diagnósticos.
Aunque el archivo era más pequeño, ese perfil causaba glitches de audio en AVP.
La prueba A/B del mismo MP4 mediante el núcleo universal eliminó los cortes.
Todos los launchers públicos delegan ahora en un único contrato de producción;
solo cambia el contenido/resolución y la capacidad ROI elegida.

La sincronización y continuidad A/V se consideran cerradas como decisión de
diseño e implementación. No se declara cerrada la fluidez de vídeo 8K en AVP:
NDI full-bandwidth continúa expuesto a la capacidad y jitter de la LAN y del
receptor, con caídas medidas por debajo de la cadencia fuente aunque sender,
audio, decode Unity y memoria permanezcan sanos.

## 6. Evolución resumida del proyecto

- **Marzo-abril de 2026:** se creó el streamer NDI inicial, se añadió audio y se separaron los helpers de FFmpeg.
- **Mayo de 2026:** se incorporó el backchannel XML/NDI, el estado de usuario, gaze y viewport procedente de Unity.
- **Junio de 2026:** se atacó la deriva de 1–2 segundos observada tras sesiones largas. El sender pasó de `cyndilib` a `libndi` directo y el decode A/V pasó a un timeline único con PyAV. Esta base se validó en NDI Monitor, Unity y Apple Vision Pro.
- **Agosto de 2026:** se instrumentaron las sesiones 8K, se introdujeron UYVY, hilo de audio, descarte de vídeo tardío, recuperación de decode, prefetch limitado, precarga de audio, métricas JSONL y captura de logs de AVP.
- **Fase final de agosto:** se mantuvieron audio y vídeo en una única fuente NDI, se añadieron bloques fijos, clock nativo único, preroll, loop persistente y diagnósticos asíncronos; el receptor pasó a cola SPSC de 10 s y callback DSP. Estas mejoras quedaron archivadas en snapshots, pero no correctamente fijadas en los repos propietarios.
- **Septiembre de 2026:** se añadió ROI ON/OFF y dibujo directo UYVY/NV12. Tras detectar cortes reales en AVP, se reconstruyó la historia y se restauraron los núcleos estables de sender/receptor, preservando las mejoras ROI. El análisis multilazo identificó después el desacople entre stalls de envío de audio y el reloj de vídeo; el vídeo pasa a seguir el PCM aceptado y se añaden métricas de espera. La fase de calidad descartó I420/29,97, añadió telemetría separada y verificó que VideoToolbox no acepta el master H.264 8192x4320. Se creó el perfil HEVC 7680x4320/23,976 a unos 151,9 Mbit/s, compatible con VideoToolbox, y superó los gates locales ROI OFF/ON sin drops. Todos los launchers se unificaron tras aislar el último glitch 1080p a la ruta legacy; queda abierta únicamente la aceptación de fluidez 8K sostenida en AVP.
