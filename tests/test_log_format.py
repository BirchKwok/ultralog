import os
import re
import shutil
import tempfile
import time
import unittest

from ultralog import UltraLog


def _read(fp: str, timeout: float = 3.0) -> str:
    """Wait until the log file has content, then return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            return open(fp).read()
        time.sleep(0.05)
    return open(fp).read() if os.path.exists(fp) else ""


class TestLogFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ultralog_fmt_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name="UltraLog", with_time=True) -> tuple:
        fp = os.path.join(self.tmp, f"{name}.log")
        logger = UltraLog(
            name=name,
            fp=fp,
            console_output=False,
            with_time=with_time,
            truncate_file=True,
            enable_rotation=False,
        )
        return logger, fp

    def test_default_format(self):
        """测试默认日志格式"""
        logger, fp = self._make()
        logger.info("test message")
        logger.close()

        output = _read(fp)
        self.assertIn("| INFO     | UltraLog |", output)
        self.assertIn(" - test message", output)
        self.assertRegex(output, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+")

    def test_custom_name_format(self):
        """测试自定义名称的日志格式"""
        logger, fp = self._make(name="CustomLogger")
        logger.info("test message")
        logger.close()

        output = _read(fp)
        self.assertIn("| INFO     | CustomLogger |", output)
        self.assertIn(" - test message", output)
        self.assertRegex(output, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+")

    def test_log_levels(self):
        """测试不同日志级别的格式"""
        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for level in levels:
            with self.subTest(level=level):
                fp = os.path.join(self.tmp, f"level_{level}.log")
                logger = UltraLog(
                    fp=fp, console_output=False, with_time=True,
                    truncate_file=True, enable_rotation=False,
                )
                getattr(logger, level.lower())("test message")
                logger.close()

                output = _read(fp)
                self.assertIn(f"| {level.ljust(8)} |", output)

    def test_no_timestamp_when_with_time_false(self):
        """测试 with_time=False 时不包含时间戳"""
        logger, fp = self._make(with_time=False)
        logger.info("no-ts message")
        logger.close()

        output = _read(fp)
        self.assertIn("no-ts message", output)
        self.assertNotRegex(output, r"\d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()
