import uvicorn

from .config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("manual_generator.main:app", host=settings.host, port=settings.port, reload=False)
