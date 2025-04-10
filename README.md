# UltraLog - High-performance Logging System

UltraLog is a high-performance logging system that supports both local file logging and remote API logging.

## Key Features

- **Thread-Safe**: Supports concurrent writes from multiple threads/clients
- **Flexible Configuration**: Extensive logging parameters via CLI or code
- **Automatic Rotation**: File rotation with size limits and backup counts
- **Formatted Output**: Consistent log formatting with timestamps
- **Lifecycle Management**: Proper resource cleanup on shutdown

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
| file_buffer_size | int | 256KB | File write buffer size |
| batch_size | int | None | Remote batch size |
| flush_interval | float | None | Remote flush interval |
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
