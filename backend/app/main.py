#creates the FastApi application, imports configs, github routes,
#register routes, sets up the db, &  exposes a health endpoint
from fastapi import FastAPI

from app.config import settings
from app.db import Base, engine
from app.github import router as github_router

app = FastAPI(title=settings.app_name)

@app.on_event("startup")
def on_startup():
    if str(engine.url).startswith("sqlite"):
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}

app.include_router(github_router)
