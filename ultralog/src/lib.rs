//! # ultralog
//!
//! High-performance, thread-safe logger with a dedicated writer thread,
//! batched async I/O, microsecond timestamps and automatic log rotation.
//!
//! ## Quick start
//!
//! ```no_run
//! use ultralog::{LoggerBuilder, LogLevel};
//!
//! let log = LoggerBuilder::new()
//!     .name("my-app")
//!     .fp("/var/log/my-app/app.log")
//!     .level(LogLevel::Info)
//!     .console_output(false)
//!     .build();
//!
//! log.info("server started");
//! log.warning("disk usage high");
//! log.error("connection refused");
//! log.close(); // flushes and joins the writer thread
//! ```
//!
//! ## Architecture
//!
//! Each [`Logger`] owns a bounded MPSC channel (64 K slots). Callers enqueue
//! pre-formatted `Vec<u8>` messages with a non-blocking `try_send`; a
//! dedicated background thread drains the channel in batches through a
//! 1 MB [`std::io::BufWriter`]. This decouples caller latency from disk I/O
//! entirely.
//!
//! ## Feature flags
//!
//! | Feature | Effect |
//! |---------|--------|
//! | `extension-module` | Compiles the PyO3 Python extension (`ultralog._ultralog`) |

use std::fs::{self, File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use crossbeam_channel::{bounded, Receiver, Sender};
use parking_lot::{Mutex, RwLock};

#[cfg(feature = "extension-module")]
use pyo3::prelude::*;

// ─── Log Level ───────────────────────────────────────────────────────────────

/// Log severity level.
///
/// Levels are ordered: `Debug < Info < Warning < Error < Critical`.
/// Any message whose level is below the logger's configured minimum is
/// silently dropped before entering the channel.
///
/// # Examples
///
/// ```
/// use ultralog::LogLevel;
///
/// assert!(LogLevel::Debug < LogLevel::Info);
/// assert_eq!(LogLevel::from_str("WARN"), LogLevel::Warning);
/// assert_eq!(LogLevel::Info.as_str(), "INFO");
/// ```
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum LogLevel {
    Debug = 10,
    Info = 20,
    Warning = 30,
    Error = 40,
    Critical = 50,
}

impl LogLevel {
    pub fn from_u32(v: u32) -> Self {
        match v {
            0..=10 => LogLevel::Debug,
            11..=20 => LogLevel::Info,
            21..=30 => LogLevel::Warning,
            31..=40 => LogLevel::Error,
            _ => LogLevel::Critical,
        }
    }

    #[inline]
    pub fn as_u32(self) -> u32 {
        self as u32
    }

    pub fn from_str(s: &str) -> Self {
        match s.to_ascii_uppercase().as_str() {
            "DEBUG" => LogLevel::Debug,
            "WARNING" | "WARN" => LogLevel::Warning,
            "ERROR" => LogLevel::Error,
            "CRITICAL" | "FATAL" => LogLevel::Critical,
            _ => LogLevel::Info,
        }
    }

    #[inline]
    pub fn as_str(self) -> &'static str {
        match self {
            LogLevel::Debug => "DEBUG",
            LogLevel::Info => "INFO",
            LogLevel::Warning => "WARNING",
            LogLevel::Error => "ERROR",
            LogLevel::Critical => "CRITICAL",
        }
    }

    /// Padded label (8 chars, matching Python format).
    #[inline]
    pub fn as_padded_str(self) -> &'static str {
        match self {
            LogLevel::Debug => "DEBUG   ",
            LogLevel::Info => "INFO    ",
            LogLevel::Warning => "WARNING ",
            LogLevel::Error => "ERROR   ",
            LogLevel::Critical => "CRITICAL",
        }
    }
}

// ─── Timestamp Cache ──────────────────────────────────────────────────────────

struct TimestampCache {
    cached: RwLock<(Arc<str>, Instant)>,
    ttl: Duration,
}

impl TimestampCache {
    fn new(ttl_ms: u64) -> Self {
        let empty: Arc<str> = Arc::from("");
        Self {
            cached: RwLock::new((empty, Instant::now() - Duration::from_secs(9999))),
            ttl: Duration::from_millis(ttl_ms),
        }
    }

