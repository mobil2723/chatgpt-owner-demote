"""
# version: 2.2.1 - force rebuild
ChatGPT Business Owner 降级代理服务 (Zeabur 优化版)
内存优化：单并发 + 强制清理进程
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional
import base64
import json
import logging
import os
import time
import asyncio
import gc
import sys
import secrets

from DrissionPage import Chromium, ChromiumOptions

# 强制日志输出到控制台（Zeabur 能捕获）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)  # 关键：确保输出到 stdout
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="ChatGPT Owner 降级服务", version="2.2.0-Optimized")

# 登录配置（通过环境变量配置）
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SESSION_SECRET = os.getenv("SESSION_SECRET")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
REQUIRE_API_LOGIN = os.getenv("REQUIRE_API_LOGIN", "false").lower() == "true"


if not SESSION_SECRET:
    SESSION_SECRET = base64.urlsafe_b64encode(os.urandom(32)).decode()
    logger.warning("SESSION_SECRET 未配置，已生成临时密钥，重启后会失效。建议设置 SESSION_SECRET。")

# 关键：限制并发数为1，防止同时启动多个 Chrome 吃光内存
browser_semaphore = asyncio.Semaphore(1)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DemoteRequest(BaseModel):
    access_token: str
    account_id: Optional[str] = None
    role: str = "standard-user"


class DemoteResponse(BaseModel):
    success: bool
    message: str
    email: Optional[str] = None
    original_role: Optional[str] = None
    new_role: Optional[str] = None
    error: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    message: str


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def require_login_for_api(request: Request) -> None:
    if REQUIRE_API_LOGIN and not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


def decode_jwt_payload(token: str) -> dict:
    """解码 JWT Token 的 payload 部分"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")
        
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        raise ValueError(f"Failed to decode JWT: {str(e)}")


def extract_user_info(token: str, session_data: dict = None) -> dict:
    """从 Token 和 Session 数据中提取用户信息"""
    result = {"user_id": None, "account_id": None, "email": None}
    
    if session_data:
        if "user" in session_data:
            result["user_id"] = session_data["user"].get("id")
            result["email"] = session_data["user"].get("email")
        if "account" in session_data:
            result["account_id"] = session_data["account"].get("id")
        
        if result["user_id"] and result["account_id"]:
            logger.info(f"从 Session 提取: user_id={result['user_id']}, account_id={result['account_id']}")
            return result
    
    try:
        payload = decode_jwt_payload(token)
        auth_info = payload.get("https://api.openai.com/auth ", {})
        account_user_id = auth_info.get("chatgpt_account_user_id", "")
        
        if account_user_id:
            if "__" in account_user_id:
                parts = account_user_id.split("__")
                if not result["user_id"]:
                    result["user_id"] = parts[0]
                if not result["account_id"] and len(parts) > 1:
                    result["account_id"] = parts[1]
            else:
                if not result["user_id"]:
                    result["user_id"] = account_user_id
        
        profile = payload.get("https://api.openai.com/profile ", {})
        if not result["email"]:
            result["email"] = profile.get("email")
            
    except Exception as e:
        logger.warning(f"JWT decode error: {e}")
    
    return result


def create_browser():
    """创建 Chrome 实例（Zeabur 容器优化版）"""
    options = ChromiumOptions().auto_port()
    
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    options.set_user_agent(ua)
    
    headless = os.getenv('HEADLESS', 'true').lower() == 'true'
    if headless:
        options.set_argument('--headless=new')
    
    # 容器必需参数
    options.set_argument('--no-sandbox')
    options.set_argument('--disable-dev-shm-usage')
    options.set_argument('--disable-gpu')
    options.set_argument('--disable-software-rasterizer')
    options.set_argument('--disable-extensions')
    options.set_argument('--single-process')  # 单进程模式（省内存）
    options.set_argument('--no-zygote')  # 禁用 zygote 进程
    
    options.set_argument('--incognito')
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--lang=zh-CN')
    options.set_argument('--window-size=1920,1080')
    options.set_argument('--memory-model=low')  # 低内存模式
    
    options.set_pref('credentials_enable_service', False)
    options.set_pref('profile.password_manager_enabled', False)
    
    browser = Chromium(options)
    logger.info(f"Chrome 进程已创建，PID: {browser.process_id if hasattr(browser, 'process_id') else 'unknown'}")
    return browser


