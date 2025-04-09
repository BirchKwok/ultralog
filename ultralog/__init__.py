import sys
import requests
from typing import Optional

from .local import UltraLog as LocalUltraLog
from .utils import LogFormatter

__version__ = "0.2.0"


class UltraLog:
    """
    Unified logger that can work in both local and remote modes.
    Automatically switches based on ULOG_MODE environment variable.
    """
    
    def __init__(
        self,
        name: Optional[str] = None,
        fp: Optional[str] = None,
        level: str = 'DEBUG',
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
        auth_token: Optional[str] = None
    ):
        """
        Initialize the unified logger.
        
        Parameters:
            name: Logger name (default: "Logger")
            fp: File path for local logging (default: None)
            level: Logging level (default: "INFO")
            truncate_file: Truncate file on initialization (default: False)
            with_time: Include timestamp in logs (default: True)
            max_file_size: Maximum file size before rotation (default: 10MB)
            backup_count: Number of backup files to keep (default: 5)
            console_output: Print logs to console (default: False)
            force_sync: Force synchronous writes (default: False)
            enable_rotation: Enable log rotation (default: True)
            file_buffer_size: Buffer size for file writes (default: 256KB)
            server_url: Remote logging server URL (default: None)
            auth_token: Authentication token for remote logging (default: None)
        """
        self._mode = 'local'
        self._server_url = server_url
        self._auth_token = auth_token

        self._formatter = LogFormatter(
            name=name or "UltraLogger",
            with_time=with_time
        )

        if self._server_url is not None and self._auth_token is not None:
            self._mode = 'remote'
        
        self.console_output = console_output
        self.name = name or "Logger"
        self._level = level
        
        if self._mode == 'local':
            self._logger = LocalUltraLog(
                name=name,
                fp=fp,
                level=level,
                truncate_file=truncate_file,
                with_time=with_time,
                max_file_size=max_file_size,
                backup_count=backup_count,
                console_output=console_output,
                force_sync=force_sync,
                enable_rotation=enable_rotation,
                file_buffer_size=file_buffer_size,
                batch_size=batch_size,
                flush_interval=flush_interval
            )
        else:
            self._logger = None  # Remote mode doesn't need local logger instance
            self.name = name or "Logger"
            self._level = level
            self.console_output = console_output
    
    @property
    def level(self) -> str:
        """Get the current logging level"""
        return self._level
    
    @level.setter
    def level(self, level: str) -> None:
        """Set the logging level"""
        if self._mode == 'local':
            self._logger.level = level
        else:
            self._level = level

    def log(self, msg: str, level: str = 'INFO') -> None:
        """Log a message with specified level"""
        if self._mode == 'local':
            self._logger.log(msg, level)
        else:
            self._remote_log(msg, level)
            
            if self.console_output:
                # prefix = f"{self.name} - {level} - " if not hasattr(self, 'with_time') else f" - {self.name} - {level} - "
                msg_bytes = self._formatter.format_message(msg, level)
                msg_str = msg_bytes.decode('utf-8').rstrip()
                print(msg_str, file=sys.stderr)

    def _remote_log(self, msg: str, level: str) -> None:
        """Send log message to remote server"""
        try:
            response = requests.post(
                f"{self._server_url}/log",
                json={
                    "level": level,
                    "message": msg
                },
                headers={
                    "Authorization": f"Bearer {self._auth_token}"
                },
                timeout=5
            )
            response.raise_for_status()
        except Exception as e:
            if self.console_output:
                print(f"Remote logging failed: {e}")

    # Convenience methods
    def debug(self, msg: str) -> None: self.log(msg, 'DEBUG')
    def info(self, msg: str) -> None: self.log(msg, 'INFO')
    def warning(self, msg: str) -> None: self.log(msg, 'WARNING')
    def error(self, msg: str) -> None: self.log(msg, 'ERROR')
    def critical(self, msg: str) -> None: self.log(msg, 'CRITICAL')

    def close(self):
        """Close the logger and release resources"""
        if self._mode == 'local' and hasattr(self, '_logger'):
            self._logger.close()
