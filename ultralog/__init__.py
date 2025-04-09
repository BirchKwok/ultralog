import os
import sys
import threading
import time
import queue
from datetime import datetime
from typing import Optional

from .utils import get_env_variable


class UltraLog:
    """
    High-performance thread-safe logger with optimized file writing and rotation.
    """

    _LOG_LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
    _TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    _TIMESTAMP_CACHE_TIME = 0.5
    _DEFAULT_FILE_BUFFER_SIZE = 256 * 1024  # 256KB
    _BATCH_SIZE = 50  # Default batch size
    _FLUSH_INTERVAL = 0.05  # Default flush interval
    _MAX_MEMORY_USAGE = 100  # MB - soft memory limit
    _CRITICAL_MEMORY_USAGE = 150  # MB - hard memory limit
    _LARGE_MESSAGE_THRESHOLD = 1024  # Bytes - consider message large if bigger

    def __init__(
        self,
        name: Optional[str] = None,
        fp: Optional[str] = None,
        level: str = 'DEBUG',
        truncate_file: bool = False,
        with_time: bool = True,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        console_output: bool = True,
        force_sync: bool = False,
        enable_rotation: bool = True,
        file_buffer_size: Optional[int] = None,
        batch_size: Optional[int] = None,
        flush_interval: Optional[float] = None,
    ):
        """
        Initialize the logger.
        
        Parameters:
            name: Logger name (default: "Logger")
            fp: File path for logging (default: None)
            level: Logging level (default: "INFO")
            truncate_file: Truncate file on initialization (default: False)
            with_time: Include timestamp in logs (default: True)
            max_file_size: Maximum file size before rotation (default: 10MB)
            backup_count: Number of backup files to keep (default: 5)
            console_output: Print logs to console (default: False)
            force_sync: Force synchronous writes (default: False)
            enable_rotation: Enable log rotation (default: True)
            file_buffer_size: Buffer size for file writes (default: 256KB)
        """
        # Initialize basic attributes
        self.name = name or "Logger"
        self.fp = fp
        self._level = self._LOG_LEVELS.get(
            get_env_variable('ULOG_LEVEL', default=level, default_type=str).upper(), 20)
        self.with_time = with_time
        self.max_file_size = max_file_size
        self.backup_count = backup_count
        self.console_output = console_output
        self.force_sync = force_sync
        self.enable_rotation = enable_rotation
        self._FILE_BUFFER_SIZE = file_buffer_size or self._DEFAULT_FILE_BUFFER_SIZE
        self._BATCH_SIZE = batch_size or self._BATCH_SIZE
        self._FLUSH_INTERVAL = flush_interval or self._FLUSH_INTERVAL

        # File handling
        self._file = None
        self._file_handle = None
        self._current_size = 0
        self._closed = False
        self._write_queue = queue.Queue()
        self._batch_buffer = []
        self._batch_lock = threading.Lock()
        self._last_flush_time = time.time()

        # Timestamp caching
        self._last_timestamp = ""
        self._last_timestamp_time = 0
        self._timestamp_lock = threading.Lock()

        # File operations lock
        self._file_lock = threading.Lock()

        # Initialize file handling
        if fp:
            if truncate_file:
                self._truncate_file()
            self._open_file()

        # Start background writer thread
        self._writer_thread = threading.Thread(
            target=self._batch_writer,
            daemon=True
        )
        self._writer_thread.start()

    @property
    def level(self):
        """Thread-safe level getter"""
        return self._level

    @level.setter 
    def level(self, value):
        """Thread-safe level setter with type conversion"""
        if isinstance(value, str):
            self._level = self._LOG_LEVELS.get(value.upper(), 20)
        else:
            self._level = int(value)

    def _cleanup(self):
        """Instance cleanup"""
        self.close()

    def _truncate_file(self):
        """Truncate log file"""
        if self.fp and os.path.exists(self.fp):
            try:
                with open(self.fp, 'w'):
                    pass
            except Exception as e:
                self._safe_console_output(f"Error truncating log file: {e}")

    def _open_file(self):
        """Open log file with error handling"""
        if not self.fp:
            return

        try:
            self._file_handle = open(self.fp, 'ab', buffering=self._FILE_BUFFER_SIZE)
            self._file = self._file_handle
            self._current_size = os.path.getsize(self.fp) if os.path.exists(self.fp) else 0
        except Exception as e:
            self._safe_console_output(f"Error opening log file: {e}")
            self._file = None
            self._file_handle = None

    def _safe_console_output(self, message: str):
        """Thread-safe console output"""
        if self.console_output:
            try:
                print(message, file=sys.stderr)
            except:
                pass

    def __del__(self):
        """Ensure proper cleanup when logger is garbage collected"""
        self.close()

    def _rotate_log(self):
        """Thread-safe log rotation with optional compression"""
        if not self.fp or not self.enable_rotation:
            return

        with self._global_lock:
            # Double-check size under lock
            if self._current_size <= self.max_file_size:
                return

            try:
                # Close current file
                if self._file:
                    self._file.close()
                    self._file = None
                    self._file_handle = None

                # Rotate backups
                for i in range(self.backup_count, 0, -1):
                    src = f"{self.fp}.{i-1}" if i > 1 else self.fp
                    dst = f"{self.fp}.{i}"

                    if os.path.exists(src):
                        if i == self.backup_count:
                            os.remove(src)  # Remove oldest backup
                        else:
                            os.rename(src, dst)

                # Reopen new log file
                self._open_file()
            except Exception as e:
                self._safe_console_output(f"Log rotation failed: {e}")

    def _get_timestamp(self) -> str:
        """Get cached timestamp (thread-safe)"""
        current_time = time.time()
        if current_time - self._last_timestamp_time > self._TIMESTAMP_CACHE_TIME:
            with self._timestamp_lock:
                if current_time - self._last_timestamp_time > self._TIMESTAMP_CACHE_TIME:
                    self._last_timestamp = datetime.now().strftime(self._TIME_FORMAT)
                    self._last_timestamp_time = current_time
        return self._last_timestamp

    def _get_level_prefix(self, level: str) -> str:
        """Dynamic level prefix generation"""
        return f"{self.name} - {level} - " if not self.with_time else f" - {self.name} - {level} - "

    def _format_message(self, msg: str, level: str) -> bytes:
        """Format log message as bytes"""
        prefix = self._get_level_prefix(level)
        timestamp = self._get_timestamp() if self.with_time else ""
        return f"{timestamp}{prefix}{msg}\n".encode('utf-8')

    def _batch_writer(self):
        """Background thread that writes batches of messages"""
        while not self._closed:
            try:
                # Get all available messages from queue
                batch = []
                while True:
                    try:
                        msg_bytes = self._write_queue.get_nowait()
                        batch.append(msg_bytes)
                        if len(batch) >= self._BATCH_SIZE:
                            break
                    except queue.Empty:
                        break

                if batch:
                    self._flush_batch(batch)
                
                # Small sleep to prevent busy waiting
                time.sleep(self._FLUSH_INTERVAL)

            except Exception as e:
                self._safe_console_output(f"Error in batch writer: {e}")

    def _flush_batch(self, batch):
        """Flush the given batch of messages to disk"""
        if not self.fp or not batch:
            return

        batch_size = sum(len(msg) for msg in batch)

        with self._file_lock:
            try:
                # Check for rotation
                if (self.enable_rotation and 
                    self._current_size + batch_size > self.max_file_size):
                    self._rotate_log()
                    self._current_size = 0

                # Write batch
                with open(self.fp, 'ab', buffering=self._FILE_BUFFER_SIZE) as f:
                    for msg_bytes in batch:
                        f.write(msg_bytes)
                        self._current_size += len(msg_bytes)
                    if self.force_sync:
                        f.flush()
            except Exception as e:
                self._safe_console_output(f"Error writing batch to log: {e}")

    def log(self, msg: str, level: str = 'INFO') -> None:
        """Asynchronous logging with level checking"""
        if self._closed:
            return
            
        level_value = self._LOG_LEVELS.get(level.upper(), 20)
        if level_value < self.level:
            return

        msg_bytes = self._format_message(msg, level)
        msg_str = msg_bytes.decode('utf-8').rstrip()

        # Console output
        if self.console_output:
            self._safe_console_output(msg_str)

        # Queue message for file output
        if self.fp:
            self._write_queue.put(msg_bytes)


    # Convenience methods
    def debug(self, msg: str) -> None: self.log(msg, 'DEBUG')
    def info(self, msg: str) -> None: self.log(msg, 'INFO')
    def warning(self, msg: str) -> None: self.log(msg, 'WARNING')
    def error(self, msg: str) -> None: self.log(msg, 'ERROR')
    def critical(self, msg: str) -> None: self.log(msg, 'CRITICAL')

    def close(self):
        """Close the logger and release resources"""
        if self._closed:
            return
            
        self._closed = True
        
        # Process all remaining messages in queue
        while not self._write_queue.empty():
            batch = []
            while not self._write_queue.empty() and len(batch) < self._BATCH_SIZE:
                try:
                    msg_bytes = self._write_queue.get_nowait()
                    batch.append(msg_bytes)
                except queue.Empty:
                    break
            if batch:
                self._flush_batch(batch)
        
        # Wait for writer thread to finish
        if hasattr(self, '_writer_thread') and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1.0)
        
        # Close file handles
        if self._file_handle:
            try:
                self._file_handle.flush()
                self._file_handle.close()
            except:
                pass
            self._file_handle = None
            self._file = None
