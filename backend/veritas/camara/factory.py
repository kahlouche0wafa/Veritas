"""Client factory — one place decides live vs scripted, based on CAMARA_MODE.

Modes:
  * scripted (default when no NOKIA_* keys)  — always returns ScriptedCamaraClient
  * live                                     — always returns LiveCamaraClient (errors if keys missing)
  * auto                                     — live if keys present, otherwise scripted
"""

from __future__ import annotations

from ..config import SETTINGS
from .base import CamaraClient
from .live import LiveCamaraClient
from .scripted import ScriptedCamaraClient


def get_camara_client() -> CamaraClient:
    mode = SETTINGS.camara_mode
    if mode == "live":
        return LiveCamaraClient()
    if mode == "scripted":
        return ScriptedCamaraClient()
    # auto
    if SETTINGS.has_live_camara:
        return LiveCamaraClient()
    return ScriptedCamaraClient()
