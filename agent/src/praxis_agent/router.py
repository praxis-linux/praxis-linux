"""Decides which model alias serves a turn, and whether that alias leaves the machine.

Phase 0 has no per-turn classifier yet: every turn goes to the configured alias
(`PRAXIS_MODEL`, default `local-fast`). Aliases are defined in litellm_config.yaml.
"""

import os
from dataclasses import dataclass
from typing import Literal

Destination = Literal["local", "cloud"]

DEFAULT_MODEL = "local-fast"


@dataclass(frozen=True)
class Route:
    model: str
    destination: Destination


def destination_of(model: str) -> Destination:
    # Only aliases that declare themselves local are treated as local, so a
    # misnamed or unknown alias is audited and shown as a cloud call.
    return "local" if model.startswith("local-") else "cloud"


def route(intent: str) -> Route:
    model = os.environ.get("PRAXIS_MODEL") or DEFAULT_MODEL
    return Route(model=model, destination=destination_of(model))
