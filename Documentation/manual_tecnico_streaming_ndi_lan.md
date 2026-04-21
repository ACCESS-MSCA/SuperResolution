# Manual Técnico Completo - Streaming NDI LAN

Actualizado: 2026-04-21

## 1. Resumen Ejecutivo

Este proyecto publica una señal NDI por red local (LAN) desde un archivo de vídeo local, con audio sincronizado y modo dual opcional para validación visual (segunda señal con overlay animado).

- Punto de entrada: `stream_video.py`
- Módulo auxiliar: `utils.py`
- Dependencias Python: `cyndilib`, `numpy`
- Herramientas externas: `ffmpeg`, `ffprobe`

## 2. Estructura y Componentes

| Ruta | Rol | Notas |
|---|---|---|
| `stream_video.py` | Orquestación completa: metadata, decode A/V, envío NDI, pacing | Incluye recuperación ante fallo del decoder |
| `utils.py` | Creación de sender NDI y overlay | Encapsula `VideoSendFrame` |
| `requirements.txt` | Dependencias Python | Runtime mínimo |
| `Videos/` | Vídeos de prueba/reproducción | Ruta por defecto |
| `Documentation/` | Manuales técnicos | HTML ES/EN + Markdown ES |

## 3. Stack Técnico

| Componente | Uso real | Motivo |
|---|---|---|
| `cyndilib` | `Sender`, `VideoSendFrame`, `AudioSendFrame` | API clara para envío NDI vídeo+audio |
| `numpy` | Buffers y operaciones por frame | Rendimiento y control de memoria |
| `ffprobe` | Lectura de resolución/fps/frame count | Metadata robusta previa |
| `ffmpeg` | Decode de vídeo BGRA + audio float32 | Compatibilidad multimedia y estabilidad |

Nota de diseño: el pipeline evita `cv2` para prevenir conflictos nativos de librerías multimedia en macOS con ciertos entornos NDI.

## 4. Arquitectura del Pipeline

```text
CLI -> parse args -> ffprobe metadata -> sender config (NDI)
    -> ffmpeg video raw BGRA pipe + ffmpeg audio f32le
    -> loop:
         BGRA frame
         bloque audio (channels,samples)
         write_video_and_audio()
         pacing monotónico
```

Contratos de datos:

- Video: `BGRA`, 8 bits/canal.
- Bytes por frame: `width * height * 4`.
- Audio: `float32`, shape `(channels, samples)`.
- Sample rate configurado: 48 kHz, 2 canales.

## 5. Flujo de Ejecución Paso a Paso

1. Parseo de argumentos y selección de vídeo de entrada.
2. Lectura de metadata con `ffprobe`.
3. Configuración del sender NDI principal.
4. Cálculo de `audio_samples_per_frame`.
5. Decode completo de audio a memoria.
6. Arranque de decoder de vídeo por pipe BGRA.
7. Bucle por frame: leer vídeo, recortar audio, enviar NDI.
8. Reinicio del decoder si el pipe falla.
9. Pacing temporal con reloj monotónico acumulativo.

## 6. Análisis por Función (`stream_video.py`)

| Función | Líneas | Responsabilidad |
|---|---|---|
| `_parse_fps` | 26-33 | Normaliza fps y fallback seguro |
| `_probe_video` | 35-90 | Consulta metadata con `ffprobe` |
| `_start_video_decoder` | 92-115 | Lanza `ffmpeg` en loop y BGRA raw |
| `_read_exact` | 117-128 | Lectura exacta de bytes por frame |
| `_decode_audio_to_array` | 130-175 | Decode audio a `float32` planar |
| `stream_video` | 177-331 | Setup, bucle, envío A/V, dual, cleanup |
| Bloque main | 334-340 | Entry point CLI y default de vídeo |

## 7. Análisis por Función (`utils.py`)

