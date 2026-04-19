"""Stub web routes — full implementation in Task 16."""
from fastapi import APIRouter

from coach.config import Settings


def build_router(settings: Settings, scheduler=None) -> APIRouter:
    return APIRouter()
