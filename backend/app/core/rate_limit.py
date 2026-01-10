import time
import redis
from fastapi import Request, HTTPException
from app.config import get_settings

settings = get_settings()

# Initialize Redis client (using same URL as Celery)
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

class RateLimiter:
    """
    Redis 滑動視窗限流器
    """
    def __init__(self, times: int = 3, hours: int = 1):
        self.times = times
        self.window = hours * 3600

    async def __call__(self, request: Request):
        # 如果是 Admin 請求，跳過限流 (由 get_user_context 判斷)
        # 這裡我們僅針對沒有正確 Admin Key 的請求進行 IP 限流。
        
        api_key = request.headers.get("X-API-Key")
        if api_key == settings.SIRI_API_KEY and api_key != "":
            return # Admin skip

        # 支援反向代理 (Nginx/Cloudflare) 取得真實 IP
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        else:
            client_ip = request.client.host
            
        key = f"rate_limit:{client_ip}"
        now = time.time()
        
        # 使用 Redis 事務 (Pipeline) 確保原子性
        pipe = redis_client.pipeline()
        # 移除視窗外的舊記錄
        pipe.zremrangebyscore(key, 0, now - self.window)
        # 計算現存記錄數
        pipe.zcard(key)
        # 加入新記錄
        pipe.zadd(key, {str(now): now})
        # 設定過期時間 (稍微大於視窗)
        pipe.expire(key, self.window + 60)
        
        results = pipe.execute()
        current_count = results[1]

        if current_count >= self.times:
            raise HTTPException(
                status_code=429, 
                detail=f"Too many requests. Limit is {self.times} per {self.window // 3600} hour(s)."
            )
        
        return True
