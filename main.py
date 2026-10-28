# uvicorn main:app --reload
# uvicorn main:app --reload --port 8008
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from globals import GlobalConfig
from routes import (module_router)

app = FastAPI()

"""Получаем токен ламоды при старте приложения"""
from module.fbs.get_lamoda_acces_token import get_access_token

@app.on_event("startup")
async def start_background_tasks():
    await get_access_token()
app.include_router(module_router)

@app.get("/")
def read_root():
    return GlobalConfig.status_string
