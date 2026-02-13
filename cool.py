from fastapi import FastAPI, Request, Path
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
import os
import asyncio
import json

import httpx

app = FastAPI(title="culo")

BOT_API = "http://127.0.0.1:49152"


# 🔁 --- Reverse proxy for bot API ---
@app.api_route("/api/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def proxy_bot_api(request: Request, path: str):
    """Forward /api/* requests to the bot's FastAPI server."""
    url = f"{BOT_API}/api/{path}"
    headers = dict(request.headers)
    headers.pop("host", None)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                params=request.query_params,
                content=await request.body(),
                timeout=30,
            )
        except httpx.ConnectError:
            return JSONResponse({"error": "Bot API unavailable"}, status_code=502)

    return StreamingResponse(
        iter([response.content]),
        status_code=response.status_code,
        headers={
            k: v for k, v in response.headers.items()
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
        },
        media_type=response.headers.get("content-type"),
    )


# 🌐 Simple health-check
@app.get("/ping")
async def ping():
    return {"status": "ok"}


# 💬 Telegram redirect
@app.get("/message-me")
async def redirect_to_telegram():
    return RedirectResponse(url="https://t.me/LettergramBot", status_code=301)


# 🎬 --- Вызов внешнего video_extractor.py ---
@app.get("/video/url={url:path}")
async def extract_video(url: str = Path(..., description="Video page URL")):
    try:
        process = await asyncio.create_subprocess_exec(
            "python", "video_extractor.py", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            return JSONResponse({
                "success": False,
                "error": f"Extractor failed: {stderr.decode().strip() or stdout.decode().strip()}"
            }, status_code=500)

        output = stdout.decode().strip()
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            result = {"success": True, "raw_output": output}

        return JSONResponse({
            "success": True,
            "source_url": url,
            "detector": "video_extractor.py",
            "data": result
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# 🎥 --- Вызов внешнего dtc.py ---
@app.get("/video2/url={url:path}")
async def detect_video(url: str = Path(..., description="Video page URL")):
    try:
        process = await asyncio.create_subprocess_exec(
            "python", "dtc.py", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            return JSONResponse({
                "success": False,
                "error": f"Detector failed: {stderr.decode().strip() or stdout.decode().strip()}"
            }, status_code=500)

        output = stdout.decode().strip()
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            result = {"success": True, "raw_output": output}

        return JSONResponse({
            "success": True,
            "source_url": url,
            "detector": "dtc.py",
            "data": result
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# 🗓 --- SUSU Schedule endpoint ---
@app.get("/schedule")
async def get_susu_schedule():
    try:
        process = await asyncio.create_subprocess_exec(
            "python", "sch.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            return JSONResponse({
                "success": False,
                "error": f"sch.py failed: {stderr.decode().strip() or stdout.decode().strip()}"
            }, status_code=500)

        output = stdout.decode().strip()
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            result = {"raw_output": output}

        return JSONResponse({
            "success": True,
            "detector": "sch.py",
            "data": result
        })

    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


# 📂 --- Раздача статических файлов ---
app.mount("/", StaticFiles(directory="/var/www/html", html=True), name="static")


# 🚧 --- Кастомный обработчик ошибок ---
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        not_found_path = "/var/www/html/404/index.html"
        if os.path.exists(not_found_path):
            return FileResponse(not_found_path, status_code=404)
    return RedirectResponse("/", status_code=302)