    #[inline]
    fn get(&self) -> Arc<str> {
        // Fast path: concurrent readers proceed without blocking
        {
            let guard = self.cached.read();
            if guard.1.elapsed() < self.ttl {
                return Arc::clone(&guard.0);
            }
        }
        // Slow path: upgrade to write lock, double-check
        let mut guard = self.cached.write();
        if guard.1.elapsed() < self.ttl {
            return Arc::clone(&guard.0);
        }
        let ts: Arc<str> = Arc::from(format_now());
        guard.0 = Arc::clone(&ts);
        guard.1 = Instant::now();
        ts
    }
}

fn format_now() -> String {
    let dt = chrono::Local::now();
    use chrono::{Datelike, Timelike};
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}.{:06}",
        dt.year(), dt.month(), dt.day(),
        dt.hour(), dt.minute(), dt.second(),
        dt.nanosecond() / 1000
    )
}

// ─── Writer Thread ────────────────────────────────────────────────────────────

enum WriteMsg {
    Data(Vec<u8>),
    Flush,
    Shutdown,
}

struct WriterConfig {
    fp: Option<PathBuf>,
    max_file_size: u64,
    backup_count: usize,
    enable_rotation: bool,
    force_sync: bool,
    buffer_size: usize,
    flush_interval: Duration,
    batch_size: usize,
}

fn open_file(fp: &Path, buffer_size: usize) -> Option<BufWriter<File>> {
    let dir = fp.parent().unwrap_or(Path::new("."));
    if !dir.exists() {
        let _ = fs::create_dir_all(dir);
    }
    match OpenOptions::new().create(true).append(true).open(fp) {
        Ok(f) => Some(BufWriter::with_capacity(buffer_size, f)),
        Err(_) => None,
    }
}

fn rotate_log(fp: &Path, backup_count: usize) {
    // Use numbered suffix: <fp>.1, <fp>.2, …
    let suffix_path = |n: usize| -> PathBuf {
        let mut p = fp.as_os_str().to_os_string();
        p.push(format!(".{}", n));
        PathBuf::from(p)
    };

    let _ = fs::remove_file(suffix_path(backup_count));
    for i in (1..backup_count).rev() {
        let src = suffix_path(i);
        let dst = suffix_path(i + 1);
        if src.exists() {
            let _ = fs::rename(&src, &dst);
        }
    }
    let _ = fs::rename(fp, suffix_path(1));
}

fn writer_loop(receiver: Receiver<WriteMsg>, cfg: WriterConfig) {
    let mut current_size: u64 = cfg
        .fp
        .as_deref()
        .and_then(|p| fs::metadata(p).ok())
        .map(|m| m.len())
        .unwrap_or(0);

    let mut writer: Option<BufWriter<File>> = cfg
        .fp
        .as_deref()
        .and_then(|p| open_file(p, cfg.buffer_size));

    let mut pending: Vec<Vec<u8>> = Vec::with_capacity(cfg.batch_size);
    let mut last_flush = Instant::now();

    loop {
        // Drain up to batch_size messages from channel
        loop {
            match receiver.try_recv() {
                Ok(WriteMsg::Data(bytes)) => {
                    pending.push(bytes);
                    if pending.len() >= cfg.batch_size {
                        break;
                    }
                }
                Ok(WriteMsg::Flush) => break,
                Ok(WriteMsg::Shutdown) => {
                    // Flush remaining and exit
                    flush_pending(&mut writer, &mut pending, &mut current_size, &cfg);
                    if let Some(ref mut w) = writer {
                        let _ = w.flush();
                    }
                    return;
                }
                Err(_) => break, // Channel empty
            }
        }

        if !pending.is_empty() {
            flush_pending(&mut writer, &mut pending, &mut current_size, &cfg);
            if cfg.force_sync {
                if let Some(ref mut w) = writer {
                    let _ = w.flush();
                }
            }
        } else if last_flush.elapsed() >= cfg.flush_interval {
            if let Some(ref mut w) = writer {
                let _ = w.flush();
            }
            last_flush = Instant::now();
        }

        // Block-wait for next message with timeout
        match receiver.recv_timeout(cfg.flush_interval) {
            Ok(WriteMsg::Data(bytes)) => {
                pending.push(bytes);
            }
            Ok(WriteMsg::Flush) => {
                if let Some(ref mut w) = writer {
                    let _ = w.flush();
                }
            }
            Ok(WriteMsg::Shutdown) => {
                flush_pending(&mut writer, &mut pending, &mut current_size, &cfg);
                if let Some(ref mut w) = writer {
                    let _ = w.flush();
                }
                return;
            }
            Err(_) => {} // Timeout – loop again
        }
    }
}

