"""Compatibility facade for the Unreal contract consumers."""
from engine_contracts import *
from engine_contracts import validate_contracts as _validate_contracts


def validate_contracts(data):
    return _validate_contracts(data, expected_engine="unreal")
