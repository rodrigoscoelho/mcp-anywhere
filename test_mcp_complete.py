#!/usr/bin/env python3
"""
Script completo para testar o MCP Anywhere como cliente MCP HTTP.
Testa tanto o endpoint oficial /mcp quanto o endpoint web /servers/.../tools/.../test
"""
import asyncio
import httpx
import json

BASE_URL = "https://mcp.fratar.com.br"
SERVER_ID = "6c335d0d"
LIBRARY_NAME = "aimsun"

async def test_mcp_client():
    """Testa como um cliente MCP HTTP oficial."""
    
    print("=" * 80)
    print("TESTE 1: Cliente MCP HTTP - Listar ferramentas")
    print("=" * 80)
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        
        # 1. Inicializar sessão (tools/list)
        list_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }
        
        print(f"\n📤 Enviando tools/list para {BASE_URL}/mcp/")
        response = await client.post("/mcp/", json=list_payload, headers=headers)
        
        print(f"📥 Status: {response.status_code}")
        
        # Se precisar de session ID, pegar e tentar novamente
        if response.status_code == 400 and "session" in response.text.lower():
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                print(f"🔄 Retentando com session ID: {session_id}")
                headers["mcp-session-id"] = session_id
                response = await client.post("/mcp/", json=list_payload, headers=headers)
                print(f"📥 Status após retry: {response.status_code}")
        
        # Parsear resposta
        tools = []
        context7_tool = None
        
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '')
            
            if 'text/event-stream' in content_type:
                print(f"\n📡 Resposta SSE recebida")
                # Parsear SSE
                for line in response.text.split('\n'):
                    if line.startswith('data: '):
                        try:
                            data = json.loads(line[6:])
                            if 'result' in data and 'tools' in data['result']:
                                tools = data['result']['tools']
                                print(f"\n✅ {len(tools)} ferramentas encontradas")
                                
                                # Procurar ferramenta do Context7
                                for tool in tools:
                                    tool_name = tool.get('name', '')
                                    if 'resolve-library' in tool_name.lower() or 'context7' in tool_name.lower():
                                        context7_tool = tool
                                        print(f"\n🎯 Ferramenta Context7 encontrada:")
                                        print(f"   Nome: {tool_name}")
                                        print(f"   Descrição: {tool.get('description', 'N/A')[:100]}...")
                                        break
                                break
                            elif 'error' in data:
                                print(f"\n❌ Erro: {data['error']}")
                        except json.JSONDecodeError:
                            continue
            elif 'application/json' in content_type:
                data = response.json()
                if 'result' in data and 'tools' in data['result']:
                    tools = data['result']['tools']
                    print(f"\n✅ {len(tools)} ferramentas encontradas")
        
        if not context7_tool:
            print("\n⚠️  Ferramenta Context7 não encontrada")
            return None
        
        # 2. Executar a ferramenta resolve-library-id
        print("\n" + "=" * 80)
        print(f"TESTE 2: Cliente MCP HTTP - Executar {context7_tool['name']}")
        print("=" * 80)
        
        call_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": context7_tool['name'],
                "arguments": {
                    "libraryName": LIBRARY_NAME
                }
            }
        }
        
        print(f"\n📤 Enviando tools/call para {BASE_URL}/mcp/")
        print(f"Ferramenta: {context7_tool['name']}")
        print(f"Argumentos: {{'libraryName': '{LIBRARY_NAME}'}}")
        
        response = await client.post("/mcp/", json=call_payload, headers=headers)
        
        print(f"\n📥 Status: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type')}")
        
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '')
            
            if 'text/event-stream' in content_type:
                print(f"\n📡 Resposta SSE:")
                # Parsear SSE
                for line in response.text.split('\n'):
                    if line.startswith('data: '):
                        try:
                            data = json.loads(line[6:])
                            print(f"\n📦 Dados parseados:")
                            print(json.dumps(data, indent=2))
                            
                            if 'result' in data:
                                print(f"\n✅ SUCESSO! Resultado da ferramenta:")
                                result = data['result']
                                if isinstance(result, list):
                                    print(f"   Total de itens: {len(result)}")
                                    for item in result[:3]:  # Mostrar primeiros 3
                                        print(f"   - {json.dumps(item, indent=6)}")
                                else:
                                    print(json.dumps(result, indent=2))
                                return result
                            elif 'error' in data:
                                print(f"\n❌ ERRO retornado pela ferramenta:")
                                print(json.dumps(data['error'], indent=2))
                                return None
                        except json.JSONDecodeError as e:
                            print(f"⚠️  Erro ao parsear linha: {e}")
            elif 'application/json' in content_type:
                data = response.json()
                print(f"\n📦 Resposta JSON:")
                print(json.dumps(data, indent=2))
                
                if 'result' in data:
                    print(f"\n✅ SUCESSO!")
                    return data['result']
                elif 'error' in data:
                    print(f"\n❌ ERRO:")
                    return None
        else:
            print(f"\n❌ Erro HTTP: {response.status_code}")
            print(response.text[:500])
        
        return None


