"""Endpoints para comprobar que el backend y la base de datos funcionan."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    """El backend está levantado."""
    return {"status": "ok", "service": "prediktia-backend"}


@router.get("/db")
def health_db(db: Session = Depends(get_db)) -> dict[str, str]:
    """Ejecuta SELECT 1 en PostgreSQL para comprobar la conexión."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Error conectando a la base de datos: %s", exc.__class__.__name__)
        raise HTTPException(status_code=503, detail="No se pudo conectar a la base de datos") from exc
    return {"status": "ok", "database": "connected"}
