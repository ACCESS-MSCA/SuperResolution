#!/bin/zsh

# Shared bootstrap and preflight helpers for the clickable launchers.
# This file is sourced, not launched directly.

# Set before any libndi initialization, including the preflight subprocess.
# In NDI's config, tcp means multi-TCP. Disabling all optional transports
# selects base single-TCP. This is process-local, never a machine-wide edit.
ndi_prepare_transport() {
    local repo_dir="$1"
    local transport="${NDI_TRANSPORT:-auto}"
    export NDI_TRANSPORT="$transport"
    case "$transport" in
        auto)
            print -- "[NDI] Transport: SDK/user configuration (auto)"
            ;;
        single-tcp)
            export NDI_CONFIG_DIR="$repo_dir/Launchers/Config/SingleTCP"
            if [[ ! -r "$NDI_CONFIG_DIR/ndi-config.v1.json" ]]; then
                ndi_fail "Falta la configuración SingleTCP: $NDI_CONFIG_DIR"
                return 1
            fi
            print -- "[NDI] Transport: single TCP (isolated sender configuration)"
            ;;
        *)
            ndi_fail "NDI_TRANSPORT debe ser auto o single-tcp."
            return 1
            ;;
    esac
}

ndi_fail() {
    print -u2 -- "[NDI] ERROR: $*"
    print -u2 -- "[NDI] Pulsa Enter para cerrar."
    if [[ -t 0 && "${NDI_NO_PAUSE:-0}" != "1" ]]; then
        read -r
    fi
    return 1
}

ndi_find_base_python() {
    local candidate
    for candidate in \
        "${NDI_BOOTSTRAP_PYTHON:-}" \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /opt/homebrew/bin/python3.12 \
        /usr/local/bin/python3.12 \
        "${commands[python3]:-}"; do
        [[ -n "$candidate" && -x "$candidate" ]] || continue
        "$candidate" -c 'import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] <= (3, 13)))' \
            >/dev/null 2>&1 || continue
        print -r -- "$candidate"
        return 0
    done
    return 1
}

ndi_prepare_python() {
    local repo_dir="$1"
    local venv_dir="${NDI_VENV_DIR:-$repo_dir/.venv}"
    local python="$venv_dir/bin/python3"
    local base_python

    if [[ ! -x "$python" ]]; then
        base_python="$(ndi_find_base_python)" || {
            ndi_fail "Se necesita Python 3.10-3.13 para crear el entorno local."
            return 1
        }
        print -- "[NDI] Primera ejecución: creando $venv_dir con $base_python ..."
        "$base_python" -m venv "$venv_dir" || {
            ndi_fail "No se pudo crear el entorno Python en $venv_dir."
            return 1
        }
    fi

    if ! "$python" -c 'import av, numpy' >/dev/null 2>&1; then
        print -- "[NDI] Instalando dependencias fijadas en requirements.txt ..."
        "$python" -m pip install --disable-pip-version-check -r "$repo_dir/requirements.txt" || {
            ndi_fail "No se pudieron instalar las dependencias. Comprueba Internet y vuelve a ejecutar el lanzador."
            return 1
        }
    fi

    "$python" -c 'from ndi_native import get_ndi_runtime; print("[NDI] Runtime:", get_ndi_runtime().version_string())' || {
        ndi_fail "No se pudo cargar libndi. Instala NDI Runtime/Tools y vuelve a intentarlo."
        return 1
    }

    export NDI_PYTHON="$python"
}

ndi_resolve_video() {
    local repo_dir="$1"
    local requested="$2"
    local resolved="$requested"
    local picked

    [[ "$resolved" = /* ]] || resolved="$repo_dir/$resolved"
    if [[ -f "$resolved" ]]; then
        print -r -- "$resolved"
        return 0
    fi

    print -u2 -- "[NDI] No se encuentra el vídeo configurado: $resolved"
    if [[ "${NDI_VIDEO_PICKER:-1}" == "1" && -x /usr/bin/osascript ]]; then
        picked="$(/usr/bin/osascript <<'APPLESCRIPT' 2>/dev/null
try
    POSIX path of (choose file with prompt "Selecciona el vídeo que quieres emitir por NDI")
on error number -128
    return ""
end try
APPLESCRIPT
)"
        picked="${picked%$'\n'}"
        if [[ -n "$picked" && -f "$picked" ]]; then
            print -r -- "$picked"
            return 0
        fi
    fi
    return 1
}
