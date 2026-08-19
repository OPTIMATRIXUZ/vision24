import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class CameraSpec:
    id: uuid.UUID
    name: str
    source: str
