# Correção da Página de Testes MCP

## Problema Identificado

A implementação inicial da página de testes estava fazendo chamadas **internas** diretamente ao MCP manager, em vez de usar o endpoint **externo** `/mcp` como qualquer outro cliente faria.

### Erro Original
```python
# ❌ ERRADO - Chamada interna
mcp_manager = getattr(request.app.state, "mcp_manager", None)
tool_manager = mcp_manager.router._tool_manager
tools = await tool_manager.get_tools()
result = await tool_func(**arguments)
```

## Solução Implementada

A página de testes agora faz requisições HTTP ao endpoint oficial `/mcp`, simulando exatamente como um cliente externo (como Claude Desktop) interagiria com o servidor.

### Implementação Correta
```python
# ✅ CORRETO - Requisição HTTP ao endpoint /mcp
mcp_url = f"{scheme}://{host}:{port}{Config.MCP_PATH_PREFIX}"

jsonrpc_request = {
    "jsonrpc": "2.0",
    "id": str(int(time.time() * 1000)),
    "method": "tools/call",
    "params": {
        "name": prefixed_tool_name,
        "arguments": arguments,
    },
}

async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.post(
        mcp_url,
        json=jsonrpc_request,
        headers=headers,
    )
```

## Detalhes da Implementação

### 1. Endpoint Utilizado
- **URL**: `{scheme}://{host}:{port}/mcp`
- **Método**: `POST`
- **Protocolo**: JSON-RPC 2.0

### 2. Formato da Requisição

```json
{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "method": "tools/call",
  "params": {
    "name": "{server_id}_{tool_name}",
    "arguments": {
      "param1": "value1",
      "param2": "value2"
    }
  }
}
```

### 3. Autenticação

A requisição usa o cookie de sessão do usuário autenticado:

```python
if "session" in request.cookies:
    headers["Cookie"] = f"session={request.cookies['session']}"
```

Isso permite que a requisição interna seja autenticada usando a mesma sessão do usuário logado na interface web.

### 4. Tratamento de Resposta

#### Resposta de Sucesso (HTTP 200)
```json
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

#### Resposta de Erro (HTTP 200 com erro JSON-RPC)
```json
{
  "jsonrpc": "2.0",
  "id": "1234567890",
  "error": {
    "code": -32600,
    "message": "Invalid Request"
  }
}
```

#### Erro HTTP (Status != 200)
```
HTTP 502 Proxy Error
HTTP 401 Unauthorized
HTTP 404 Not Found
etc.
```

## Vantagens da Nova Abordagem

### 1. **Conformidade com o Protocolo MCP**
- Usa o endpoint oficial `/mcp`
- Segue o padrão JSON-RPC 2.0
- Comportamento idêntico a clientes externos

### 2. **Teste Real da API**
- Testa a stack completa (HTTP → MCP → Tool)
- Valida autenticação e autorização
- Detecta problemas de configuração

### 3. **Simulação Autêntica**
- Replica exatamente como Claude Desktop faria
- Mesmos headers, formato e protocolo
- Útil para debugging de integrações

### 4. **Isolamento de Camadas**
- Não depende de detalhes internos do MCP manager
- Respeita a arquitetura de camadas
- Mais robusto a mudanças internas

## Fluxo de Execução

```
┌─────────────────┐
│   Web Browser   │
│  (User clicks   │
│ "Execute Tool") │
└────────┬────────┘
         │
         │ POST /test/execute
         │ {server_id, tool_name, arguments}
         ▼
┌─────────────────────────┐
│  test_routes.py         │
│  execute_tool()         │
│                         │
│  1. Valida servidor     │
│  2. Monta JSON-RPC      │
│  3. Faz POST para /mcp  │
└────────┬────────────────┘
         │
         │ POST /mcp
         │ JSON-RPC 2.0 request
         ▼
┌─────────────────────────┐
│  MCP Endpoint           │
│  (FastMCP HTTP App)     │
│                         │
│  1. Valida autenticação │
│  2. Processa JSON-RPC   │
│  3. Chama tool          │
└────────┬────────────────┘
         │
         │ Executa tool
         ▼
┌─────────────────────────┐
│  MCP Server Tool        │
│  (Container/Process)    │
│                         │
│  Executa lógica do tool │
└────────┬────────────────┘
         │
         │ Retorna resultado
         ▼
┌─────────────────────────┐
│  JSON-RPC Response      │
│                         │
│  {result: {...}}        │
└────────┬────────────────┘
         │
         │ HTTP 200 OK
         ▼
┌─────────────────────────┐
│  test_routes.py         │
│                         │
│  Formata e retorna JSON │
└────────┬────────────────┘
         │
         │ JSON Response
         ▼
┌─────────────────┐
│   Web Browser   │
│  Exibe resultado│
└─────────────────┘
```

## Arquivos Modificados

### `src/mcp_anywhere/web/test_routes.py`

**Mudanças principais:**
1. Adicionado import `httpx` para requisições HTTP
2. Adicionado import `Config` para obter o path do MCP
3. Reescrita completa da função `execute_tool()`:
   - Constrói URL do endpoint `/mcp`
   - Cria requisição JSON-RPC 2.0
   - Faz POST HTTP com httpx
   - Trata respostas e erros HTTP

**Linhas modificadas:** ~120 linhas (função execute_tool completa)

## Testes Recomendados

### 1. Teste Básico
```bash
# 1. Inicie o servidor
mcp-anywhere serve http

# 2. Acesse http://localhost:8000/test
# 3. Selecione um servidor
# 4. Selecione uma tool
# 5. Preencha os parâmetros
# 6. Clique em "Execute Tool"
# 7. Verifique o resultado
```

### 2. Teste de Erro
- Tente executar com parâmetros inválidos
- Verifique se a mensagem de erro é clara
- Confirme que o status é "Error"

### 3. Teste de Timeout
- Execute uma tool que demora mais de 30 segundos
- Verifique se o timeout é tratado corretamente

### 4. Teste de Autenticação
- Faça logout
- Tente acessar `/test`
- Confirme redirecionamento para login

## Compatibilidade

### Versões Suportadas
- ✅ Python 3.11+
- ✅ httpx (já instalado como dependência)
- ✅ FastMCP (versão atual do projeto)

### Modos de Transporte
- ✅ HTTP mode (testado e funcional)
- ⚠️ STDIO mode (não aplicável - página web requer HTTP)

## Próximos Passos

### Melhorias Futuras (Opcional)

1. **Cache de Resultados**
   - Armazenar execuções recentes
   - Permitir re-execução rápida

2. **Histórico de Testes**
   - Salvar histórico de execuções
   - Exportar resultados

3. **Testes em Lote**
   - Executar múltiplas tools em sequência
   - Criar suítes de teste

4. **Métricas de Performance**
   - Gráficos de tempo de execução
   - Comparação entre tools

## Conclusão

A correção implementada garante que a página de testes:

✅ Usa o endpoint oficial `/mcp`  
✅ Segue o protocolo JSON-RPC 2.0  
✅ Simula clientes externos corretamente  
✅ Testa a stack completa da aplicação  
✅ Mantém autenticação e segurança  
✅ Fornece feedback claro de erros  

A implementação agora está **alinhada com a arquitetura MCP** e fornece uma ferramenta de teste **confiável e autêntica** para validar configurações de servidores MCP.

