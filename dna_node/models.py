"""Domain models."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    REDUCING = "REDUCING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DRAINING = "DRAINING"
    DEAD = "DEAD"
    DISABLED = "DISABLED"


class ChunkStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    RETRY = "RETRY"


@dataclass
class ChunkSpec:
    chunk_id: str
    chunk_index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "ChunkSpec":
        return cls(**json.loads(s))


@dataclass
class ChunkResult:
    chunk_id: str
    chunk_index: int
    matches: int
    mismatches: int
    total_bases: int
    checksum: str
    output_path: str
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Manifest:
    run_id: str
    input_a_size: int
    input_b_size: int
    comparable_size: int
    chunk_size: int
    chunks: list[ChunkSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "run_id": self.run_id,
            "input_a_size": self.input_a_size,
            "input_b_size": self.input_b_size,
            "comparable_size": self.comparable_size,
            "chunk_size": self.chunk_size,
            "chunks": [asdict(c) for c in self.chunks],
            "warnings": self.warnings,
        })

    @classmethod
    def from_json(cls, s: str) -> "Manifest":
        d = json.loads(s)
        return cls(
            run_id=d["run_id"],
            input_a_size=d["input_a_size"],
            input_b_size=d["input_b_size"],
            comparable_size=d["comparable_size"],
            chunk_size=d["chunk_size"],
            chunks=[ChunkSpec(**c) for c in d["chunks"]],
            warnings=d.get("warnings", []),
        )


@dataclass
class LeaderInfo:
    node_id: str
    priority: int
    epoch: int
    token: str
    acquired_at: float

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "LeaderInfo":
        return cls(**json.loads(s))
