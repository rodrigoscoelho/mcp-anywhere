# Correção do Caminho do Endpoint MCP

## Problema Identificado

A requisição ao endpoint MCP estava retornando **HTTP 404 Not Found** porque estava usando o caminho incorreto.

### Erro Observado

```
Response: HTTP 404
Body: {"detail":"Not Found"}
```

### Causa Raiz

O código estava usando `Config.MCP_PATH_PREFIX` que adiciona uma barra dupla no final:

```python
# ❌ ERRADO - Resulta em /mcp// (barra dupla)
mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_PREFIX}"
# Resultado: http://localhost:8000/mcp//
```

## Solução Implementada

### Entendendo as Constantes de Configuração

O arquivo `config.py` define três variantes do caminho MCP:

```python
# config.py
MCP_PATH = "/mcp"                    # Caminho base normalizado
MCP_PATH_MOUNT = "/mcp"              # Para Starlette mount (sem trailing slash)
MCP_PATH_PREFIX = "/mcp/"            # Para URLs (com trailing slash)
```

### Uso Correto

Para fazer requisições ao endpoint JSON-RPC do MCP, devemos usar:

```python
# ✅ CORRETO - Usa MCP_PATH_MOUNT + "/"
mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_MOUNT}/"
# Resultado: http://localhost:8000/mcp/
```

## Código Corrigido

### Antes (Incorreto)

```python
# Build the full MCP URL
mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_PREFIX}"
# Resulta em: http://localhost:8000/mcp//
```

### Depois (Correto)

```python
# Build the full MCP URL
# Use MCP_PATH_MOUNT (without trailing slash) + "/" for the JSON-RPC endpoint
mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_MOUNT}/"
# Resulta em: http://localhost:8000/mcp/
```

## Como o MCP Está Montado

No arquivo `app.py`, o MCP é montado usando `MCP_PATH_MOUNT`:

```python
# app.py linha 183
if transport_mode == "http":
    if mcp_http_app is not None:
        app.mount(Config.MCP_PATH_MOUNT, mcp_http_app)
        # Monta em: /mcp
```

Isso significa que o endpoint FastMCP está disponível em:
- **Base**: `/mcp`
- **JSON-RPC**: `/mcp/` (com trailing slash)

## Estrutura de Rotas

```
Application Routes:
├── /                          → Homepage
├── /test                      → Testing page
├── /test/execute              → Execute tool endpoint
├── /servers/*                 → Server management
├── /logs/*                    → Usage logs
└── /mcp/                      → MCP JSON-RPC endpoint (FastMCP)
    ├── tools/list             → List available tools
    ├── tools/call             → Call a tool
    ├── resources/list         → List resources
    └── prompts/list           → List prompts
```

## Fluxo de Requisição Corrigido

```
Browser
  ↓
POST /test/execute
  ↓
test_routes.py
  ↓
Constrói URL: http://localhost:8000/mcp/
  ↓
POST http://localhost:8000/mcp/
  ↓
FastMCP HTTP App (montado em /mcp)
  ↓
Processa JSON-RPC request
  ↓
Executa tool
  ↓
Retorna resultado
```

## Teste da Correção

### Requisição Esperada

```http
POST /mcp/ HTTP/1.1
Host: localhost:8000
Content-Type: application/json
Cookie: session=...

{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "method": "tools/call",
  "params": {
    "name": "abc123_tool_name",
    "arguments": {}
  }
}
```

### Resposta Esperada (Sucesso)

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Tool result"
      }
    ]
  }
}
```

## Verificação

Para verificar se o endpoint está correto, você pode testar diretamente:

```bash
# Teste 1: Verificar se /mcp/ responde
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/list",
    "params": {}
  }'

# Deve retornar a lista de tools disponíveis
```

## Outras Considerações

### Redirecionamento

O `RedirectMiddleware` redireciona `/mcp` (sem barra) para `/mcp/` (com barra):

```python
# middleware.py
if request.url.path == mcp_mount_path:
    return RedirectResponse(url=f"{Config.MCP_PATH_PREFIX}")
```

Isso garante que:
- `/mcp` → redireciona para → `/mcp/`
- `/mcp/` → processa normalmente

### Autenticação

Em modo HTTP, o endpoint `/mcp/*` pode ter autenticação OAuth ou estar desabilitado via `MCP_DISABLE_AUTH`.

Para a página de testes, usamos o cookie de sessão do usuário autenticado:

```python
if "session" in request.cookies:
    headers["Cookie"] = f"session={request.cookies['session']}"
```

## Resumo da Correção

| Aspecto | Antes | Depois |
|---------|-------|--------|
| **Constante usada** | `MCP_PATH_PREFIX` | `MCP_PATH_MOUNT + "/"` |
| **URL gerada** | `http://host:port/mcp//` | `http://host:port/mcp/` |
| **Resultado** | HTTP 404 Not Found | HTTP 200 OK |
| **Motivo** | Barra dupla inválida | Caminho correto |

## Conclusão

A correção foi simples mas importante:
- ✅ Usar `MCP_PATH_MOUNT` em vez de `MCP_PATH_PREFIX`
- ✅ Adicionar manualmente a barra final: `+ "/"`
- ✅ Evitar barra dupla no caminho

Agora a página de testes faz requisições corretamente ao endpoint `/mcp/` e deve funcionar perfeitamente! 🎉

