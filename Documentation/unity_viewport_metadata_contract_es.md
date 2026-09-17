# Contrato de metadata de viewport Unity

Actualizado: 2026-07-06

Navegacion: [Indice](index_es.md) | [EN](unity_viewport_metadata_contract_en.md) | [HTML](unity_viewport_metadata_contract_es.html)

Este documento define la metadata emitida por la build de Unity entregada para reportar la region NDI visible y el centro de gaze de vuelta al streamer Python.

## Alcance

Unity emite un elemento XML de metadata NDI llamado `access_viewport`. Python lo recibe por el backchannel del sender NDI y lo parsea a `UnityViewportMetadata`.

Implementacion actual:

- Productor Unity: `NdiHeadViewportMetadataPayloadProvider`
- Receptor Python: `extensions/backchannel/receiver.py`
- Parser Python: `integrations/unity/parsers.py`
- Holder de estado runtime: `integrations/unity/handlers.py`
- Consumidor de overlay debug: `stream_video.py`

## Superficie de integracion

La superficie de integracion prevista en Python es el objeto tipado devuelto por `try_parse_unity_viewport(...)` y guardado en `UnityViewportStateHandler.state.latest`.

Hechos importantes de validez:

- `gaze_uv` es valido solo cuando `gaze_hit` es true.
- Los valores UV son floats normalizados en `[0, 1]`.
- El origen UV de Unity/textura es abajo-izquierda.
- El origen de frame NumPy es arriba-izquierda, por lo que `v` se invierte al mapear a pixeles.
- `uv_min/uv_max` es una bounding box UV axis-aligned y no basta por si sola para casos ERP que cruzan el seam.

Mapeo a pixel:

```python
pixel_x = int(round(u * (frame_width - 1)))
pixel_y = int(round((1.0 - v) * (frame_height - 1)))
```

## Flujo de metadata

```text
Unity head/camera + collider de superficie NDI
    -> XML access_viewport
    -> backchannel NDI de metadata
    -> NdiSenderBackchannelReceiver
    -> try_parse_unity_viewport(...)
    -> UnityViewportMetadata
```

## Ejemplo XML

```xml
<access_viewport
  schema_version="1"
  id="unity_receiver"
  seq="42"
  timestamp="123.456789"
  scene="NDI"
  camera="Main Camera"
  cam_px_w="1920"
  cam_px_h="1080"
  head_px="0.001"
  head_py="1.600"
  head_pz="-0.250"
  head_qx="0"
  head_qy="0.120"
  head_qz="0"
  head_qw="0.993"
  hit_any="1"
  hit_center="1"
  gaze_hit="1"
  corner_hits="4"
  plane_intersection="1"
  plane_intersection_any="1"
  uv_poly_n="4"
  uv_projection="MeshTextureCoordinates"
  uv_edge_samples="16"
  uv_contains_north_pole="0"
  uv_contains_south_pole="0"
  erp_frustum_valid="0"
  uv_cx="0.500"
  uv_cy="0.500"
  gaze_uv_x="0.500"
  gaze_uv_y="0.500"
  uv_min_x="0.250"
  uv_min_y="0.250"
  uv_max_x="0.750"
  uv_max_y="0.750"
  uv_poly0_x="0.250"
  uv_poly0_y="0.250"
  uv_poly1_x="0.250"
  uv_poly1_y="0.750"
  uv_poly2_x="0.750"
  uv_poly2_y="0.750"
  uv_poly3_x="0.750"
  uv_poly3_y="0.250" />
```

El payload real puede incluir atributos adicionales, especialmente puntos world y datos de frustum equirectangular.

## Sistema de coordenadas

- `u = 0`: borde izquierdo de la imagen fuente.
- `u = 1`: borde derecho.
- `v = 0`: borde inferior en UV de Unity/textura.
- `v = 1`: borde superior en UV de Unity/textura.

Los arrays de frame en NumPy usan origen arriba-izquierda:

```python
pixel_x = int(round(u * (frame_width - 1)))
pixel_y = int(round((1.0 - v) * (frame_height - 1)))
```

Aplicar clamp de UVs a `[0, 1]` antes de indexar un frame.

## Tipo Python parseado

`integrations/unity/parsers.py` expone:

```python
@dataclass(frozen=True)
class UnityViewportMetadata:
    schema_version: int
    source_id: str
    sequence: int
    scene: str
    camera: str
    uv_projection: str
    hit_any: bool
    plane_intersection: bool
    hit_center: bool
    gaze_hit: bool
    corner_hits: int
    contains_north_pole: bool
    contains_south_pole: bool
    erp_frustum_valid: bool
    erp_edge_normals: tuple[tuple[float, float, float], ...]
    erp_corner_directions: tuple[tuple[float, float, float], ...]
    uv_center: tuple[float, float]
    gaze_uv: tuple[float, float]
    gaze_world: tuple[float, float, float]
    uv_min: tuple[float, float]
    uv_max: tuple[float, float]
    uv_corners: tuple[tuple[float, float], ...]
    uv_corner_direct_hits: tuple[bool, bool, bool, bool]
    uv_polygon: tuple[tuple[float, float], ...]
```

