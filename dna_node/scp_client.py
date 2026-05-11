"""SCP/SSH client wrapper. Supports a "local" mode for demos via shared volume."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


class ScpError(RuntimeError):
    pass


class ScpClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ---- shared helpers ----
    def _ssh_opts(self) -> list[str]:
        opts = [
            "-o", f"ConnectTimeout={self.cfg.scp_connect_timeout_seconds}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
        ]
        if self.cfg.control_plane_ssh_key:
            opts += ["-i", self.cfg.control_plane_ssh_key]
        return opts

    def _remote(self, path: str) -> str:
        return f"{self.cfg.control_plane_user}@{self.cfg.control_plane_host}:{path}"

    def _run(self, cmd: list[str]) -> None:
        log.debug("exec: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise ScpError(f"cmd={cmd} rc={res.returncode} stderr={res.stderr.strip()}")

    def _retry(self, func, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(1, self.cfg.scp_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last = e
                log.warning("op failed attempt=%d/%d: %s",
                            attempt, self.cfg.scp_retries, e)
                time.sleep(min(2 ** (attempt - 1), 5))
        assert last is not None
        raise last

    # ---- public ----
    def ensure_remote_dir(self, remote_dir: str) -> None:
        if self.cfg.use_local_control_plane:
            Path(self._local_remote_path(remote_dir)).mkdir(parents=True, exist_ok=True)
            return
        self._retry(
            self._run,
            ["ssh", *self._ssh_opts(),
             f"{self.cfg.control_plane_user}@{self.cfg.control_plane_host}",
             f"mkdir -p {remote_dir}"],
        )

    def upload(self, local_path: str, remote_path: str) -> None:
        """Atomic-ish upload: write to remote .tmp then rename to final path."""
        remote_tmp = remote_path + ".tmp"
        if self.cfg.use_local_control_plane:
            self._local_upload(local_path, remote_path)
            return

        # ensure parent dir
        self.ensure_remote_dir(os.path.dirname(remote_path))
        self._retry(
            self._run,
            ["scp", *self._ssh_opts(), local_path, self._remote(remote_tmp)],
        )
        self._retry(
            self._run,
            ["ssh", *self._ssh_opts(),
             f"{self.cfg.control_plane_user}@{self.cfg.control_plane_host}",
             f"mv {remote_tmp} {remote_path}"],
        )

    def download(self, remote_path: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        if self.cfg.use_local_control_plane:
            src = self._local_remote_path(remote_path)
            if not os.path.exists(src):
                raise ScpError(f"local-mode source missing: {src}")
            shutil.copy2(src, local_path)
            return
        self._retry(
            self._run,
            ["scp", *self._ssh_opts(), self._remote(remote_path), local_path],
        )

    def remote_exists(self, remote_path: str) -> bool:
        if self.cfg.use_local_control_plane:
            return os.path.exists(self._local_remote_path(remote_path))
        try:
            self._run(
                ["ssh", *self._ssh_opts(),
                 f"{self.cfg.control_plane_user}@{self.cfg.control_plane_host}",
                 f"test -f {remote_path}"]
            )
            return True
        except ScpError:
            return False

    def remote_size(self, remote_path: str) -> int:
        if self.cfg.use_local_control_plane:
            return os.path.getsize(self._local_remote_path(remote_path))
        res = subprocess.run(
            ["ssh", *self._ssh_opts(),
             f"{self.cfg.control_plane_user}@{self.cfg.control_plane_host}",
             f"stat -c %s {remote_path}"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            raise ScpError(f"stat failed: {res.stderr.strip()}")
        return int(res.stdout.strip())

    # ---- local-mode helpers ----
    def _local_remote_path(self, remote_path: str) -> str:
        """Map an absolute "remote" path into the shared volume directory."""
        base = Path(self.cfg.control_plane_local_dir)
        # Strip the base_dir prefix if present so paths nest cleanly.
        rp = remote_path
        if rp.startswith(self.cfg.control_plane_base_dir):
            rp = rp[len(self.cfg.control_plane_base_dir):]
        rp = rp.lstrip("/")
        return str(base / rp)

    def _local_upload(self, local_path: str, remote_path: str) -> None:
        dst = self._local_remote_path(remote_path)
        tmp = dst + ".tmp"
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, tmp)
        os.replace(tmp, dst)
