import os
import subprocess
import tempfile
import shutil
import threading
import unittest
import time
import requests

from ultralog.local import UltraLog
from ultralog.server import args

class TestLocalAPI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ultralog_test_")
        self.log_file = os.path.join(self.test_dir, "test.log")
        self.ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)

    def tearDown(self):
        self.ulog.close()
        shutil.rmtree(self.test_dir)

    def test_local_log_levels(self):
        """Test local logging levels"""
        test_messages = [
            ("debug", "This is a debug message"),
            ("info", "This is an info message"),
            ("warning", "This is a warning message"),
            ("error", "This is an error message"),
            ("critical", "This is a critical message")
        ]

        for level, msg in test_messages:
            getattr(self.ulog, level)(msg)

        self.ulog.close()

        # validate that all messages are logged correctly
        with open(self.log_file) as f:
            content = f.read()
            for level, msg in test_messages:
                self.assertIn(msg, content)
                self.assertIn(level.upper(), content)

    def test_local_log_rotation(self):
        """test log rotation"""
        self.ulog.close()
        self.ulog = UltraLog(
            fp=self.log_file,
            truncate_file=True,
            console_output=True,
            max_file_size=200,
            backup_count=2,
            force_sync=True,
            enable_rotation=True,
            file_buffer_size=0
        )

        # Write enough logs to trigger rotation
        large_msg = "x" * 50
        for i in range(10):
            self.ulog.info(f"Test message {i}: {large_msg}")
            time.sleep(0.05)
        
        # Close the logger to trigger rotation
        self.ulog.close()
        time.sleep(1)
        
        # Check if the log files have been rotated
        print(f"Test directory: {self.test_dir}")
        rotated_files = [f for f in os.listdir(self.test_dir) if f.startswith("test.log")]
        print(f"Rotated files: {rotated_files}")
        print(f"Full file list: {os.listdir(self.test_dir)}")
        self.assertIn("test.log.1", rotated_files)
        self.assertIn("test.log.2", rotated_files)

class TestRemoteAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess
        cls.server_process = subprocess.Popen(
            ["python", "-m", "ultralog.server", "--host", "127.0.0.1", "--port", "9999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        time.sleep(2)

    @classmethod
    def tearDownClass(cls):
        cls.server_process.terminate()
        cls.server_process.wait()

    def test_health_check(self):
        """Test health check endpoint"""
        response = requests.get("http://127.0.0.1:9999/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})

    def test_log_without_auth(self):
        """Test without authentication"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"message": "test", "level": "info"}
        )
        self.assertEqual(response.status_code, 403)

    def test_log_with_auth(self):
        """Test with authentication"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"message": "test message", "level": "info"},
            headers={"Authorization": f"Bearer {args.auth_token}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

    def test_log_invalid_level(self):
        """Test invalid log level"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"message": "test", "level": "invalid"},
            headers={"Authorization": f"Bearer {args.auth_token}"}
        )
        self.assertEqual(response.status_code, 200)

    def test_log_missing_message(self):
        """Test missing message"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"level": "info"},
            headers={"Authorization": f"Bearer {args.auth_token}"}
        )
        self.assertEqual(response.status_code, 200)

class TestConcurrentLogging(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ultralog_test_")
        self.log_file = os.path.join(self.test_dir, "concurrent.log")
        self.server_process = None

    def tearDown(self):
        if self.server_process:
            self.server_process.terminate()
            self.server_process.wait()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_local_concurrent_writes(self):
        """Test local concurrent writes"""
        num_threads = 10
        messages_per_thread = 100
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)

        def worker(thread_id):
            for i in range(messages_per_thread):
                ulog.info(f"Thread {thread_id} message {i}")
                time.sleep(0.001)
        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        ulog.close()

        with open(self.log_file) as f:
            content = f.read()
            for i in range(num_threads):
                for j in range(messages_per_thread):
                    self.assertIn(f"Thread {i} message {j}", content)

    def test_remote_concurrent_writes(self):
        """Test remote concurrent writes"""
        self.server_process = subprocess.Popen(
            ["python", "-m", "ultralog.server", "--host", "127.0.0.1", "--port", "9999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)

        num_threads = 5
        messages_per_thread = 50
        auth_token = args.auth_token

        def worker(thread_id):
            for i in range(messages_per_thread):
                response = requests.post(
                    "http://127.0.0.1:9999/log",
                    json={"message": f"Thread {thread_id} message {i}", "level": "info"},
                    headers={"Authorization": f"Bearer {auth_token}"}
                )
                self.assertEqual(response.status_code, 200)
                time.sleep(0.01)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Check if the server log contains all messages
        server_log = os.path.join("logs", "ultralog.log")
        if os.path.exists(server_log):
            with open(server_log) as f:
                content = f.read()
                for i in range(num_threads):
                    for j in range(messages_per_thread):
                        self.assertIn(f"Thread {i} message {j}", content)

if __name__ == "__main__":
    unittest.main()
