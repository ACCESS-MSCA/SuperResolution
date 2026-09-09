# Vídeo ideal y optimización del streaming 8K

Actualizado: 2026-08-28

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
- BGRA como modo de salida para 8K. El lanzador 8K debe conservar `--uyvy`.

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

El lanzador activa automáticamente UYVY, cuatro frames de prefetch, precarga de audio cuando es posible y diagnósticos.

Desde la validación AVP del 09/09/2026, los lanzadores 8K usan por defecto
NDI_TRANSPORT=single-tcp mediante una configuración local del proceso. En una
prueba limpia de 334,905 s sobre Ethernet, el receptor mantuvo 2350,7–2500,0 ms
de PCM sin underruns, concealment ni fallos DSP. NDI_TRANSPORT=auto permite
comparar con la configuración del SDK. Este resultado es un baseline acotado:
la reentrada en la escena también cambió el Mac de Wi‑Fi a Ethernet, por lo que
TCP no queda aislado como causa única hasta repetir la prueba en la misma interfaz.

## 4. Qué se ha optimizado en el streaming

### Timeline y sincronía A/V

- Se sustituyó el modelo anterior, que calculaba audio a partir del índice de frame, por un timeline único con PyAV.
- Audio y vídeo conservan los tiempos del mismo contenedor.
- El audio se emite en bloques continuos de 1024 muestras que atraviesan el loop sin paquetes finales cortos ni rebases acumulativos.
- Exactamente un sender —la fuente combinada recomendada— activa el reloj nativo de audio NDI. Python no vuelve a dormir ese mismo PCM con un segundo reloj.
- El vídeo conserva su deadline dentro de la timeline común y arranca 2500 ms después del audio para permitir el prefill efectivo del receptor AVP.
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

- La salida 8K usa UYVY 4:2:2 empaquetado en vez de BGRA: reduce cada frame de cuatro a dos bytes por píxel y evita la conversión BGRA dentro de NDI.
- El envío de vídeo es asíncrono y se conserva el buffer hasta que NDI deja de utilizarlo.
- El decode de vídeo dispone de una cola limitada de prefetch; el perfil actual usa cuatro frames. Esto absorbe variaciones breves del decode sin permitir crecimiento ilimitado de memoria.
- Audio y vídeo se decodifican por rutas separadas. El audio se envía desde un hilo dedicado para que un frame 8K costoso no vacíe la cola de audio del receptor.
- El launcher 8K principal arranca con ROI OFF: no inicia el backchannel ni dibuja overlays, y anuncia `roi_feedback="0"` para que Unity tampoco calcule ni envíe viewport/gaze.
- La variante `Stream_NDI_Default_8K_ROI.command`/`.app` activa ROI ON. En ese modo, el ROI y el marcador de gaze se dibujan directamente sobre UYVY 4:2:2, sin volver a BGRA ni copiar el frame completo. El overlay `--dual`/cuadrado sigue limitado a BGRA.
- Se añadieron recuperación de errores de decode y descarte controlado de frames de vídeo tardíos.

### Audio

- La salida se normaliza a PCM `float32` planar, estéreo y `48 kHz`, que es el formato entregado a NDI.
- Se envían siempre bloques de 1024 muestras; `audio_short_blocks`, `audio_output_gaps` y `audio_output_bursts` deben permanecer a cero.
- La fuente combinada debe reportar `audio_native_clock=true`; la fuente separada de diagnóstico mantiene ese clock desactivado.
- El lanzador 8K solicita precargar el audio mediante PyAV para aislarlo de bloqueos del disco o del decode de vídeo, sin depender de un ejecutable `ffmpeg` externo.
- La precarga se decide por memoria, no por una duración arbitraria: presupuesto máximo de `256 MiB` de PCM decodificado.
- Ghost Towns necesita aproximadamente 47 MiB de PCM, por lo que entra holgadamente en ese presupuesto cuando FFmpeg está disponible.
- Si la precarga no puede realizarse, existe un fallback de decode continuo con PyAV; funciona, pero la precarga sigue siendo preferible para una sesión 8K de producción.

### Diagnóstico y operación

- Se añadieron logs JSONL y resúmenes por segundo de retrasos, gaps, tiempos de decode/envío, descartes, errores y diferencia A/V.
- Se añadió captura correlacionable de logs de Apple Vision Pro/Xcode.
- Se crearon lanzadores específicos para perfiles 8K y un lanzador `.app` para doble clic.
- Los lanzadores ya no dependen del Python activo en Terminal: crean y reutilizan `.venv`, instalan las dependencias fijadas y comprueban `libndi`.
- Se añadió detección de `libndi` en la ruta oficial del NDI SDK para Apple y selección de vídeo mediante Finder si falta el configurado.
- Ghost Towns se conserva con Git LFS para que Git sólo almacene el puntero y el binario grande se gestione fuera del historial normal.

## 5. Resultado actual

El camino recomendado es:

```text
WebM VP9/Opus 8K24
  -> PyAV: decode y timestamps del contenedor
  -> prefetch de vídeo + PCM fijo de 1024 muestras en hilo dedicado
  -> un único reloj nativo NDI para audio + preroll de 2500 ms
  -> UYVY 4:2:2
  -> libndi directo
  -> fuente NDI combinada StreamNDI
```

La arquitectura base fue validada en NDI Monitor, Unity Editor, Unity AVP Simulator y Apple Vision Pro. Una regresión posterior demostró que el sender y el receptor activos ya no coincidían con esa implementación: el sender perdió bloques fijos/clock nativo y Unity usaba un clip circular alimentado desde `Update`. Ambos núcleos históricos se restauraron el 8 de septiembre de 2026 conservando ROI ON/OFF y UYVY. La validación estática pasa, pero la versión restaurada necesita todavía una nueva prueba larga en AVP antes de volver a declararse estable.

## 6. Evolución resumida del proyecto

- **Marzo-abril de 2026:** se creó el streamer NDI inicial, se añadió audio y se separaron los helpers de FFmpeg.
- **Mayo de 2026:** se incorporó el backchannel XML/NDI, el estado de usuario, gaze y viewport procedente de Unity.
- **Junio de 2026:** se atacó la deriva de 1–2 segundos observada tras sesiones largas. El sender pasó de `cyndilib` a `libndi` directo y el decode A/V pasó a un timeline único con PyAV. Esta base se validó en NDI Monitor, Unity y Apple Vision Pro.
- **Agosto de 2026:** se instrumentaron las sesiones 8K, se introdujeron UYVY, hilo de audio, descarte de vídeo tardío, recuperación de decode, prefetch limitado, precarga de audio, métricas JSONL y captura de logs de AVP.
- **Fase final de agosto:** se mantuvieron audio y vídeo en una única fuente NDI, se añadieron bloques fijos, clock nativo único, preroll, loop persistente y diagnósticos asíncronos; el receptor pasó a cola SPSC de 10 s y callback DSP. Estas mejoras quedaron archivadas en snapshots, pero no correctamente fijadas en los repos propietarios.
- **Septiembre de 2026:** se añadió ROI ON/OFF y dibujo directo UYVY. Tras detectar cortes reales en AVP, se reconstruyó la historia y se restauraron los núcleos estables de sender/receptor, preservando las mejoras ROI. Queda pendiente la validación larga final.
