# MCP Anywhere - Resumo dos Modos de Transporte

## ✅ Resultado dos Testes

**Status:** AMBOS os modos de transporte funcionam corretamente!

## 🎯 Endpoint MCP

**URL:** `http://localhost:8000/mcp/`

O caminho é configurável via variável de ambiente `MCP_PATH` (padrão: `/mcp`).

## 📡 Protocolo de Transporte

### Modo Padrão: SSE (Server-Sent Events)

O MCP Anywhere usa **SSE (Server-Sent Events)** como protocolo de transporte padrão para o endpoint HTTP.

### Header Accept Obrigatório

**IMPORTANTE:** O FastMCP exige **AMBOS** os tipos de conteúdo no header Accept:

```
Accept: application/json, text/event-stream
```

### Por que ambos?

- O FastMCP valida que os clientes podem lidar com respostas JSON e SSE
- O servidor retorna `406 Not Acceptable` se apenas um tipo de conteúdo for especificado
- Isso garante compatibilidade com a especificação do protocolo MCP

### Como Funciona

1. **Cliente envia:**
   ```
   Accept: application/json, text/event-stream
   ```

2. **Servidor responde com:**
   ```
   Content-Type: text/event-stream
   ```

3. **Formato da resposta:** SSE com mensagens JSON-RPC

## 🔧 Formato SSE

### Estrutura da Resposta

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}

```

Cada mensagem SSE:
- Começa com `event: message`
- Contém uma linha `data:` com a resposta JSON-RPC
- Termina com uma linha em branco

### Como Parsear Respostas SSE

```python
for line in response.text.split('\n'):
    if line.startswith('data: '):
        data_json = line[6:].strip()
        parsed = json.loads(data_json)
        # Usar a resposta JSON-RPC parseada
```

## 🚀 Handshake do Protocolo MCP

### 1. Inicializar Sessão

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "my-client", "version": "1.0.0"}
    }
  }'
```

**Resposta:**
- Status: 200 OK
- Content-Type: text/event-stream
- Header: `mcp-session-id: <session-id>`

**IMPORTANTE:** Salve o `mcp-session-id` dos headers da resposta!

### 2. Enviar Notificação Initialized

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "method": "notifications/initialized"
  }'
```

### 3. Usar Métodos MCP

Agora você pode usar qualquer método MCP (tools/list, tools/call, etc.):

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
```

## 📝 Exemplo Prático: Testando com Context7

### Passo 1: Listar Ferramentas

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
```

### Passo 2: Buscar Biblioteca "aimsun"

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "context7_resolve-library-id",
      "arguments": {"libraryName": "aimsun"}
    }
  }'
```

**Resultado:**
- Encontra: "Aimsun Next Users Manual"
- Library ID: `/websites/aimsun_br`
- Code Snippets: 1654
- Trust Score: 7.5

### Passo 3: Obter Documentação

```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <session-id>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "context7_get-library-docs",
      "arguments": {
        "context7CompatibleLibraryID": "/websites/aimsun_br",
        "topic": "getting started"
      }
    }
  }'
```

**Resultado:**
- Retorna snippets de código do Aimsun
- Exemplos em C++, Python
- Documentação em português (BR)

## 🧪 Scripts de Teste

### 1. Teste de Modos de Transporte

```bash
uv run python test_transport_modes.py
```

Testa:
- Inicialização de sessão
- Listagem de ferramentas
- Chamadas básicas de ferramentas

### 2. Teste Context7 com Aimsun

```bash
uv run python test_context7_aimsun.py
```

Demonstra:
- Workflow completo do Context7
- Resolução de library ID para "aimsun"
- Recuperação de documentação

**Resultado do Teste:**
```
✓ Session initialized (ID: 8c6c66e0b1294df1b444c49005ab882a)
✓ Found 30 tools
✓ Found library ID: /websites/aimsun_br
✓ Retrieved documentation with code snippets
```

## ⚠️ Problemas Comuns

### Erro: "Not Acceptable: Client must accept both application/json and text/event-stream"

**Solução:** Inclua ambos os tipos de conteúdo no header Accept:
```
Accept: application/json, text/event-stream
```

### Erro: "Bad Request: No valid session ID provided"

**Solução:** Inclua o header `mcp-session-id` da resposta de initialize em todas as requisições subsequentes.

### Erro: "Invalid request parameters" em tools/list

**Solução:** Certifique-se de:
1. Ter chamado `initialize` primeiro
2. Ter enviado `notifications/initialized`
3. Estar usando o `mcp-session-id` correto

## 📊 Resumo Executivo

| Aspecto | Detalhes |
|---------|----------|
| **Endpoint** | `http://localhost:8000/mcp/` |
| **Modo de Transporte** | SSE (Server-Sent Events) |
| **Header Accept** | `application/json, text/event-stream` (OBRIGATÓRIO) |
| **Formato de Resposta** | SSE com mensagens JSON-RPC |
| **Comportamento Padrão** | Servidor sempre responde com formato SSE |
| **Gerenciamento de Sessão** | Obrigatório via header `mcp-session-id` |
| **Status dos Testes** | ✅ Todos os testes passaram com sucesso |

## 🎯 Instruções de Uso

### Para Desenvolvedores

1. **Sempre use o header Accept completo:**
   ```
   Accept: application/json, text/event-stream
   ```

2. **Siga o handshake MCP:**
   - initialize → initialized → métodos

3. **Mantenha o session ID:**
   - Salve o `mcp-session-id` da resposta de initialize
   - Use-o em todas as requisições subsequentes

4. **Parse respostas SSE:**
   - Procure por linhas começando com `data: `
   - Extraia e parse o JSON

### Para Testes

Use os scripts fornecidos:
```bash
# Teste básico de transporte
uv run python test_transport_modes.py

# Teste completo com Context7
uv run python test_context7_aimsun.py
```

## 📚 Referências

- [Documentação MCP](https://spec.modelcontextprotocol.io/)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [Server-Sent Events (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [Documentação SSE do Projeto](docs/SSE_SUPPORT.md)
- [Guia de Modos de Transporte](TRANSPORT_MODES.md)

