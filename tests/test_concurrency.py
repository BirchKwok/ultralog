import os
import tempfile
import shutil
import threading
import multiprocessing
from ultralog import UltraLog

class ConcurrencyTest:
    def __init__(self):
        self.test_dir = tempfile.mkdtemp(prefix="ultralog_concurrency_")
        self.log_file = os.path.join(self.test_dir, "concurrency.log")
        self.num_threads = 10
        self.num_messages_per_thread = 10000
        self.num_processes = 5
        self.num_messages_per_process = 20000
        
    def cleanup(self):
        shutil.rmtree(self.test_dir)
        
    def test_config_race_condition(self):
        """测试多线程同时修改logger配置"""
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)
        threads = []
        
        def config_worker(thread_id):
            # 每个线程尝试修改不同配置
            if thread_id % 2 == 0:
                ulog.level = 'DEBUG' if thread_id % 4 == 0 else 'INFO'
            else:
                ulog.console_output = True if thread_id % 3 == 0 else False
            ulog.info(f"Thread {thread_id} config changed")
        
        for i in range(self.num_threads):
            t = threading.Thread(target=config_worker, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        ulog.close()
        
        # 验证日志完整性
        with open(self.log_file) as f:
            lines = f.readlines()
            print(f"配置修改测试: 共写入{len(lines)}条日志")
            
    def test_message_race_condition(self):
        """测试多线程日志写入竞态条件"""
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)
        threads = []
        expected_count = self.num_threads * self.num_messages_per_thread
        
        def message_worker(thread_id):
            for i in range(self.num_messages_per_thread):
                ulog.info(f"Thread {thread_id} message {i}")
        
        for i in range(self.num_threads):
            t = threading.Thread(target=message_worker, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        ulog.close()
        
        # 验证日志完整性
        with open(self.log_file) as f:
            lines = f.readlines()
            print(f"多线程写入测试: 预期{expected_count}条, 实际{len(lines)}条")
            assert len(lines) == expected_count, "日志数量不匹配，可能存在竞态条件"
            
    @staticmethod
    def process_worker(process_id, num_messages, log_file):
        """多进程日志写入的工作函数"""
        # Create logger inside process to avoid pickling issues
        logger = UltraLog(fp=log_file, console_output=False)
        try:
            for i in range(num_messages):
                logger.info(f"Process {process_id} message {i}")
        finally:
            logger.close()

    def test_multiprocess_logging(self):
        """测试多进程日志写入"""
        expected_count = self.num_processes * self.num_messages_per_process
        
        # 清空日志文件
        with open(self.log_file, 'w'):
            pass
            
        # Use process pool for better performance
        with multiprocessing.Pool(processes=self.num_processes) as pool:
            pool.starmap(
                self.process_worker,
                [(i, self.num_messages_per_process, self.log_file) 
                 for i in range(self.num_processes)]
            )
            
        # 验证日志完整性
        with open(self.log_file) as f:
            lines = f.readlines()
            print(f"多进程写入测试: 预期{expected_count}条, 实际{len(lines)}条")
            assert len(lines) == expected_count, "日志数量不匹配，可能存在进程间冲突"
            
    def run_all_tests(self):
        """运行所有并发测试"""
        print("=== 开始并发测试 ===")
        
        self.test_config_race_condition()
        self.test_message_race_condition()
        self.test_multiprocess_logging()
        
        self.cleanup()
        print("=== 并发测试完成 ===")

if __name__ == "__main__":
    tester = ConcurrencyTest()
    tester.run_all_tests()