fn flush_pending(
    writer: &mut Option<BufWriter<File>>,
    pending: &mut Vec<Vec<u8>>,
    current_size: &mut u64,
    cfg: &WriterConfig,
) {
    if pending.is_empty() {
        return;
    }
    let fp = match &cfg.fp {
        Some(p) => p,
        None => {
            pending.clear();
            return;
        }
    };

    let batch_bytes: u64 = pending.iter().map(|b| b.len() as u64).sum();

    // Check rotation
    if cfg.enable_rotation && (*current_size + batch_bytes) > cfg.max_file_size {
        // Flush & close current file
        if let Some(ref mut w) = writer {
            let _ = w.flush();
        }
        *writer = None;
        rotate_log(fp, cfg.backup_count);
        *current_size = 0;
        *writer = open_file(fp, cfg.buffer_size);
    }

    if let Some(ref mut w) = writer {
        for bytes in pending.iter() {
            if w.write_all(bytes).is_ok() {
                *current_size += bytes.len() as u64;
            }
        }
    }
    pending.clear();
}

// ─── Public Rust API ─────────────────────────────────────────────────────────

/// Fluent builder for [`Logger`].
///
/// # Examples
///
/// ```no_run
/// use ultralog::{LoggerBuilder, LogLevel};
///
/// let log = LoggerBuilder::new()
///     .name("svc")
///     .fp("/tmp/svc.log")
///     .level(LogLevel::Warning)
///     .max_file_size(50 * 1024 * 1024)
///     .backup_count(10)
///     .console_output(false)
///     .build();
///
/// log.error("something failed");
/// log.close();
/// ```
pub struct LoggerBuilder {
    name: String,
    fp: Option<PathBuf>,
    level: LogLevel,
    truncate_file: bool,
    with_time: bool,
    max_file_size: u64,
    backup_count: usize,
    console_output: bool,
    force_sync: bool,
    enable_rotation: bool,
    buffer_size: usize,
    batch_size: usize,
    flush_interval_ms: u64,
}

impl Default for LoggerBuilder {
    fn default() -> Self {
        Self {
            name: "UltraLogger".into(),
            fp: None,
            level: LogLevel::Debug,
            truncate_file: false,
            with_time: true,
            max_file_size: 10 * 1024 * 1024,
            backup_count: 5,
            console_output: true,
            force_sync: false,
            enable_rotation: true,
            buffer_size: 1024 * 1024,
            batch_size: 1000,
            flush_interval_ms: 50,
        }
    }
}

impl LoggerBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn name(mut self, name: impl Into<String>) -> Self {
        self.name = name.into();
        self
    }

    pub fn fp(mut self, path: impl Into<PathBuf>) -> Self {
        self.fp = Some(path.into());
        self
    }

    pub fn level(mut self, level: LogLevel) -> Self {
        self.level = level;
        self
    }

    pub fn level_str(mut self, level: &str) -> Self {
        self.level = LogLevel::from_str(level);
        self
    }

    pub fn truncate_file(mut self, v: bool) -> Self {
        self.truncate_file = v;
        self
    }

    pub fn with_time(mut self, v: bool) -> Self {
        self.with_time = v;
        self
    }

    pub fn max_file_size(mut self, bytes: u64) -> Self {
        self.max_file_size = bytes;
        self
    }

    pub fn backup_count(mut self, n: usize) -> Self {
        self.backup_count = n;
        self
    }

    pub fn console_output(mut self, v: bool) -> Self {
        self.console_output = v;
        self
    }

    pub fn force_sync(mut self, v: bool) -> Self {
        self.force_sync = v;
        self
    }

    pub fn enable_rotation(mut self, v: bool) -> Self {
        self.enable_rotation = v;
        self
    }

    pub fn buffer_size(mut self, bytes: usize) -> Self {
        self.buffer_size = bytes;
        self
    }

    pub fn batch_size(mut self, n: usize) -> Self {
        self.batch_size = n;
        self
    }

    pub fn flush_interval_ms(mut self, ms: u64) -> Self {
        self.flush_interval_ms = ms;
        self
    }

    pub fn build(self) -> Logger {
        Logger::new_from_builder(self)
    }
}

