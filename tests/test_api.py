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
        """测试本地日志级别功能"""
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

        # 验证日志文件内容
        with open(self.log_file) as f:
            content = f.read()
            for level, msg in test_messages:
                self.assertIn(msg, content)
                self.assertIn(level.upper(), content)

    def test_local_log_rotation(self):
        """测试本地日志轮转功能"""
        self.ulog.close()
        self.ulog = UltraLog(
            fp=self.log_file,
            truncate_file=True,
            console_output=True,  # 启用控制台输出以查看调试信息
            max_file_size=200,  # 200字节
            backup_count=2,
            force_sync=True,
            enable_rotation=True,
            file_buffer_size=0  # 禁用缓冲
        )

        # 写入足够多的日志以触发轮转
        large_msg = "x" * 50  # 每条消息50字节
        for i in range(10):  # 总共500字节
            self.ulog.info(f"Test message {i}: {large_msg}")
            time.sleep(0.05)  # 确保每条消息都写入
        
        # 强制刷新并等待轮转完成
        self.ulog.close()
        time.sleep(1)  # 等待轮转完成
        
        # 验证日志轮转文件存在
        print(f"Test directory: {self.test_dir}")
        rotated_files = [f for f in os.listdir(self.test_dir) if f.startswith("test.log")]
        print(f"Rotated files: {rotated_files}")
        print(f"Full file list: {os.listdir(self.test_dir)}")
        self.assertIn("test.log.1", rotated_files)
        self.assertIn("test.log.2", rotated_files)

class TestRemoteAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 使用subprocess启动服务器
        import subprocess
        cls.server_process = subprocess.Popen(
            ["python", "-m", "ultralog.server", "--host", "127.0.0.1", "--port", "9999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        # 等待服务器启动
        time.sleep(2)

    @classmethod
    def tearDownClass(cls):
        cls.server_process.terminate()
        cls.server_process.wait()

    def test_health_check(self):
        """测试健康检查接口"""
        response = requests.get("http://127.0.0.1:9999/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})

    def test_log_without_auth(self):
        """测试未认证的日志接口访问"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"message": "test", "level": "info"}
        )
        self.assertEqual(response.status_code, 403)

    def test_log_with_auth(self):
        """测试认证后的日志接口"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"message": "test message", "level": "info"},
            headers={"Authorization": f"Bearer {args.auth_token}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

    def test_log_invalid_level(self):
        """测试无效日志级别"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"message": "test", "level": "invalid"},
            headers={"Authorization": f"Bearer {args.auth_token}"}
        )
        self.assertEqual(response.status_code, 200)  # 服务器应接受无效级别并默认为INFO

    def test_log_missing_message(self):
        """测试缺少消息内容"""
        response = requests.post(
            "http://127.0.0.1:9999/log",
            json={"level": "info"},
            headers={"Authorization": f"Bearer {args.auth_token}"}
        )
        self.assertEqual(response.status_code, 200)  # 服务器应处理空消息

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
        """测试本地日志的并发写入"""
        num_threads = 10
        messages_per_thread = 100
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)

        def worker(thread_id):
            for i in range(messages_per_thread):
                ulog.info(f"Thread {thread_id} message {i}")
                time.sleep(0.001)  # 微小延迟以增加并发可能性

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        ulog.close()

        # 验证所有消息都被写入
        with open(self.log_file) as f:
            content = f.read()
            for i in range(num_threads):
                for j in range(messages_per_thread):
                    self.assertIn(f"Thread {i} message {j}", content)

    def test_remote_concurrent_writes(self):
        """测试远程API的并发写入"""
        # 启动服务器
        self.server_process = subprocess.Popen(
            ["python", "-m", "ultralog.server", "--host", "127.0.0.1", "--port", "9999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(2)  # 等待服务器启动

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
                time.sleep(0.01)  # 微小延迟

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 验证服务器日志文件
        server_log = os.path.join("logs", "ultralog.log")
        if os.path.exists(server_log):
            with open(server_log) as f:
                content = f.read()
                for i in range(num_threads):
                    for j in range(messages_per_thread):
                        self.assertIn(f"Thread {i} message {j}", content)

if __name__ == "__main__":
    unittest.main()
