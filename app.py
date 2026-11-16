"""Web entry point for the single-page property search application."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from rentfinder.config import ConfigError, Settings
from rentfinder.report import build_payload
from rentfinder.search import BrowserFetcher, run_search

ROOT = Path(__file__).parent
app = FastAPI(title="Riyadh Property Search", docs_url=None, redoc_url=None)


class SearchRequest(BaseModel):
    max_price: int = Field(default=40000, ge=1, le=10_000_000)
    min_price: int = Field(default=0, ge=0, le=10_000_000)
    types: str = "الكل"
    period: str = "الكل"
    sources: list[str] = ["الكل"]
    districts: str = ""
    pages: int = Field(default=1, ge=1, le=25)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "rentfinder" / "templates" / "report.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search")
async def search(request: SearchRequest) -> dict:
    if request.min_price > request.max_price:
        raise HTTPException(status_code=400, detail="الحد الأدنى للسعر أكبر من الحد الأقصى")
    env = {
        "INPUT_MAX_PRICE": str(request.max_price),
        "INPUT_MIN_PRICE": str(request.min_price),
        "INPUT_TYPES": request.types,
        "INPUT_PERIOD": request.period,
        "INPUT_SOURCES": ",".join(request.sources),
        "INPUT_DISTRICTS": request.districts,
        "INPUT_PAGES": str(request.pages),
    }
    try:
        settings = Settings.from_env(env)
        result = await asyncio.to_thread(_run_search, settings)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - return a useful API error
        raise HTTPException(status_code=502, detail=f"تعذر تنفيذ البحث: {exc}") from exc
    return build_payload(result, settings)


def _run_search(settings: Settings):
    with BrowserFetcher() as fetcher:
        return run_search(settings, fetcher, max_runtime_seconds=45)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000)