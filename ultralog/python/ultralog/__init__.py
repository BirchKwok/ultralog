"""
ultralog – High-performance logger powered by a Rust core.

All threading, locking, buffering and log-rotation are handled entirely
inside the compiled Rust extension (``ultralog._ultralog``).  This module
provides a thin Python wrapper for convenience.
"""

import sys
from typing import Optional

from ultralog._ultralog import UltraLog as _RustLog  # type: ignore[import]

__version__ = "0.5.0"


# ── Remote helper ─────────────────────────────────────────────────────────────
def _remote_post(server_url: str, auth_token: str, level: str, msg: str,
                 console_output: bool) -> None:
    try:
        import requests
        response = requests.post(
            f"{server_url}/log",
            json={"level": level, "message": msg},
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=5,
        )
        response.raise_for_status()
    except Exception as exc:
        if console_output:
            print(f"Remote logging failed: {exc}", file=sys.stderr)


# ── Public UltraLog ───────────────────────────────────────────────────────────

class UltraLog:
    """
    High-performance logger backed by a Rust core (via PyO3 / maturin).

    All threading, locking, buffering and rotation are handled entirely inside
    the Rust extension.  This Python class is a thin dispatch layer only.

    Parameters
    ----------
    name : str, optional
        Logger name.  Default: ``"UltraLog"``.
    fp : str, optional
        File path for local logging.
    level : str
        Minimum log level (``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR`` /
        ``CRITICAL``).  Default: ``"DEBUG"``.
    truncate_file : bool
        Truncate the log file on initialisation.  Default: ``False``.
    with_time : bool
        Prepend a timestamp to every message.  Default: ``True``.
    max_file_size : int
        Maximum file size in bytes before rotation.  Default: 10 MB.
    backup_count : int
        Number of rotated backup files to retain.  Default: ``5``.
    console_output : bool
        Echo log messages to stderr.  Default: ``True``.
    force_sync : bool
        Flush to disk after every write batch.  Default: ``False``.
    enable_rotation : bool
        Enable automatic log rotation.  Default: ``True``.
    file_buffer_size : int, optional
        Write-buffer size in bytes.  Default: 1 MB.
    batch_size : int, optional
        Maximum messages per write batch.  Default: ``1000``.
    flush_interval : float, optional
        Seconds between background flushes.  Default: ``0.05``.
    server_url : str, optional
        HTTP base URL of a remote ``ultralog.server``.
    auth_token : str, optional
        Bearer token for the remote server.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        fp: Optional[str] = None,
        level: str = "DEBUG",
        truncate_file: bool = False,
        with_time: bool = True,
        max_file_size: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        console_output: bool = True,
        force_sync: bool = False,
        enable_rotation: bool = True,
        file_buffer_size: Optional[int] = None,
        batch_size: Optional[int] = None,
        flush_interval: Optional[float] = None,
        server_url: Optional[str] = None,
        auth_token: Optional[str] = None,
    ):
        self._name = name or "UltraLog"
        self._server_url = server_url
        self._auth_token = auth_token
        self._mode = "remote" if (server_url and auth_token) else "local"
        self.console_output = console_output
        self._level = level
        self._logger = None

        if self._mode == "local":
            buf   = file_buffer_size or 1024 * 1024
            bat   = batch_size or 1000
            fi_ms = int((flush_interval or 0.05) * 1000)

            self._logger = _RustLog(
                name=self._name,
                fp=fp,
                level=level,
                truncate_file=truncate_file,
                with_time=with_time,
                max_file_size=max_file_size,
                backup_count=backup_count,
                console_output=console_output,
                force_sync=force_sync,
                enable_rotation=enable_rotation,
                buffer_size=buf,
                batch_size=bat,
                flush_interval_ms=fi_ms,
            )

    # ── Level property ────────────────────────────────────────────────────────

    @property
    def level(self) -> str:
        if self._logger is not None:
            return self._logger.level
        return self._level

    @level.setter
    def level(self, value: str) -> None:
        self._level = value
        if self._logger is not None:
            self._logger.level = value

    # ── Logging – single Rust FFI hop, zero Python-level locking ─────────────

    def log(self, msg: str, level: str = "INFO") -> None:
        if self._logger is not None:
            self._logger.log(msg, level)
        elif self._mode == "remote":
            _remote_post(
                self._server_url, self._auth_token, level, msg, self.console_output
            )

    def debug(self, msg: str) -> None:
        if self._logger is not None:
            self._logger.debug(msg)
        else:
            self.log(msg, "DEBUG")

    def info(self, msg: str) -> None:
        if self._logger is not None:
            self._logger.info(msg)
        else:
            self.log(msg, "INFO")

    def warning(self, msg: str) -> None:
        if self._logger is not None:
            self._logger.warning(msg)
        else:
            self.log(msg, "WARNING")

    def error(self, msg: str) -> None:
        if self._logger is not None:
            self._logger.error(msg)
        else:
            self.log(msg, "ERROR")

    def critical(self, msg: str) -> None:
        if self._logger is not None:
            self._logger.critical(msg)
        else:
            self.log(msg, "CRITICAL")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def flush(self) -> None:
        if self._logger is not None:
            self._logger.flush()

    def close(self) -> None:
        if self._logger is not None:
            self._logger.close()

    # ── Backend info ──────────────────────────────────────────────────────────

    @staticmethod
    def backend() -> str:
        """Always ``'rust'``."""
        return "rust"
