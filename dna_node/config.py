"""Environment-driven configuration for a compute node."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return int(v)


def _resolve_concurrency(value: str, max_concurrency: int) -> int:
    if value == "auto":
        cpu = os.cpu_count() or 2
        return max(1, min(cpu - 1, max_concurrency))
    return max(1, min(int(value), max_concurrency))


@dataclass(frozen=True)
class Config:
    # Identity
    node_id: str
    node_priority: int
    can_be_leader: bool
    leader_can_process: bool

    # Concurrency
    worker_concurrency: int
    max_concurrency: int

    # Redis
    redis_url: str

    # Run
    run_id: str

    # Control plane
    control_plane_host: str
    control_plane_user: str
    control_plane_base_dir: str
    control_plane_ssh_key: str
    control_plane_local_dir: str  # only used when host == "local"

    # Local cache
    local_data_dir: str

    # Inputs
    input_a_name: str
    input_b_name: str

    # Processing
    chunk_size_bytes: int

    # Leader / heartbeat
    leader_lock_ttl_ms: int
    leader_renew_interval_seconds: float
    heartbeat_interval_seconds: float
    node_dead_after_seconds: float

    # SCP
    scp_connect_timeout_seconds: int
    scp_retries: int

    # Stream
    job_min_idle_ms: int

    @classmethod
    def from_env(cls) -> "Config":
        max_concurrency = _env_int("MAX_CONCURRENCY", 4)
        raw_conc = os.getenv("WORKER_CONCURRENCY", "auto")
        return cls(
            node_id=os.environ["NODE_ID"],
            node_priority=_env_int("NODE_PRIORITY", 100),
            can_be_leader=_env_bool("CAN_BE_LEADER", True),
            leader_can_process=_env_bool("LEADER_CAN_PROCESS", False),
            worker_concurrency=_resolve_concurrency(raw_conc, max_concurrency),
            max_concurrency=max_concurrency,
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
            run_id=os.getenv("RUN_ID", "run-001"),
            control_plane_host=os.getenv("CONTROL_PLANE_HOST", "local"),
            control_plane_user=os.getenv("CONTROL_PLANE_USER", "ubuntu"),
            control_plane_base_dir=os.getenv("CONTROL_PLANE_BASE_DIR", "/data/dna-demo"),
            control_plane_ssh_key=os.getenv("CONTROL_PLANE_SSH_KEY", ""),
            control_plane_local_dir=os.getenv("CONTROL_PLANE_LOCAL_DIR", "/control-plane"),
            local_data_dir=os.getenv("LOCAL_DATA_DIR", "/worker-cache"),
            input_a_name=os.getenv("INPUT_A_NAME", "A.clean"),
            input_b_name=os.getenv("INPUT_B_NAME", "B.clean"),
            chunk_size_bytes=_env_int("CHUNK_SIZE_BYTES", 32 * 1024 * 1024),
            leader_lock_ttl_ms=_env_int("LEADER_LOCK_TTL_MS", 7000),
            leader_renew_interval_seconds=float(os.getenv("LEADER_RENEW_INTERVAL_SECONDS", "2")),
            heartbeat_interval_seconds=float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "2")),
            node_dead_after_seconds=float(os.getenv("NODE_DEAD_AFTER_SECONDS", "10")),
            scp_connect_timeout_seconds=_env_int("SCP_CONNECT_TIMEOUT_SECONDS", 10),
            scp_retries=_env_int("SCP_RETRIES", 3),
            job_min_idle_ms=_env_int("JOB_MIN_IDLE_MS", 30000),
        )

    # Convenience paths
    @property
    def local_run_dir(self) -> Path:
        return Path(self.local_data_dir) / self.run_id

    @property
    def local_partials_dir(self) -> Path:
        return self.local_run_dir / "partials"

    @property
    def local_input_a(self) -> Path:
        return self.local_run_dir / self.input_a_name

    @property
    def local_input_b(self) -> Path:
        return self.local_run_dir / self.input_b_name

    @property
    def remote_run_dir(self) -> str:
        return f"{self.control_plane_base_dir}/runs/{self.run_id}"

    @property
    def remote_inputs_dir(self) -> str:
        return f"{self.control_plane_base_dir}/inputs"

    @property
    def remote_partials_dir(self) -> str:
        return f"{self.remote_run_dir}/partials"

    @property
    def remote_partials_meta_dir(self) -> str:
        return f"{self.remote_run_dir}/partials_meta"

    @property
    def remote_final_dir(self) -> str:
        return f"{self.remote_run_dir}/final"

    @property
    def use_local_control_plane(self) -> bool:
        return self.control_plane_host.lower() == "local"
