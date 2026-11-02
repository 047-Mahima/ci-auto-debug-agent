#creates the FastApi application, imports configs, github routes,
#register routes, sets up the db, &  exposes a health endpoint
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.config import settings
from app.db import Base, engine
from app.github import router as github_router
from app.reviews import router as reviews_router

app = FastAPI(title=settings.app_name)

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

REVIEW_PAGE = Path(__file__).parent / "static" / "review.html"


@app.get("/review", include_in_schema=False)
def review_page():
    return FileResponse(REVIEW_PAGE)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}

app.include_router(github_router)
app.include_router(reviews_router)
