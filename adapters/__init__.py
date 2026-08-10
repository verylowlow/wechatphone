# -*- coding: utf-8 -*-
"""App adapter registry.

Usage:
    from adapters import get_app, list_apps, DEFAULT_APP
    cfg = get_app("dingtalk")

Each app's UI details (window titles / button names / hotkeys) are best
effort guesses — they MUST be verified on a real machine. See adapters/*.py
for per-app notes on what is uncertain.
"""
from __future__ import annotations

from adapters.base import AppConfig
from adapters.wechat import WECHAT
from adapters.dingtalk import DINGTALK
from adapters.wecom import WECOM

DEFAULT_APP = "wechat"

_REGISTRY: dict[str, AppConfig] = {
    WECHAT.key: WECHAT,
    DINGTALK.key: DINGTALK,
    WECOM.key: WECOM,
}


def get_app(key: str) -> AppConfig:
    cfg = _REGISTRY.get((key or "").strip().lower())
    if cfg is None:
        raise KeyError(f"未知应用 '{key}', 可选: {', '.join(_REGISTRY)}")
    return cfg


def list_apps() -> list[str]:
    return list(_REGISTRY)
