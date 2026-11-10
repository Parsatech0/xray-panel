"""Run the panel with uvicorn."""

import uvicorn

from app.config import settings


def main():
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PANEL_PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()
