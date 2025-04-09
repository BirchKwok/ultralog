import os
import tempfile
import shutil
import threading
import multiprocessing
from ultralog.local import UltraLog

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
        """Test concurrent configuration modification"""
        ulog = UltraLog(fp=self.log_file, truncate_file=True, console_output=False)
        threads = []
        
        def config_worker(thread_id):
            # Each thread tries to modify different configurations
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
        
        # Verify log integrity
        with open(self.log_file) as f:
            lines = f.readlines()
            print(f"Configuration modification test: Total {len(lines)} logs written")
            
    def test_message_race_condition(self):
        """Test race conditions in multi-thread log writing"""
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
        
        # Verify log integrity
        with open(self.log_file) as f:
            lines = f.readlines()
            print(f"Multi-thread write test: Expected {expected_count}, Actual {len(lines)}")
            assert len(lines) == expected_count, "Log count mismatch, possible race condition"
            
    @staticmethod
    def process_worker(process_id, num_messages, log_file):
        """Worker function for multi-process log writing"""
        # Create logger inside process to avoid pickling issues
        logger = UltraLog(fp=log_file, console_output=False)
        try:
            for i in range(num_messages):
                logger.info(f"Process {process_id} message {i}")
        finally:
            logger.close()

    def test_multiprocess_logging(self):
        """Test multi-process log writing"""
        expected_count = self.num_processes * self.num_messages_per_process
        
        # Clear log file
        with open(self.log_file, 'w'):
            pass
            
        # Use process pool for better performance
        with multiprocessing.Pool(processes=self.num_processes) as pool:
            pool.starmap(
                self.process_worker,
                [(i, self.num_messages_per_process, self.log_file) 
                 for i in range(self.num_processes)]
            )
            
        # Verify log integrity
        with open(self.log_file) as f:
            lines = f.readlines()
            print(f"Multi-process write test: Expected {expected_count}, Actual {len(lines)}")
            assert len(lines) == expected_count, "Log count mismatch, possible inter-process conflict"
            
    def run_all_tests(self):
        """Run all concurrency tests"""
        print("=== Starting concurrency tests ===")
        
        self.test_config_race_condition()
        self.test_message_race_condition()
        self.test_multiprocess_logging()
        
        self.cleanup()
        print("=== Concurrency tests completed ===")

if __name__ == "__main__":
    tester = ConcurrencyTest()
    tester.run_all_tests()