def inject_anti_detection(page):
    """注入反检测脚本"""
    script = '''
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    window.chrome = { runtime: {} };
    '''
    try:
        page.run_js(script)
    except Exception as e:
        logger.warning(f"注入反检测脚本失败: {e}")


def kill_browser_completely(browser):
    """彻底关闭浏览器并清理内存"""
    if not browser:
        return
    
    try:
        # 先关闭所有标签
        browser.close_tabs()
    except Exception as e:
        logger.debug(f"关闭标签页失败: {e}")
    
    try:
        # 退出浏览器
        browser.quit()
        logger.info("Browser.quit() 已调用")
    except Exception as e:
        logger.error(f"Browser.quit() 失败: {e}")
    
    # 强制垃圾回收
    gc.collect()
    logger.info("垃圾回收完成，内存已释放")


@app.post("/api/demote/owner", response_model=DemoteResponse)
async def demote_owner(request: DemoteRequest, http_request: Request):
    """
    降级接口（带并发控制）
    关键：使用信号量确保同时只有1个 Chrome 在运行
    """
    require_login_for_api(http_request)
    
    valid_roles = ["standard-user", "account-admin"]
    if request.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")
    
    logger.info("=" * 50)
    logger.info("收到降级请求，等待获取执行锁...")
    
    # 关键：限制并发，确保内存不会爆炸
    async with browser_semaphore:
        logger.info("获取到执行锁，开始处理...")
        
        access_token = request.access_token.strip()
        session_data = None
        
        # 解析 Session JSON
        if access_token.startswith('{'):
            try:
                session_data = json.loads(access_token)
                access_token = session_data.get("accessToken", "")
                if not access_token:
                    raise HTTPException(status_code=400, detail="accessToken not found in session data")
                logger.info("检测到完整 Session JSON")
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid JSON format")
        else:
            logger.warning("输入的是纯 Token，建议使用完整的 Session JSON")
        
        # 提取用户信息
        user_info = extract_user_info(access_token, session_data)
        user_id = user_info["user_id"]
        account_id = request.account_id or user_info["account_id"]
        email = user_info["email"]
        
        if not user_id:
            raise HTTPException(status_code=400, detail="无法提取 user_id，请提供完整 Session JSON")
        if not account_id:
            raise HTTPException(status_code=400, detail="无法提取 account_id，请提供完整 Session JSON")
        
        logger.info(f"开始降级: user={user_id}, account={account_id}, role={request.role}, email={email}")
        
        # 执行降级（同步函数转异步执行，避免阻塞）
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, 
            execute_demote_sync, 
            access_token, account_id, user_id, request.role
        )
        
        logger.info(f"降级结果: {result}")
        logger.info("=" * 50)
        
        if result.get("success"):
            role_display = "普通成员" if request.role == "standard-user" else "管理员"
            return DemoteResponse(
                success=True,
                message=f"成功降级为{role_display}",
                email=email,
                new_role=request.role
            )
        else:
            error_msg = result.get("error") or f"HTTP {result.get('status')}: {result.get('data')}"
            return DemoteResponse(
                success=False,
                message="降级失败",
                email=email,
                error=error_msg
            )


