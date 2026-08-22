#!/usr/bin/env python
"""Compatibility façade for the responsibility-split Unreal validators."""

from __future__ import annotations

import unreal_static_build as _build
import unreal_static_crossfile as _crossfile
import unreal_static_delegate as _delegate
import unreal_static_include as _include
import unreal_static_lifecycle as _lifecycle
import unreal_static_model as _model
import unreal_static_network as _network
import unreal_static_reflection as _reflection
import unreal_static_registry as _registry
import unreal_static_runner as _runner
import unreal_static_safety as _safety
import unreal_static_scan as _scan
from unreal_static_build import *  # noqa: F403
from unreal_static_crossfile import *  # noqa: F403
from unreal_static_delegate import *  # noqa: F403
from unreal_static_include import *  # noqa: F403
from unreal_static_lifecycle import *  # noqa: F403
from unreal_static_model import *  # noqa: F403
from unreal_static_network import *  # noqa: F403
from unreal_static_reflection import *  # noqa: F403
from unreal_static_registry import *  # noqa: F403
from unreal_static_runner import *  # noqa: F403
from unreal_static_safety import *  # noqa: F403
from unreal_static_scan import *  # noqa: F403

__all__ = [
    *_model.__all__,
    *_scan.__all__,
    *_reflection.__all__,
    *_delegate.__all__,
    *_lifecycle.__all__,
    *_build.__all__,
    *_include.__all__,
    *_network.__all__,
    *_crossfile.__all__,
    *_safety.__all__,
    *_registry.__all__,
    *_runner.__all__,
]
