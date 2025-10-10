# Debug da Página de Testes MCP

## Problema Atual

Ainda estamos recebendo **HTTP 404** ao tentar executar tools através da página de testes, mesmo após corrigir o caminho do endpoint.

## Endpoint de Debug Adicionado

Foi criado um novo endpoint de debug para ajudar a diagnosticar o problema:

**URL**: `GET /test/debug`

### Como Usar

1. Acesse: `https://mcp.fratar.com.br/test/debug`
2. Você verá um JSON com informações de configuração

### Informações Retornadas

```json
{
  "mcp_path_mount": "/mcp",
  "mcp_path_prefix": "/mcp/",
  "server_url": "https://mcp.fratar.com.br",
  "transport_mode": "http",
  "mcp_manager_available": true,
  "request_url": "https://mcp.fratar.com.br/test/debug",
  "request_host": "mcp.fratar.com.br",
  "request_port": 443,
  "request_scheme": "https",
  "mounted_servers": ["server1", "server2"]
}
```

## Logs Adicionados

O código agora registra informações detalhadas no log:

```python
logger.info(f"Making internal MCP request to: {mcp_url}")
logger.info(f"Tool name: {server_id}_{tool_name}")
logger.info(f"Arguments: {arguments}")
logger.info(f"Request headers: {headers}")
logger.info(f"Request payload: {jsonrpc_request}")
logger.info(f"Response status: {response.status_code}")
logger.info(f"Response body: {response.text[:500]}")
```

## Mudanças na Requisição Interna

### Antes
```python
# Usava o host e porta da requisição original
host = request.url.hostname or "localhost"
port = request.url.port or 8000
scheme = request.url.scheme or "http"
```

### Agora
```python
# Sempre usa localhost para requisições internas
host = "127.0.0.1"
port = 8000  # Porta padrão do uvicorn
scheme = "http"  # Requisições internas usam HTTP
```

**Motivo**: Como o servidor está atrás de um proxy Apache, fazer requisições para o domínio externo pode causar loops ou problemas de roteamento. É melhor fazer requisições internas diretamente para o uvicorn.

## Possíveis Causas do 404

### 1. MCP Não Montado em Modo HTTP

**Verificar**: O endpoint `/test/debug` deve mostrar `"transport_mode": "http"`

Se mostrar `"stdio"` ou outro valor, o MCP não está montado como HTTP.

**Solução**: Verificar como o servidor foi iniciado:
```bash
# Deve ser iniciado em modo HTTP
mcp-anywhere serve http
```

### 2. Porta Incorreta

**Verificar**: O uvicorn está rodando na porta 8000?

```bash
# Verificar processos
ps aux | grep uvicorn
netstat -tlnp | grep 8000
```

**Solução**: Ajustar a porta no código se necessário.

### 3. MCP Manager Não Disponível

**Verificar**: O endpoint `/test/debug` deve mostrar `"mcp_manager_available": true`

Se for `false`, o MCP manager não foi inicializado.

**Solução**: Verificar logs de inicialização do servidor.

### 4. Proxy Bloqueando Requisições Internas

**Verificar**: O Apache pode estar bloqueando requisições de localhost para localhost.

**Solução**: Configurar o Apache para permitir requisições internas ou fazer a requisição diretamente via socket Unix.

### 5. Autenticação Falhando

**Verificar**: O cookie de sessão está sendo passado corretamente?

Nos logs, procure por:
```
Request headers: {'Content-Type': 'application/json', 'Cookie': 'session=...'}
```

**Solução**: Verificar se o cookie está presente e válido.

## Testes Manuais

### Teste 1: Verificar se /mcp/ responde

```bash
# Teste direto no servidor
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/list",
    "params": {}
  }'
```

**Resultado esperado**: Lista de tools ou erro de autenticação (não 404)

### Teste 2: Verificar debug endpoint

```bash
# Acessar endpoint de debug
curl https://mcp.fratar.com.br/test/debug \
  -H "Cookie: session=YOUR_SESSION_COOKIE"
```

**Resultado esperado**: JSON com configurações

### Teste 3: Verificar logs do servidor

```bash
# Ver logs em tempo real
tail -f /var/log/mcp-anywhere/server.log

# Ou se estiver usando systemd
journalctl -u mcp-anywhere -f
```

**Procurar por**:
- "Making internal MCP request to: ..."
- "Response status: ..."
- Erros de conexão ou timeout

## Próximos Passos

1. **Acessar `/test/debug`** e verificar as configurações
2. **Verificar os logs** do servidor durante uma tentativa de execução
3. **Testar o endpoint `/mcp/` diretamente** com curl
4. **Verificar a configuração do Apache** (proxy reverso)

## Configuração do Apache

Se o problema for o proxy, a configuração do Apache deve ser algo como:

```apache
<VirtualHost *:443>
    ServerName mcp.fratar.com.br
    
    # Proxy para o uvicorn
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/
    
    # Permitir WebSocket (se necessário)
    RewriteEngine on
    RewriteCond %{HTTP:Upgrade} websocket [NC]
    RewriteCond %{HTTP:Connection} upgrade [NC]
    RewriteRule ^/?(.*) "ws://127.0.0.1:8000/$1" [P,L]
    
    # SSL
    SSLEngine on
    SSLCertificateFile /path/to/cert.pem
    SSLCertificateKeyFile /path/to/key.pem
</VirtualHost>
```

## Alternativa: Usar ASGI Transport

Se as requisições HTTP internas continuarem falhando, podemos mudar a abordagem para usar o ASGI transport diretamente (sem fazer requisições HTTP):

```python
from httpx import AsyncClient, ASGITransport

# Criar cliente ASGI que fala diretamente com a app
transport = ASGITransport(app=request.app)
async with AsyncClient(transport=transport, base_url="http://test") as client:
    response = await client.post("/mcp/", json=jsonrpc_request)
```

**Vantagens**:
- Não depende de rede ou proxy
- Mais rápido
- Evita problemas de autenticação

**Desvantagens**:
- Não testa a stack HTTP completa
- Pode ter comportamento diferente de clientes externos

## Resumo

| Item | Status | Ação |
|------|--------|------|
| ✅ Endpoint de debug | Adicionado | Acessar `/test/debug` |
| ✅ Logs detalhados | Adicionados | Verificar logs do servidor |
| ✅ Requisição interna | Modificada | Usa 127.0.0.1:8000 |
| ⏳ Diagnóstico | Pendente | Executar testes manuais |
| ⏳ Correção final | Pendente | Depende do diagnóstico |

## Comandos Úteis

```bash
# Ver configuração atual
curl https://mcp.fratar.com.br/test/debug

# Testar MCP diretamente
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}'

# Ver logs
tail -f /var/log/mcp-anywhere/server.log

# Verificar processos
ps aux | grep uvicorn
ps aux | grep mcp-anywhere

# Verificar portas
netstat -tlnp | grep 8000
lsof -i :8000
```

Por favor, execute esses testes e compartilhe os resultados para que possamos identificar a causa exata do problema!

