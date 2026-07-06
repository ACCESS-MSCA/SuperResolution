# Manual Tecnico - SuperResolution NDI Streaming

Actualizado: 2026-07-06

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
CLI -> LoopingMediaReader (PyAV)
    -> eventos de media ordenados por tiempo (video/audio)
    -> reloj unico de reproduccion en stream_video.py
    -> NativeNdiSender (libndi)
    -> salida NDI principal
    -> salida NDI secundaria opcional (--dual)
    -> overlay opcional con metadata de Unity
```

Audio y video salen del mismo timeline de media. El sistema ya no reconstruye audio por ventanas calculadas a partir del frame index de video.

## Componentes principales

| Ruta | Rol | Notas |
|---|---|---|
| `stream_video.py` | Orquestacion runtime | Scheduling, dual output, metadata, cleanup |
| `media_reader.py` | Reader de media unificado | Loop por pasadas, decode A/V y ordenacion temporal |
| `ndi_native.py` | Bindings minimos a NDI | Sender directo y capture de metadata |
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

- Un timeline de media: el PTS de media es la fuente de scheduling.
- Un reloj de reproduccion: `stream_video.py` controla el pacing.
- Sender directo: se evita el wrapper de alto nivel en el sender runtime.
- Metadata como extension: la metadata de viewport Unity enriquece el pipeline base sin redefinirlo.

## Flujo de ejecucion

1. Se parsean argumentos CLI.
2. `LoopingMediaReader` abre el media y detecta streams de video/audio.
3. Se configuran sender principal y sender secundario opcional.
4. El runtime consume eventos de video o audio ya ordenados por tiempo de media.
5. El scheduler espera al deadline correspondiente y envia el frame o bloque de audio.
6. Si hay metadata reciente de Unity, el overlay debug puede dibujarla sobre el frame antes del envio.
7. Al llegar al final del clip, el reader reabre el media y continua con offset temporal acumulado.

## Operacion

```bash
python3 -m pip install -r requirements.txt
python3 stream_video.py
python3 stream_video.py Videos/big_buck_bunny.mp4 --dual
python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
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

La arquitectura base queda organizada alrededor de sender directo, timeline unico y metadata como capa de extension. Las pruebas largas siguen recomendadas como practica de QA continuo.
