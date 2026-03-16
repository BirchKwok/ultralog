# UltraLog - High-performance Logging System

UltraLog is a high-performance logging system with a **Rust core** (via PyO3/maturin). All threading, locking, buffering and log rotation are handled entirely inside the Rust extension — Python is just a thin dispatch layer.

## Key Features

- **Rust Core**: Zero-overhead async logging backed by a compiled Rust extension
- **Thread-Safe**: Lock-free reads via `RwLock`; bounded MPSC channel (64K slots) for producers
- **3M+ msg/s**: 20–50× faster than Python's standard `logging` module
- **Low Memory**: Pre-allocated `BufWriter`; near-zero Python heap overhead per message
- **Automatic Rotation**: File rotation with configurable size limits and backup counts
- **Flexible Configuration**: Extensive parameters — level, rotation, buffering, flush interval
- **Lifecycle Management**: Guaranteed flush on `close()` via Rust `JoinHandle`

## Performance

UltraLog is designed for **million-level throughput** with minimal memory overhead. All measurements below were taken on Apple Silicon (M-series) and are reproducible by running `python benchmark.py`.

### Single-Thread (500,000 messages)

| Backend | Throughput (msg/s) | Peak memory | Speedup |
|---------|-------------------:|------------:|--------:|
| **UltraLog (Rust)** | **2,300,000+** | **<0.01 MB** | **21×** |
| Standard `logging` | ~108,000 | ~0.01 MB | 1× |

### Multi-Thread (8 threads × 100,000 messages)

| Backend | Throughput (msg/s) | Peak memory | Speedup |
|---------|-------------------:|------------:|--------:|
| **UltraLog (Rust)** | **2,600,000+** | **0.09 MB** | **50×** |
| Standard `logging` | ~52,000 | ~0.37 MB | 1× |

### Key Performance Advantages

1. **2–3M msg/s**: Async batched I/O via bounded MPSC channel (64K slots) + `BufWriter`
2. **Near-zero memory**: Python heap overhead per message is effectively 0 — all buffering is in Rust
3. **Lock-free timestamp reads**: `RwLock`-based cache lets all threads read concurrently
4. **Zero-copy formatting**: Direct `extend_from_slice` byte assembly, no `write!` macro overhead
5. **`#[inline]` hot paths**: `log()`, `info()`, `format_message()` inlined by the Rust compiler
6. **MT faster than ST**: Multi-thread throughput exceeds single-thread thanks to eliminated lock contention

## Installation

```bash
git clone https://github.com/birchkwok/ultralog.git
cd ultralog
pip install -e .
```

## Basic Usage

### Local Mode (Default)

```python
from ultralog import UltraLog

# Basic initialization
logger = UltraLog(name="MyApp")

# Logging examples
logger.debug("Debug message")
logger.info("Application started")
logger.warning("Low disk space")
logger.error("Failed to connect")
logger.critical("System crash")

# Explicit cleanup (optional)
logger.close()
```

### Remote Mode

```python
from ultralog import UltraLog

# Remote configuration
logger = UltraLog(
    name="MyApp",
    server_url="http://your-server-ip:8000",
    auth_token="your_secret_token"
)

# Same logging interface
logger.info("Remote log message")
```

## Log Formatting

The Rust core uses a fixed, loguru-style format optimised for zero-copy assembly:

```
YYYY-MM-DD HH:MM:SS.ffffff | LEVEL    | <name> | - <message>
```

Example output:
```
2025-04-19 07:50:22.139045 | INFO     | MyApp | - Application started
```

| Field | Description |
|-------|-------------|
| `YYYY-MM-DD HH:MM:SS.ffffff` | Timestamp with microsecond precision (omitted when `with_time=False`) |
| `LEVEL` | Padded to 8 chars: `DEBUG   `, `INFO    `, `WARNING `, `ERROR   `, `CRITICAL` |
| `<name>` | Logger name set via the `name` parameter |
| `<message>` | The log message passed to `info()`, `error()`, etc. |

Disable timestamps for even lower overhead:

```python
logger = UltraLog(name="MyApp", with_time=False)
logger.info("Application started")
# → INFO     | MyApp | - Application started
```

## Server Configuration

Run the server with custom parameters:

```bash
python -m ultralog.server \
  --log-dir /var/log/myapp \
  --log-file app.log \
  --log-level DEBUG \
  --max-file-size 10485760 \
  --backup-count 5 \
  --console-output \
  --auth-token your_secure_token
```

## Advanced Configuration

### UltraLog Initialization Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| name | str | "UltraLogger" | Logger name prefix |
| fp | str | None | Local log file path |
| level | str | "INFO" | Minimum log level |
| truncate_file | bool | False | Truncate existing log file |
| with_time | bool | True | Include timestamps |
| max_file_size | int | 10MB | Max file size before rotation |
| backup_count | int | 5 | Number of backup files |
| console_output | bool | False | Print to console |
| force_sync | bool | False | Force synchronous writes |
| enable_rotation | bool | True | Enable log rotation |
| file_buffer_size | int | 1MB | File write buffer size |
| batch_size | int | 1000 | Write batch size |
| flush_interval | float | 0.05 | Flush interval (seconds) |
| server_url | str | None | Remote server URL |
| auth_token | str | None | Remote auth token |

## Development

### Running Tests

```bash
pytest tests/
```

### Building Package

```bash
python -m build
```

## API Documentation

Interactive API docs available at:
`http://localhost:8000/docs` when server is running

## Best Practices

1. For production:
   - Use proper log rotation settings
   - Set appropriate log levels
   - Use secure authentication tokens
   - Monitor log file sizes

2. For remote logging:
   - Implement retry logic in your application
   - Consider batch sizes for high throughput
   - Monitor network connectivity

3. General:
   - Use meaningful logger names
   - Include context in log messages
   - Regularly review log retention policy