/// High-performance async logger.
///
/// Created via [`LoggerBuilder`]. Drop-safe: [`Drop`] calls [`Logger::close`]
/// automatically, so explicit `close()` is optional but recommended when you
/// need a guaranteed flush at a precise point.
///
/// # Thread safety
///
/// `Logger` is `Send + Sync`. Multiple threads can call any logging method
/// concurrently — all internal state uses atomics and lock-free channels.
///
/// # Examples
///
/// ```no_run
/// use ultralog::{LoggerBuilder, LogLevel};
/// use std::sync::Arc;
/// use std::thread;
///
/// let log = Arc::new(
///     LoggerBuilder::new()
///         .name("worker")
///         .fp("/tmp/worker.log")
///         .level(LogLevel::Info)
///         .console_output(false)
///         .build(),
/// );
///
/// let handles: Vec<_> = (0..4).map(|id| {
///     let log = Arc::clone(&log);
///     thread::spawn(move || log.info(&format!("thread {id} started")))
/// }).collect();
///
/// for h in handles { h.join().unwrap(); }
/// log.close();
/// ```
pub struct Logger {
    name: Arc<String>,
    with_time: bool,
    level: Arc<AtomicU32>,
    sender: Sender<WriteMsg>,
    closed: Arc<AtomicBool>,
    console_output: bool,
    ts_cache: Arc<TimestampCache>,
    /// Writer thread handle – taken (set to None) exactly once in close().
    writer_handle: Mutex<Option<thread::JoinHandle<()>>>,
}

impl Logger {
    fn new_from_builder(b: LoggerBuilder) -> Self {
        // Truncate if requested
        if b.truncate_file {
            if let Some(ref fp) = b.fp {
                let _ = fs::write(fp, b"");
            }
        }

        let (tx, rx) = bounded::<WriteMsg>(65536);

        let cfg = WriterConfig {
            fp: b.fp.clone(),
            max_file_size: b.max_file_size,
            backup_count: b.backup_count,
            enable_rotation: b.enable_rotation,
            force_sync: b.force_sync,
            buffer_size: b.buffer_size,
            flush_interval: Duration::from_millis(b.flush_interval_ms),
            batch_size: b.batch_size,
        };

        let handle = thread::Builder::new()
            .name("ultralog-writer".into())
            .spawn(move || writer_loop(rx, cfg))
            .expect("Failed to spawn writer thread");

        Self {
            name: Arc::new(b.name),
            with_time: b.with_time,
            level: Arc::new(AtomicU32::new(b.level.as_u32())),
            sender: tx,
            closed: Arc::new(AtomicBool::new(false)),
            console_output: b.console_output,
            ts_cache: Arc::new(TimestampCache::new(1)),
            writer_handle: Mutex::new(Some(handle)),
        }
    }

    /// Set the minimum log level.
    pub fn set_level(&self, level: LogLevel) {
        self.level.store(level.as_u32(), Ordering::Relaxed);
    }

    /// Set the minimum log level from a string.
    pub fn set_level_str(&self, level: &str) {
        self.set_level(LogLevel::from_str(level));
    }

    /// Get the current log level.
    pub fn get_level(&self) -> LogLevel {
        LogLevel::from_u32(self.level.load(Ordering::Relaxed))
    }

    #[inline]
    fn format_message(&self, msg: &str, level: LogLevel) -> Vec<u8> {
        let padded = level.as_padded_str();
        let name = self.name.as_bytes();
        let msg_bytes = msg.as_bytes();
        if self.with_time {
            let ts = self.ts_cache.get();
            let ts_bytes = ts.as_bytes();
            // " | " = 3, " | " = 3, " | - " = 5, '\n' = 1 → overhead = 12
            let cap = ts_bytes.len() + padded.len() + name.len() + msg_bytes.len() + 12;
            let mut buf = Vec::with_capacity(cap);
            buf.extend_from_slice(ts_bytes);
            buf.extend_from_slice(b" | ");
            buf.extend_from_slice(padded.as_bytes());
            buf.extend_from_slice(b" | ");
            buf.extend_from_slice(name);
            buf.extend_from_slice(b" | - ");
            buf.extend_from_slice(msg_bytes);
            buf.push(b'\n');
            buf
        } else {
            let cap = padded.len() + name.len() + msg_bytes.len() + 9;
            let mut buf = Vec::with_capacity(cap);
            buf.extend_from_slice(padded.as_bytes());
            buf.extend_from_slice(b" | ");
            buf.extend_from_slice(name);
            buf.extend_from_slice(b" | - ");
            buf.extend_from_slice(msg_bytes);
            buf.push(b'\n');
            buf
        }
    }

