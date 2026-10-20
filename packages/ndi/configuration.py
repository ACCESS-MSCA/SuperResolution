"""Process-local NDI policy shared by GUI and command-line senders.

NDI_TRANSPORT is our application setting, not an NDI SDK environment variable.
Translate it before loading the SDK; never modify the user's global NDI config.
"""
from __future__ import annotations
import atexit
import copy
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile


def configuration(base: dict, transport: str, address: str) -> dict:
    if transport not in ("auto", "single-tcp"):
        raise ValueError("NDI_TRANSPORT must be auto or single-tcp")
    result = copy.deepcopy(base)
    ndi = result.setdefault("ndi", {})
    if not isinstance(ndi, dict):
        raise ValueError("NDI configuration 'ndi' must be an object")
    if transport == "single-tcp":
        # 'tcp' is optional multi-TCP. Disabling optional protocols keeps base
        # single-TCP, exactly as the existing production launcher configuration.
        for protocol in ("rudp", "tcp", "unicast", "multicast"):
            ndi.setdefault(protocol, {}).setdefault("send", {})["enable"] = False
    if address:
        ip = ipaddress.ip_address(address)
        if ip.version != 4 or ip.is_unspecified or ip.is_multicast or ip.is_loopback:
            raise ValueError("NDI_BIND_ADDRESS must be this Mac's LAN IPv4 address")
        ndi.setdefault("adapters", {})["allowed"] = [str(ip)]
    return result


def prepare_environment() -> dict:
    transport = os.environ.get("NDI_TRANSPORT", "auto")
    address = os.environ.get("NDI_BIND_ADDRESS", "").strip()
    if transport == "auto" and not address:
        return {"transport": transport, "address": None, "generated": False}
    # Fail clearly if a saved station address is no longer present on this Mac.
    if address:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((address, 0))
    source = Path(os.environ.get("NDI_CONFIG_DIR", str(Path.home() / ".ndi"))) / "ndi-config.v1.json"
    base = json.loads(source.read_text()) if source.exists() else {}
    data = configuration(base, transport, address)
    directory = Path(tempfile.mkdtemp(prefix="access-ndi-process-"))
    os.chmod(directory, 0o700)
    target = directory / "ndi-config.v1.json"
    target.write_text(json.dumps(data))
    os.chmod(target, 0o600)
    os.environ["NDI_CONFIG_DIR"] = str(directory)
    # Registered before the SDK destructor, so libndi stops first on exit.
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    return {"transport": transport, "address": address or None, "generated": True}
