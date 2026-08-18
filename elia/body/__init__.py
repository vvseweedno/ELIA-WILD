"""ELIA WILD sensorimotor body adapters.

Adapters expose configured capabilities; they never expand authority merely because
an LLM asks for a new endpoint, executable, server, or credential.
"""

from .fabric import SensorimotorFabric
from .types import BodyCapability, BodyResult

__all__ = ["BodyCapability", "BodyResult", "SensorimotorFabric"]
