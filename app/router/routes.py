"""
路由配置模块，负责设置和配置应用程序的路由
"""

from fastapi import FastAPI, Request, HTTPException, Body, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import List, Union, Any

from app.core.security import verify_auth_token
from app.log.logger import get_routes_logger
from app.router import gemini_routes, openai_routes
from app.service.key.key_manager import get_key_manager_instance

logger = get_routes_logger()

# 配置Jinja2模板
templates = Jinja2Templates(directory="app/templates")


def setup_routers(app: FastAPI) -> None:
    """
    设置应用程序的路由

    Args:
        app: FastAPI应用程序实例
    """
    # 包含API路由
    app.include_router(openai_routes.router)
    app.include_router(gemini_routes.router)
    app.include_router(gemini_routes.router_v1beta)

    # 添加页面路由
    setup_page_routes(app)

    # 添加API密钥管理路由
    setup_key_management_routes(app)

    # 添加健康检查路由
    setup_health_routes(app)


def verify_token(request: Request):
    """
    验证请求中的认证令牌
    """
    auth_token = request.cookies.get("auth_token")
    if not auth_token or not verify_auth_token(auth_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return auth_token


def setup_page_routes(app: FastAPI) -> None:
    """
    设置页面相关的路由

    Args:
        app: FastAPI应用程序实例
    """

    @app.get("/", response_class=HTMLResponse)
    async def auth_page(request: Request):
        """认证页面"""
        return templates.TemplateResponse("auth.html", {"request": request})

    @app.post("/auth")
    async def authenticate(request: Request):
        """处理认证请求"""
        try:
            form = await request.form()
            auth_token = form.get("auth_token")
            if not auth_token:
                logger.warning("Authentication attempt with empty token")
                return RedirectResponse(url="/", status_code=302)

            if verify_auth_token(auth_token):
                logger.info("Successful authentication")
                response = RedirectResponse(url="/keys", status_code=302)
                response.set_cookie(
                    key="auth_token", value=auth_token, httponly=True, max_age=3600
                )
                return response
            logger.warning("Failed authentication attempt with invalid token")
            return RedirectResponse(url="/", status_code=302)
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return RedirectResponse(url="/", status_code=302)

    @app.get("/keys", response_class=HTMLResponse)
    async def keys_page(request: Request):
        """密钥管理页面"""
        try:
            auth_token = request.cookies.get("auth_token")
            if not auth_token or not verify_auth_token(auth_token):
                logger.warning("Unauthorized access attempt to keys page")
                return RedirectResponse(url="/", status_code=302)

            key_manager = await get_key_manager_instance()
            keys_status = await key_manager.get_keys_by_status()
            total = len(keys_status["valid_keys"]) + len(keys_status["invalid_keys"])
            logger.info(f"Keys status retrieved successfully. Total keys: {total}")
            return templates.TemplateResponse(
                "keys_status.html",
                {
                    "request": request,
                    "valid_keys": keys_status["valid_keys"],
                    "invalid_keys": keys_status["invalid_keys"],
                    "total": total,
                },
            )
        except Exception as e:
            logger.error(f"Error retrieving keys status: {str(e)}")
            raise


def setup_key_management_routes(app: FastAPI) -> None:
    """
    设置API密钥管理相关的路由
    
    Args:
        app: FastAPI应用程序实例
    """
    
    def normalize_keys_input(keys_input: Any) -> List[str]:
        """
        处理各种格式的密钥输入，转换为标准的字符串列表
        
        Args:
            keys_input: 各种类型的输入（字符串、列表等）
            
        Returns:
            List[str]: 标准化后的密钥列表
        """
        # 如果已经是列表，确保列表中的每个元素都是字符串
        if isinstance(keys_input, list):
            return [str(key).strip() for key in keys_input if key and str(key).strip()]
        
        # 如果是字符串，尝试按逗号或换行符切分
        if isinstance(keys_input, str):
            keys_str = keys_input.strip()
            if ',' in keys_str:
                return [key.strip() for key in keys_str.split(',') if key.strip()]
            else:
                return [key.strip() for key in keys_str.split('\n') if key.strip()]
        
        # 其他类型，尝试转换为字符串后处理
        try:
            return normalize_keys_input(str(keys_input))
        except:
            return []
    
    @app.post("/api/keys/add")
    async def add_keys(
        keys: Any = Body(..., description="要添加的API密钥，支持字符串、数组或逗号分隔的列表"),
        _=Depends(verify_token)
    ):
        """添加一个或多个API密钥"""
        try:
            # 标准化输入
            keys_list = normalize_keys_input(keys)
            
            if not keys_list:
                return JSONResponse({
                    "status": "error",
                    "message": "未提供有效的密钥"
                }, status_code=400)
            
            logger.info(f"Processing request to add {len(keys_list)} keys")
            import time
            start_time = time.time()
            
            key_manager = await get_key_manager_instance()
            added_keys, invalid_keys, existing_keys = await key_manager.add_keys(keys_list)
            
            elapsed_time = time.time() - start_time
            logger.info(
                f"Key validation completed in {elapsed_time:.2f} seconds. "
                f"Added: {len(added_keys)}, Invalid: {len(invalid_keys)}, Existing: {len(existing_keys)}"
            )
            
            return JSONResponse({
                "status": "success",
                "message": f"成功添加 {len(added_keys)} 个密钥, {len(invalid_keys)} 个无效, {len(existing_keys)} 个已存在",
                "added_keys": added_keys,
                "invalid_keys": invalid_keys,
                "existing_keys": existing_keys
            })
        except Exception as e:
            logger.error(f"Error adding keys: {str(e)}")
            return JSONResponse({
                "status": "error",
                "message": f"添加密钥失败: {str(e)}"
            }, status_code=500)
    
    @app.post("/api/keys/remove")
    async def remove_keys(
        keys: Any = Body(..., description="要删除的API密钥，支持字符串、数组或逗号分隔的列表"),
        _=Depends(verify_token)
    ):
        """删除一个或多个API密钥"""
        try:
            # 标准化输入
            keys_list = normalize_keys_input(keys)
            
            if not keys_list:
                return JSONResponse({
                    "status": "error",
                    "message": "未提供有效的密钥"
                }, status_code=400)
            
            key_manager = await get_key_manager_instance()
            removed_keys, not_found_keys = await key_manager.remove_keys(keys_list)
            
            return JSONResponse({
                "status": "success",
                "message": f"成功删除 {len(removed_keys)} 个密钥, {len(not_found_keys)} 个未找到",
                "removed_keys": removed_keys,
                "not_found_keys": not_found_keys
            })
        except Exception as e:
            logger.error(f"Error removing keys: {str(e)}")
            return JSONResponse({
                "status": "error",
                "message": f"删除密钥失败: {str(e)}"
            }, status_code=500)
    
    @app.post("/api/keys/reset-status")
    async def reset_key_status(
        keys: Any = Body(..., description="要重置状态的API密钥，支持字符串、数组或逗号分隔的列表"),
        _=Depends(verify_token)
    ):
        """重置一个或多个API密钥的失败计数，使其变为有效状态"""
        try:
            # 标准化输入
            keys_list = normalize_keys_input(keys)
            
            if not keys_list:
                return JSONResponse({
                    "status": "error",
                    "message": "未提供有效的密钥"
                }, status_code=400)
            
            key_manager = await get_key_manager_instance()
            
            # 使用KeyManager的reset_key_status方法
            reset_keys = await key_manager.reset_key_status(keys_list)
            
            logger.info(f"重置了 {len(reset_keys)} 个密钥的状态")
            
            return JSONResponse({
                "status": "success",
                "message": f"成功重置 {len(reset_keys)} 个密钥的状态",
                "reset_keys": reset_keys
            })
        except Exception as e:
            logger.error(f"Error resetting key status: {str(e)}")
            return JSONResponse({
                "status": "error",
                "message": f"重置密钥状态失败: {str(e)}"
            }, status_code=500)


def setup_health_routes(app: FastAPI) -> None:
    """
    设置健康检查相关的路由

    Args:
        app: FastAPI应用程序实例
    """

    @app.get("/health")
    async def health_check(request: Request):
        """健康检查端点"""
        logger.info("Health check endpoint called")
        return {"status": "healthy"}
