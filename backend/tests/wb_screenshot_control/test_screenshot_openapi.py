from typing import Any, get_args

from backend.app.factory import create_app
from backend.app.http.principals import require_admin, require_authenticated
from backend.commands.export_openapi import filter_by_tag
from backend.modules.wb_screenshot_control import domain
from backend.modules.wb_screenshot_control.presentation.http import schemas
from backend.modules.wb_screenshot_control.presentation.http.common import STATUS_BY_CODE
from backend.modules.wb_screenshot_control.presentation.http.extension import require_extension
from backend.shared.settings import load_settings
from backend.tests.wb_screenshot_control.routes import walk_routes

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
GUARDS = {require_authenticated, require_admin, require_extension}
OPEN_BY_DESIGN = {("POST", "/api/v1/wb/screenshot-control/extension/pair")}


def _calls(dependant: Any) -> set[Any]:
    found = {dependant.call} if dependant.call is not None else set()
    for child in dependant.dependencies:
        found |= _calls(child)
    return found


def test_the_spec_builds_and_every_module_operation_is_guarded() -> None:
    app = create_app(SETTINGS)
    spec = app.openapi()
    module_paths = [path for path in spec["paths"] if "screenshot-control" in path]
    assert len(module_paths) >= 20

    unguarded = []
    admin_without_admin_guard = []
    for route in walk_routes(app.routes):
        if "screenshot-control" not in route.path:
            continue
        calls = _calls(route.dependant)
        for method in route.methods:
            if (method, route.path) not in OPEN_BY_DESIGN and not calls & GUARDS:
                unguarded.append((method, route.path))
            if route.path.startswith("/api/v1/admin/") and require_admin not in calls:
                admin_without_admin_guard.append((method, route.path))
    assert unguarded == []
    assert admin_without_admin_guard == []


def test_the_filtered_spec_keeps_the_module_and_its_schemas_only() -> None:
    spec = filter_by_tag(create_app(SETTINGS).openapi(), "wb-screenshot-control")

    assert spec["paths"] and all("screenshot-control" in path for path in spec["paths"])
    names = set(spec["components"]["schemas"])
    assert {"SubmissionOut", "Summary", "AdminSubmissionDetail", "MatchOut", "PipelineOut", "ManagersOut"} <= names
    assert not any(name.startswith(("Return", "Seller")) for name in names)
    status = spec["components"]["schemas"]["SubmissionOut"]["properties"]["status"]
    assert set(status["enum"]) == set(get_args(schemas.Status))


def test_contract_vocabularies_match_the_domain() -> None:
    """Значения живут в двух местах: Literal для контракта и кортежи для ограничений таблиц."""
    assert get_args(schemas.Outcome) == domain.OUTCOMES
    assert get_args(schemas.OutcomeSource) == domain.OUTCOME_SOURCES
    assert get_args(schemas.Verdict) == domain.VERDICTS
    assert get_args(schemas.Channel) == domain.CHANNELS
    assert get_args(schemas.Stage) == domain.STAGES
    assert get_args(schemas.Mode) == domain.MODES
    assert get_args(schemas.Layout) == domain.LAYOUTS
    assert get_args(schemas.StorageState) == domain.STORAGE_STATES
    assert get_args(schemas.Status) == ("awaiting_outcome", "processing", *domain.VERDICTS)


def test_reason_dictionaries_are_closed_and_distinct() -> None:
    reasons = get_args(schemas.ReasonCode)
    assert len(reasons) == len(set(reasons))
    assert {"same_text_time_other_chat", "same_chat_similar_text", "messages_overlap", "order_conflict"} <= set(reasons)
    assert not any(":" in reason for reason in reasons)
    manager = set(get_args(schemas.ManagerReasonCode))
    assert manager.isdisjoint({"cross_user_duplicate", "possible_tampering", "near_image"})


def test_every_error_code_has_an_http_status() -> None:
    assert set(STATUS_BY_CODE) == set(get_args(schemas.ErrorCode))
