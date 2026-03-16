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

### Python (pip / PyPI)

```bash
pip install ultralog
```

Or from source:

```bash
git clone https://github.com/BirchKwok/ultralog.git
cd ultralog
pip install -e .
```

### Rust (Cargo)

Add to your `Cargo.toml`:

```toml
[dependencies]
ultralog = "0.5"
```

No feature flags are needed for the pure-Rust API. The `extension-module`
feature is only required when building the Python `.so` extension.

---

## Rust Crate Usage

### Quick start

```rust
use ultralog::{LoggerBuilder, LogLevel};

fn main() {
    let log = LoggerBuilder::new()
        .name("my-app")
        .fp("/var/log/my-app/app.log")
        .level(LogLevel::Info)
        .console_output(false)
        .build();

    log.info("server started");
    log.warning("disk usage above 80 %");
    log.error("connection refused");

    log.close(); // flush + join writer thread
}
```

### Console-only (no file)

```rust
let log = LoggerBuilder::new()
    .name("dev")
    .level(LogLevel::Debug)
    .console_output(true)
    .build();

log.debug("verbose output");
log.close();
```

### Multi-threaded

`Logger` is `Send + Sync`. Wrap it in an `Arc` to share across threads:

```rust
use ultralog::{LoggerBuilder, LogLevel};
use std::sync::Arc;
use std::thread;

let log = Arc::new(
    LoggerBuilder::new()
        .name("worker")
        .fp("/tmp/worker.log")
        .level(LogLevel::Info)
        .console_output(false)
        .build(),
);

let handles: Vec<_> = (0..8).map(|id| {
    let log = Arc::clone(&log);
    thread::spawn(move || {
        for i in 0..10_000 {
            log.info(&format!("thread {id} msg {i}"));
        }
    })
}).collect();

for h in handles { h.join().unwrap(); }
log.close();
```

### Dynamic level change

```rust
let log = LoggerBuilder::new().name("svc").build();

log.set_level(LogLevel::Debug);
log.debug("verbose during startup");

log.set_level(LogLevel::Warning);  // atomically, thread-safe
log.debug("this is now filtered");
log.warning("this passes");

log.close();
```

### Log rotation

```rust
let log = LoggerBuilder::new()
    .name("rotating")
    .fp("/var/log/app/app.log")
    .level(LogLevel::Info)
    .max_file_size(50 * 1024 * 1024)  // rotate at 50 MB
    .backup_count(10)                  // keep .1 … .10
    .enable_rotation(true)
    .console_output(false)
    .build();

log.info("logging with rotation");
log.close();
```

### `LoggerBuilder` reference

| Method | Type | Default | Description |
|--------|------|---------|-------------|
| `.name(s)` | `&str` | `"UltraLogger"` | Name shown in every log line |
| `.fp(path)` | `impl Into<PathBuf>` | `None` | Log file path; `None` = console only |
| `.level(l)` | `LogLevel` | `Debug` | Minimum level to emit |
| `.level_str(s)` | `&str` | — | Same as `.level()`, accepts strings |
| `.truncate_file(b)` | `bool` | `false` | Truncate file on startup |
| `.with_time(b)` | `bool` | `true` | Prepend microsecond timestamp |
| `.max_file_size(n)` | `u64` | `10 MB` | Bytes before rotation |
| `.backup_count(n)` | `usize` | `5` | Rotated files to retain |
| `.console_output(b)` | `bool` | `true` | Echo to `stderr` |
| `.force_sync(b)` | `bool` | `false` | `fsync` after every batch |
| `.enable_rotation(b)` | `bool` | `true` | Size-based rotation |
| `.buffer_size(n)` | `usize` | `1 MB` | `BufWriter` size in bytes |
| `.batch_size(n)` | `usize` | `1000` | Max messages per batch |
| `.flush_interval_ms(ms)` | `u64` | `50` | Background flush interval |

### `Logger` method reference

| Method | Description |
|--------|-------------|
| `log(&str, LogLevel)` | Emit at an explicit level |
| `debug(&str)` | Emit at `Debug` |
| `info(&str)` | Emit at `Info` |
| `warning(&str)` | Emit at `Warning` |
| `error(&str)` | Emit at `Error` |
| `critical(&str)` | Emit at `Critical` |
| `set_level(LogLevel)` | Change minimum level atomically |
| `set_level_str(&str)` | Same, accepts a string |
| `get_level() -> LogLevel` | Read current level |
| `flush()` | Flush buffered writes immediately |
| `close()` | Flush, join writer thread, mark closed |

