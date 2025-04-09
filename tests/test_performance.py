import os
import time
import logging
import tempfile
import shutil
import random
import string
from threading import Thread
import psutil
from loguru import logger

from ultralog import UltraLog

class PerformanceTest:
    def __init__(self):
        self.test_dir = tempfile.mkdtemp(prefix="ultralog_test_")
        self.log_file = os.path.join(self.test_dir, "test.log")
        self.num_messages = 100000
        self.num_threads = 10
        self.message_sizes = {
            'small': 50,
            'medium': 500,
            'large': 5000
        }
        self.log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        
    def cleanup(self):
        shutil.rmtree(self.test_dir)
        
    def generate_message(self, size):
        """生成指定大小的随机消息"""
        return ''.join(random.choices(string.ascii_letters + string.digits, k=size))
        
    def test_ultralog_single_thread(self, msg_size='medium', level='INFO'):
        """测试UltraLog单线程性能"""
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)
        msg = self.generate_message(self.message_sizes[msg_size])
        start = time.time()
        mem_before = psutil.Process().memory_info().rss
        
        for i in range(self.num_messages):
            if level == 'DEBUG':
                ulog.debug(msg)
            elif level == 'INFO':
                ulog.info(msg)
            elif level == 'WARNING':
                ulog.warning(msg)
            elif level == 'ERROR':
                ulog.error(msg)
            
        ulog.close()
        duration = time.time() - start
        mem_after = psutil.Process().memory_info().rss
        print(f"UltraLog单线程({level}/{msg_size}): {self.num_messages}条日志, "
              f"耗时{duration:.2f}秒, 吞吐量{self.num_messages/duration:.2f}条/秒, "
              f"内存使用: {(mem_after - mem_before)/1024/1024:.2f}MB")
        
    def test_ultralog_multi_thread(self):
        """测试UltraLog多线程性能"""
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)
        threads = []
        start = time.time()
        mem_before = psutil.Process().memory_info().rss
        
        def worker(thread_id):
            for i in range(self.num_messages // self.num_threads):
                ulog.info(f"Thread {thread_id} message {i}")
        
        for i in range(self.num_threads):
            t = Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        ulog.close()
        duration = time.time() - start
        mem_after = psutil.Process().memory_info().rss
        print(f"UltraLog多线程({self.num_threads}线程): {self.num_messages}条日志, "
              f"耗时{duration:.2f}秒, 吞吐量{self.num_messages/duration:.2f}条/秒, "
              f"内存使用: {(mem_after - mem_before)/1024/1024:.2f}MB")

    def test_standard_logging_multi_thread(self):
        """测试标准logging多线程性能"""
        logging.basicConfig(
            filename=self.log_file,
            filemode='w',
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        std_log = logging.getLogger("std_log")
        threads = []
        start = time.time()
        mem_before = psutil.Process().memory_info().rss
        
        def worker(thread_id):
            for i in range(self.num_messages // self.num_threads):
                std_log.info(f"Thread {thread_id} message {i}")
        
        for i in range(self.num_threads):
            t = Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        duration = time.time() - start
        mem_after = psutil.Process().memory_info().rss
        print(f"标准logging多线程({self.num_threads}线程): {self.num_messages}条日志, "
              f"耗时{duration:.2f}秒, 吞吐量{self.num_messages/duration:.2f}条/秒, "
              f"内存使用: {(mem_after - mem_before)/1024/1024:.2f}MB")

    def test_loguru_multi_thread(self):
        """测试loguru多线程性能"""
        logger.remove()
        logger.add(self.log_file, mode='w')
        threads = []
        start = time.time()
        mem_before = psutil.Process().memory_info().rss
        
        def worker(thread_id):
            for i in range(self.num_messages // self.num_threads):
                logger.info(f"Thread {thread_id} message {i}")
        
        for i in range(self.num_threads):
            t = Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        duration = time.time() - start
        mem_after = psutil.Process().memory_info().rss
        print(f"Loguru多线程({self.num_threads}线程): {self.num_messages}条日志, "
              f"耗时{duration:.2f}秒, 吞吐量{self.num_messages/duration:.2f}条/秒, "
              f"内存使用: {(mem_after - mem_before)/1024/1024:.2f}MB")
        
    def test_standard_logging(self, msg_size='medium', level='INFO'):
        """测试标准logging模块性能"""
        logging.basicConfig(
            filename=self.log_file,
            filemode='w',
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.DEBUG
        )
        std_log = logging.getLogger("std_log")
        msg = self.generate_message(self.message_sizes[msg_size])
        start = time.time()
        mem_before = psutil.Process().memory_info().rss
        
        for i in range(self.num_messages):
            if level == 'DEBUG':
                std_log.debug(msg)
            elif level == 'INFO':
                std_log.info(msg)
            elif level == 'WARNING':
                std_log.warning(msg)
            elif level == 'ERROR':
                std_log.error(msg)
            
        duration = time.time() - start
        mem_after = psutil.Process().memory_info().rss
        print(f"标准logging单线程({level}/{msg_size}): {self.num_messages}条日志, "
              f"耗时{duration:.2f}秒, 吞吐量{self.num_messages/duration:.2f}条/秒, "
              f"内存使用: {(mem_after - mem_before)/1024/1024:.2f}MB")
        
    def test_loguru(self, msg_size='medium', level='INFO'):
        """测试loguru性能"""
        logger.remove()
        logger.add(self.log_file, mode='w')
        msg = self.generate_message(self.message_sizes[msg_size])
        start = time.time()
        mem_before = psutil.Process().memory_info().rss
        
        for i in range(self.num_messages):
            if level == 'DEBUG':
                logger.debug(msg)
            elif level == 'INFO':
                logger.info(msg)
            elif level == 'WARNING':
                logger.warning(msg)
            elif level == 'ERROR':
                logger.error(msg)
            
        duration = time.time() - start
        mem_after = psutil.Process().memory_info().rss
        print(f"Loguru单线程({level}/{msg_size}): {self.num_messages}条日志, "
              f"耗时{duration:.2f}秒, 吞吐量{self.num_messages/duration:.2f}条/秒, "
              f"内存使用: {(mem_after - mem_before)/1024/1024:.2f}MB")
        
    def run_all_tests(self):
        """运行所有性能测试"""
        print("=== 开始性能测试 ===")
        print(f"每条测试将写入{self.num_messages}条日志")
        
        # 测试不同消息大小
        print("\n=== 测试不同消息大小 ===")
        for size in self.message_sizes:
            print(f"\n消息大小: {size}")
            self.test_ultralog_single_thread(msg_size=size)
            self.test_standard_logging(msg_size=size)
            self.test_loguru(msg_size=size)
            
        # 测试不同日志级别
        print("\n=== 测试不同日志级别 ===")
        for level in self.log_levels:
            print(f"\n日志级别: {level}")
            self.test_ultralog_single_thread(level=level)
            self.test_standard_logging(level=level)
            self.test_loguru(level=level)
            
        # 测试多线程性能
        print("\n=== 测试多线程性能 ===")
        self.test_ultralog_multi_thread()
        self.test_standard_logging_multi_thread()
        self.test_loguru_multi_thread()
        
        self.cleanup()
        print("\n=== 性能测试完成 ===")

if __name__ == "__main__":
    tester = PerformanceTest()
    tester.run_all_tests()
