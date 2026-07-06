# Decisión de Arquitectura: Migración del Sender A/V de `cyndilib` a `libndi`

Actualizado: 2026-06-18

Estado: implementado en código y validado funcionalmente en NDI Monitor, Unity Editor, Unity AVP Simulator y build en Apple Vision Pro

Navegación:
- Manual core ES HTML: `../manual_tecnico_streaming_ndi_es.html`
- Manual core EN HTML: `../technical_manual_streaming_ndi_en.html`
- Deliverable EN: `./ndi_sender_libndi_decision_en.html`

## 1. Contexto

El proyecto necesita emitir una señal NDI desde Python con estos requisitos:

- vídeo BGRA procedente de `ffmpeg`,
- audio `float32` planar,
- soporte para distintos frame rates y ficheros con o sin audio,
- canal de metadata desde Unity hacia el sender para dibujar viewport u otras features futuras,
- base técnica clara para que varias personas puedan evolucionar el sistema.

Durante las pruebas largas se observó una deriva A/V sistemática:

- después de ~20-30 minutos, el audio quedaba retrasado entre 1 y 2 segundos respecto al vídeo,
- el problema se reproducía tanto en Unity como en NDI Monitor,
- por tanto el receptor Unity dejó de ser el principal sospechoso y el foco pasó al sender Python.

## 2. Implementación anterior

La versión anterior del sender A/V usaba `cyndilib` como wrapper de alto nivel sobre NDI:

- `Sender`
- `VideoSendFrame`
- `AudioSendFrame`
- `write_video`, `write_audio`, `write_video_and_audio`

Además, el proyecto usaba:

- `ffprobe` para metadata del media,
- `ffmpeg` para decode de vídeo y audio,
- `numpy` para buffers y overlays,
- un backchannel de metadata que ya tocaba `libndi` casi directamente vía `ctypes`.

## 3. Problema observado con la arquitectura anterior

Se probaron varias estrategias sin resolver la deriva larga:

1. pacing manual con reloj monotónico,
2. ventanas exactas de audio por frame,
3. control manual del loop de vídeo,
4. timestamps/timecodes explícitos,
5. clocks de NDI ajustados,
6. combinación de clocks internos y externos.

La deriva seguía apareciendo de forma consistente tras sesiones largas.

Conclusión operativa:

- seguir añadiendo lógica correctiva encima del wrapper aumentaba complejidad,
- y no estaba dando una mejora estable verificable.

## 4. Decisión tomada

Se sustituye el sender A/V de alto nivel basado en `cyndilib` por un sender mínimo directo sobre `libndi`.

La migración afecta solo al corazón de envío A/V. No se reescribe todo el proyecto.

Se mantiene:

- decode y probing con `ffmpeg` / `ffprobe`,
- almacenamiento de audio en memoria para acceso determinista,
- overlays `numpy`,
- parsing y dispatch de metadata Unity,
- backchannel de metadata.

Se reemplaza:

- creación del sender A/V,
- estructuras de frame de vídeo/audio,
- llamadas de envío A/V.

## 5. Motivación de diseño

La motivación principal no es “bajar de nivel porque sí”, sino reducir incertidumbre.

Con `libndi` directo ganamos:

- control explícito de `NDIlib_send_create_t`,
- control explícito de `NDIlib_video_frame_v2_t` y `NDIlib_audio_frame_v3_t`,
- control explícito de `clock_video` y `clock_audio`,
- menos buffering implícito del wrapper,
- menos comportamiento opaco en el camino crítico A/V,
- una dependencia Python menos en el runtime obligatorio.

Lo importante aquí es la trazabilidad:

- si la deriva desaparece, el wrapper era un factor relevante,
- si la deriva persiste, el problema está en otra parte del pipeline y ya no se pierde tiempo culpando a la capa equivocada.

## 6. Costes y tradeoffs

Lo que se gana:

- arquitectura más directa en el sender,
- menor dependencia de clases internas del wrapper,
- mejor capacidad de instrumentación y depuración,
- coherencia técnica con el backchannel, que ya estaba cerca de `libndi`.

