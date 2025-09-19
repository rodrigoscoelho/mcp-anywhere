"""Routes for managing API tokens via the admin UI."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from mcp_anywhere.auth.api_tokens import APITokenService
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)
templates = Jinja2Templates(directory="src/mcp_anywhere/web/templates")


async def _require_authenticated(request: Request) -> bool:
    return bool(request.session.get("user_id"))


async def api_tokens_get(request: Request) -> Response:
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    service: APITokenService | None = getattr(request.app.state, "api_token_service", None)
    if service is None:
        logger.error("API token service unavailable; returning 500")
        return HTMLResponse("API token service unavailable", status_code=500)

    tokens = await service.list_tokens()

    # Retrieve any freshly created token stored in the session
    new_token_value = request.session.pop("new_api_token", None)
    new_token_name = request.session.pop("new_api_token_name", None)

    message = request.query_params.get("message")

    context = {
        "request": request,
        "tokens": tokens,
        "new_token_value": new_token_value,
        "new_token_name": new_token_name,
        "message": message,
    }
    return templates.TemplateResponse("settings/api_tokens.html", context)


async def api_tokens_post(request: Request) -> Response:
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    service: APITokenService | None = getattr(request.app.state, "api_token_service", None)
    if service is None:
        logger.error("API token service unavailable; returning 500")
        return HTMLResponse("API token service unavailable", status_code=500)

    form = await request.form()

    def _coerce_str(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    action = _coerce_str(form.get("action"))

    if action == "create":
        name = _coerce_str(form.get("name")) or "API Token"
        user_id_obj = request.session.get("user_id")
        if not isinstance(user_id_obj, int):
            logger.warning("API token creation attempted without numeric user id")
            return RedirectResponse(
                url="/settings/api-keys?message=invalid", status_code=302
            )

        issued = await service.issue_token(name=name, created_by=user_id_obj)
        request.session["new_api_token"] = issued.token
        request.session["new_api_token_name"] = issued.metadata.name
        logger.info("API token issued: %s (id=%s)", issued.metadata.name, issued.metadata.id)
        return RedirectResponse(url="/settings/api-keys?message=created", status_code=302)

    if action == "revoke":
        token_id_raw = _coerce_str(form.get("token_id"))
        try:
            token_id = int(token_id_raw)
        except (TypeError, ValueError):
            return RedirectResponse(
                url="/settings/api-keys?message=invalid", status_code=302
            )

        success = await service.revoke_token(token_id)
        logger.info("API token revoked: %s (success=%s)", token_id, success)
        message = "revoked" if success else "not-found"
        return RedirectResponse(url=f"/settings/api-keys?message={message}", status_code=302)

    return RedirectResponse(url="/settings/api-keys", status_code=302)


api_token_routes = [
    Route("/settings/api-keys", endpoint=api_tokens_get, methods=["GET"]),
    Route("/settings/api-keys", endpoint=api_tokens_post, methods=["POST"]),
]
