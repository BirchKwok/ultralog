"""
Performance benchmark: UltraLog (Rust) vs Python standard logging
Run after: maturin develop --features extension-module

Throughput and peak memory are measured in two SEPARATE passes so that
tracemalloc instrumentation does not inflate the timing numbers.
"""
import gc, logging, os, shutil, tempfile, threading, time, tracemalloc

from ultralog._ultralog import UltraLog as RustLog

TMP = tempfile.mkdtemp(prefix="ultralog_bench_")
_cnt = 0

def _next_fp(prefix):
    global _cnt; _cnt += 1
    return os.path.join(TMP, f"{prefix}_{_cnt}.log")


# ── Backend factories ─────────────────────────────────────────────────────────

def ul_make(fp):
    return RustLog(fp=fp, level="DEBUG", truncate_file=True,
                   console_output=False, with_time=True, enable_rotation=False)

def ul_log(log, msg): log.info(msg)
def ul_close(log):    log.close()


def sl_make(fp):
    import logging as L
    name = f"sl_{_cnt}"
    logger = L.getLogger(name)
    logger.setLevel(L.DEBUG)
    logger.handlers.clear()
    fh = L.FileHandler(fp, mode="w")
    fh.setFormatter(L.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | - %(message)s"))
    logger.addHandler(fh)
    logger._fh = fh
    return logger

def sl_log(log, msg):  log.info(msg)
def sl_close(log):
    log._fh.flush(); log._fh.close(); log.handlers.clear()


# ── Benchmark primitives ──────────────────────────────────────────────────────

def _time_st(make, do_log, close, n, fp):
    """Throughput only – no tracemalloc overhead."""
    log = make(fp)
    t0 = time.perf_counter()
    for i in range(n):
        do_log(log, f"bench message {i} padding padding padding")
    close(log)
    return n / (time.perf_counter() - t0)


def _time_mt(make, do_log, close, n_threads, n_per, fp):
    log = make(fp)
    t0 = time.perf_counter()
    threads = [
        threading.Thread(target=lambda: [do_log(log, f"mt {i}") for i in range(n_per)])
        for _ in range(n_threads)
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    close(log)
    return (n_threads * n_per) / (time.perf_counter() - t0)


def _mem_st(make, do_log, close, n, fp):
    """Peak heap allocation – separate small run with tracemalloc."""
    gc.collect()
    tracemalloc.start()
    log = make(fp)
    for i in range(n):
        do_log(log, f"bench message {i} padding padding padding")
    close(log)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / (1024 * 1024)


def _mem_mt(make, do_log, close, n_threads, n_per, fp):
    gc.collect()
    tracemalloc.start()
    log = make(fp)
    threads = [
        threading.Thread(target=lambda: [do_log(log, f"mt {i}") for i in range(n_per)])
        for _ in range(n_threads)
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    close(log)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / (1024 * 1024)


def bench_st(label, make, do_log, close, n, mem_n=20_000):
    tps = _time_st(make, do_log, close, n,     _next_fp("t"))
    mem = _mem_st (make, do_log, close, mem_n, _next_fp("m"))
    print(f"  {label:30s}  {tps:>12,.0f} msg/s   {mem:>6.2f} MB")
    return tps, mem


def bench_mt(label, make, do_log, close, n_threads, n_per, mem_per=5_000):
    tps = _time_mt(make, do_log, close, n_threads, n_per,  _next_fp("t"))
    mem = _mem_mt (make, do_log, close, n_threads, mem_per, _next_fp("m"))
    total = n_threads * n_per
    print(f"  {label:30s}  {tps:>12,.0f} msg/s   {mem:>6.2f} MB  ({n_threads}T×{n_per:,}={total:,})")
    return tps, mem


# ── Run ────────────────────────────────────────────────────────────────────────

N    = 500_000
N_MT = 100_000
THRS = 8
HDR  = f"  {'Backend':30s}  {'Throughput':>12s}   {'Peak mem':>8s}"
SEP  = "  " + "-" * 58

print(f"\n{'='*72}")
print(f"  Single-threaded  ({N:,} messages, memory sampled over 20,000)")
print(f"{'='*72}")
print(HDR); print(SEP)
ul_st_tps, ul_st_mem = bench_st("UltraLog (Rust)",  ul_make, ul_log, ul_close, N)
sl_st_tps, sl_st_mem = bench_st("Standard logging", sl_make, sl_log, sl_close, N)

print(f"\n{'='*72}")
print(f"  Multi-threaded  ({THRS} threads × {N_MT:,} messages, memory sampled over 5,000/thread)")
print(f"{'='*72}")
print(HDR); print(SEP)
ul_mt_tps, ul_mt_mem = bench_mt("UltraLog (Rust)",  ul_make, ul_log, ul_close, THRS, N_MT)
sl_mt_tps, sl_mt_mem = bench_mt("Standard logging", sl_make, sl_log, sl_close, THRS, N_MT)

print(f"\n{'='*72}")
print(f"  Summary")
print(f"{'='*72}")
print(f"  Single-thread  : UltraLog {ul_st_tps:>12,.0f} msg/s  peak {ul_st_mem:.2f} MB")
print(f"                   stdlib   {sl_st_tps:>12,.0f} msg/s  peak {sl_st_mem:.2f} MB")
print(f"                   speedup  {ul_st_tps/sl_st_tps:>8.1f}×   "
      f"mem ratio {sl_st_mem/max(ul_st_mem,0.001):.1f}× less")
print(f"  Multi-thread   : UltraLog {ul_mt_tps:>12,.0f} msg/s  peak {ul_mt_mem:.2f} MB")
print(f"                   stdlib   {sl_mt_tps:>12,.0f} msg/s  peak {sl_mt_mem:.2f} MB")
print(f"                   speedup  {ul_mt_tps/sl_mt_tps:>8.1f}×   "
      f"mem ratio {sl_mt_mem/max(ul_mt_mem,0.001):.1f}× less")
print(f"{'='*72}\n")

shutil.rmtree(TMP, ignore_errors=True)
