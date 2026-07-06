# Decisión de Arquitectura: Timeline único de media con `PyAV`

Actualizado: 2026-06-18

Estado: implementado en código y validado funcionalmente en NDI Monitor, Unity Editor, Unity AVP Simulator y build en Apple Vision Pro

Navegación:
- Manual core ES HTML: `../manual_tecnico_streaming_ndi_es.html`
- Manual core EN HTML: `../technical_manual_streaming_ndi_en.html`
- Deliverable EN: `./pyav_media_timeline_decision_en.html`
- Decisión previa relacionada: `./ndi_sender_libndi_decision_es.html`

## 1. Contexto

Después de migrar el sender A/V a `libndi` directo, el problema de deriva larga no desapareció por sí solo.

Observaciones importantes:

- Unity y NDI Monitor mostraban el mismo desfase.
- La capa receiver dejó de ser la principal sospechosa.
- El wrapper del sender ya no explicaba por sí solo la deriva.

La hipótesis más fuerte pasó a ser el propio modelo de tiempo del streamer.

## 2. Problema detectado en el pipeline anterior

El pipeline anterior seguía dos caminos lógicos distintos:

- vídeo: decode por frames y pacing según fps de vídeo,
- audio: decode completo, almacenamiento en memoria y troceado manual por ventana para cada frame de vídeo.

Eso introducía dos relojes implícitos:

1. el reloj real del contenedor multimedia,
2. el reloj reconstruido por el script a partir del índice de frame de vídeo.

En sesiones largas, sobre todo con framerates como `23.976` o cambios de señal, este modelo era un candidato claro a acumular error o desajuste.

## 3. Decisión tomada

Se sustituye el modelo de decode separado y reconstrucción manual A/V por un reader unificado basado en `PyAV`.

La nueva regla es simple:

- audio y vídeo salen del mismo contenedor,
- cada evento lleva su tiempo de media,
- el runtime usa un único reloj de reproducción,
- el sender NDI recibe audio y vídeo ya ordenados por ese timeline.

## 4. Cambios aplicados

Ficheros relevantes:

- `media_reader.py`: nuevo módulo de decode y ordenación temporal.
- `stream_video.py`: ahora consume `MediaVideoEvent` y `MediaAudioEvent`.
- `utils.py`: el sender NDI se crea con `clock_video=False` y `clock_audio=False` para evitar doble autoridad de timing.
- `requirements.txt`: se añade `av==17.1.0`.

## 5. Motivación de diseño

La intención no es meter otra librería porque sí. La intención es quitar lógica casera donde más daño puede hacer.

Con `PyAV` ganamos:

- un timeline compartido por audio y vídeo,
- soporte más natural para material con framerate fraccional,
- menos matemáticas manuales de muestras por frame,
- menos riesgo de drift acumulado por reconstrucción,
- menos acoplamiento entre “cadencia de vídeo” y “entrega de audio”.

## 6. Qué se deja de hacer

La implementación nueva deja de depender de estas ideas en el camino crítico:

- decode completo del audio a memoria para ir cortándolo por frame de vídeo,
- cálculo manual de ventanas exactas de audio a partir de `frame_idx`,
- asumir que el audio se debe derivar de la rejilla temporal del vídeo,
- mezclar clocking interno de NDI con clocking externo como si fueran equivalentes.

## 7. Qué se mantiene

Se mantiene:

- sender directo `libndi`,
- overlays en `numpy`,
- metadata desde Unity,
- modo dual de salida,
- loop del clip controlado por pasadas,
- audio `float32` planar hacia NDI.

## 8. Costes y tradeoffs

Lo que se gana:

- arquitectura temporal más coherente,
- menos código correctivo específico,
- mejor base para soportar fuentes heterogéneas,
- lectura más clara del pipeline para el equipo.

Lo que se paga:

- una dependencia Python adicional (`PyAV`),
- necesidad de validar wheel/instalación en las máquinas objetivo,
- un reader algo más sofisticado que el piping bruto previo.

## 9. Dependencias fijadas

Dependencias Python obligatorias tras esta decisión:

- `numpy==2.3.5`
- `av==17.1.0`

Dependencia runtime externa:

- `libndi`

`cyndilib` queda como dependencia opcional/legacy, no como parte del camino principal.

## 10. Riesgos abiertos

1. que alguna señal de origen ya venga desincronizada,
2. que haya diferencias de comportamiento entre builds de `PyAV` según plataforma,
3. que todavía quede un problema real en la generación de la fuente y no en el sender,
4. que `ffmpeg.py` siga existiendo como helper legacy y deba limpiarse en una fase posterior.

## 11. Estado de validación actual

Validación funcional completada en la matriz actual del proyecto:

1. NDI Monitor,
2. Unity Editor,
3. Unity AVP Simulator,
4. build en Apple Vision Pro.

Resultado observado: el streamer funciona correctamente en estos targets tras el cambio a timeline unificado con `PyAV`.

## 12. QA recomendada a partir de aquí

1. mantener sesiones largas como prueba de regresión operativa,
2. seguir comparando NDI Monitor y Unity cuando aparezca una fuente nueva o sospechosa,
3. repetir la validación con señales `24/23.976` y `60/59.94`,
4. revisar cambios de source en caliente y reinicios del sender como casos base de regresión.

## 13. Resumen ejecutivo

La decisión de introducir `PyAV` no busca complejidad gratuita. Busca quitar una reconstrucción A/V manual que era precisamente el tipo de pieza propensa a introducir deriva larga.

La arquitectura queda mejor definida:

- `PyAV` decide el tiempo de media,
- `stream_video.py` decide cuándo sale cada evento,
- `libndi` se dedica a enviar.
