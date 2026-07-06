# Guia de instalacion y ejecucion

Actualizado: 2026-07-06

Navegacion: [Indice](index_es.md) | [EN](setup_and_run_en.md) | [HTML](setup_and_run_es.html)

Esta guia cubre instalacion inicial, checks de runtime y validacion con NDI Monitor y la build de Unity Simulator entregada.

## Que ejecuta este proyecto

`SuperResolution` publica una fuente NDI de video/audio desde Python y puede recibir metadata Unity por el backchannel del sender NDI. En la integracion actual, Unity reporta estado de gaze/viewport como metadata `access_viewport`, y Python la parsea a `UnityViewportMetadata`.

```text
media file -> PyAV LoopingMediaReader -> stream_video.py scheduler
    -> NativeNdiSender/libndi -> fuente NDI visible para Unity y NDI Monitor
    <- backchannel NDI de metadata Unity con estado gaze/viewport
```

## Requisitos

- Host macOS.
- Python 3.11 fue usado durante desarrollo.
- NDI Runtime o NDI SDK instalado para que `libndi` sea visible desde Python.
- Configuracion de red/firewall que permita descubrimiento NDI entre Python, NDI Monitor y Unity.

Dependencias Python fijadas:

```text
numpy==2.3.5
av==17.1.0
```

`stream_video.py` usa PyAV para decode de media. Una instalacion de sistema de `ffmpeg` no forma parte del runtime principal, aunque puede ser util para diagnostico o tooling legacy/offline.

## Instalacion

Desde la raiz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

## Check de visibilidad de NDI Runtime

```bash
python3 -c "from ndi_native import get_ndi_runtime; rt = get_ndi_runtime(); print(rt.lib.NDIlib_version().decode('utf-8', errors='replace'))"
```

Resultado esperado: una version de NDI Runtime impresa, por ejemplo `NDI SDK APPLE ...`.

Fallo comun: `Could not load NDI runtime library`.

Accion: instalar NDI Runtime/SDK y asegurar que `libndi` puede encontrarse desde el proceso Python.

## Ejecutar el streamer

```bash
python3 stream_video.py
python3 stream_video.py Videos/big_buck_bunny.mp4
python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
python3 stream_video.py Videos/big_buck_bunny.mp4 --no-rx-metadata
python3 stream_video.py Videos/big_buck_bunny.mp4 --dual
```

Por defecto, la fuente NDI principal se llama `StreamNDI`.

## Validar sin Unity

1. Arrancar el streamer Python.
2. Abrir NDI Monitor.
3. Confirmar que aparece `StreamNDI`.
4. Confirmar que el video es visible y que hay audio si la fuente tiene audio.
5. Dejar el stream corriendo una duracion representativa si importa el comportamiento de sesiones largas.

Esto aisla el sender Python y el timeline de media antes de anadir Unity.

## Validar con la build de Unity Simulator

1. Arrancar Python con metadata activada:

   ```bash
   python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
   ```

2. Lanzar la build de Unity Simulator entregada.
3. En Unity, conectar el receiver NDI a la fuente Python, normalmente `StreamNDI`.
4. Mover la vista/cabeza simulada para que la superficie NDI sea visible.
5. Observar la consola Python.

Logs esperados:

```text
[info] backchannel receiver started
[RX Viewport] seq=12 scene=NDI uv=(0.123,0.234)-(0.456,0.678) hit_any=1 plane_intersection=1 poly_n=4 gaze_hit=1 gaze_uv=(0.321,0.456)
```

Con `--rx-metadata-verbose`, tambien se imprimen los XML crudos.

## Como recibe Python la metadata Unity

- `extensions/backchannel/receiver.py`: captura frames de metadata NDI desde el sender activo.
- `extensions/backchannel/dispatcher.py`: despacha mensajes XML parseados a handlers de integracion.
- `integrations/unity/parsers.py`: convierte XML `access_viewport` a `UnityViewportMetadata`.
- `integrations/unity/handlers.py`: mantiene el ultimo estado de viewport.
- `stream_video.py`: dibuja el overlay debug de viewport cuando existe metadata reciente.

El stale timeout del overlay debug actual en `stream_video.py` es de 3 segundos.

## Troubleshooting

| Sintoma | Causa probable | Accion |
|---|---|---|
| Python no arranca NDI | `libndi` no es visible | Instalar NDI Runtime/SDK y repetir el check de runtime |
| NDI Monitor no muestra `StreamNDI` | Runtime NDI, firewall, descubrimiento LAN o sender parado | Revisar consola, segmento de red y firewall |
| Unity no ve la fuente | Descubrimiento NDI o seleccion de fuente | Confirmar primero en NDI Monitor y reconectar Unity a `StreamNDI` |
| Python no muestra logs `[RX Viewport]` | Sender de metadata Unity no conectado o stale | Ejecutar con `--rx-metadata-verbose`, revisar provider/sender Unity |
| El marcador de gaze salta a una esquina | No se comprobo validez de `gaze_hit` | Tratar `gaze_uv` como valido solo cuando `gaze_hit` es true |
| ROI ERP/360 incorrecta cerca del seam | Se uso solo `uv_min/uv_max` | Usar geometria ERP-aware descrita en el contrato de metadata |

## Documentos relacionados

- `Documentation/unity_viewport_metadata_contract_es.md`
- `Documentation/Deliverable/unity_gaze_metadata_handoff_es.md`
- `Documentation/manual_tecnico_streaming_ndi_es.md`