| Función | Líneas | Responsabilidad |
|---|---|---|
| `_pixelate_roi` | 10-16 | Pixelado simple por submuestreo |
| `draw_square` | 19-43 | Overlay animado con movimiento sinusoidal |
| `make_sender` | 46-57 | Setup de sender NDI y `VideoSendFrame` |

## 8. Temporización y Sincronización

### 8.1 Pacing de vídeo

- Estrategia acumulativa: `next_frame_time += frame_duration`.
- Si hay overrun, se resincroniza con `now` para evitar deriva continua.

### 8.2 Relación audio/frame

- `audio_samples_per_frame = round(sample_rate / fps)`.
- Si la relación es fraccional, se prioriza bloque fijo y se avisa por consola.

### 8.3 Integridad de buffer

- `np.frombuffer(...).copy()` asegura memoria escribible.
- Evita: `ValueError: buffer source array is read-only`.

## 9. Modo Dual

Con `--dual`, se crea una segunda fuente NDI con sufijo `-Square`.

- Salida principal: vídeo limpio.
- Salida secundaria: overlay animado.
- Ambas pueden compartir el mismo bloque de audio por frame.

## 10. Operativa

Instalación:

```bash
cd <project-root>
python3 -m pip install -r requirements.txt
```

Ejecución:

```bash
python3 ./stream_video.py
python3 ./stream_video.py Videos/alhaja.mp4
python3 ./stream_video.py Videos/alhaja.mp4 --dual
```

Validación rápida:

1. Abrir NDI Monitor (u otro receptor NDI).
2. Verificar fuente principal.
3. En dual, verificar fuente secundaria.
4. Confirmar audio si el asset contiene pista.

## 11. Troubleshooting Avanzado

| Síntoma | Causa probable | Acción recomendada |
|---|---|---|
| No hay salida NDI | Runtime NDI o red | Verificar runtime y mismo segmento LAN |
| No hay audio | ffmpeg ausente o archivo sin pista | Instalar ffmpeg y validar pista |
| Jitter / stutter | Sobrecarga o overruns de timing | Reducir resolución/fps del asset |
| Error read-only buffer | Buffer inmutable | Usar copia writable (ya integrado) |
| Fallo intermitente de frames | Pipe de decoder cerrado | Reinicio automático del decoder |

## 12. Rendimiento y Escalabilidad

- Ruta crítica: decode + envío NDI por frame.
- Modo dual incrementa coste (copias/overlay/envío).
- En hosts limitados, priorizar assets con bitrate/fps moderado.

Recomendaciones:

- Usar vídeos con fps estable.
- Mantener stack NDI y sistema actualizado.
- Monitorizar warnings de timing en consola.

## 13. Guía de Extensión

1. Añadir overlays nuevos en `utils.py`.
2. Añadir flags/modos en el parser CLI del bloque `__main__`.
3. Añadir telemetría de loop cada N frames.
4. Escalar a múltiples fuentes con varios senders sincronizados.

## 14. Referencias de Código Clave

| Archivo | Líneas | Punto clave |
|---|---|---|
| `stream_video.py` | 35-90 | Metadata robusta con ffprobe |
| `stream_video.py` | 130-175 | Decode audio a estructura interna |
| `stream_video.py` | 248-331 | Bucle principal + recuperación |
| `stream_video.py` | 271-273 | Fix de buffer escribible |
| `utils.py` | 46-57 | Setup de sender y formato frame |

## 15. Checklist de Validación

1. Dependencias Python instaladas.
2. `ffmpeg`/`ffprobe` en PATH.
3. Ejecución estable modo normal.
4. Ejecución estable modo `--dual`.
5. Audio y vídeo visibles en receptor NDI.

## 16. Conclusión Técnica

La implementación actual ofrece un pipeline NDI LAN estable y mantenible, con sincronización práctica de vídeo/audio y recuperación automática ante cortes del decoder. Esta documentación permite operar, depurar y extender el sistema con bajo riesgo.
