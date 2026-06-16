"""
core/exceptions.py

Custom exception classes and handlers for Lumio.
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class LumioException(Exception):
    """
    Custom exception for all Lumio backend errors.
    Raised anywhere in the app with a status code and detail message.
    """
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail      = detail
        super().__init__(detail)


async def lumio_exception_handler(
    request: Request,
    exc:     LumioException,
) -> JSONResponse:
    """Handles all LumioException instances."""
    return JSONResponse(
        status_code = exc.status_code,
        content     = {
            "error":   True,
            "detail":  exc.detail,
            "path":    str(request.url),
        }
    )


async def generic_exception_handler(
    request: Request,
    exc:     Exception,
) -> JSONResponse:
    """
    Catches all unhandled exceptions.
    Never exposes raw error to frontend.
    """
    return JSONResponse(
        status_code = 500,
        content     = {
            "error":  True,
            "detail": "An unexpected error occurred. Please try again.",
            "path":   str(request.url),
        }
    )