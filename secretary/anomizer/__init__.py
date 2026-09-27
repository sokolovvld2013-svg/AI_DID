"""Anomizer module for document anonymization."""
from .src.anomizer import router as anomizer_router

__all__ = ["anomizer_router"]