`Logger` implements `Drop` — if you do not call `close()` explicitly the
destructor will flush automatically when the `Logger` goes out of scope.

---

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

## Rust API Reference

The high-performance core is exposed directly as `ultralog._ultralog.UltraLog`.
The top-level `ultralog.UltraLog` is a thin wrapper that delegates to it —
both share the same interface and parameters.

```python
# Thin Python wrapper (recommended)
from ultralog import UltraLog

# Direct Rust extension (zero wrapper overhead)
from ultralog._ultralog import UltraLog
```

### Constructor

```python
UltraLog(
    name: str              = "UltraLogger",   # logger name shown in every line
    fp: str | None         = None,            # log file path; None → console only
    level: str             = "DEBUG",         # minimum level to emit
    truncate_file: bool    = False,           # wipe the file on startup
    with_time: bool        = True,            # include timestamp in output
    max_file_size: int     = 10 * 1024 * 1024, # bytes before rotation (10 MB)
    backup_count: int      = 5,              # rotated files to keep (.1 … .N)
    console_output: bool   = True,           # echo to stderr
    force_sync: bool       = False,          # fsync after every batch
    enable_rotation: bool  = True,           # automatic size-based rotation
    buffer_size: int       = 1024 * 1024,    # BufWriter size in bytes (1 MB)
    batch_size: int        = 1000,           # max messages per write batch
    flush_interval_ms: int = 50,             # background flush interval (ms)
)
```

### Methods

| Method | Description |
|--------|-------------|
| `log(msg, level="INFO")` | Emit a message at the given level string |
| `debug(msg)` | Emit at `DEBUG` |
| `info(msg)` | Emit at `INFO` |
| `warning(msg)` | Emit at `WARNING` |
| `error(msg)` | Emit at `ERROR` |
| `critical(msg)` | Emit at `CRITICAL` |
| `flush()` | Flush buffered writes to disk immediately |
| `close()` | Flush and shut down the writer thread (blocks until done) |

### `level` Property

```python
logger.level          # → "INFO"  (current minimum level)
logger.level = "WARNING"  # raise the floor dynamically, thread-safe
```

Accepted level strings (case-insensitive): `DEBUG`, `INFO`, `WARNING` / `WARN`, `ERROR`, `CRITICAL` / `FATAL`.

---

### Examples

#### File logging with rotation

```python
from ultralog import UltraLog

logger = UltraLog(
    name="MyApp",
    fp="/var/log/myapp/app.log",
    level="INFO",
    max_file_size=50 * 1024 * 1024,  # rotate at 50 MB
    backup_count=10,
    console_output=False,
)
logger.info("Service started")
logger.close()
```

#### Console-only logger (no file)

```python
logger = UltraLog(name="dev", fp=None, level="DEBUG", console_output=True)
logger.debug("Debugging something")
logger.close()
```

#### Dynamic level change at runtime

```python
logger = UltraLog(name="MyApp", level="DEBUG")
logger.debug("verbose during startup")

logger.level = "WARNING"        # suppress DEBUG / INFO in production
logger.debug("this is filtered")
logger.warning("this passes")
logger.close()
```

#### Multi-threaded usage

```python
import threading
from ultralog import UltraLog

logger = UltraLog(name="worker", fp="workers.log", level="INFO",
                  console_output=False)

def worker(tid):
    for i in range(1000):
        logger.info(f"thread {tid} message {i}")

threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
for t in threads: t.start()
for t in threads: t.join()

logger.close()   # guarantees all 8,000 messages are flushed before returning
```

#### High-throughput write (no timestamp, no rotation)

```python
logger = UltraLog(
    name="ingest",
    fp="ingest.log",
    level="INFO",
    with_time=False,         # ~5 % faster — skips timestamp formatting
    enable_rotation=False,
    console_output=False,
    buffer_size=4 * 1024 * 1024,  # 4 MB BufWriter
    batch_size=2000,
    flush_interval_ms=100,
)
for record in data_stream:
    logger.info(record)
logger.close()
```

#### Using `log()` for programmatic level dispatch

```python
def emit(logger, level: str, msg: str):
    logger.log(msg, level)   # level resolved in Rust, no Python dispatch overhead

emit(logger, "ERROR", "something went wrong")
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
