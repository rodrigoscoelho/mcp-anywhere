"""Rotas para configuraAAo LLM (provider/model e OpenRouter API key).

Responsabilidades:
- GET /settings/llm: exibe valores efetivos (DB > ENV) e formulArio para alterar provider/model e
  opcionalmente salvar a OpenRouter API key (criptografada).
- POST /settings/llm: valida e persiste configuraAAes em DB via settings_store.
- ProteAAo: redireciona para /auth/login se usuArio nAo autenticado (mesma abordagem usada em outras rotas).
"""

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from mcp_anywhere.logging_config import get_logger
from mcp_anywhere.settings_store import (
    get_app_setting,
    get_effective_setting,
    set_app_setting,
)

templates = Jinja2Templates(directory="src/mcp_anywhere/web/templates")
logger = get_logger(__name__)


async def _require_authenticated(request: Request):
    """Retorna True se usuArio autenticado, False caso contrArio."""
    user_id = request.session.get("user_id")
    return bool(user_id)


def _coerce_form_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


async def settings_llm_get(request: Request) -> Response:
    """Renderiza a pAgina de configuraAAes LLM.

    Contextos passados para o template:
    - provider_atual, model_atual : valores efetivos (get_effective_setting)
    - provider_persistido, model_persistido: valores salvos no DB (get_app_setting) para preencher form
    - has_openrouter_key_persisted: booleano se existe chave salva no DB (nAo exibir o valor)
    - message: opcional (ex.: ?saved=1)
    """
    # AutenticaAAo: mesma estratAgia usada em change_password e outras rotas protegidas
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        logger.info(f"User not authenticated, redirecting to login: {login_url}")
        return RedirectResponse(url=login_url, status_code=302)

    # Valores efetivos
    provider_atual = await get_effective_setting("llm.provider")
    model_atual = await get_effective_setting("llm.model")

    # Valores persistidos (raw) para preencher o formulArio se existirem
    provider_row = await get_app_setting("llm.provider")
    model_row = await get_app_setting("llm.model")

    provider_persistido = provider_row[0] if provider_row else None
    model_persistido = model_row[0] if model_row else None

    # Indica se hA chave OpenRouter salva (nAo exibimos o conteAodo)
    key_row = await get_app_setting("llm.openrouter_api_key")
    has_openrouter_key_persisted = bool(key_row and key_row[0])

    # Mensagem simples via query param (padrAo existente no projeto)
    saved = request.query_params.get("saved")
    message = "ConfiguraAAes salvas com sucesso." if saved else None

    return templates.TemplateResponse(
        "settings/llm.html",
        {
            "request": request,
            "provider_atual": provider_atual,
            "model_atual": model_atual,
            "provider_persistido": provider_persistido,
            "model_persistido": model_persistido,
            "has_openrouter_key_persisted": has_openrouter_key_persisted,
            "message": message,
        },
    )


async def settings_llm_post(request: Request) -> Response:
    """Processa submissAo do formulArio e persiste configuraAAes no DB.

    Regras:
    - provider obrigatA3rio: "anthropic" ou "openrouter"
    - model obrigatA3rio e deve corresponder ao provider:
        - anthropic => "claude-sonnet-4-20250514"
        - openrouter => "openai/gpt-5"
    - openrouter_api_key opcional: se fornecida (nAo vazia) -> salvar com encrypt=True
      se vazia -> NAO sobrepor o valor existente (nAo chamar set_app_setting para a chave)
    - ApA3s salvar: redirecionar para GET /settings/llm?saved=1
    """
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    form = await request.form()
    provider = _coerce_form_str(form.get("provider"))
    model = _coerce_form_str(form.get("model"))
    openrouter_api_key = _coerce_form_str(form.get("openrouter_api_key"))

    # ValidaAAes
    if provider not in {"anthropic", "openrouter"}:
        return templates.TemplateResponse(
            "settings/llm.html",
            {
                "request": request,
                "message": "Provider invAlido. Valor permitido: 'anthropic' ou 'openrouter'.",
            },
            status_code=400,
        )

    valid_models = {
        "anthropic": "claude-sonnet-4-20250514",
        "openrouter": "openai/gpt-5",
    }
    expected_model = valid_models.get(provider)
    if model != expected_model:
        return templates.TemplateResponse(
            "settings/llm.html",
            {
                "request": request,
                "message": "Model invAlido para o provider selecionado.",
            },
            status_code=400,
        )

    # PersistAancia (DB)
    try:
        await set_app_setting("llm.provider", provider)
        await set_app_setting("llm.model", model)
        if openrouter_api_key:
            # Persistir criptografado
            await set_app_setting("llm.openrouter_api_key", openrouter_api_key, encrypt=True)
    except Exception as exc:
        logger.exception("Erro salvando configuraAAes LLM")
        return templates.TemplateResponse(
            "settings/llm.html",
            {"request": request, "message": f"Falha ao salvar: {str(exc)}"},
            status_code=500,
        )

    # Usamos redirect com query param para exibir mensagem apA3s GET
    return RedirectResponse(url="/settings/llm?saved=1", status_code=302)



