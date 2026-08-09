"""Zeroshift: turn a docker-compose file into deployable Zerops config."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app import diagram, storage
from app.emitters import (
    import_document,
    render_import_yaml,
    render_zerops_yaml,
    zerops_document,
)
from app.translate import TranslationError, translate
from app.validate import schemas_available, validate_import, validate_zerops_yaml

MAX_COMPOSE_BYTES = 128 * 1024
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Zeroshift", docs_url="/api/docs")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

EXAMPLE = """services:
  web:
    build: .
    ports: ["8000:8000"]
    depends_on: [db, cache]
    command: gunicorn app.wsgi --bind 0.0.0.0:8000
    environment:
      SECRET_KEY: dev-only
    volumes:
      - ./media:/app/media
  db:
    image: mysql:8
  cache:
    image: redis:7
  analytics:
    image: mongo:7
"""


@app.on_event("startup")
def startup() -> None:
    try:
        storage.init_schema()
    except Exception as exc:
        print(f"share links unavailable, using memory store: {exc}")


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"compose": EXAMPLE, "schemas": schemas_available()}
    )


def build_result(compose: str, project_name: str) -> dict:
    translation = translate(compose, project_name=project_name or "migrated")
    import_doc = import_document(translation)
    zerops_doc = zerops_document(translation)
    return {
        "translation": translation,
        "import_yaml": render_import_yaml(translation),
        "zerops_yaml": render_zerops_yaml(translation),
        "diagram": diagram.render(translation),
        "import_errors": validate_import(import_doc),
        "zerops_errors": validate_zerops_yaml(zerops_doc),
    }


@app.post("/migrate", response_class=HTMLResponse)
def migrate(request: Request, compose: str = Form(...), project_name: str = Form("migrated"),
            share: str = Form("")):
    if len(compose.encode("utf-8")) > MAX_COMPOSE_BYTES:
        raise HTTPException(413, "Compose file is too large.")
    try:
        result = build_result(compose, project_name)
    except TranslationError as exc:
        return templates.TemplateResponse(
            request, "index.html",
            {"compose": compose, "error": str(exc), "schemas": schemas_available()},
            status_code=400,
        )

    share_id = None
    if share:
        try:
            share_id = storage.save(compose, project_name)
        except Exception as exc:
            print(f"could not persist share link: {exc}")

    return templates.TemplateResponse(
        request, "result.html",
        {"compose": compose, "project_name": project_name, "share_id": share_id, **result},
    )


@app.get("/m/{share_id}", response_class=HTMLResponse)
def shared(request: Request, share_id: str):
    record = storage.load(share_id)
    if not record:
        raise HTTPException(404, "That share link does not exist or has expired.")
    result = build_result(record["compose"], record["project_name"])
    return templates.TemplateResponse(
        request, "result.html",
        {
            "compose": record["compose"],
            "project_name": record["project_name"],
            "share_id": share_id,
            **result,
        },
    )


@app.post("/api/translate")
def api_translate(payload: dict):
    compose = payload.get("compose", "")
    if not compose:
        raise HTTPException(400, "Provide a 'compose' field.")
    if len(compose.encode("utf-8")) > MAX_COMPOSE_BYTES:
        raise HTTPException(413, "Compose file is too large.")
    try:
        translation = translate(compose, project_name=payload.get("project_name", "migrated"))
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from exc

    return JSONResponse({
        "services": [
            {
                "hostname": s.hostname,
                "type": s.type,
                "kind": s.kind,
                "source_image": s.source_image,
                "public": s.public,
                "priority": s.priority,
                "ports": s.ports,
            }
            for s in translation.services
        ],
        "notes": [
            {"severity": n.severity, "service": n.service, "title": n.title, "detail": n.detail}
            for n in translation.all_notes
        ],
        "import_yaml": render_import_yaml(translation),
        "zerops_yaml": render_zerops_yaml(translation),
        "valid": not validate_import(import_document(translation))
        and not validate_zerops_yaml(zerops_document(translation)),
    })


DOWNLOADABLE = {
    "zerops.yaml": render_zerops_yaml,
    "zerops-project-import.yml": render_import_yaml,
}


@app.get("/m/{share_id}/download/{filename}", response_class=PlainTextResponse)
def download(share_id: str, filename: str):
    render = DOWNLOADABLE.get(filename)
    if not render:
        raise HTTPException(404, "No such file.")
    record = storage.load(share_id)
    if not record:
        raise HTTPException(404, "That share link does not exist or has expired.")
    translation = translate(record["compose"], project_name=record["project_name"])
    return PlainTextResponse(
        render(translation),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