Lo que se pierde:

- comodidad de las abstracciones de `cyndilib`,
- menos protección frente a errores de structs o llamadas nativas,
- más responsabilidad propia sobre compatibilidad y mantenimiento,
- necesidad de validar bien memoria, formatos y lifecycle.

Decisión consciente:

- se acepta más código low-level a cambio de menos incertidumbre en el camino crítico.

## 7. Alcance exacto de la migración

Ficheros introducidos o modificados en esta fase:

- `ndi_native.py`
- `utils.py`
- `stream_video.py`
- `extensions/backchannel/receiver.py`
- `requirements.txt`

Rol de cada uno:

- `ndi_native.py`: runtime `libndi`, structs `ctypes`, sender A/V mínimo.
- `utils.py`: fábrica del sender NDI directo.
- `stream_video.py`: sigue orquestando decode, loop, audio windows y overlays.
- `receiver.py`: adapta el backchannel para aceptar sender nativo o legacy.
- `requirements.txt`: reduce dependencia Python obligatoria a `numpy`.

## 8. Estado de dependencias tras la decisión

Dependencias runtime obligatorias:

- `numpy==2.3.5`
- `ffmpeg`
- `ffprobe`
- `libndi`

Dependencias opcionales / legacy:

- `cyndilib`: ya no es necesario para el sender A/V.
- Puede seguir existiendo instalado como fallback para localizar un `libndi` bundleado o para comparar comportamiento legacy.

## 9. Versiones observadas en la máquina de desarrollo

Referencias observadas durante la migración:

- Python: `3.11.0`
- NumPy: `2.3.5`
- FFmpeg: `8.1`
- FFprobe: `8.1`
- NDI runtime detectado: `NDI SDK APPLE 6.2.1.0`

Estas referencias no sustituyen una matriz de QA completa, pero sí fijan el contexto técnico de la decisión.

## 10. Qué NO cambia

No cambia en esta fase:

- el contrato de metadata con Unity,
- la semántica del viewport overlay,
- el mecanismo de decode con `ffmpeg`,
- el loop controlado del vídeo por pasadas del clip,
- el soporte de dual output,
- la estrategia de audio planar `float32`.

Es una migración de la capa de envío NDI, no una reescritura del pipeline entero.

## 11. Riesgos abiertos

Riesgos todavía presentes:

1. que la deriva A/V persista incluso con sender directo,
2. que existan diferencias de comportamiento entre macOS, NDI Monitor y otros receivers,
3. que el problema real esté en timing del media o en cómo se trocea el audio por frame,
4. que ciertas rutas de inicialización de `libndi` dependan del entorno de ejecución.

Lectura correcta del riesgo:

- la migración a `libndi` reduce una capa de sospecha,
- pero no garantiza por sí sola que la deriva desaparezca.

## 12. Estado de validación actual

Validación funcional completada en la matriz actual del proyecto:

1. NDI Monitor,
2. Unity Editor,
3. Unity AVP Simulator,
4. build en Apple Vision Pro.

Resultado observado: tras simplificar el sender y después mover el timeline A/V a `PyAV`, el comportamiento actual es correcto en todos estos targets.

## 13. QA recomendada a partir de aquí

1. mantener sesiones largas como prueba de regresión operativa,
2. seguir usando NDI Monitor como referencia rápida cuando aparezca una fuente sospechosa,
3. repetir la batería con señales `24/23.976` y `60/59.94`,
4. tratar reinicios del sender y cambios de source en caliente como casos básicos de regresión.

## 14. Resumen ejecutivo de la decisión

La decisión de migrar el sender A/V de `cyndilib` a `libndi` se toma para simplificar el camino crítico y reducir comportamiento opaco en una incidencia de sincronía larga no resuelta por ajustes incrementales.

No es una reescritura total.

Es una reducción deliberada de capas:

- menos wrapper en el sender,
- mismo pipeline de decode y metadata,
- mejor capacidad de aislar la causa real del drift.
