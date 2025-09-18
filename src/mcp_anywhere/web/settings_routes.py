"""Rotas para configuração LLM (provider/model e OpenRouter API key).

Responsabilidades:
- GET /settings/llm: exibe valores efetivos (DB > ENV) e formulário para alterar provider/model e
  opcionalmente salvar a OpenRouter API key (criptografada).
- POST /settings/llm: valida e persiste configurações em DB via settings_store.
- Proteção: redireciona para /auth/login se usuário não autenticado (mesma abordagem usada em outras rotas).
"""

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
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
    """Retorna True se usuário autenticado, False caso contrário."""
    user_id = request.session.get("user_id")
    return bool(user_id)


async def settings_llm_get(request: Request) -> HTMLResponse:
    """Renderiza a página de configurações LLM.

    Contextos passados para o template:
    - provider_atual, model_atual : valores efetivos (get_effective_setting)
    - provider_persistido, model_persistido: valores salvos no DB (get_app_setting) para preencher form
    - has_openrouter_key_persisted: booleano se existe chave salva no DB (não exibir o valor)
    - message: opcional (ex.: ?saved=1)
    """
    # Autenticação: mesma estratégia usada em change_password e outras rotas protegidas
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        logger.info(f"User not authenticated, redirecting to login: {login_url}")
        return RedirectResponse(url=login_url, status_code=302)

    # Valores efetivos
    provider_atual = await get_effective_setting("llm.provider")
    model_atual = await get_effective_setting("llm.model")

    # Valores persistidos (raw) para preencher o formulário se existirem
    provider_row = await get_app_setting("llm.provider")
    model_row = await get_app_setting("llm.model")

    provider_persistido = provider_row[0] if provider_row else None
    model_persistido = model_row[0] if model_row else None

    # Indica se há chave OpenRouter salva (não exibimos o conteúdo)
    key_row = await get_app_setting("llm.openrouter_api_key")
    has_openrouter_key_persisted = bool(key_row and key_row[0])

    # Mensagem simples via query param (padrão existente no projeto)
    saved = request.query_params.get("saved")
    message = "Configurações salvas com sucesso." if saved else None

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


async def settings_llm_post(request: Request) -> HTMLResponse:
    """Processa submissão do formulário e persiste configurações no DB.

    Regras:
    - provider obrigatório: "anthropic" ou "openrouter"
    - model obrigatório e deve corresponder ao provider:
        - anthropic => "claude-sonnet-4-20250514"
        - openrouter => "openai/gpt-5"
    - openrouter_api_key opcional: se fornecida (não vazia) -> salvar com encrypt=True
      se vazia -> NÃO sobrepor o valor existente (não chamar set_app_setting para a chave)
    - Após salvar: redirecionar para GET /settings/llm?saved=1
    """
    if not await _require_authenticated(request):
        login_url = f"/auth/login?next={request.url}"
        return RedirectResponse(url=login_url, status_code=302)

    form = await request.form()
    provider = (form.get("provider") or "").strip()
    model = (form.get("model") or "").strip()
    openrouter_api_key = (form.get("openrouter_api_key") or "").strip()

    # Validações
    if provider not in {"anthropic", "openrouter"}:
        return templates.TemplateResponse(
            "settings/llm.html",
            {
                "request": request,
                "message": "Provider inválido. Valor permitido: 'anthropic' ou 'openrouter'.",
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
                "message": "Model inválido para o provider selecionado.",
            },
            status_code=400,
        )

    # Persistência (DB)
    try:
        await set_app_setting("llm.provider", provider)
        await set_app_setting("llm.model", model)
        if openrouter_api_key:
            # Persistir criptografado
            await set_app_setting("llm.openrouter_api_key", openrouter_api_key, encrypt=True)
    except Exception as exc:
        logger.exception("Erro salvando configurações LLM")
        return templates.TemplateResponse(
            "settings/llm.html",
            {"request": request, "message": f"Falha ao salvar: {str(exc)}"},
            status_code=500,
        )

    # Usamos redirect com query param para exibir mensagem após GET
    return RedirectResponse(url="/settings/llm?saved=1", status_code=302)


# Rotas exportadas para serem agregadas em app.py (mesmo padrão usado por config_routes/secret_routes)
settings_routes = [
    Route("/settings/llm", endpoint=settings_llm_get, methods=["GET"]),
    Route("/settings/llm", endpoint=settings_llm_post, methods=["POST"]),
]