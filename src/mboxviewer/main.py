import uvicorn
from .config import load_settings
from .api import create_app


def main():
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
