#!/usr/bin/env python3
"""测试 UltraLog 的日志轮转功能"""

import os
import time
import shutil
from ultralog import UltraLog


def test_log_rotation():
    """测试日志轮转功能"""
    print("=" * 60)
    print("开始测试 UltraLog 日志轮转功能")
    print("=" * 60)
    
    # 测试配置
    test_dir = "test_rotation_logs"
    log_file = os.path.join(test_dir, "rotation_test.log")
    max_file_size = 1024  # 1KB - 小文件以便快速触发轮转
    backup_count = 3
    
    # 清理之前的测试文件
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir)
    
    print(f"\n配置信息:")
    print(f"  日志目录: {test_dir}")
    print(f"  日志文件: {log_file}")
    print(f"  最大文件大小: {max_file_size} 字节")
    print(f"  备份数量: {backup_count}")
    
    # 创建 logger
    logger = UltraLog(
        name="RotationTest",
        fp=log_file,
        level='INFO',
        max_file_size=max_file_size,
        backup_count=backup_count,
        console_output=True,
        enable_rotation=True,
        force_sync=True,  # 强制同步以确保立即写入
        truncate_file=True
    )
    
    print("\n开始写入日志...")
    
    # 写入足够多的日志来触发多次轮转
    message = "X" * 100  # 每条日志约 100 字节
    num_messages = 50  # 写入 50 条消息，应该触发多次轮转
    
    for i in range(num_messages):
        logger.info(f"Message {i:03d}: {message}")
        if i % 10 == 0:
            time.sleep(0.5)  # 给后台线程时间处理
            print(f"  已写入 {i} 条消息...")
    
    print(f"\n总共写入 {num_messages} 条消息")
    print("等待后台线程完成写入...")
    time.sleep(2)  # 等待批处理完成
    
    # 关闭 logger
    logger.close()
    print("\nLogger 已关闭")
    
    # 检查文件
    print("\n" + "=" * 60)
    print("检查生成的文件:")
    print("=" * 60)
    
    files_found = []
    
    # 检查主日志文件
    if os.path.exists(log_file):
        size = os.path.getsize(log_file)
        files_found.append((log_file, size))
        print(f"✓ 主日志文件: {log_file} ({size} 字节)")
    else:
        print(f"✗ 主日志文件不存在: {log_file}")
    
    # 检查备份文件
    for i in range(1, backup_count + 1):
        backup_file = f"{log_file}.{i}"
        if os.path.exists(backup_file):
            size = os.path.getsize(backup_file)
            files_found.append((backup_file, size))
            print(f"✓ 备份文件 {i}: {backup_file} ({size} 字节)")
        else:
            print(f"  备份文件 {i}: {backup_file} (不存在)")
    
    # 验证结果
    print("\n" + "=" * 60)
    print("测试结果:")
    print("=" * 60)
    
    # 检查是否发生了轮转
    rotation_occurred = len(files_found) > 1
    
    if rotation_occurred:
        print(f"✓ 日志轮转成功！")
        print(f"  - 找到 {len(files_found)} 个文件")
        print(f"  - 主日志文件大小: {files_found[0][1]} 字节")
        
        # 检查主文件大小是否小于限制
        if files_found[0][1] <= max_file_size:
            print(f"✓ 主文件大小符合预期（<= {max_file_size} 字节）")
        else:
            print(f"⚠ 主文件大小超出限制: {files_found[0][1]} > {max_file_size}")
        
        # 检查备份文件
        backup_files = [f for f in files_found if f[0] != log_file]
        if backup_files:
            print(f"✓ 成功创建 {len(backup_files)} 个备份文件")
            for backup_file, size in backup_files:
                basename = os.path.basename(backup_file)
                print(f"    - {basename}: {size} 字节")
        
        # 读取并显示部分日志内容
        print("\n主日志文件的最后几行:")
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
                for line in lines[-3:]:
                    print(f"  {line.rstrip()}")
        except Exception as e:
            print(f"  无法读取日志文件: {e}")
        
        # 读取第一个备份文件的前几行
        if backup_files:
            first_backup = backup_files[0][0]
            print(f"\n第一个备份文件 ({os.path.basename(first_backup)}) 的前几行:")
            try:
                with open(first_backup, 'r') as f:
                    lines = f.readlines()
                    for line in lines[:3]:
                        print(f"  {line.rstrip()}")
            except Exception as e:
                print(f"  无法读取备份文件: {e}")
        
        print("\n✅ 轮转功能测试通过！")
        
    else:
        print("✗ 日志轮转未发生")
        print(f"  - 只找到 {len(files_found)} 个文件")
        if files_found:
            print(f"  - 文件大小: {files_found[0][1]} 字节")
        print("\n❌ 轮转功能测试失败！")
    
    # 列出所有文件
    print("\n" + "=" * 60)
    print("测试目录中的所有文件:")
    print("=" * 60)
    for filename in sorted(os.listdir(test_dir)):
        filepath = os.path.join(test_dir, filename)
        size = os.path.getsize(filepath)
        print(f"  {filename}: {size} 字节")
    
    print("\n" + "=" * 60)
    print(f"测试完成！测试文件保存在: {test_dir}")
    print("=" * 60)
    
    return rotation_occurred


if __name__ == "__main__":
    success = test_log_rotation()
    exit(0 if success else 1)


