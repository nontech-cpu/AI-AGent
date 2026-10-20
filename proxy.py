"""Local reverse proxy for PeculiarAI.

Public ngrok stays pointed at port 8000. This proxy sends application/API
routes to FastAPI on 8001 and all website/Streamlit routes (including its
WebSocket) to Streamlit on 8501.
"""
import asyncio
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

BACKEND = "http://127.0.0.1:8001"
STREAMLIT = "http://127.0.0.1:8501"
API_PREFIXES = (
    "/health", "/telegram/", "/web/", "/google/", "/docs", "/openapi.json", "/redoc"
)
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host"
}


def upstream_for(path: str) -> str:
    if path in ("/", "/health", "/docs", "/openapi.json", "/redoc"):
        return BACKEND
    if any(path.startswith(prefix) for prefix in API_PREFIXES):
        return BACKEND
    return STREAMLIT


async def proxy_http(request: web.Request) -> web.StreamResponse:
    upstream = upstream_for(request.path)
    target = f"{upstream}{request.rel_url}"
    session: ClientSession = request.app["client"]
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    body = await request.read() if request.can_read_body else None
    try:
        async with session.request(
            request.method, target, headers=headers, data=body,
            allow_redirects=False, timeout=ClientTimeout(total=120),
        ) as upstream_response:
            payload = await upstream_response.read()
            response_headers = {
                k: v for k, v in upstream_response.headers.items()
                if k.lower() not in HOP_BY_HOP and k.lower() not in {"content-length", "content-encoding"}
            }
            return web.Response(
                status=upstream_response.status,
                headers=response_headers,
                body=payload,
            )
    except Exception as exc:
        return web.json_response(
            {"detail": "Upstream service unavailable", "upstream": upstream, "error": str(exc)},
            status=502,
        )


async def proxy_websocket(request: web.Request) -> web.WebSocketResponse:
    # Streamlit's browser WebSocket is always routed to Streamlit.
    target = f"{STREAMLIT}{request.rel_url}"
    # Streamlit offers subprotocols such as "streamlit" and a session token.
    # Accept the offered protocols on the downstream handshake, and forward
    # them upstream so the browser WebSocket can complete successfully.
    offered_protocols = tuple(
        item.strip()
        for item in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if item.strip()
    )
    # Negotiate Streamlit's real subprotocol with the browser. The browser also
    # offers a per-session token, which is still forwarded upstream below.
    client_ws = web.WebSocketResponse(heartbeat=30, protocols=("streamlit",))
    await client_ws.prepare(request)
    session: ClientSession = request.app["client"]
    try:
        # Streamlit's upstream server does not negotiate the browser's custom
        # subprotocol list. Do not forward Sec-WebSocket-Protocol upstream;
        # the proxy handles browser-side negotiation separately.
        async with session.ws_connect(
            target, heartbeat=30, max_msg_size=0
        ) as upstream_ws:
            async def client_to_upstream():
                async for msg in client_ws:
                    if msg.type == WSMsgType.TEXT:
                        await upstream_ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await upstream_ws.send_bytes(msg.data)
                    elif msg.type == WSMsgType.CLOSE:
                        await upstream_ws.close()
                        break
                    elif msg.type == WSMsgType.ERROR:
                        break

            async def upstream_to_client():
                async for msg in upstream_ws:
                    if msg.type == WSMsgType.TEXT:
                        await client_ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await client_ws.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break

            tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:
        # Log failures instead of silently leaving the browser on a loading screen.
        print(f"WebSocket proxy error for {request.rel_url}: {type(exc).__name__}: {exc}")
    finally:
        await client_ws.close()
    return client_ws


async def startup(app: web.Application):
    app["client"] = ClientSession(timeout=ClientTimeout(total=120))


async def cleanup(app: web.Application):
    await app["client"].close()


app = web.Application(client_max_size=25 * 1024 * 1024)
app.on_startup.append(startup)
app.on_cleanup.append(cleanup)
app.router.add_route("GET", "/{tail:.*}", lambda request: proxy_websocket(request) if request.headers.get("Upgrade", "").lower() == "websocket" else proxy_http(request))
app.router.add_route("POST", "/{tail:.*}", proxy_http)
app.router.add_route("PUT", "/{tail:.*}", proxy_http)
app.router.add_route("PATCH", "/{tail:.*}", proxy_http)
app.router.add_route("DELETE", "/{tail:.*}", proxy_http)
app.router.add_route("OPTIONS", "/{tail:.*}", proxy_http)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8000, access_log=None)