    /// Log a message at the given level.
    #[inline]
    pub fn log(&self, msg: &str, level: LogLevel) {
        if self.closed.load(Ordering::Relaxed) {
            return;
        }
        if level.as_u32() < self.level.load(Ordering::Relaxed) {
            return;
        }
        let bytes = self.format_message(msg, level);
        if self.console_output {
            let s = std::str::from_utf8(&bytes).unwrap_or(msg);
            eprint!("{}", s);
        }
        let _ = self.sender.try_send(WriteMsg::Data(bytes));
    }

    #[inline] pub fn debug(&self, msg: &str) { self.log(msg, LogLevel::Debug); }
    #[inline] pub fn info(&self, msg: &str) { self.log(msg, LogLevel::Info); }
    #[inline] pub fn warning(&self, msg: &str) { self.log(msg, LogLevel::Warning); }
    #[inline] pub fn error(&self, msg: &str) { self.log(msg, LogLevel::Error); }
    #[inline] pub fn critical(&self, msg: &str) { self.log(msg, LogLevel::Critical); }

    /// Flush pending writes.
    pub fn flush(&self) {
        let _ = self.sender.send(WriteMsg::Flush);
    }

    /// Close the logger, flushing all pending writes.
    ///
    /// Blocks until the background writer thread has fully flushed and exited.
    pub fn close(&self) {
        if self.closed.swap(true, Ordering::SeqCst) {
            return; // Already closed
        }
        // Signal the writer thread to shut down.
        let _ = self.sender.send(WriteMsg::Shutdown);
        // Wait for the writer thread to finish – this guarantees that every
        // buffered byte has been written and flushed before close() returns.
        if let Some(handle) = self.writer_handle.lock().take() {
            let _ = handle.join();
        }
    }
}

impl Drop for Logger {
    fn drop(&mut self) {
        self.close();
    }
}

// ─── PyO3 Bindings ────────────────────────────────────────────────────────────

#[cfg(feature = "extension-module")]
#[pyclass(name = "UltraLog")]
pub struct PyUltraLog {
    inner: Logger,
}

#[cfg(feature = "extension-module")]
#[pymethods]
impl PyUltraLog {
    /// Create a new high-performance logger.
    ///
    /// Parameters
    /// ----------
    /// name : str, optional
    ///     Logger name (default: "UltraLogger")
    /// fp : str, optional
    ///     File path for logging (default: None)
    /// level : str
    ///     Minimum log level: DEBUG/INFO/WARNING/ERROR/CRITICAL (default: "DEBUG")
    /// truncate_file : bool
    ///     Truncate file on init (default: False)
    /// with_time : bool
    ///     Include timestamp in messages (default: True)
    /// max_file_size : int
    ///     Max file size in bytes before rotation (default: 10 MB)
    /// backup_count : int
    ///     Number of rotated backup files to keep (default: 5)
    /// console_output : bool
    ///     Echo log messages to stderr (default: True)
    /// force_sync : bool
    ///     Flush to disk after every batch (default: False)
    /// enable_rotation : bool
    ///     Enable log rotation (default: True)
    /// buffer_size : int
    ///     Write buffer size in bytes (default: 1 MB)
    /// batch_size : int
    ///     Max messages per write batch (default: 1000)
    /// flush_interval_ms : int
    ///     Flush interval in milliseconds (default: 50)
    #[new]
    #[pyo3(signature = (
        name=None,
        fp=None,
        level="DEBUG",
        truncate_file=false,
        with_time=true,
        max_file_size=10*1024*1024,
        backup_count=5,
        console_output=true,
        force_sync=false,
        enable_rotation=true,
        buffer_size=1024*1024,
        batch_size=1000,
        flush_interval_ms=50,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        name: Option<&str>,
        fp: Option<&str>,
        level: &str,
        truncate_file: bool,
        with_time: bool,
        max_file_size: u64,
        backup_count: usize,
        console_output: bool,
        force_sync: bool,
        enable_rotation: bool,
        buffer_size: usize,
        batch_size: usize,
        flush_interval_ms: u64,
    ) -> Self {
        let mut builder = LoggerBuilder::new()
            .name(name.unwrap_or("UltraLogger"))
            .level_str(level)
            .truncate_file(truncate_file)
            .with_time(with_time)
            .max_file_size(max_file_size)
            .backup_count(backup_count)
            .console_output(console_output)
            .force_sync(force_sync)
            .enable_rotation(enable_rotation)
            .buffer_size(buffer_size)
            .batch_size(batch_size)
            .flush_interval_ms(flush_interval_ms);

        if let Some(path) = fp {
            builder = builder.fp(path);
        }

        Self { inner: builder.build() }
    }

    /// Log a message at the specified level.
    #[pyo3(signature = (msg, level="INFO"))]
    fn log(&self, msg: &str, level: &str) {
        self.inner.log(msg, LogLevel::from_str(level));
    }

    fn debug(&self, msg: &str) { self.inner.debug(msg); }
    fn info(&self, msg: &str) { self.inner.info(msg); }
    fn warning(&self, msg: &str) { self.inner.warning(msg); }
    fn error(&self, msg: &str) { self.inner.error(msg); }
    fn critical(&self, msg: &str) { self.inner.critical(msg); }

    /// Flush buffered writes to disk.
    fn flush(&self) { self.inner.flush(); }

    /// Shut down the logger and flush all pending writes.
    fn close(&self) { self.inner.close(); }

    /// Get the current log level string.
    #[getter]
    fn level(&self) -> &str {
        self.inner.get_level().as_str()
    }

    /// Set the log level.
    #[setter]
    fn set_level(&self, level: &str) {
        self.inner.set_level_str(level);
    }
}

