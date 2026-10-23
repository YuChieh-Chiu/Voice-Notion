"""在 JSON／multipart 解析前限制實際 body，含沒有 Content-Length 的串流。"""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    return await JSONResponse(
                        status_code=400, content={"detail": "Invalid content length"}
                    )(scope, receive, send)
                if length < 0 or length > self.max_bytes:
                    return await JSONResponse(
                        status_code=413, content={"detail": "Request body too large"}
                    )(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                raise HTTPException(status_code=413, detail="Request body too large")
            return message

        return await self.app(scope, bounded_receive, send)
