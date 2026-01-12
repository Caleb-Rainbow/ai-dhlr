"""
设备ID模块
获取或创建设备唯一标识符
"""
from .logger import get_logger

# 默认设备ID
DEFAULT_DEVICE_ID = "dhlr"


def get_or_create_device_id(config_manager) -> str:
    """
    获取或创建设备ID
    
    如果配置中已有设备ID则返回，否则使用默认值"dhlr"并保存
    
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
    
    # 使用默认设备ID
    device_id = DEFAULT_DEVICE_ID
    
    # 保存到配置
    config.system.device_id = device_id
    config_manager.save()
    
    logger.info(f"已设置默认设备ID: {device_id}")
    return device_id
