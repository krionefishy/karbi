from backend.modules.wb_screenshot_control.presentation.http.admin import router as admin_router
from backend.modules.wb_screenshot_control.presentation.http.extension import router as extension_router
from backend.modules.wb_screenshot_control.presentation.http.submissions import router

__all__ = ["admin_router", "extension_router", "router"]
