#!/usr/bin/env python
"""Map an immutable-generation transition to one retryable Direct capability failure."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_result import CapabilityResult, failure


def generation_transition_failure(error: BaseException) -> CapabilityResult:
    return failure(
        "RAG_GENERATION_TRANSITION",
        str(error),
        retry_allowed=True,
        retry_mode="same_arguments",
    )


def generation_transition_boundary(handler: Callable[..., CapabilityResult]):
    @wraps(handler)
    def guarded(*args: Any, **kwargs: Any) -> CapabilityResult:
        try:
            return handler(*args, **kwargs)
        except RagGenerationTransitionError as exc:
            return generation_transition_failure(exc)

    return guarded


__all__ = ["generation_transition_boundary", "generation_transition_failure"]
