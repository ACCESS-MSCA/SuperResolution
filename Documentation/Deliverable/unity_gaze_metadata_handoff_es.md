# Nota de entrega: metadata de gaze Unity para hiper-resolucion

Estado: implementado en el proveedor de metadata Unity y parseado en Python

Actualizado: 2026-07-06

Navegacion: [Indice de documentacion](../index_es.md) | [EN](unity_gaze_metadata_handoff_en.md) | [HTML](unity_gaze_metadata_handoff_es.html)

## Proposito

Esta nota explica como el proyecto Python `SuperResolution` recibe metadata de gaze/viewport desde la build de Unity Simulator entregada. Identifica los puntos de integracion entregados, el contrato de datos y la ruta actual de validacion.

## Alcance entregado

La entrega incluye:

- Runtime Python de streaming NDI en `SuperResolution`.
- Sender directo basado en `libndi`.
- Timeline A/V basado en PyAV.
- Receptor de metadata de vuelta desde Unity.
- Parser de metadata de viewport Unity.
- Overlay debug de ROI/gaze en Python.
- Build de Unity Simulator que se espera conectar a la fuente NDI Python y emitir metadata `access_viewport`.

La entrega no incluye:

- Un algoritmo de hiper-resolucion de produccion.
- Un perfil final de calidad foveated.
- Sincronizacion exacta metadata-a-frame de video.
- Un instalador propio para NDI Runtime o Python.

## Ruta de validacion

La siguiente ruta valida que la fuente Python, el receiver Unity y el backchannel de metadata estan conectados:

1. Instalar y ejecutar Python siguiendo `Documentation/setup_and_run_es.md`.
2. Confirmar que `StreamNDI` aparece en NDI Monitor.
3. Lanzar la build de Unity Simulator y conectarla a la fuente NDI de Python.
4. Ejecutar Python con:

   ```bash
   python3 stream_video.py Videos/big_buck_bunny.mp4 --rx-metadata-verbose
   ```

5. Confirmar que aparecen lineas `[RX Viewport]` en la consola Python.
6. Leer la ultima metadata parseada desde `UnityViewportStateHandler.state.latest`.
7. Confirmar que `viewport.gaze_hit` y `viewport.gaze_uv` llegan poblados al objeto parseado `UnityViewportMetadata`.

## Contrato principal

Usar `Documentation/unity_viewport_metadata_contract_es.md` como contrato tecnico de campos.

Campos principales para integracion gaze/viewport:

- `schema_version`: version del contrato, actualmente `1`.
- `sequence`: secuencia monotona del payload Unity.
- `gaze_hit`: indica si `gaze_uv` es valido.
- `gaze_uv`: centro foveal normalizado en UV de textura/Unity.
- `uv_projection`: modo de proyeccion planar o equirectangular.
- `uv_polygon`: poligono de ROI visible sobre la superficie.
- `erp_frustum_valid` y `erp_corner_directions`: geometria preferida para reconstruir viewport en equirectangular/360.

Conversion a pixel:

```python
x = int(round(u * (width - 1)))
y = int(round((1.0 - v) * (height - 1)))
```

## Notas de uso de metadata

La metadata Unity aporta estado de gaze y region visible. No define el algoritmo downstream de procesado de imagen.

- `gaze_uv` es valido solo cuando `gaze_hit` es true.
- Los valores UV estan normalizados en `[0, 1]`.
- El mapeo a espacio frame en NumPy invierte `v`.
- Radio foveal, falloff, perfil de calidad y politica de fallback estan fuera del contrato de metadata Unity.
- `uv_polygon` describe el limite visible de la superficie en espacio UV.
- Fuentes equirectangulares requieren tratamiento ERP-aware; `uv_min/uv_max` por si solos pueden ser ambiguos cerca del seam.

## Puntos de extension en Python

Archivos Python relevantes:

- `integrations/unity/parsers.py`: contrato tipado de metadata.
- `integrations/unity/handlers.py`: ultimo estado de viewport.
- `stream_video.py`: runtime loop actual y overlay debug.
- `utils.py`: helpers simples de frame/overlay ya existentes.

Forma actual del runtime:

```text
MediaVideoEvent frame
    -> ultimo UnityViewportMetadata
    -> interpretacion opcional gaze/ROI
    -> procesado downstream opcional de frame
    -> NativeNdiSender.write_video(...)
```

## Riesgos y restricciones

| Riesgo | Impacto | Mitigacion |
|---|---|---|
| La metadata es ultimo estado, no frame-locked | El gaze puede ir ligeramente adelantado/retrasado respecto al frame procesado | Definir una politica de frescura adecuada al procesado downstream |
| Unity no manda radio foveal | Radio/falloff no forman parte del contrato de metadata | Definir estos parametros en configuracion downstream si hacen falta |
| Cruce de seam en ERP | Un bbox UV ingenuo puede envolver mal | Usar campos ERP-aware del contrato de metadata |
| Metadata ausente/stale | El centro de gaze puede ser invalido | Gate por `gaze_hit` y edad de metadata |
| Builds antiguas de Unity pueden no tener `schema_version` | El parser vera version `0` | Tratar `0` como legacy y preferir la build Simulator entregada |

## Checklist de validacion

Antes de anadir procesado downstream:

- Las dependencias Python se instalan correctamente.
- El check de visibilidad NDI imprime una version.
- `StreamNDI` aparece en NDI Monitor.
- La build de Unity Simulator recibe la fuente Python.
- La consola Python muestra `[RX Viewport]`.
- `gaze_hit` pasa a `1` cuando se mira la superficie NDI.
- `gaze_uv` cambia al mover vista/cabeza.
- El marcador debug de gaze sigue el centro esperado.

Despues de anadir procesado downstream:

- El procesado responde a updates validos de `gaze_uv` como se espera.
- El comportamiento de fallback es estable cuando Unity deja de mandar metadata.
- El contenido ERP/360 se prueba alrededor del seam horizontal.
- El comportamiento de sesiones largas se prueba con media representativo.

## Resumen de handoff

La senal de gaze/viewport ya existe y esta parseada en Python. El objeto tipado `UnityViewportMetadata` es la superficie de integracion prevista; el XML crudo queda disponible para diagnostico con `--rx-metadata-verbose`. Radio/falloff, politica de frescura y comportamiento alrededor del seam ERP quedan intencionadamente en el procesado downstream.
