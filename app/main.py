"""Zeroshift: turn a docker-compose file into deployable Zerops config."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import diagram, storage
from app.emitters import (
    import_document,
    render_import_yaml,
    render_zerops_yaml,
    zerops_document,
)
from app.translate import TranslationError, translate
from app.validate import validate_import, validate_zerops_yaml

MAX_COMPOSE_BYTES = 128 * 1024

app = FastAPI(title="Zeroshift API", docs_url="/api/docs")


@app.on_event("startup")
def startup() -> None:
    try:
        storage.init_schema()
    except Exception as exc:
        print(f"share links unavailable, using memory store: {exc}")


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.post("/api/translate")
def api_translate(payload: dict):
    compose = payload.get("compose", "")
    if not compose:
        raise HTTPException(400, "Provide a 'compose' field.")
    if len(compose.encode("utf-8")) > MAX_COMPOSE_BYTES:
        raise HTTPException(413, "Compose file is too large.")
    project_name = payload.get("project_name", "migrated")
    try:
        translation = translate(compose, project_name=project_name)
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from exc

    share_id = None
    if payload.get("share"):
        try:
            share_id = storage.save(compose, project_name)
        except Exception as exc:
            print(f"could not persist share link: {exc}")

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
        "diagram": diagram.render(translation),
        "import_yaml": render_import_yaml(translation),
        "zerops_yaml": render_zerops_yaml(translation),
        "share_id": share_id,
        "valid": not validate_import(import_document(translation))
        and not validate_zerops_yaml(zerops_document(translation)),
    })


@app.get("/api/shared/{share_id}")
def api_shared(share_id: str):
    record = storage.load(share_id)
    if not record:
        raise HTTPException(404, "That share link does not exist or has expired.")
    translation = translate(record["compose"], project_name=record["project_name"])
    return JSONResponse({
        "compose": record["compose"],
        "project_name": record["project_name"],
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
        "diagram": diagram.render(translation),
        "import_yaml": render_import_yaml(translation),
        "zerops_yaml": render_zerops_yaml(translation),
        "share_id": share_id,
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


UI_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/m/{share_id}")
def shared_page(share_id: str):
    return FileResponse(UI_DIR / "index.html")


if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