async def test_web_endpoint():
    """Testa o endpoint web /servers/.../tools/.../test."""
    
    print("\n" + "=" * 80)
    print("TESTE 3: Endpoint Web - Executar ferramenta via UI")
    print("=" * 80)
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0, follow_redirects=True) as client:
        # Login
        print("\n🔐 Fazendo login...")
        login_response = await client.post(
            "/auth/login",
            data={"username": "admin", "password": "Ropi1604mcp!"}
        )
        print(f"Login status: {login_response.status_code}")
        
        if login_response.status_code != 200:
            print("❌ Falha no login")
            return
        
        # Buscar o tool_id correto do banco de dados
        print(f"\n🔍 Buscando tool_id para resolve-library-id...")
        
        # Fazer requisição para a página do servidor para pegar o tool_id
        server_page = await client.get(f"/servers/{SERVER_ID}")
        
        if server_page.status_code == 200:
            # Procurar pelo tool_id no HTML
            import re
            match = re.search(r'tool-test-result-([a-f0-9]{8})', server_page.text)
            if match:
                tool_id = match.group(1)
                print(f"✅ Tool ID encontrado: {tool_id}")
                
                # Testar a ferramenta
                print(f"\n📤 Testando ferramenta via endpoint web...")
                print(f"URL: /servers/{SERVER_ID}/tools/{tool_id}/test")
                
                test_response = await client.post(
                    f"/servers/{SERVER_ID}/tools/{tool_id}/test",
                    data={"libraryName": LIBRARY_NAME},
                    headers={
                        "HX-Request": "true",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                )
                
                print(f"\n📥 Status: {test_response.status_code}")
                print(f"\n📄 Resposta HTML:")
                
                html = test_response.text
                
                # Verificar se há erro
                if "Erro" in html or "erro" in html:
                    print("❌ Erro detectado na resposta:")
                    # Extrair mensagem de erro
                    error_match = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
                    if error_match:
                        print(f"   {error_match.group(1)}")
                    else:
                        print(html[:500])
                
                # Verificar se há conteúdo
                elif "Conteúdo (content)" in html:
                    if '[]' in html or 'content">[]</pre>' in html:
                        print("⚠️  PROBLEMA: Content está vazio []")
                    else:
                        print("✅ Content parece ter dados!")
                        # Tentar extrair o conteúdo
                        content_match = re.search(r'Conteúdo \(content\)</h4>.*?<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
                        if content_match:
                            content = content_match.group(1)
                            print(f"\n📦 Conteúdo extraído:")
                            print(content[:500])
                else:
                    print("⚠️  Resposta inesperada:")
                    print(html[:500])
            else:
                print("❌ Tool ID não encontrado na página")
        else:
            print(f"❌ Erro ao acessar página do servidor: {server_page.status_code}")


async def main():
    """Executa todos os testes."""
    
    print("\n🧪 INICIANDO TESTES COMPLETOS DO MCP ANYWHERE")
    print(f"Servidor: {BASE_URL}")
    print(f"Server ID: {SERVER_ID}")
    print(f"Library Name: {LIBRARY_NAME}")
    print("=" * 80)
    
    # Aguardar o serviço estar pronto
    print("\n⏳ Aguardando serviço estar pronto...")
    await asyncio.sleep(5)
    
    # Teste 1 e 2: Cliente MCP HTTP
    result = await test_mcp_client()
    
    # Teste 3: Endpoint web
    await test_web_endpoint()
    
    print("\n" + "=" * 80)
    print("🏁 TESTES CONCLUÍDOS")
    print("=" * 80)
    
    if result:
        print("\n✅ Testes bem-sucedidos! A ferramenta retornou resultados.")
    else:
        print("\n⚠️  Verifique os logs acima para detalhes dos erros.")


if __name__ == "__main__":
    asyncio.run(main())

