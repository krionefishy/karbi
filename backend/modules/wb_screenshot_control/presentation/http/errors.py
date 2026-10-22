from typing import Any

from fastapi import HTTPException

from backend.modules.wb_screenshot_control.presentation.http.schemas import ErrorCode, ErrorResponse

ERROR_HEADER = "X-Error-Code"


def api_error(status_code: int, code: ErrorCode, detail: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code, detail, headers={ERROR_HEADER: code, **(headers or {})})


def documented(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Ошибки маршрута для OpenAPI: тело одно на всех, код — в заголовке."""
    header = {ERROR_HEADER: {"description": "Машинный код ошибки (ErrorCode)", "schema": {"type": "string"}}}
    return {code: {"model": ErrorResponse, "headers": header} for code in status_codes}


def not_found() -> HTTPException:
    return api_error(404, "not_found", "Скриншот не найден")