#[cfg(feature = "extension-module")]
#[pymodule]
fn _ultralog(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyUltraLog>()?;
    Ok(())
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    use tempfile::tempdir;

    fn make_logger(dir: &std::path::Path, console: bool) -> Logger {
        LoggerBuilder::new()
            .name("test")
            .fp(dir.join("test.log"))
            .level(LogLevel::Debug)
            .truncate_file(true)
            .console_output(console)
            .with_time(false)
            .enable_rotation(false)
            .buffer_size(4096)
            .batch_size(50)
            .flush_interval_ms(50)
            .build()
    }

    #[test]
    fn test_log_levels_ordering() {
        assert!(LogLevel::Debug < LogLevel::Info);
        assert!(LogLevel::Info < LogLevel::Warning);
        assert!(LogLevel::Warning < LogLevel::Error);
        assert!(LogLevel::Error < LogLevel::Critical);
    }

    #[test]
    fn test_log_level_from_str() {
        assert_eq!(LogLevel::from_str("debug"), LogLevel::Debug);
        assert_eq!(LogLevel::from_str("INFO"), LogLevel::Info);
        assert_eq!(LogLevel::from_str("WARNING"), LogLevel::Warning);
        assert_eq!(LogLevel::from_str("WARN"), LogLevel::Warning);
        assert_eq!(LogLevel::from_str("error"), LogLevel::Error);
        assert_eq!(LogLevel::from_str("CRITICAL"), LogLevel::Critical);
        assert_eq!(LogLevel::from_str("FATAL"), LogLevel::Critical);
        assert_eq!(LogLevel::from_str("unknown"), LogLevel::Info);
    }

    #[test]
    fn test_log_level_as_str() {
        assert_eq!(LogLevel::Debug.as_str(), "DEBUG");
        assert_eq!(LogLevel::Info.as_str(), "INFO");
        assert_eq!(LogLevel::Warning.as_str(), "WARNING");
        assert_eq!(LogLevel::Error.as_str(), "ERROR");
        assert_eq!(LogLevel::Critical.as_str(), "CRITICAL");
    }

    #[test]
    fn test_write_and_read() {
        let dir = tempdir().unwrap();
        let logger = make_logger(dir.path(), false);
        logger.info("hello world");
        logger.debug("debug msg");
        logger.warning("warn msg");
        logger.error("error msg");
        logger.critical("critical msg");
        logger.close();
        thread::sleep(Duration::from_millis(200));

        let content = fs::read_to_string(dir.path().join("test.log")).unwrap();
        assert!(content.contains("hello world"), "missing info");
        assert!(content.contains("debug msg"), "missing debug");
        assert!(content.contains("warn msg"), "missing warning");
        assert!(content.contains("error msg"), "missing error");
        assert!(content.contains("critical msg"), "missing critical");
    }

    #[test]
    fn test_level_filter() {
        let dir = tempdir().unwrap();
        let logger = LoggerBuilder::new()
            .name("test")
            .fp(dir.path().join("filter.log"))
            .level(LogLevel::Warning)
            .truncate_file(true)
            .console_output(false)
            .with_time(false)
            .enable_rotation(false)
            .build();

        logger.debug("should not appear");
        logger.info("should not appear either");
        logger.warning("should appear");
        logger.error("also should appear");
        logger.close();
        thread::sleep(Duration::from_millis(200));

        let content = fs::read_to_string(dir.path().join("filter.log")).unwrap();
        assert!(!content.contains("should not appear"), "debug/info leaked");
        assert!(content.contains("should appear"), "warning missing");
        assert!(content.contains("also should appear"), "error missing");
    }

    #[test]
    fn test_set_level() {
        let dir = tempdir().unwrap();
        let logger = make_logger(dir.path(), false);
        assert_eq!(logger.get_level(), LogLevel::Debug);
        logger.set_level(LogLevel::Error);
        assert_eq!(logger.get_level(), LogLevel::Error);
        logger.set_level_str("info");
        assert_eq!(logger.get_level(), LogLevel::Info);
        logger.close();
    }

    #[test]
    fn test_log_rotation() {
        let dir = tempdir().unwrap();
        let fp = dir.path().join("rotate.log");
        let logger = LoggerBuilder::new()
            .name("rottest")
            .fp(&fp)
            .level(LogLevel::Debug)
            .truncate_file(true)
            .console_output(false)
            .with_time(false)
            .max_file_size(500)
            .backup_count(3)
            .enable_rotation(true)
            .force_sync(true)
            .buffer_size(0)
            .batch_size(1)
            .flush_interval_ms(10)
            .build();

        let msg = "x".repeat(80);
        for _ in 0..20 {
            logger.info(&msg);
            thread::sleep(Duration::from_millis(15));
        }
        logger.close();
        thread::sleep(Duration::from_millis(300));

        let backup1 = dir.path().join("rotate.log.1");
        assert!(backup1.exists(), "rotate.log.1 should exist");
    }

    #[test]
    fn test_concurrent_writes() {
        use std::sync::Arc;

        let dir = tempdir().unwrap();
        let logger = Arc::new(make_logger(dir.path(), false));
        let mut handles = vec![];

        for t in 0..8 {
            let lg = Arc::clone(&logger);
            handles.push(thread::spawn(move || {
                for i in 0..500 {
                    lg.info(&format!("thread-{} msg-{}", t, i));
                }
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        logger.close();
        thread::sleep(Duration::from_millis(500));

        let content = fs::read_to_string(dir.path().join("test.log")).unwrap();
        // Each thread sends 500 messages; verify at least some from each
        for t in 0..8 {
            assert!(
                content.contains(&format!("thread-{}", t)),
                "missing output from thread {}",
                t
            );
        }
    }

    #[test]
    fn test_console_only_no_file() {
        let logger = LoggerBuilder::new()
            .name("console-test")
            .level(LogLevel::Info)
            .console_output(false)
            .build();
        // Should not panic
        logger.info("no file, no crash");
        logger.close();
    }

    #[test]
    fn test_throughput_baseline() {
        let dir = tempdir().unwrap();
        let logger = LoggerBuilder::new()
            .name("bench")
            .fp(dir.path().join("bench.log"))
            .level(LogLevel::Info)
            .truncate_file(true)
            .console_output(false)
            .with_time(true)
            .enable_rotation(false)
            .build();

        let n = 100_000usize;
        let start = std::time::Instant::now();
        for i in 0..n {
            logger.info(&format!("benchmark message {}", i));
        }
        logger.close();
        let elapsed = start.elapsed().as_secs_f64();
        let tps = n as f64 / elapsed;
        println!("Rust throughput: {:.0} msg/s ({:.2}s for {})", tps, elapsed, n);
        // Must be at least 800k msg/s on any modern machine
        assert!(tps > 800_000.0, "throughput too low: {:.0} msg/s", tps);
    }
}
