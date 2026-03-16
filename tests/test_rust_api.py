"""
Tests for the Rust-backed Python API (ultralog._ultralog.UltraLog).

These tests verify correctness of the Rust extension and are intentionally
independent of the pure-Python fallback so we can measure performance deltas.

Run with:
    maturin develop --features pyo3/extension-module
    pytest tests/test_rust_api.py -v
"""

import os
import time
import shutil
import tempfile
import threading
import unittest

import pytest

# Skip the entire module if the Rust extension is not compiled yet.
try:
    from ultralog._ultralog import UltraLog as RustLog  # type: ignore[import]
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not RUST_AVAILABLE,
    reason="Rust extension not compiled – run `maturin develop` first",
)


def _wait_for_file(path: str, timeout: float = 3.0, min_bytes: int = 1) -> bool:
    """Poll until the file exists and has at least *min_bytes* bytes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path) and os.path.getsize(path) >= min_bytes:
            return True
        time.sleep(0.05)
    return False


class TestRustLogBasic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ultralog_rust_")
        self.fp = os.path.join(self.tmp, "test.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, **kw) -> RustLog:
        defaults = dict(
            fp=self.fp,
            level="DEBUG",
            truncate_file=True,
            with_time=False,
            console_output=False,
            enable_rotation=False,
        )
        defaults.update(kw)
        return RustLog(**defaults)

    # ── Smoke test ─────────────────────────────────────────────────────────

    def test_import_and_instantiate(self):
        log = self._make()
        log.close()

    # ── Message content ────────────────────────────────────────────────────

    def test_all_levels_written(self):
        log = self._make()
        log.debug("debug-msg")
        log.info("info-msg")
        log.warning("warning-msg")
        log.error("error-msg")
        log.critical("critical-msg")
        log.close()

        assert _wait_for_file(self.fp, min_bytes=10)
        content = open(self.fp).read()
        for word in ("debug-msg", "info-msg", "warning-msg", "error-msg", "critical-msg"):
            assert word in content, f"'{word}' not found in log"

    def test_log_method_with_level_string(self):
        log = self._make()
        log.log("via-log-method", "WARNING")
        log.close()

        assert _wait_for_file(self.fp, min_bytes=5)
        content = open(self.fp).read()
        assert "via-log-method" in content
        assert "WARNING" in content

    def test_level_filter_respects_minimum(self):
        log = self._make(level="WARNING")
        log.debug("hidden-debug")
        log.info("hidden-info")
        log.warning("visible-warning")
        log.error("visible-error")
        log.close()

        assert _wait_for_file(self.fp, min_bytes=5)
        content = open(self.fp).read()
        assert "hidden-debug" not in content
        assert "hidden-info" not in content
        assert "visible-warning" in content
        assert "visible-error" in content

    def test_set_level_dynamically(self):
        log = self._make(level="ERROR")
        log.info("before-level-change")  # should be filtered
        log.level = "DEBUG"
        log.info("after-level-change")   # should pass
        log.close()

        assert _wait_for_file(self.fp, min_bytes=5)
        content = open(self.fp).read()
        assert "before-level-change" not in content
        assert "after-level-change" in content

    def test_get_level_property(self):
        log = self._make(level="INFO")
        assert log.level == "INFO"
        log.level = "ERROR"
        assert log.level == "ERROR"
        log.close()

    # ── Timestamp ──────────────────────────────────────────────────────────

    def test_with_time_true(self):
        log = self._make(with_time=True)
        log.info("timed-message")
        log.close()

        assert _wait_for_file(self.fp, min_bytes=5)
        content = open(self.fp).read()
        # Timestamp pattern: 2025-03-16 ...
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content), \
            "No timestamp found"

    def test_with_time_false(self):
        log = self._make(with_time=False)
        log.info("no-timestamp-message")
        log.close()

        assert _wait_for_file(self.fp, min_bytes=5)
        content = open(self.fp).read()
        import re
        assert not re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content), \
            "Unexpected timestamp found"

    # ── Log rotation ───────────────────────────────────────────────────────

    def test_log_rotation_creates_backup(self):
        log = RustLog(
            fp=self.fp,
            level="DEBUG",
            truncate_file=True,
            with_time=False,
            console_output=False,
            max_file_size=300,
            backup_count=3,
            enable_rotation=True,
            force_sync=True,
            buffer_size=0,
            batch_size=1,
            flush_interval_ms=10,
        )
        msg = "x" * 60
        for _ in range(20):
            log.info(msg)
            time.sleep(0.02)
        log.close()
        time.sleep(0.5)

        backup = self.fp + ".1"
        assert os.path.exists(backup), "Backup file .1 not created"

    # ── Thread safety ──────────────────────────────────────────────────────

    def test_concurrent_writes_no_data_loss(self):
        n_threads = 8
        msgs_per_thread = 200
        log = self._make()
        errors = []

        def worker(tid):
            try:
                for i in range(msgs_per_thread):
                    log.info(f"t{tid}-m{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        log.close()

        assert not errors, f"Errors during concurrent writes: {errors}"
        assert _wait_for_file(self.fp, min_bytes=100)
        content = open(self.fp).read()
        for tid in range(n_threads):
            assert f"t{tid}-m0" in content, f"Missing output from thread {tid}"

    # ── No-file (console-only) ─────────────────────────────────────────────

    def test_console_only_no_crash(self):
        log = RustLog(level="INFO", console_output=False)
        log.info("console-only message")
        log.close()  # Should not raise

    # ── flush / close idempotence ──────────────────────────────────────────

    def test_double_close_no_error(self):
        log = self._make()
        log.info("msg")
        log.close()
        log.close()  # Second close must not raise

    def test_flush(self):
        log = self._make()
        log.info("flush-test")
        log.flush()
        time.sleep(0.2)
        log.close()

        content = open(self.fp).read()
        assert "flush-test" in content


class TestRustLogPerformance(unittest.TestCase):
    """
    Performance regression tests.  These ensure the Rust backend is
    significantly faster than the Python fallback.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ultralog_perf_")
        self.fp = os.path.join(self.tmp, "perf.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _throughput(self, n: int = 100_000) -> float:
        log = RustLog(
            fp=self.fp,
            level="INFO",
            truncate_file=True,
            with_time=True,
            console_output=False,
            enable_rotation=False,
        )
        start = time.perf_counter()
        for i in range(n):
            log.info(f"benchmark message {i}")
        log.close()
        elapsed = time.perf_counter() - start
        return n / elapsed

    def test_throughput_minimum_200k_per_second(self):
        tps = self._throughput(100_000)
        print(f"\n[Rust] throughput: {tps:,.0f} msg/s")
        assert tps > 200_000, f"Throughput too low: {tps:,.0f} msg/s"

    def test_throughput_multithread(self):
        n_threads = 8
        n_per_thread = 50_000
        log = RustLog(
            fp=self.fp,
            level="INFO",
            truncate_file=True,
            with_time=True,
            console_output=False,
            enable_rotation=False,
        )
        start = time.perf_counter()

        def worker():
            for i in range(n_per_thread):
                log.info(f"mt benchmark {i}")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        log.close()

        elapsed = time.perf_counter() - start
        total = n_threads * n_per_thread
        tps = total / elapsed
        print(f"\n[Rust] multithreaded throughput: {tps:,.0f} msg/s")
        assert tps > 500_000, f"Multithreaded throughput too low: {tps:,.0f} msg/s"

    @pytest.mark.skipif(
        not __import__("importlib.util", fromlist=["find_spec"]).find_spec("psutil"),
        reason="psutil not installed",
    )
    def test_memory_stays_bounded(self):
        import psutil
        proc = psutil.Process()
        before = proc.memory_info().rss / (1024 * 1024)
        self._throughput(200_000)
        after = proc.memory_info().rss / (1024 * 1024)
        delta = after - before
        print(f"\n[Rust] memory delta: {delta:.1f} MB")
        assert delta < 100, f"Memory grew too much: {delta:.1f} MB"


class TestRustVsStdlib(unittest.TestCase):
    """
    Compare Rust backend throughput against Python's standard logging.
    Verifies the order-of-magnitude improvement claim.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ultralog_cmp_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @pytest.mark.skipif(
        not RUST_AVAILABLE,
        reason="Rust extension not compiled",
    )
    def test_rust_faster_than_stdlib(self):
        import logging as stdlib_logging

        n = 50_000

        # Rust
        fp_r = os.path.join(self.tmp, "rust.log")
        rlog = RustLog(fp=fp_r, level="INFO", truncate_file=True,
                       console_output=False, with_time=True, enable_rotation=False)
        t0 = time.perf_counter()
        for i in range(n):
            rlog.info(f"msg {i}")
        rlog.close()
        rust_time = time.perf_counter() - t0

        # stdlib logging
        fp_s = os.path.join(self.tmp, "stdlib.log")
        slog = stdlib_logging.getLogger("bench_cmp")
        slog.setLevel(stdlib_logging.DEBUG)
        slog.handlers.clear()
        fh = stdlib_logging.FileHandler(fp_s, mode="w")
        fh.setFormatter(stdlib_logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | - %(message)s"
        ))
        slog.addHandler(fh)
        t0 = time.perf_counter()
        for i in range(n):
            slog.info(f"msg {i}")
        fh.flush()
        fh.close()
        slog.handlers.clear()
        stdlib_time = time.perf_counter() - t0

        ratio = stdlib_time / rust_time if rust_time > 0 else float("inf")
        print(
            f"\n[Comparison] Rust: {n/rust_time:,.0f} msg/s  "
            f"stdlib: {n/stdlib_time:,.0f} msg/s  "
            f"Speedup: {ratio:.1f}x"
        )
        # Rust must be at least 3× faster than stdlib (typically 30×+)
        assert ratio >= 3.0, (
            f"Rust speedup only {ratio:.1f}x – expected at least 3× "
            f"(Rust {rust_time:.2f}s vs stdlib {stdlib_time:.2f}s)"
        )


if __name__ == "__main__":
    unittest.main()
