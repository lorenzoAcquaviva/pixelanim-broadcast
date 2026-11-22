"""Punto di ingresso ASGI: uvicorn main:app"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from core import lifespan
from api import router

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="PixelAnim Broadcast", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)
