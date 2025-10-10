# Correção do Header Accept - HTTP 406

## Problema Identificado

Após corrigir o caminho do endpoint, recebemos um novo erro mais específico:

```
HTTP 406 Not Acceptable
{
  "jsonrpc": "2.0",
  "id": "server-error",
  "error": {
    "code": -32600,
    "message": "Not Acceptable: Client must accept both application/json and text/event-stream"
  }
}
```

## Causa Raiz

O servidor MCP (FastMCP) **exige** que o cliente inclua no header `Accept` tanto `application/json` quanto `text/event-stream`.

Isso é necessário porque o MCP pode retornar:
- **JSON** para respostas síncronas normais
- **Server-Sent Events (SSE)** para respostas streaming/assíncronas

### Header Anterior (Incorreto)

```python
headers = {
    "Content-Type": "application/json",
}
# Faltava o header Accept!
```

Quando o header `Accept` não é especificado, o httpx usa um valor padrão que não inclui `text/event-stream`.

## Solução Implementada

### Header Corrigido

```python
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
```

Agora o cliente indica que aceita **ambos** os tipos de conteúdo que o MCP pode retornar.

## Código Modificado

**Arquivo**: `src/mcp_anywhere/web/test_routes.py`

**Linhas**: 192-195

**Antes**:
```python
# Prepare headers
headers = {
    "Content-Type": "application/json",
}
```

**Depois**:
```python
# Prepare headers
# MCP requires Accept header with both application/json and text/event-stream
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
```

## Por Que o MCP Exige Isso?

### 1. Suporte a Streaming

O MCP pode retornar respostas de duas formas:

**Resposta Síncrona (JSON)**:
```json
{
  "jsonrpc": "2.0",
  "id": "123",
  "result": {
    "content": [{"type": "text", "text": "Result"}]
  }
}
```

**Resposta Streaming (SSE)**:
```
data: {"type": "progress", "progress": 0.5}

data: {"type": "result", "content": [...]}
```

### 2. Negociação de Conteúdo

O servidor usa o header `Accept` para decidir qual formato usar:
- Se o cliente aceita apenas `application/json` → erro 406
- Se o cliente aceita `text/event-stream` → pode usar streaming
- Se o cliente aceita **ambos** → servidor escolhe o melhor formato

### 3. Compatibilidade com Clientes MCP

Clientes MCP oficiais (como Claude Desktop) sempre enviam:
```
Accept: application/json, text/event-stream
```

Nossa implementação agora faz o mesmo! ✅

## Progressão dos Erros

Veja como fomos resolvendo os problemas:

| Tentativa | Erro | Causa | Solução |
|-----------|------|-------|---------|
| 1 | HTTP 502 Proxy Error | Chamada interna ao manager | Usar endpoint `/mcp` |
| 2 | HTTP 404 Not Found | Barra dupla na URL | Usar `MCP_PATH_MOUNT + "/"` |
| 3 | HTTP 404 Not Found | Requisição ao proxy externo | Usar `127.0.0.1:8000` |
| 4 | **HTTP 406 Not Acceptable** | **Faltava header Accept** | **Adicionar Accept header** |
| 5 | ✅ **Sucesso esperado!** | - | - |

## Teste da Correção

### Requisição Completa Esperada

```http
POST http://127.0.0.1:8000/mcp/ HTTP/1.1
Host: 127.0.0.1:8000
Content-Type: application/json
Accept: application/json, text/event-stream
Cookie: session=eyJ1c2VyX2lkIjogMSwgInVzZXJuYW1lIjogImFkbWluIn0=...

{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "method": "tools/call",
  "params": {
    "name": "server_id_tool_name",
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
        "text": "Tool execution result"
      }
    ]
  }
}
```

## Headers HTTP Completos

### Headers que Enviamos

```python
{
    "Content-Type": "application/json",           # Tipo do corpo da requisição
    "Accept": "application/json, text/event-stream",  # Tipos que aceitamos
    "Cookie": "session=..."                       # Autenticação
}
```

### Headers que o MCP Espera

De acordo com a especificação MCP e FastMCP:

1. **Content-Type**: `application/json` ✅
2. **Accept**: Deve incluir `application/json` E `text/event-stream` ✅
3. **Authorization** ou **Cookie**: Para autenticação ✅

## Comparação com Clientes Oficiais

### Claude Desktop

```http
POST /mcp/ HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
Authorization: Bearer <token>
```

### Nossa Implementação

```http
POST /mcp/ HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
Cookie: session=<session_cookie>
```

**Diferença**: Usamos cookie em vez de Bearer token, mas ambos são válidos! ✅

## Verificação

Para confirmar que está funcionando, os logs devem mostrar:

```
INFO: Making internal MCP request to: http://127.0.0.1:8000/mcp/
INFO: Tool name: abc123_tool_name
INFO: Arguments: {}
INFO: Request headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    'Cookie': 'session=...'
}
INFO: Request payload: {
    'jsonrpc': '2.0',
    'id': '1234567890',
    'method': 'tools/call',
    'params': {...}
}
INFO: Response status: 200
INFO: Response body: {"jsonrpc":"2.0","id":"1234567890","result":{...}}
```

## Resumo da Correção

| Aspecto | Antes | Depois |
|---------|-------|--------|
| **Header Accept** | Não especificado | `application/json, text/event-stream` |
| **Resultado** | HTTP 406 | HTTP 200 (esperado) |
| **Compatibilidade** | Incompatível com MCP | Compatível com MCP ✅ |
| **Streaming** | Não suportado | Suportado ✅ |

## Conclusão

A correção foi simples mas essencial:

✅ Adicionar header `Accept: application/json, text/event-stream`  
✅ Seguir a especificação MCP  
✅ Compatibilidade com clientes oficiais  
✅ Suporte a respostas streaming  

Agora a página de testes deve funcionar corretamente! 🎉

## Próximo Teste

Tente executar uma tool novamente e verifique se:
1. O status da resposta é **200 OK**
2. O resultado é exibido corretamente na interface
3. Não há mais erros 406

Se ainda houver problemas, verifique os logs para ver o novo erro (se houver).

