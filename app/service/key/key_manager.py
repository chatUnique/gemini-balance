import asyncio
from itertools import cycle
from typing import Dict, List, Union, Tuple

from app.config.config import settings
from app.log.logger import get_key_manager_logger

logger = get_key_manager_logger()


class KeyManager:
    def __init__(self, api_keys: list):
        self.api_keys = api_keys
        self.key_cycle = cycle(api_keys)
        self.key_cycle_lock = asyncio.Lock()
        self.failure_count_lock = asyncio.Lock()
        self.key_failure_counts: Dict[str, int] = {key: 0 for key in api_keys}
        self.MAX_FAILURES = settings.MAX_FAILURES
        self.paid_key = settings.PAID_KEY

    async def get_paid_key(self) -> str:
        return self.paid_key

    async def get_next_key(self) -> str:
        """获取下一个API key"""
        async with self.key_cycle_lock:
            return next(self.key_cycle)

    async def is_key_valid(self, key: str) -> bool:
        """检查key是否有效"""
        async with self.failure_count_lock:
            return self.key_failure_counts[key] < self.MAX_FAILURES

    async def reset_failure_counts(self):
        """重置所有key的失败计数"""
        async with self.failure_count_lock:
            for key in self.key_failure_counts:
                self.key_failure_counts[key] = 0

    async def get_next_working_key(self) -> str:
        """获取下一可用的API key"""
        initial_key = await self.get_next_key()
        current_key = initial_key

        while True:
            if await self.is_key_valid(current_key):
                return current_key

            current_key = await self.get_next_key()
            if current_key == initial_key:
                # await self.reset_failure_counts() 取消重置
                return current_key

    async def handle_api_failure(self, api_key: str) -> str:
        """处理API调用失败"""
        async with self.failure_count_lock:
            self.key_failure_counts[api_key] += 1
            if self.key_failure_counts[api_key] >= self.MAX_FAILURES:
                logger.warning(
                    f"API key {api_key} has failed {self.MAX_FAILURES} times"
                )

        return await self.get_next_working_key()

    def get_fail_count(self, key: str) -> int:
        """获取指定密钥的失败次数"""
        return self.key_failure_counts.get(key, 0)

    async def get_keys_by_status(self) -> dict:
        """获取分类后的API key列表，包括失败次数"""
        valid_keys = {}
        invalid_keys = {}

        async with self.failure_count_lock:
            for key in self.api_keys:
                fail_count = self.key_failure_counts[key]
                if fail_count < self.MAX_FAILURES:
                    valid_keys[key] = fail_count
                else:
                    invalid_keys[key] = fail_count

        return {"valid_keys": valid_keys, "invalid_keys": invalid_keys}
    
    async def add_keys(self, keys: Union[str, List[str]]) -> Tuple[List[str], List[str]]:
        """
        添加一个或多个API密钥
        
        Args:
            keys: 单个密钥字符串或密钥列表
            
        Returns:
            Tuple[List[str], List[str]]: 成功添加的密钥列表和已存在的密钥列表
        """
        if isinstance(keys, str):
            keys = [keys]
            
        added_keys = []
        existing_keys = []
        
        async with self.key_cycle_lock:
            # 重建key_cycle
            self.api_keys = list(self.api_keys)  # 确保是可变列表
            
            for key in keys:
                key = key.strip()
                if not key:  # 跳过空键
                    continue
                    
                if key in self.api_keys:
                    existing_keys.append(key)
                    continue
                
                self.api_keys.append(key)
                added_keys.append(key)
                
                # 更新失败计数字典
                async with self.failure_count_lock:
                    self.key_failure_counts[key] = 0
            
            # 重新创建cycle迭代器
            self.key_cycle = cycle(self.api_keys)
            
        logger.info(f"Added {len(added_keys)} new API keys")
        return added_keys, existing_keys
    
    async def remove_keys(self, keys: Union[str, List[str]]) -> Tuple[List[str], List[str]]:
        """
        删除一个或多个API密钥
        
        Args:
            keys: 单个密钥字符串或密钥列表
            
        Returns:
            Tuple[List[str], List[str]]: 成功删除的密钥列表和不存在的密钥列表
        """
        if isinstance(keys, str):
            keys = [keys]
            
        removed_keys = []
        not_found_keys = []
        
        # 确保至少保留一个密钥
        if len(self.api_keys) <= len(keys):
            logger.warning("Cannot remove all API keys, at least one key must remain")
            return [], keys
        
        async with self.key_cycle_lock:
            # 重建key_cycle
            self.api_keys = list(self.api_keys)  # 确保是可变列表
            
            for key in keys:
                key = key.strip()
                if not key:  # 跳过空键
                    continue
                    
                if key not in self.api_keys:
                    not_found_keys.append(key)
                    continue
                
                self.api_keys.remove(key)
                removed_keys.append(key)
                
                # 更新失败计数字典
                async with self.failure_count_lock:
                    if key in self.key_failure_counts:
                        del self.key_failure_counts[key]
            
            # 重新创建cycle迭代器
            self.key_cycle = cycle(self.api_keys)
            
        logger.info(f"Removed {len(removed_keys)} API keys")
        return removed_keys, not_found_keys


_singleton_instance = None
_singleton_lock = asyncio.Lock()


async def get_key_manager_instance(api_keys: list = None) -> KeyManager:
    """
    获取 KeyManager 单例实例。

    如果尚未创建实例，将使用提供的 api_keys 初始化 KeyManager。
    如果已创建实例，则忽略 api_keys 参数，返回现有单例。
    """
    global _singleton_instance

    async with _singleton_lock:
        if _singleton_instance is None:
            if api_keys is None:
                raise ValueError("API keys are required to initialize the KeyManager")
            _singleton_instance = KeyManager(api_keys)
        return _singleton_instance
