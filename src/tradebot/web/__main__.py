"""Автономный запуск дашборда:  python -m tradebot.web  (только 127.0.0.1:8000)."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("tradebot.web.api:app", host="127.0.0.1", port=8000, log_level="info")