def execute_demote_sync(access_token: str, account_id: str, user_id: str, role: str) -> dict:
    """
    同步执行降级（在线程池中运行，避免阻塞主事件循环）
    """
    browser = None
    try:
        logger.info("[Sync] 创建 Chrome 实例...")
        browser = create_browser()
        page = browser.latest_tab
        
        logger.info("[Sync] 打开 chatgpt.com...")
        page.get('https://chatgpt.com ')
        inject_anti_detection(page)
        page.wait.doc_loaded()
        time.sleep(2)
        
        url = f"https://chatgpt.com/backend-api/accounts/ {account_id}/users/{user_id}"
        logger.info(f"[Sync] 目标 API: {url}")
        
        js_code = f'''
        window.__result = null;
        window.__done = false;
        
        (async function() {{
            try {{
                const resp = await fetch("{url}", {{
                    method: "PATCH",
                    headers: {{
                        "Authorization": "Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "*/*"
                    }},
                    body: JSON.stringify({{ "role": "{role}" }})
                }});
                const text = await resp.text();
                let data = text;
                try {{ data = JSON.parse(text); }} catch(e) {{}}
                window.__result = {{ status: resp.status, data: data }};
            }} catch (err) {{
                window.__result = {{ error: err.message }};
            }}
            window.__done = true;
        }})();
        '''
        
        page.run_js(js_code)
        
        # 等待完成
        for i in range(30):
            time.sleep(1)
            done = page.run_js('return window.__done;')
            if done:
                break
            logger.info(f"[Sync] 等待中... {i+1}s")
        
        result = page.run_js('return JSON.stringify(window.__result);')
        if result and result != 'null':
            data = json.loads(result)
            if data.get("error"):
                return {"success": False, "error": data.get("error")}
            elif data.get("status") == 200:
                return {"success": True, "data": data.get("data")}
            else:
                return {"success": False, "status": data.get("status"), "data": data.get("data")}
        else:
            return {"success": False, "error": "请求超时"}
            
    except Exception as e:
        logger.error(f"[Sync] 执行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}
    finally:
        logger.info("[Sync] 清理 Chrome 资源...")
        kill_browser_completely(browser)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "memory_optimized": True, "max_concurrent": 1}


@app.get("/login")
async def serve_login():
    return FileResponse("../frontend/login.html")


@app.post("/api/login", response_model=LoginResponse)
async def login(request_data: LoginRequest, request: Request):
    if not ADMIN_PASSWORD:
        return JSONResponse(status_code=500, content={"success": False, "message": "未配置 ADMIN_PASSWORD"})

    if (secrets.compare_digest(request_data.username, ADMIN_USERNAME)
            and secrets.compare_digest(request_data.password, ADMIN_PASSWORD)):
        request.session["logged_in"] = True
        request.session["username"] = ADMIN_USERNAME
        return {"success": True, "message": "登录成功"}

    return JSONResponse(status_code=401, content={"success": False, "message": "用户名或密码错误"})


@app.post("/api/logout", response_model=LoginResponse)
async def logout(request: Request):
    request.session.clear()
    return {"success": True, "message": "已退出登录"}


@app.get("/")
async def serve_frontend(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login")
    return FileResponse("../frontend/index.html")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """中间件：记录所有请求到日志（Zeabur 控制台可见）"""
    logger.info(f"请求: {request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"响应: {response.status_code}")
    return response


app.mount("/static", StaticFiles(directory="../frontend"), name="static")

# 挂载 backend/static 目录到 /images 路径
app.mount("/img", StaticFiles(directory="static"), name="img")


if __name__ == "__main__":
    import uvicorn
    import os
    
    # 先打印启动日志
    logger.info("=" * 50)
    logger.info("服务启动中... 版本: 2.2.0-Optimized")
    logger.info("内存优化: 单并发 + 强制清理")
    logger.info("=" * 50)
    
    # 读取环境变量（Zeabur 用 WEB_PORT 或 PORT）
    port = int(os.getenv("PORT", os.getenv("WEB_PORT", 8000)))
    logger.info(f"启动服务在端口: {port}")
    
    # 启动服务（单 worker，因为代码里已经用信号量控制了并发）
    uvicorn.run(app, host="0.0.0.0", port=port)
