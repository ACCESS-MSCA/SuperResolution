# Manual Tecnico - SuperResolution NDI Streaming

Actualizado: 2026-09-16

Navegacion: [Indice](index_es.md) | [EN](technical_manual_streaming_ndi_en.md) | [HTML](manual_tecnico_streaming_ndi_es.html)

Este manual describe la implementacion actual del streamer NDI en Python. El foco principal de la arquitectura es mantener sincronia A/V estable en sesiones largas, reducir capas opacas y dejar una base clara para extender features como overlays de viewport enviados desde Unity.

## Resumen ejecutivo

- Entrada principal: `stream_video.py`.
- Decode A/V unificado: `media_reader.py` con `PyAV`.
- Sender NDI: `ndi_native.py` con `libndi` directo.
- Procesado de frame y overlays: `numpy`.
- Backchannel Unity: metadata NDI recibida y dibujada opcionalmente sobre el video saliente.

## Arquitectura actual

```text
launcher universal -> LoopingMediaReader persistente (PyAV/VideoToolbox)
    -> vídeo: prefetch acotado -> lease NV12 reutilizable -> ROI opcional directo
    -> audio: preload -> bloques PCM fijos de 1024 muestras -> thread dedicado
    -> timeline continuo + timecodes NDI A/V explícitos
    -> NativeNdiSender (libndi) -> StreamNDI combinado -> single-TCP
    -> salida NDI secundaria opcional (--dual)
    -> overlay opcional con metadata de Unity
```

Audio y video salen del mismo timeline de media. El sistema ya no reconstruye audio por ventanas calculadas a partir del frame index de video.

Los buffers de salida NV12 se reutilizan en vez de reservar un array 8K completo
por frame. El lease se conserva durante la propiedad asíncrona de `libndi` y solo
se libera cuando vuelve el siguiente envío o durante el flush de cierre. En
7680x4320 evita una reserva repetida de aproximadamente 47,5 MiB. Los diagnósticos
incluyen contadores de reservas y reutilizaciones; píxeles, clocks, ROI y audio
no cambian.

## Componentes principales

| Ruta | Rol | Notas |
|---|---|---|
| `stream_video.py` | Orquestacion runtime | Scheduling, dual output, metadata, cleanup |
| `media_reader.py` | Reader de media unificado | Loop por pasadas, decode A/V y ordenacion temporal |
| `ndi_native.py` | Bindings minimos a NDI | Sender directo y capture de metadata |
| `create_8k_uhd_master.py` | Preparacion offline | HEVC/NV12 7680x4320 y normalizacion AAC opcional |
| `utils.py` | Helpers visuales y fabrica de sender | Overlay simple y configuracion del sender |
| `extensions/backchannel/receiver.py` | Canal de vuelta de metadata | Recibe XML desde los receivers |
| `integrations/unity/` | Integracion Unity | Parsing y estado de viewport |

## Dependencias

| Componente | Estado | Motivo |
|---|---|---|
| `numpy==2.3.5` | Obligatoria | Buffers de video/audio y overlays |
| `av==17.1.0` | Obligatoria | Timeline unificado y decode A/V |
| `libndi` | Obligatoria | Sender NDI nativo |
| `cyndilib` | Opcional/legacy | Ya no es necesario para streaming; puede ayudar a localizar un `libndi` bundled en desarrollo |
| `ffmpeg` | Opcional fuera del runtime | Util para preprocessing o launchers auxiliares, no para el streaming principal |

## Principios de diseno

- Un timeline de media: el PTS de media y el PCM nativo completado gobiernan la continuidad.
- Un reloj A/V compartido: preroll de sender cero y timecodes explícitos.
- Un perfil universal: Monitor, SIM y Device reciben la misma emisión.
- Sender directo: se evita el wrapper de alto nivel en el sender runtime.
- Metadata como extension: la metadata de viewport Unity enriquece el pipeline base sin redefinirlo.

## Flujo de ejecucion

1. Se parsean argumentos CLI.
2. `LoopingMediaReader` abre el media y detecta streams de video/audio.
3. Se precarga audio si cabe y se llena el prefetch antes de liberar la timeline.
4. Un thread emite PCM continuo; vídeo sigue el progreso PCM aceptado y descarta frames vencidos.
5. Audio y vídeo reciben timecodes de la misma posición de contenido.
6. Si hay metadata reciente de Unity, el overlay debug puede dibujarla sobre el frame antes del envio.
7. Al llegar al final, los readers persistentes se vacían/reposicionan y continúan sin rebasar la timeline.

## Operacion

```bash
python3 -m pip install -r requirements.txt
Launchers/Stream_NDI_Default.command
Launchers/Stream_NDI_Default_8K.command
Launchers/Stream_NDI_Default_8K_ROI.command
```

Ver `setup_and_run_es.md` para instalacion inicial y ruta completa de validacion.

## QA recomendado

Matriz validada el 2026-06-18:

- NDI Monitor
- Unity Editor
- Unity AVP Simulator
- Build en Apple Vision Pro

QA operativo:

1. Validar primero en NDI Monitor.
2. Repetir despues en Unity.
3. Probar sesiones largas con senales representativas.
4. Comparar comportamiento 24/23.976 fps y 60/59.94 fps.
5. Comprobar fuentes con audio, sin audio y cambios de senal si aplican.

## Troubleshooting

| Sintoma | Area probable | Accion |
|---|---|---|
| No aparece la fuente NDI | Runtime NDI o visibilidad de red | Verificar instalacion de `libndi` y visibilidad LAN |
| El script falla al arrancar | Dependencia `PyAV` | Reinstalar `requirements.txt` |
| Drift A/V largo | Fuente de origen o scheduler | Comparar NDI Monitor frente a Unity y revisar logs |
| Video con tirones | Carga de decode o CPU | Reducir complejidad de fuente y revisar avisos de timing |
| No se pinta el viewport | Backchannel o metadata stale | Revisar consola Python y emision de metadata desde Unity |

## Estado actual

La continuidad y sincronización A/V están cerradas como baseline técnico. Todos
los launchers públicos comparten single-TCP, preroll cero, timecodes, audio
precargado, prefetch, VideoToolbox/NV12 y diagnósticos. El A/B del 16/09 eliminó
los glitches del antiguo launcher 1080p al pasarlo por esta ruta.

La fluidez 8K sostenida en AVP no está cerrada: bajo la LAN medida, la entrega
full-bandwidth puede caer por debajo de la cadencia aunque sender y audio sigan
sanos. La siguiente decisión es infraestructura de baja latencia o una arquitectura
NDI comprimida con licencia; no otro launcher por receptor.

La preparación HEVC/NV12 evita variabilidad innecesaria del master y mantiene
VideoToolbox, pero no modifica el codec de transporte NDI estándar. En la prueba
Device del 16/09 el sender produjo aproximadamente 304–320 Mbit/s de salida y
la recepción siguió llegando por ráfagas. Aumentar el buffer solo puede absorber
ráfagas acotadas; no reconstruye frames que no llegan al receptor.
