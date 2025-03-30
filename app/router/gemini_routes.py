from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse, JSONResponse
from copy import deepcopy
import time
from pydantic import BaseModel
from app.config.config import settings
from app.log.logger import get_gemini_logger
from app.core.security import SecurityService
from app.domain.gemini_models import GeminiContent, GeminiRequest
from app.service.chat.gemini_chat_service import GeminiChatService
from app.service.key.key_manager import KeyManager, get_key_manager_instance
from app.service.model.model_service import ModelService
from app.handler.retry_handler import RetryHandler
from app.core.constants import API_VERSION
from typing import List, Dict, Any

# 定义请求模型
class KeysVerifyRequest(BaseModel):
    keys: List[str]

# 路由设置
router = APIRouter(prefix=f"/gemini/{API_VERSION}")
router_v1beta = APIRouter(prefix=f"/{API_VERSION}")
logger = get_gemini_logger()

# 初始化服务
security_service = SecurityService(settings.ALLOWED_TOKENS, settings.AUTH_TOKEN)
model_service = ModelService(settings.SEARCH_MODELS, settings.IMAGE_MODELS)


async def get_key_manager():
    """获取密钥管理器实例"""
    return await get_key_manager_instance()


async def get_next_working_key(key_manager: KeyManager = Depends(get_key_manager)):
    """获取下一个可用的API密钥"""
    return await key_manager.get_next_working_key()


@router.get("/models")
@router_v1beta.get("/models")
async def list_models(
    _=Depends(security_service.verify_key_or_goog_api_key),
    key_manager: KeyManager = Depends(get_key_manager)
):
    """获取可用的Gemini模型列表"""
    logger.info("-" * 50 + "list_gemini_models" + "-" * 50)
    logger.info("Handling Gemini models list request")
    
    api_key = await key_manager.get_next_working_key()
    logger.info(f"Using API key: {api_key}")
    
    models_json = model_service.get_gemini_models(api_key)
    model_mapping = {x.get("name", "").split("/", maxsplit=1)[1]: x for x in models_json["models"]}
    
    # 添加搜索模型
    if model_service.search_models:
        for name in model_service.search_models:
            model = model_mapping.get(name)
            if not model:
                continue
                
            item = deepcopy(model)
            item["name"] = f"models/{name}-search"
            display_name = f'{item.get("displayName")} For Search'
            item["displayName"] = display_name
            item["description"] = display_name
            
            models_json["models"].append(item)
    
    # 添加图像生成模型
    if model_service.image_models:
        for name in model_service.image_models:
            model = model_mapping.get(name)
            if not model:
                continue
                
            item = deepcopy(model)
            item["name"] = f"models/{name}-image"
            display_name = f'{item.get("displayName")} For Image'
            item["displayName"] = display_name
            item["description"] = display_name
            
            models_json["models"].append(item)
            
    return models_json


@router.post("/models/{model_name}:generateContent")
@router_v1beta.post("/models/{model_name}:generateContent")
@RetryHandler(max_retries=3, key_arg="api_key")
async def generate_content(
    model_name: str,
    request: GeminiRequest,
    _=Depends(security_service.verify_key_or_goog_api_key),
    api_key: str = Depends(get_next_working_key),
    key_manager: KeyManager = Depends(get_key_manager)
):
    """非流式生成内容"""
    logger.info("-" * 50 + "gemini_generate_content" + "-" * 50)
    logger.info(f"Handling Gemini content generation request for model: {model_name}")
    logger.info(f"Request: \n{request.model_dump_json(indent=2)}")
    logger.info(f"Using API key: {api_key}")
    
    if not model_service.check_model_support(model_name):
        raise HTTPException(status_code=400, detail=f"Model {model_name} is not supported")
    
    try:
        chat_service = GeminiChatService(settings.BASE_URL, key_manager)
        response = await chat_service.generate_content(
            model=model_name,
            request=request,
            api_key=api_key
        )
        return response
    except Exception as e:
        logger.error(f"Chat completion failed after retries: {str(e)}")
        raise HTTPException(status_code=500, detail="Chat completion failed") from e


@router.post("/models/{model_name}:streamGenerateContent")
@router_v1beta.post("/models/{model_name}:streamGenerateContent")
@RetryHandler(max_retries=3, key_arg="api_key")
async def stream_generate_content(
    model_name: str,
    request: GeminiRequest,
    _=Depends(security_service.verify_key_or_goog_api_key),
    api_key: str = Depends(get_next_working_key),
    key_manager: KeyManager = Depends(get_key_manager)
):
    """流式生成内容"""
    logger.info("-" * 50 + "gemini_stream_generate_content" + "-" * 50)
    logger.info(f"Handling Gemini streaming content generation for model: {model_name}")
    logger.info(f"Request: \n{request.model_dump_json(indent=2)}")
    logger.info(f"Using API key: {api_key}")
    
    if not model_service.check_model_support(model_name):
        raise HTTPException(status_code=400, detail=f"Model {model_name} is not supported")
    
    try:
        chat_service = GeminiChatService(settings.BASE_URL, key_manager)
        response_stream = chat_service.stream_generate_content(
            model=model_name,
            request=request,
            api_key=api_key
        )
        return StreamingResponse(response_stream, media_type="text/event-stream")
    except Exception as e:
        logger.error(f"Streaming request failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Streaming request failed") from e


@router.post("/verify-key/{api_key}")
async def verify_key(api_key: str):
    """验证Gemini API密钥的有效性"""
    logger.info("-" * 50 + "verify_gemini_key" + "-" * 50)
    logger.info("Verifying API key validity")
    
    try:
        key_manager = await get_key_manager()
        
        # 使用KeyManager的validate_key方法验证密钥
        is_valid = await key_manager.validate_key(api_key)
        
        if is_valid:
            return JSONResponse({"status": "valid"})
        return JSONResponse({"status": "invalid"})
    except Exception as e:
        logger.error(f"Key verification failed: {str(e)}")
        return JSONResponse({"status": "invalid", "error": str(e)})


@router.post("/verify-keys")
@router_v1beta.post("/verify-keys")
async def verify_keys(request: KeysVerifyRequest):
    """批量验证多个Gemini API密钥的有效性"""
    logger.info("-" * 50 + "verify_multiple_gemini_keys" + "-" * 50)
    keys = request.keys
    key_count = len(keys)
    logger.info(f"Verifying {key_count} API keys")
    
    # 记录开始时间
    start_time = time.time()
    
    try:
        key_manager = await get_key_manager()
        
        # 使用批量验证方法
        results = await key_manager.validate_keys(keys)
        
        # 转换结果格式
        formatted_results = {
            key: {"status": "valid" if is_valid else "invalid"} 
            for key, is_valid in results.items()
        }
        
        # 添加统计信息
        valid_count = sum(1 for is_valid in results.values() if is_valid)
        invalid_count = len(results) - valid_count
        
        # 记录执行时间
        elapsed_time = time.time() - start_time
        logger.info(
            f"Key validation completed in {elapsed_time:.2f} seconds. "
            f"Total: {key_count}, Valid: {valid_count}, Invalid: {invalid_count}"
        )
        
        return JSONResponse({
            "results": formatted_results,
            "summary": {
                "total": key_count,
                "valid": valid_count,
                "invalid": invalid_count,
                "elapsed_seconds": round(elapsed_time, 2)
            }
        })
    except Exception as e:
        # 记录错误执行时间
        elapsed_time = time.time() - start_time
        logger.error(f"Batch key verification failed in {elapsed_time:.2f} seconds: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": f"批量验证失败: {str(e)}"
        }, status_code=500)
