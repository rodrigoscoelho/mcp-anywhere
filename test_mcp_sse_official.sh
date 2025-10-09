#!/bin/bash

# Script para testar o endpoint MCP oficial com suporte a SSE
# Testa tanto respostas JSON quanto Server-Sent Events (SSE)

set -e

BASE_URL="http://localhost:8000"
MCP_ENDPOINT="${BASE_URL}/mcp/"

echo "=========================================="
echo "Teste MCP Anywhere - SSE Support"
echo "=========================================="
echo ""
echo "Base URL: ${BASE_URL}"
echo "MCP Endpoint: ${MCP_ENDPOINT}"
echo ""

# Cores para output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Função para fazer requisição MCP
make_mcp_request() {
    local method=$1
    local params=$2
    local accept_header=${3:-"application/json"}
    
    local payload=$(cat <<EOF
{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "${method}",
    "params": ${params}
}
EOF
)
    
    echo -e "${BLUE}📤 Enviando requisição: ${method}${NC}"
    echo "Payload: ${payload}"
    echo ""
    
    response=$(curl -s -w "\n%{http_code}\n%{content_type}" \
        -X POST "${MCP_ENDPOINT}" \
        -H "Content-Type: application/json" \
        -H "Accept: ${accept_header}" \
        -d "${payload}")
    
    # Separar body, status code e content-type
    body=$(echo "$response" | head -n -2)
    status_code=$(echo "$response" | tail -n 2 | head -n 1)
    content_type=$(echo "$response" | tail -n 1)
    
    echo -e "${BLUE}📥 Status: ${status_code}${NC}"
    echo -e "${BLUE}📥 Content-Type: ${content_type}${NC}"
    echo ""
    
    # Verificar se é SSE
    if [[ "$content_type" == *"text/event-stream"* ]]; then
        echo -e "${YELLOW}📡 Resposta SSE detectada!${NC}"
        echo "Raw SSE Stream:"
        echo "$body"
        echo ""
        
        # Parsear SSE
        echo -e "${GREEN}Parseando SSE...${NC}"
        echo "$body" | grep "^data: " | while read -r line; do
            data="${line#data: }"
            echo "Data line: $data" | python3 -m json.tool 2>/dev/null || echo "$data"
        done
    else
        echo -e "${GREEN}📦 Resposta JSON:${NC}"
        echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
    fi
    
    echo ""
    echo "----------------------------------------"
    echo ""
}

# Teste 1: Listar ferramentas (tools/list) - JSON
echo -e "${GREEN}=== TESTE 1: tools/list (Accept: application/json) ===${NC}"
echo ""
make_mcp_request "tools/list" "{}" "application/json"

# Teste 2: Listar ferramentas (tools/list) - SSE
echo -e "${GREEN}=== TESTE 2: tools/list (Accept: text/event-stream) ===${NC}"
echo ""
make_mcp_request "tools/list" "{}" "text/event-stream"

# Teste 3: Listar ferramentas (tools/list) - Ambos
echo -e "${GREEN}=== TESTE 3: tools/list (Accept: application/json, text/event-stream) ===${NC}"
echo ""
make_mcp_request "tools/list" "{}" "application/json, text/event-stream"

# Teste 4: Chamar uma ferramenta específica
echo -e "${GREEN}=== TESTE 4: tools/call - brave-search__brave_web_search ===${NC}"
echo ""
tool_params=$(cat <<'EOF'
{
    "name": "brave-search__brave_web_search",
    "arguments": {
        "query": "MCP protocol"
    }
}
EOF
)
make_mcp_request "tools/call" "$tool_params" "application/json, text/event-stream"

# Teste 5: Chamar ferramenta context7
echo -e "${GREEN}=== TESTE 5: tools/call - context7__resolve-library-id ===${NC}"
echo ""
tool_params=$(cat <<'EOF'
{
    "name": "context7__resolve-library-id",
    "arguments": {
        "libraryName": "react"
    }
}
EOF
)
make_mcp_request "tools/call" "$tool_params" "application/json, text/event-stream"

echo ""
echo -e "${GREEN}=========================================="
echo "Testes concluídos!"
echo -e "==========================================${NC}"
echo ""
echo "Resumo:"
echo "  ✓ Endpoint MCP oficial: ${MCP_ENDPOINT}"
echo "  ✓ Suporte a JSON: Testado"
echo "  ✓ Suporte a SSE: Testado"
echo "  ✓ Chamadas de ferramentas: Testadas"
echo ""

