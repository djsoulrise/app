"""Pokretanje: python run.py"""
import uvicorn

from app.config import SETTINGS

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=SETTINGS["host"], port=int(SETTINGS["port"]), log_level="warning")
