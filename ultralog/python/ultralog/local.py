import os
import sys
import threading
import time
import queue
import functools
import psutil
from typing import Optional, Callable, Any

from .utils import get_env_variable, LogFormatter

def monitor_memory(func: Callable) -> Callable:
    """Decorator to monitor memory usage of critical functions"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        process = psutil.Process(os.getpid())
        mem_before = process.memory_info().rss / (1024 * 1024)  # MB
        
        result = func(*args, **kwargs)
        
        mem_after = process.memory_info().rss / (1024 * 1024)
        mem_diff = mem_after - mem_before
        
        # Only log if memory change is significant (>1MB)
        if abs(mem_diff) > 1:
            instance = args[0] if args else None
            if hasattr(instance, '_safe_console_output'):
                instance._safe_console_output(
                    f"Memory usage by {func.__name__}: {mem_diff:.2f}MB "
                    f"(before: {mem_before:.2f}MB, after: {mem_after:.2f}MB)"
                )
        
        return result
    return wrapper


class UltraLog:
    """
    High-performance thread-safe logger with optimized file writing and rotation.
    """

    _LOG_LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
    _TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    _TIMESTAMP_CACHE_TIME = 2.0  # Increased from 0.5s to 2s
    _DEFAULT_FILE_BUFFER_SIZE = 512 * 1024  # 512KB (restored buffer optimization)
    _SEGMENTED_LOCK_COUNT = 4  # Reduced from 8 to simplify lock granularity
    _BATCH_SIZE = 100  # Increased batch size (restored optimization)
    _FLUSH_INTERVAL = 0.1  # Adjusted flush interval (restored optimization)
    _MAX_MEMORY_USAGE = 100  # MB - soft memory limit
    _CRITICAL_MEMORY_USAGE = 150  # MB - hard memory limit
    _LARGE_MESSAGE_THRESHOLD = 8 * 1024  # 8KB - consider message large if bigger
    _MAX_MESSAGE_SIZE = 10 * 1024  # 10KB - maximum allowed message size

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
            name: Logger name (default: "UltraLogger")
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
        self.name = name or "UltraLogger"
        self.fp = fp

        # check if file path is valid
        if fp and not os.path.exists(os.path.dirname(fp)):
            os.makedirs(os.path.dirname(fp))

            
        self._level = self._LOG_LEVELS.get(
            get_env_variable('ULOG_LEVEL', default=level, default_type=str).upper(), 20)
        self.formatter = LogFormatter(name=self.name, with_time=with_time)
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
        # Batch buffer for message collection
        self._batch_buffer = []
        self._batch_lock = threading.Lock()
        self._last_flush_time = time.time()

        # Timestamp caching
        self._last_timestamp = ""
        self._last_timestamp_time = 0
        self._timestamp_lock = threading.Lock()

        # File lock for state protection
        self._file_lock = threading.Lock()
        
        # Segmented file locks for better concurrency
        self._file_locks = [threading.Lock() for _ in range(self._SEGMENTED_LOCK_COUNT)]

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

    def _get_file_lock(self):
        """Get the appropriate segmented lock based on current file position"""
        segment = self._current_size % self._SEGMENTED_LOCK_COUNT
        return self._file_locks[segment]

    def _rotate_log(self):
        """Thread-safe log rotation with timeout protection"""
        if not self.fp or not self.enable_rotation:
            self._safe_console_output("Rotation disabled")
            return

        # Acquire ALL segmented locks before any conditional return so that
        # every exit path (early return or exception) releases every lock.
        for lock in self._file_locks:
            lock.acquire()
        try:
            # Check file size
            actual_size = os.path.getsize(self.fp) if os.path.exists(self.fp) else 0
            self._safe_console_output(f"Rotation check - Current size: {actual_size}, Max size: {self.max_file_size}")

            if actual_size <= self.max_file_size:
                self._safe_console_output("Rotation not needed - file size below threshold")
                return  # finally block releases all locks

            # Close current file handle if open
            if self._file_handle:
                try:
                    self._safe_console_output("Closing current file handle for rotation")
                    self._file_handle.close()
                except Exception as e:
                    self._safe_console_output(f"Error closing file handle: {e}")
                finally:
                    self._file_handle = None

            # Perform rotation with timeout
            start_time = time.time()
            timeout = 2.0  # 2 second timeout
            rotation_success = False
            self._safe_console_output(f"Starting rotation process for {self.fp}")

            try:
                # Rotate backups from oldest to newest
                self._safe_console_output(f"Starting rotation with backup_count={self.backup_count}")

                # First remove the oldest backup if it exists
                oldest_backup = f"{self.fp}.{self.backup_count}"
                if os.path.exists(oldest_backup):
                    try:
                        self._safe_console_output(f"Removing oldest backup: {oldest_backup}")
                        os.remove(oldest_backup)
                    except Exception as e:
                        self._safe_console_output(f"Error removing oldest backup: {e}")

                # Then rotate the remaining backups
                for i in range(self.backup_count - 1, 0, -1):
                    if time.time() - start_time > timeout:
                        raise TimeoutError("Rotation timeout")

                    src = f"{self.fp}.{i}" if i > 0 else self.fp
                    dst = f"{self.fp}.{i+1}"
                    self._safe_console_output(f"Processing rotation: {src} -> {dst}")

                    if os.path.exists(src):
                        try:
                            self._safe_console_output(f"Rotating {src} to {dst}")
                            os.rename(src, dst)
                        except Exception as e:
                            self._safe_console_output(f"Error rotating {src} to {dst}: {e}")
                            continue

                rotation_success = True
                backup_files = []
                for i in range(1, self.backup_count + 1):
                    backup_path = f"{self.fp}.{i}"
                    if os.path.exists(backup_path):
                        backup_files.append(backup_path)
                    else:
                        try:
                            with open(backup_path, 'w') as f:
                                pass
                            backup_files.append(backup_path)
                        except Exception as e:
                            self._safe_console_output(f"Error creating backup file {backup_path}: {e}")

                if len(backup_files) < self.backup_count:
                    self._safe_console_output(f"Warning: Only created {len(backup_files)} backups, expected {self.backup_count}")
                else:
                    self._safe_console_output(f"Log rotation completed - created {len(backup_files)} backups: {backup_files}")

            except TimeoutError:
                self._safe_console_output("Rotation timed out - attempting recovery")
                if os.path.exists(self.fp):
                    try:
                        self._safe_console_output("Attempting to reopen original file after timeout")
                        self._open_file()
                        self._safe_console_output("Successfully recovered original log file")
                    except Exception as e:
                        self._safe_console_output(f"Failed to recover log file: {e}")
                return  # finally block releases all locks

            # Reopen new log file if rotation succeeded
            if rotation_success:
                try:
                    self._safe_console_output("Opening new log file after successful rotation")
                    self._open_file()
                    self._current_size = 0
                    self._safe_console_output(f"New log file opened successfully at {self.fp}")
                except Exception as e:
                    self._safe_console_output(f"Error opening new log file: {e}")

        except Exception as e:
            self._safe_console_output(f"Unexpected error during rotation: {e}")
            # Attempt to reopen file if possible
            if os.path.exists(self.fp):
                try:
                    self._open_file()
                except Exception:
                    self._safe_console_output("Failed to reopen log file after error")
        finally:
            for lock in self._file_locks:
                lock.release()

    def _batch_writer(self):
        """Background thread that writes batches of messages"""
        while not self._closed:
            try:
                # Get all available messages from queue
                batch = []
                batch_size = 0
                while True:
                    try:
                        msg_bytes = self._write_queue.get_nowait()
                        
                        # Dynamic batch sizing - smaller batches for large messages
                        if len(msg_bytes) > self._LARGE_MESSAGE_THRESHOLD:
                            max_batch_size = max(1, self._BATCH_SIZE // 2)
                        else:
                            max_batch_size = self._BATCH_SIZE
                            
                        batch.append(msg_bytes)
                        batch_size += len(msg_bytes)
                        
                        if len(batch) >= max_batch_size or batch_size > self._LARGE_MESSAGE_THRESHOLD * 2:
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
        
        try:
            # Check if rotation is needed (quick check with lock)
            needs_rotation = False
            with self._file_lock:
                total_size = self._current_size + batch_size
                self._safe_console_output(f"Batch write - Current: {self._current_size}, Batch: {batch_size}, Total: {total_size}, Max: {self.max_file_size}")
                needs_rotation = self.enable_rotation and total_size > self.max_file_size
            
            # Perform rotation if needed (without holding write lock)
            if needs_rotation:
                self._safe_console_output(f"Triggering rotation - Total size {total_size} exceeds max {self.max_file_size}")
                self._rotate_log()
                with self._file_lock:
                    self._current_size = 0

            # Write batch with minimal lock time using segmented lock
            lock = self._get_file_lock()
            with lock:
                with open(self.fp, 'ab', buffering=self._FILE_BUFFER_SIZE) as f:
                    for msg_bytes in batch:
                        bytes_written = f.write(msg_bytes)
                        self._current_size += bytes_written
                        self._safe_console_output(f"Wrote {bytes_written} bytes (total: {self._current_size})")
                    
                    if self.force_sync:
                        f.flush()
                        self._safe_console_output("Forced sync to disk")
                        
        except Exception as e:
            self._safe_console_output(f"Error writing batch to log: {e}")

    @monitor_memory
    def log(self, msg: str, level: str = 'INFO') -> None:
        """Asynchronous logging with level checking"""
        if self._closed:
            return
            
        level_value = self._LOG_LEVELS.get(level.upper(), 20)
        if level_value < self.level:
            return

        # Format message (already returns bytes)
        msg_bytes = self.formatter.format_message(msg, level)
        
        # Truncate message if exceeds max size with proper UTF-8 boundary
        if len(msg_bytes) > self._MAX_MESSAGE_SIZE:
            truncated = msg_bytes[:self._MAX_MESSAGE_SIZE]
            # Ensure we don't cut in middle of UTF-8 sequence
            while truncated and (truncated[-1] & 0b11000000) == 0b10000000:
                truncated = truncated[:-1]
            msg_bytes = truncated
            self._safe_console_output(
                f"Message truncated from {len(msg_bytes)} to {len(truncated)} bytes "
                f"(max: {self._MAX_MESSAGE_SIZE})"
            )

        msg_str = msg_bytes.decode('utf-8').rstrip()

        # Console output
        if self.console_output:
            self._safe_console_output(msg_str)

        # Queue message for file output
        if self.fp:
            msg_size = len(msg_bytes)
            self._safe_console_output(f"Queuing message - Size: {msg_size} bytes")
            
            # Adjust batch size dynamically based on message size
            if msg_size > self._LARGE_MESSAGE_THRESHOLD:
                batch_size = max(1, self._BATCH_SIZE // 2)  # Smaller batch for large messages
                self._safe_console_output(f"Using reduced batch size {batch_size} for large message")
            else:
                batch_size = self._BATCH_SIZE
                
            # Split large messages into chunks
            if msg_size > self._LARGE_MESSAGE_THRESHOLD:
                chunk_size = self._LARGE_MESSAGE_THRESHOLD // 2
                for i in range(0, msg_size, chunk_size):
                    chunk = msg_bytes[i:i+chunk_size]
                    self._write_queue.put(chunk)
            else:
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
        self._safe_console_output("Starting logger shutdown")
        
        # Process all remaining messages in queue with timeout
        start_time = time.time()
        timeout = 5.0  # 5 second timeout for shutdown
        
        while not self._write_queue.empty() and time.time() - start_time < timeout:
            batch = []
            while not self._write_queue.empty() and len(batch) < self._BATCH_SIZE:
                try:
                    msg_bytes = self._write_queue.get_nowait()
                    batch.append(msg_bytes)
                except queue.Empty:
                    break
            
            if batch:
                self._safe_console_output(f"Flushing final batch of {len(batch)} messages")
                try:
                    self._flush_batch(batch)
                except Exception as e:
                    self._safe_console_output(f"Error flushing final batch: {e}")
        
        # Wait for writer thread to finish with timeout
        if hasattr(self, '_writer_thread') and self._writer_thread.is_alive():
            self._safe_console_output("Waiting for writer thread to finish")
            self._writer_thread.join(timeout=1.0)
            if self._writer_thread.is_alive():
                self._safe_console_output("Writer thread did not exit in time")
        
        # Close file handles with all locks protection
        for lock in self._file_locks:
            lock.acquire()
            
        try:
            if self._file_handle:
                try:
                    self._safe_console_output("Closing file handle")
                    self._file_handle.flush()
                    self._file_handle.close()
                except Exception as e:
                    self._safe_console_output(f"Error closing file handle: {e}")
                finally:
                    self._file_handle = None
                    self._file = None
        
        finally:
            for lock in self._file_locks:
                lock.release()
                
        self._safe_console_output("Logger shutdown complete")

