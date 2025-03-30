import asyncio
from itertools import cycle
from typing import Dict, List, Union, Tuple
import httpx

from app.config.config import settings
from app.log.logger import get_key_manager_logger
from app.service.client.api_client import GeminiApiClient

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
        self.api_client = GeminiApiClient(settings.BASE_URL)
        self.test_model = settings.TEST_MODEL
        # 设置并行验证的阈值，当密钥数量超过此值时使用并行验证
        self.PARALLEL_VALIDATION_THRESHOLD = 2
        # 设置最大并行验证任务数
        self.MAX_PARALLEL_TASKS = 20
        # 验证超时时间（秒）
        self.VALIDATION_TIMEOUT = 5
        # 验证并发控制信号量
        self.validation_semaphore = asyncio.Semaphore(self.MAX_PARALLEL_TASKS)

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
    
    async def validate_key(self, key: str) -> bool:
        """
        验证API密钥是否有效 - 优化版
        
        Args:
            key: 要验证的API密钥
            
        Returns:
            bool: 密钥是否有效
        """
        try:
            # 使用更轻量的请求来测试API密钥，仅请求1个token输出
            # 使用最快的模型gemini-1.5-flash来测试
            # 设置较短的超时时间
            timeout = httpx.Timeout(self.VALIDATION_TIMEOUT, connect=self.VALIDATION_TIMEOUT)
            
            # 直接使用httpx发出请求，绕过GeminiApiClient以提高速度
            model = "gemini-1.5-flash"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
                "generationConfig": {"maxOutputTokens": 1}  # 只请求一个token
            }
            
            url = f"{settings.BASE_URL}/models/{model}:generateContent?key={key}"
            
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
                
                # 只需检查响应状态码，无需解析响应内容
                if response.status_code == 200:
                    logger.info(f"API key validation successful: {key[:5]}...")
                    return True
                else:
                    logger.warning(f"API key validation failed with status {response.status_code}: {key[:5]}...")
                    return False
        except Exception as e:
            logger.warning(f"API key validation failed: {key[:5]}... - {str(e)}")
            return False
    
    async def _validate_key_with_semaphore(self, key: str) -> Tuple[str, bool]:
        """
        使用信号量控制的密钥验证方法
        
        Args:
            key: 要验证的API密钥
            
        Returns:
            Tuple[str, bool]: 密钥和验证结果
        """
        async with self.validation_semaphore:
            is_valid = await self.validate_key(key)
            return key, is_valid
    
    async def validate_keys(self, keys: List[str]) -> Dict[str, bool]:
        """
        批量验证多个API密钥 - 优化版
        
        Args:
            keys: 要验证的API密钥列表
            
        Returns:
            Dict[str, bool]: 密钥验证结果字典，键为密钥，值为验证结果
        """
        if not keys:
            return {}
        
        # 如果密钥数量少，使用顺序验证
        if len(keys) <= self.PARALLEL_VALIDATION_THRESHOLD:
            result = {}
            for key in keys:
                result[key] = await self.validate_key(key)
            return result
        
        # 使用信号量控制的并行验证
        logger.info(f"Using parallel validation for {len(keys)} keys with semaphore control")
        
        # 创建所有验证任务
        tasks = [self._validate_key_with_semaphore(key) for key in keys]
        
        # 使用gather并发执行所有任务
        results_list = await asyncio.gather(*tasks)
        
        # 构建结果字典
        return {key: is_valid for key, is_valid in results_list}
    
    async def add_keys(self, keys: Union[str, List[str]]) -> Tuple[List[str], List[str], List[str]]:
        """
        添加一个或多个API密钥，验证有效性后再添加
        
        Args:
            keys: 单个密钥字符串或密钥列表
            
        Returns:
            Tuple[List[str], List[str], List[str]]: 成功添加的密钥列表、无效的密钥列表和已存在的密钥列表
        """
        if isinstance(keys, str):
            keys = [keys]
            
        added_keys = []
        invalid_keys = []
        existing_keys = []
        keys_to_validate = []
        
        # 先过滤掉空密钥和已存在的密钥
        for key in keys:
            key = key.strip()
            if not key:  # 跳过空键
                continue
                
            # 检查密钥是否已存在
            if key in self.api_keys:
                existing_keys.append(key)
                continue
                
            keys_to_validate.append(key)
        
        # 如果没有需要验证的密钥，直接返回
        if not keys_to_validate:
            logger.info(f"No new keys to validate, {len(existing_keys)} keys already exist")
            return added_keys, invalid_keys, existing_keys
        
        # 批量验证密钥
        logger.info(f"Validating {len(keys_to_validate)} keys")
        validation_results = await self.validate_keys(keys_to_validate)
        
        # 根据验证结果分类并添加有效密钥
        async with self.key_cycle_lock:
            # 重建key_cycle，只需执行一次
            self.api_keys = list(self.api_keys)  # 确保是可变列表
            
            for key, is_valid in validation_results.items():
                if is_valid:
                    # 添加有效密钥
                    self.api_keys.append(key)
                    
                    # 更新失败计数字典
                    async with self.failure_count_lock:
                        self.key_failure_counts[key] = 0
                    
                    added_keys.append(key)
                    logger.info(f"Added valid API key: {key[:5]}...")
                else:
                    invalid_keys.append(key)
                    logger.warning(f"Skipped invalid API key: {key[:5]}...")
            
            # 如果有有效密钥被添加，重新创建cycle迭代器
            if added_keys:
                self.key_cycle = cycle(self.api_keys)
        
        logger.info(f"Added {len(added_keys)} new API keys, {len(invalid_keys)} invalid keys, {len(existing_keys)} existing keys")
        return added_keys, invalid_keys, existing_keys
    
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