async def settings_containers_get(request: Request) -> Response:
    """Renderiza configuracao de preservacao de containers."""
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    preserve_value = await get_effective_setting("containers.preserve")
    preserve_enabled = True
    if preserve_value is not None:
        preserve_enabled = preserve_value.lower() in ("true", "1", "yes")

    saved = request.query_params.get("saved")
    message = "Preferencia salva com sucesso." if saved else None

    return templates.TemplateResponse(
        "settings/containers.html",
        {
            "request": request,
            "preserve_enabled": preserve_enabled,
            "message": message,
        },
    )


async def settings_containers_post(request: Request) -> Response:
    """Processa atualizacao da preferencia de preservacao de containers."""
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    form = await request.form()
    mode = _coerce_form_str(form.get("preserve_mode")).lower()
    preserve_enabled = mode == "enable"

    value = "true" if preserve_enabled else "false"
    try:
        await set_app_setting("containers.preserve", value)
        # Atualiza variaveis em memoria para novos managers
        import os

        os.environ["MCP_PRESERVE_CONTAINERS"] = value

        container_manager = getattr(request.app.state, "container_manager", None)
        if container_manager:
            if hasattr(container_manager, "set_preserve_preference"):
                container_manager.set_preserve_preference(preserve_enabled)
            else:
                container_manager.preserve_containers = preserve_enabled
            if hasattr(container_manager, "load_preserve_setting"):
                await container_manager.load_preserve_setting()
    except Exception as exc:
        logger.exception("Erro salvando preservacao de containers")
        return templates.TemplateResponse(
            "settings/containers.html",
            {
                "request": request,
                "message": f"Falha ao salvar: {exc}",
                "preserve_enabled": preserve_enabled,
            },
            status_code=500,
        )

    return RedirectResponse(url="/settings/containers?saved=1", status_code=302)


async def settings_security_get(request: Request) -> Response:
    """Renderiza a pAgina de configuraAAes de autenticaAAo MCP."""
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    disable_value = await get_effective_setting("mcp.disable_auth")
    auth_disabled = bool(
        disable_value and disable_value.lower() in ("true", "1", "yes")
    )

    saved = request.query_params.get("saved")
    message = "ConfiguraAAes salvas com sucesso." if saved else None

    return templates.TemplateResponse(
        "settings/security.html",
        {
            "request": request,
            "auth_disabled": auth_disabled,
            "message": message,
        },
    )


async def settings_security_post(request: Request) -> Response:
    """Processa submissAo da configuraAAo de autenticaAAo MCP."""
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    form = await request.form()
    mode = _coerce_form_str(form.get("mode"))

    if mode not in {"require", "disable"}:
        return templates.TemplateResponse(
            "settings/security.html",
            {
                "request": request,
                "auth_disabled": getattr(
                    request.app.state, "mcp_auth_disabled", False
                ),
                "message": "Valor invAlido para modo de autenticaAAo.",
            },
            status_code=400,
        )

    disable = mode == "disable"

    try:
        await set_app_setting("mcp.disable_auth", "true" if disable else "false")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Erro salvando configuraAAo de autenticaAAo", exc_info=exc)
        return templates.TemplateResponse(
            "settings/security.html",
            {
                "request": request,
                "auth_disabled": getattr(
                    request.app.state, "mcp_auth_disabled", False
                ),
                "message": f"Falha ao salvar: {exc}",
            },
            status_code=500,
        )

    request.app.state.mcp_auth_disabled = disable

    return RedirectResponse(url="/settings/security?saved=1", status_code=302)


# Rotas exportadas para serem agregadas em app.py (mesmo padrAo usado por config_routes/secret_routes)
settings_routes = [
    Route("/settings/llm", endpoint=settings_llm_get, methods=["GET"]),
    Route("/settings/llm", endpoint=settings_llm_post, methods=["POST"]),
    Route("/settings/containers", endpoint=settings_containers_get, methods=["GET"]),
    Route("/settings/containers", endpoint=settings_containers_post, methods=["POST"]),
    Route("/settings/security", endpoint=settings_security_get, methods=["GET"]),
    Route("/settings/security", endpoint=settings_security_post, methods=["POST"]),
]
