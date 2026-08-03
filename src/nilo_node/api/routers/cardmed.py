"""Cardmed-Dev registration, uploads, and status API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from nilo_node.api.deps import require_auth
from nilo_node.cardmed.models import CardmedRegisterRequest
from nilo_node.cardmed.service import CardmedService
from nilo_node.config.models import AppConfig


class RegisterResponse(BaseModel):
    device_id: str
    node_id: str
    registered_at: datetime


def create_cardmed_router(config: AppConfig, cardmed: CardmedService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/cardmed", tags=["cardmed"])
    auth = Depends(require_auth(config))

    @router.post("/register", dependencies=[auth])
    async def register_device(body: CardmedRegisterRequest) -> RegisterResponse:
        assignment = cardmed.register(body)
        return RegisterResponse(
            device_id=assignment.device_id,
            node_id=assignment.node_id,
            registered_at=assignment.registered_at,
        )

    @router.delete("/register", dependencies=[auth])
    async def unregister_device(device_id: str) -> dict[str, Any]:
        if not cardmed.unregister(device_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Device not registered: {device_id}",
            )
        return {"device_id": device_id, "unregistered": True}

    @router.get("/status", dependencies=[auth])
    async def cardmed_status() -> dict[str, Any]:
        return cardmed.get_status().model_dump(mode="json")

    @router.post("/photos", dependencies=[auth])
    async def upload_photo(
        device_id: str = Form(...),
        capture_ts: str = Form(...),
        file: UploadFile = File(...),
        reading_id: str | None = Form(None),
        metadata: str | None = Form(None),
    ) -> dict[str, Any]:
        try:
            parsed_ts = datetime.fromisoformat(capture_ts.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid capture_ts: {capture_ts}",
            ) from exc

        mime = file.content_type or "application/octet-stream"
        if mime not in config.cardmed.allowed_mime_types:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported content type: {mime}",
            )

        data = await file.read()
        meta: dict[str, Any] | None = None
        if metadata:
            try:
                meta = json.loads(metadata)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="metadata must be valid JSON",
                ) from exc

        try:
            result = await cardmed.ingest_photo(
                device_id=device_id,
                data=data,
                mime_type=mime,
                capture_ts=parsed_ts,
                reading_id=reading_id,
                metadata=meta,
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        return result.model_dump(mode="json")

    return router
