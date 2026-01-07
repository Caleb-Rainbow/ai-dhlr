"""
设备ID生成模块
生成设备唯一标识符
"""
import hashlib
import platform
import uuid
import os
from typing import Optional

from .logger import get_logger


def _get_mac_address() -> str:
    """获取MAC地址"""
    try:
        mac = uuid.getnode()
        return ':'.join(('%012x' % mac)[i:i+2] for i in range(0, 12, 2))
    except Exception:
        return ""


def _get_cpu_serial() -> str:
    """获取CPU序列号"""
    try:
        system = platform.system()
        
        if system == "Linux":
            # Linux系统，尝试读取CPU信息
            try:
                with open('/proc/cpuinfo', 'r') as f:
                    for line in f:
                        if line.startswith('Serial'):
                            return line.split(':')[1].strip()
            except:
                pass
            
            # 尝试读取机器ID
            try:
                with open('/etc/machine-id', 'r') as f:
                    return f.read().strip()
            except:
                pass
        
        elif system == "Windows":
            # Windows系统，使用WMIC获取CPU ID
            try:
                import subprocess
                result = subprocess.check_output(
                    'wmic cpu get processorid', 
                    shell=True
                ).decode()
                lines = result.strip().split('\n')
                if len(lines) > 1:
                    return lines[1].strip()
            except:
                pass
        
        return ""
    except Exception:
        return ""


def _get_disk_serial() -> str:
    """获取磁盘序列号"""
    try:
        system = platform.system()
        
        if system == "Linux":
            # Linux系统，尝试读取根文件系统UUID
            try:
                import subprocess
                result = subprocess.check_output(
                    "blkid -s UUID -o value $(findmnt -n -o SOURCE /)",
                    shell=True, stderr=subprocess.DEVNULL
                ).decode()
                return result.strip()
            except:
                pass
            
            # 备用方案：读取DMI信息
            try:
                with open('/sys/class/dmi/id/product_serial', 'r') as f:
                    return f.read().strip()
            except:
                pass
        
        elif system == "Windows":
            try:
                import subprocess
                result = subprocess.check_output(
                    'wmic diskdrive get serialnumber',
                    shell=True
                ).decode()
                lines = result.strip().split('\n')
                if len(lines) > 1:
                    return lines[1].strip()
            except:
                pass
        
        return ""
    except Exception:
        return ""


def generate_device_id() -> str:
    """
    生成设备唯一ID
    
    算法：SHA256(MAC地址 + CPU序列号 + 磁盘序列号)[:16]
    
    Returns:
        16字符的十六进制设备ID
    """
    logger = get_logger()
    
    components = []
    
    # 收集硬件标识符
    mac = _get_mac_address()
    if mac:
        components.append(mac)
        logger.debug(f"MAC地址: {mac}")
    
    cpu = _get_cpu_serial()
    if cpu:
        components.append(cpu)
        logger.debug(f"CPU序列号: {cpu[:8]}...")
    
    disk = _get_disk_serial()
    if disk:
        components.append(disk)
        logger.debug(f"磁盘序列号: {disk[:8]}...")
    
    # 如果没有获取到任何硬件标识，使用随机UUID作为后备
    if not components:
        logger.warning("无法获取硬件标识，使用随机UUID")
        components.append(str(uuid.uuid4()))
    
    # 组合并哈希
    combined = "|".join(components)
    hash_obj = hashlib.sha256(combined.encode('utf-8'))
    device_id = hash_obj.hexdigest()[:16].upper()
    
    logger.info(f"生成设备ID: {device_id}")
    return device_id


def get_or_create_device_id(config_manager) -> str:
    """
    获取或创建设备ID
    
    如果配置中已有设备ID则返回，否则生成新的并保存
    
    Args:
        config_manager: 配置管理器实例
        
    Returns:
        设备ID字符串
    """
    logger = get_logger()
    
    # 检查配置中是否已有设备ID
    config = config_manager.config
    existing_id = getattr(config.system, 'device_id', None)
    
    if existing_id and existing_id.strip():
        logger.info(f"使用已有设备ID: {existing_id}")
        return existing_id
    
    # 生成新的设备ID
    device_id = generate_device_id()
    
    # 保存到配置
    config.system.device_id = device_id
    config_manager.save()
    
    logger.info(f"已生成并保存新设备ID: {device_id}")
    return device_id
