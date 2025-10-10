# Correção do MCP Session ID - HTTP 400

## Problema Identificado

Após corrigir o header `Accept`, recebemos um novo erro:

```
HTTP 400 Bad Request
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32600,
    "message": "Bad Request: Missing session ID"
  }
}
```

## Causa Raiz

O MCP usa um **session ID específico** que é diferente do cookie de sessão da web UI.

### Como Funciona o MCP Session ID

1. **Primeira Requisição**: Cliente faz requisição sem `mcp-session-id`
2. **Resposta do Servidor**: Servidor retorna header `mcp-session-id: <uuid>`
3. **Requisições Subsequentes**: Cliente deve incluir esse ID em todas as requisições

### Exemplo de Fluxo

```
Cliente → POST /mcp/ (sem mcp-session-id)
Servidor → 400 Bad Request + Header: mcp-session-id: abc-123

Cliente → POST /mcp/ (com Header: mcp-session-id: abc-123)
Servidor → 200 OK + Resultado
```

## Solução Implementada

### Lógica de Retry com Session ID

Implementamos um loop de retry que:
1. Faz a primeira requisição sem session ID
2. Se o servidor retornar um `mcp-session-id` no header, captura
3. Faz uma segunda requisição incluindo o session ID
4. Retorna o resultado

### Código Implementado

**Arquivo**: `src/mcp_anywhere/web/test_routes.py`

**Linhas**: 231-264

```python
# Try at most twice: first attempt may return a session id we must echo back
max_attempts = 2
attempt = 0
mcp_session_id = None

while attempt < max_attempts:
    # Add MCP session ID if we have one from a previous attempt
    if mcp_session_id:
        headers["mcp-session-id"] = mcp_session_id
        logger.info(f"Using MCP session ID: {mcp_session_id}")
    
    # Make the request to the official /mcp endpoint
    response = await client.post(
        mcp_url,
        json=jsonrpc_request,
        headers=headers,
        follow_redirects=True,
    )
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    logger.info(f"Response status: {response.status_code}")
    logger.info(f"Response headers: {dict(response.headers)}")
    logger.info(f"Response body: {response.text[:500]}")
    
    # If server returned a session id header, retry with it
    returned_session_id = response.headers.get("mcp-session-id")
    if returned_session_id and not mcp_session_id:
        logger.info(f"Server returned mcp-session-id: {returned_session_id}, retrying...")
        mcp_session_id = returned_session_id
        attempt += 1
        continue
    
    # No session ID needed or we already have it, break the loop
    break
```

## Logs Esperados

### Primeira Tentativa (Sem Session ID)

```
INFO: Making internal MCP request to: http://127.0.0.1:8000/mcp/
INFO: Tool name: 6c335d0d_resolve-library-id
INFO: Arguments: {'libraryName': 'aimsun'}
INFO: Request headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    'Cookie': 'session=...'
}
INFO: Response status: 400
INFO: Response headers: {'mcp-session-id': 'abc-123-def-456'}
INFO: Response body: {"error": {"message": "Missing session ID"}}
INFO: Server returned mcp-session-id: abc-123-def-456, retrying...
```

### Segunda Tentativa (Com Session ID)

```
INFO: Using MCP session ID: abc-123-def-456
INFO: Request headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    'Cookie': 'session=...',
    'mcp-session-id': 'abc-123-def-456'
}
INFO: Response status: 200
INFO: Response body: {"jsonrpc":"2.0","result":{...}}
```

## Comparação com MCP Manager

O `MCPManager` já implementa essa lógica:

```python
# mcp_manager.py linha 371-449
headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
if getattr(self, "_mcp_session_id", None):
    headers["mcp-session-id"] = self._mcp_session_id

# Try at most twice: first attempt may return a session id we must echo back
max_attempts = 2
attempt = 0

while attempt < max_attempts:
    response = await self._http_client.post(mcp_path, json=payload, headers=headers)
    
    # If server returned a session id header, retry with it
    session_id = response.headers.get("mcp-session-id")
    if session_id and "mcp-session-id" not in headers:
        logger.debug(f"Server requested mcp-session-id={session_id}; retrying once with this header")
        self._mcp_session_id = session_id
        headers["mcp-session-id"] = session_id
        attempt += 1
        continue
    
    break
```

Nossa implementação segue o **mesmo padrão**! ✅

## Headers HTTP Completos

### Primeira Requisição

```http
POST http://127.0.0.1:8000/mcp/ HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
Cookie: session=eyJ1c2VyX2lkIjogMSwgInVzZXJuYW1lIjogImFkbWluIn0=...

{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "method": "tools/call",
  "params": {...}
}
```

### Segunda Requisição (Com Session ID)

```http
POST http://127.0.0.1:8000/mcp/ HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
Cookie: session=eyJ1c2VyX2lkIjogMSwgInVzZXJuYW1lIjogImFkbWluIn0=...
mcp-session-id: abc-123-def-456

{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "method": "tools/call",
  "params": {...}
}
```

## Por Que o MCP Precisa de Session ID?

### 1. Rastreamento de Contexto

O MCP mantém contexto entre múltiplas chamadas:
- Estado de conversação
- Recursos carregados
- Configurações temporárias

### 2. Isolamento de Clientes

Múltiplos clientes podem usar o mesmo servidor MCP simultaneamente. O session ID garante que cada cliente tenha seu próprio contexto isolado.

### 3. Segurança

O session ID adiciona uma camada extra de validação além da autenticação básica.

## Progressão Completa dos Erros

| # | Erro | Causa | Solução |
|---|------|-------|---------|
| 1 | HTTP 502 | Chamada interna ao manager | ✅ Usar endpoint `/mcp` |
| 2 | HTTP 404 | Barra dupla na URL | ✅ Usar `MCP_PATH_MOUNT + "/"` |
| 3 | HTTP 404 | Requisição ao proxy externo | ✅ Usar `127.0.0.1:8000` |
| 4 | HTTP 406 | Faltava header `Accept` | ✅ Adicionar Accept header |
| 5 | HTTP 400 | Faltava `mcp-session-id` | ✅ **Implementar retry com session ID** |
| 6 | **✨ Sucesso!** | - | **Aguardando teste** |

## Teste da Correção

### Verificar nos Logs

Procure por estas mensagens:

```
INFO: Server returned mcp-session-id: <uuid>, retrying...
INFO: Using MCP session ID: <uuid>
INFO: Response status: 200
```

### Resultado Esperado

```json
{
  "success": true,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Tool execution result"
      }
    ]
  },
  "duration_ms": 150,
  "timestamp": 1234567890
}
```

## Resumo da Correção

| Aspecto | Antes | Depois |
|---------|-------|--------|
| **Tentativas** | 1 (falha imediata) | Até 2 (retry com session ID) |
| **Header mcp-session-id** | Não enviado | Enviado na 2ª tentativa |
| **Resultado** | HTTP 400 | HTTP 200 (esperado) |
| **Compatibilidade** | Incompatível | Compatível com MCP ✅ |

## Conclusão

A correção implementa o **protocolo completo de session ID do MCP**:

✅ Primeira requisição sem session ID  
✅ Captura do session ID retornado pelo servidor  
✅ Segunda requisição com session ID  
✅ Logs detalhados para debug  
✅ Compatibilidade total com o protocolo MCP  

Agora a página de testes deve funcionar perfeitamente! 🎉

## Próximo Teste

Execute uma tool novamente e verifique:
1. Logs mostram "Server returned mcp-session-id"
2. Logs mostram "Using MCP session ID"
3. Response status é 200
4. Resultado é exibido corretamente na interface

Se ainda houver problemas, compartilhe os novos logs!