`schema_version == 0` significa que el payload viene de una build Unity antigua sin `schema_version` explicito.

## Referencia de campos

| Campo | Tipo | Significado | Notas |
|---|---:|---|---|
| `schema_version` | int | Version del contrato. Version actual: `1`. | `0` significa legacy/sin version explicita. |
| `source_id` | str | Id de fuente de metadata en Unity. | Diagnostico. |
| `sequence` | int | Secuencia monotona del payload Unity. | Util para detectar repetidos/stale. |
| `scene` | str | Escena Unity que contiene la superficie NDI. | Diagnostico. |
| `camera` | str | Camara Unity usada para la proyeccion. | Diagnostico. |
| `uv_projection` | str | `MeshTextureCoordinates`, `EquirectangularSphere` o modo auto efectivo. | Determina interpretacion planar vs ERP. |
| `hit_any` | bool | Al menos un ray directo del viewport golpeo la superficie. | Confianza diagnostica. |
| `plane_intersection` | bool | Existe un poligono ROI visible sobre la superficie. | Gate para uso de poligono. |
| `hit_center` | bool | El ray central del viewport golpeo la superficie. | Fuente actual de validez de gaze. |
| `gaze_hit` | bool | `gaze_uv` es valido. | Gate de validez para `gaze_uv`. |
| `corner_hits` | int | Numero de esquinas del viewport con hit directo. | Confianza/debug. |
| `contains_north_pole` | bool | Viewport ERP contiene polo norte. | Manejo de poligono ERP. |
| `contains_south_pole` | bool | Viewport ERP contiene polo sur. | Manejo de poligono ERP. |
| `erp_frustum_valid` | bool | Datos de esquina/frustum ERP validos. | Para reconstruccion de geometria ERP. |
| `erp_edge_normals` | 4x vec3 | Normales de bordes great-circle del frustum ERP. | Containment ERP avanzado. |
| `erp_corner_directions` | 4x vec3 | Direcciones de esquinas del viewport ERP. | Geometria de frustum ERP. |
| `uv_center` | vec2 | Centro/centroide del ROI UV visible. | Centro fallback/diagnostico. |
| `gaze_uv` | vec2 | UV directo del centro de gaze. | Valido solo cuando `gaze_hit` es true. |
| `gaze_world` | vec3 | Punto world del hit central. | Diagnostico 3D opcional. |
| `uv_min` | vec2 | Minimo de bbox UV axis-aligned. | Bounds planares simples, no representacion ERP unica. |
| `uv_max` | vec2 | Maximo de bbox UV axis-aligned. | Bounds planares simples, no representacion ERP unica. |
| `uv_corners` | 4x vec2 | Esquinas proyectadas en orden BL, TL, TR, BR. | Reconstruccion debug/legacy. |
| `uv_corner_direct_hits` | 4 bools | Si cada esquina fue hit directo. | Confianza/debug. |
| `uv_polygon` | N vec2 | Poligono ROI visible clipeado. | Limite visible en espacio UV. |

## Notas ERP/equirectangular

Para `uv_projection == "EquirectangularSphere"`:

- `uv_min/uv_max` puede ser enganoso cuando el viewport cruza el seam horizontal.
- `uv_polygon`, `contains_north_pole`, `contains_south_pole` y `erp_corner_directions` aportan la geometria ERP-aware emitida por Unity.
- `gaze_uv` sigue siendo una coordenada UV normalizada y sigue dependiendo de `gaze_hit`.

## Frescura y timing

La metadata Unity se entrega como ultimo estado de control, no como metadata exacta por frame de video. El overlay debug actual en `stream_video.py` usa:

```text
viewport_stale_timeout_seconds = 3.0
```

Procesado sensible a latencia puede definir su propia politica de frescura segun requisitos de algoritmo y transporte.

## Reglas de compatibilidad

- Ignorar atributos XML desconocidos.
- Tolerar atributos opcionales ausentes.
- Tratar `schema_version == 1` como contrato actual.
- Tratar `schema_version == 0` como legacy/sin version explicita.
- Tratar `gaze_uv` como valido solo cuando `gaze_hit` es true.

## Limites conocidos

- Unity no envia radio foveal, falloff ni parametros de calidad SR.
- Python contiene actualmente un overlay debug, no un paso de procesado SR de produccion.
- El manejo de seam ERP pertenece al procesado downstream en espacio imagen.
