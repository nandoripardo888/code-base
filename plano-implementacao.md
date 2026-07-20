# Plano de Implementação — Code Context Harness

O plano adota recuperação textual, estrutural e semântica, sempre validando o
conteúdo no arquivo original. Também incorpora o isolamento de parsers nativos,
evitando que falhas do Tree-sitter derrubem a CLI ou o servidor.

## 0. Controle de execução

Última atualização: **20 de julho de 2026**.

Este documento define o destino arquitetural e a ordem de implementação. A
fotografia detalhada do que está disponível e validado fica em
[`docs/project-status.md`](docs/project-status.md).

### Marco atual

**Fases 0, 1, 2, 3, 4 e 5 concluídas localmente. Fase 6 é a próxima.**

O repositório chegou ao item 24 da sequência recomendada. O próximo trabalho é o
item 25, `feat: add mcp adapter`.

| Fase | Estado | Evidência resumida |
|---|---|---|
| 0 — Bootstrap | Concluída localmente | Versão, help, lint, format, Mypy e testes passam |
| 1 — Core lexical | Concluída localmente | Fluxo CLI/API sem índice validado |
| 2 — Persistência | Concluída localmente | SQLite, migrações, FTS5 e incremental validados |
| 3 — Estrutural | Concluída localmente | Supervisor isolado, parsers, símbolos, referências, tools e chunks validados |
| 4 — Semântica | Concluída localmente | Provider opcional, cache por hash, vetores locais e semantic search validados |
| 5 — Híbrida/contexto | Concluída localmente | Ranking, contexto por orçamento e mapa validados |
| 6 — MCP | Próxima | Sem SDK ou adaptador MCP |
| 7 — Hardening | Não iniciada | Existem somente artefatos preliminares |

### Legenda de acompanhamento

- `[x]`: implementado e validado;
- `[ ]`: ainda necessário;
- **Concluída localmente**: entregas e critério de saída passaram no ambiente
  local;
- **Próxima**: primeira fase sem implementação funcional;
- **Não iniciada**: pode possuir documentação ou contratos preparatórios, mas
  nenhuma entrega funcional da fase foi aceita.

O marco só deverá avançar quando todas as entregas obrigatórias e o critério de
saída da fase estiverem comprovados por testes ou por uma validação reproduzível.

## 1. Visão geral

### 1.1 Nome provisório

`code-harness`

O nome poderá ser alterado sem impacto arquitetural. Neste documento, `code-harness` representa:

- a biblioteca Python;
- a CLI;
- o mecanismo de indexação;
- as ferramentas de busca;
- o adaptador MCP.

### 1.2 Objetivo

Construir um sistema local em Python capaz de investigar bases de código e fornecer contexto verificável para LLMs.

O sistema deverá combinar:

- busca textual exata;
- busca por arquivos e caminhos;
- busca estrutural por símbolos;
- busca semântica por intenção;
- ranking híbrido;
- leitura seletiva de arquivos;
- expansão controlada de definições e referências;
- construção de contexto dentro de um orçamento de tokens.

As mesmas capacidades deverão ser acessíveis por:

1. API Python;
2. terminal;
3. MCP;
4. futuramente HTTP, plugin de IDE ou outro protocolo.

### 1.3 Decisão arquitetural principal

As ferramentas não pertencem ao MCP.

O MCP será apenas um adaptador que transforma chamadas do protocolo em chamadas para a camada de aplicação.

```text
CLI ───────────────┐
                   │
API Python ────────┼──▶ Application Tools ───▶ Core/Domain
                   │              │
MCP ───────────────┘              ▼
                           Infrastructure
```

A remoção completa da pasta MCP não poderá impedir a CLI ou a API Python de funcionar.

---

## 2. Escopo inicial

### 2.1 Incluído na versão 1

A primeira versão funcional deverá oferecer:

- abertura e registro de um projeto;
- descoberta de arquivos;
- regras de exclusão;
- busca textual com Ripgrep;
- busca regex;
- leitura de arquivos e intervalos de linhas;
- índice local incremental;
- extração de símbolos;
- isolamento de parsers nativos;
- chunking sintático com fallback textual;
- embeddings opcionais;
- busca semântica;
- ranking híbrido;
- construção de contexto para LLM;
- CLI completa;
- API Python pública;
- servidor MCP fino;
- execução em Windows e Linux;
- resultados com caminho e linhas de origem.

### 2.2 Linguagens

A arquitetura será genérica, mas a implementação deverá priorizar:

- Java;
- PL/SQL;
- Python;
- SQL;
- arquivos de configuração e documentação.

Todo arquivo textual suportado terá busca textual, mesmo sem parser estrutural.

O suporte estrutural será progressivo:

| Linguagem | Busca textual | Símbolos | Referências | Chunking sintático |
|---|---:|---:|---:|---:|
| Java | Sim | Sim | Parcial inicialmente | Sim |
| Python | Sim | Sim | Parcial inicialmente | Sim |
| PL/SQL | Sim | Sim, com extrator dedicado | Parcial | Sim |
| SQL | Sim | Parcial | Não inicialmente | Parcial |
| Outras | Sim | Não inicialmente | Não | Fallback textual |

### 2.3 Fora do escopo inicial

Não fazer parte da primeira versão:

- edição automática de arquivos;
- execução autônoma de comandos do projeto;
- agente LLM interno;
- compilação da base de código;
- substituição completa de um LSP;
- análise semântica de tipos equivalente a compiladores;
- indexação distribuída;
- sincronização do índice em nuvem;
- interface gráfica;
- hospedagem multiusuário.

O sistema será inicialmente de leitura e recuperação de contexto.

---

## 3. Princípios obrigatórios

### 3.1 Local-first

Por padrão:

- arquivos são processados localmente;
- índices ficam dentro do ambiente local;
- embeddings podem ser gerados localmente;
- nenhum código é enviado para serviço externo sem configuração explícita.

### 3.2 Degradação segura

Cada camada deverá continuar funcionando quando a camada superior estiver indisponível.

Exemplos:

- se o índice não existir, `search_text` usa Ripgrep diretamente;
- se o Tree-sitter falhar, o arquivo continua pesquisável textualmente;
- se embeddings estiverem desativados, busca textual e estrutural continuam funcionando;
- se o MCP não estiver instalado, CLI e API Python continuam funcionando;
- se o índice estiver parcialmente corrompido, a leitura direta do arquivo continua disponível.

### 3.3 Validação no arquivo atual

Índices localizam candidatos, mas não são a fonte final da resposta.

Antes de devolver um trecho para a LLM, o sistema deverá:

1. abrir o arquivo atual;
2. verificar se o caminho ainda existe;
3. conferir o hash ou a versão indexada;
4. extrair novamente as linhas solicitadas;
5. indicar se o índice estava desatualizado.

### 3.4 Resultados rastreáveis

Todo resultado de código deverá possuir, quando aplicável:

- caminho relativo;
- linha inicial;
- linha final;
- conteúdo;
- linguagem;
- tipo de resultado;
- estratégia que encontrou o resultado;
- score;
- hash do arquivo;
- identificador do símbolo;
- motivo resumido da seleção.

### 3.5 Contratos estruturados

A camada de aplicação não deverá produzir Markdown específico para MCP ou terminal.

Ela retornará objetos estruturados. Cada interface será responsável pela apresentação.

### 3.6 Dependências direcionadas para dentro

```text
interfaces ───▶ application ───▶ domain
                      ▲
                      │
              infrastructure
```

Regras:

- `domain` não importa nenhuma outra camada;
- `application` importa somente `domain`;
- `infrastructure` implementa contratos definidos no `domain`;
- `interfaces` chamam a camada `application`;
- `bootstrap` instancia e conecta tudo;
- `application` nunca importa MCP, Typer, Rich ou detalhes de SQLite.

---

## 4. Estrutura inicial do repositório

```text
code-harness/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── .gitignore
├── .editorconfig
├── config.example.yaml
│
├── docs/
│   ├── architecture.md
│   ├── tools.md
│   ├── indexing.md
│   ├── semantic-search.md
│   ├── mcp-adapter.md
│   ├── troubleshooting.md
│   └── adr/
│       ├── 0001-layered-architecture.md
│       ├── 0002-sqlite-source-of-truth.md
│       ├── 0003-native-parser-isolation.md
│       └── 0004-mcp-as-adapter.md
│
├── src/
│   └── code_harness/
│       ├── __init__.py
│       │
│       ├── domain/
│       │   ├── models/
│       │   │   ├── project.py
│       │   │   ├── source_file.py
│       │   │   ├── code_location.py
│       │   │   ├── code_chunk.py
│       │   │   ├── code_symbol.py
│       │   │   ├── code_reference.py
│       │   │   ├── search_hit.py
│       │   │   └── index_report.py
│       │   ├── protocols/
│       │   │   ├── file_catalog.py
│       │   │   ├── source_reader.py
│       │   │   ├── text_searcher.py
│       │   │   ├── structural_analyzer.py
│       │   │   ├── embedding_provider.py
│       │   │   ├── vector_index.py
│       │   │   └── repository_store.py
│       │   ├── enums.py
│       │   └── errors.py
│       │
│       ├── application/
│       │   ├── dto/
│       │   │   ├── requests.py
│       │   │   └── responses.py
│       │   ├── tools/
│       │   │   ├── list_files.py
│       │   │   ├── search_files.py
│       │   │   ├── search_text.py
│       │   │   ├── search_regex.py
│       │   │   ├── read_file.py
│       │   │   ├── read_range.py
│       │   │   ├── get_outline.py
│       │   │   ├── find_symbol.py
│       │   │   ├── find_references.py
│       │   │   ├── semantic_search.py
│       │   │   ├── search_code.py
│       │   │   ├── build_context.py
│       │   │   ├── index_project.py
│       │   │   └── get_index_status.py
│       │   ├── indexing/
│       │   │   ├── index_coordinator.py
│       │   │   ├── chunk_builder.py
│       │   │   └── change_detector.py
│       │   ├── ranking/
│       │   │   ├── hybrid_ranker.py
│       │   │   ├── score_normalizer.py
│       │   │   └── deduplicator.py
│       │   └── context/
│       │       ├── context_builder.py
│       │       ├── token_budget.py
│       │       └── context_expander.py
│       │
│       ├── infrastructure/
│       │   ├── filesystem/
│       │   │   ├── local_file_catalog.py
│       │   │   ├── local_source_reader.py
│       │   │   ├── path_guard.py
│       │   │   ├── ignore_rules.py
│       │   │   └── encoding_detector.py
│       │   ├── ripgrep/
│       │   │   ├── ripgrep_searcher.py
│       │   │   ├── command_builder.py
│       │   │   └── output_parser.py
│       │   ├── parsers/
│       │   │   ├── registry.py
│       │   │   ├── native_worker.py
│       │   │   ├── native_supervisor.py
│       │   │   ├── tree_sitter_analyzer.py
│       │   │   ├── java_analyzer.py
│       │   │   ├── python_analyzer.py
│       │   │   ├── plsql_analyzer.py
│       │   │   └── textual_fallback.py
│       │   ├── persistence/
│       │   │   ├── sqlite_store.py
│       │   │   ├── schema.py
│       │   │   ├── migrations.py
│       │   │   └── fts_index.py
│       │   ├── embeddings/
│       │   │   ├── local_embedding_provider.py
│       │   │   ├── remote_embedding_provider.py
│       │   │   └── embedding_cache.py
│       │   ├── vectors/
│       │   │   ├── local_vector_index.py
│       │   │   └── memory_vector_index.py
│       │   └── tokens/
│       │       └── token_counter.py
│       │
│       ├── interfaces/
│       │   ├── cli/
│       │   │   ├── main.py
│       │   │   ├── commands/
│       │   │   └── renderers/
│       │   ├── mcp/
│       │   │   ├── server.py
│       │   │   ├── handlers.py
│       │   │   └── serializers.py
│       │   └── python_api/
│       │       └── harness.py
│       │
│       ├── bootstrap/
│       │   ├── container.py
│       │   ├── settings.py
│       │   └── logging.py
│       │
│       └── version.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── end_to_end/
│   ├── performance/
│   └── fixtures/
│       └── sample_repository/
│
└── scripts/
    ├── benchmark_repository.py
    └── build_test_repository.py
```

---

## 5. Stack técnica

### 5.1 Base

- Python 3.12;
- `pyproject.toml`;
- layout `src`;
- tipagem estática;
- dataclasses imutáveis nos modelos de domínio;
- composição manual de dependências;
- SQLite como fonte de verdade do índice;
- SQLite FTS para índice textual;
- Ripgrep para busca textual direta;
- Tree-sitter atrás de processo isolado;
- framework de CLI baseado em subcomandos;
- SDK MCP restrito à camada de interface;
- testes com `pytest`;
- lint e formatação automatizados;
- CI em Windows e Linux.

### 5.2 Dependências opcionais

Separar funcionalidades por extras:

```text
code-harness
code-harness[parsers]
code-harness[semantic]
code-harness[mcp]
code-harness[all]
```

A instalação básica deverá permitir:

- listar arquivos;
- pesquisar texto;
- pesquisar regex;
- ler arquivos;
- usar a CLI.

Isso evita obrigar o usuário a instalar modelos de embeddings ou bibliotecas nativas para operações simples.

---

## 6. Modelos centrais

### 6.1 Localização

```python
@dataclass(frozen=True)
class CodeLocation:
    path: str
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None
```

### 6.2 Trecho de código

```python
@dataclass(frozen=True)
class CodeSnippet:
    location: CodeLocation
    content: str
    language: str | None
    file_hash: str
```

### 6.3 Símbolo

```python
@dataclass(frozen=True)
class CodeSymbol:
    symbol_id: str
    name: str
    qualified_name: str | None
    kind: str
    location: CodeLocation
    signature: str | None
    parent_symbol_id: str | None
```

Tipos iniciais:

- module;
- package;
- class;
- interface;
- enum;
- record;
- method;
- constructor;
- function;
- procedure;
- field;
- constant;
- trigger;
- view.

### 6.4 Resultado de busca

```python
@dataclass(frozen=True)
class SearchHit:
    snippet: CodeSnippet
    score: float
    match_type: str
    matched_terms: tuple[str, ...]
    symbol: CodeSymbol | None
    reason: str | None
```

Valores possíveis de `match_type`:

- `exact`;
- `regex`;
- `full_text`;
- `symbol`;
- `reference`;
- `semantic`;
- `hybrid`;
- `path`.

### 6.5 Envelope de resposta

Toda tool deverá devolver metadados comuns:

```python
@dataclass(frozen=True)
class ToolResult[T]:
    data: T
    elapsed_ms: int
    truncated: bool
    warnings: tuple[str, ...]
    index_state: str | None
```

### 6.6 Erros tipados

Definir códigos estáveis:

- `project_not_found`;
- `path_outside_project`;
- `file_not_found`;
- `binary_file`;
- `unsupported_encoding`;
- `ripgrep_unavailable`;
- `index_not_ready`;
- `index_corrupted`;
- `parser_unavailable`;
- `parser_timeout`;
- `parser_crash`;
- `parser_circuit_open`;
- `embedding_unavailable`;
- `invalid_query`;
- `result_limit_exceeded`.

CLI e MCP convertem esses erros para seus próprios formatos sem alterar o domínio.

---

## 7. Catálogo das tools

### 7.1 Tools fundamentais

| Tool | Responsabilidade |
|---|---|
| `list_files` | Listar arquivos permitidos do projeto |
| `search_files` | Localizar arquivos por nome, extensão ou caminho |
| `search_text` | Pesquisar texto literal |
| `search_regex` | Pesquisar expressão regular |
| `read_file` | Ler arquivo com limites |
| `read_range` | Ler intervalo de linhas |
| `get_file_outline` | Retornar estrutura de símbolos de um arquivo |
| `find_symbol` | Localizar símbolos pelo nome |
| `find_definition` | Localizar definição provável |
| `find_references` | Localizar referências textuais e estruturais |
| `semantic_search` | Pesquisar trechos por significado |
| `search_code` | Combinar busca textual, estrutural e semântica |
| `build_context` | Construir pacote de contexto para uma LLM |
| `get_repository_map` | Resumir diretórios, arquivos e símbolos principais |
| `index_project` | Criar ou atualizar o índice |
| `get_index_status` | Consultar situação e estatísticas do índice |

### 7.2 Tools administrativas

Estas devem existir na aplicação, mas não precisam ser expostas ao MCP por padrão:

- `clear_index`;
- `repair_index`;
- `validate_index`;
- `migrate_index`;
- `benchmark_index`;
- `doctor`.

### 7.3 Limites comuns

Toda busca deverá aceitar, quando aplicável:

- `max_results`;
- `max_chars`;
- `max_tokens`;
- `context_lines`;
- `include_globs`;
- `exclude_globs`;
- `languages`;
- `case_sensitive`;
- `timeout_seconds`.

---

## 8. API Python pública

A API Python será a interface estável de mais alto nível.

```python
from code_harness import CodeHarness

harness = CodeHarness.open("C:/projetos/nbs")

matches = harness.search_text(
    query="montar_agenda_consultor",
    file_globs=["*.pck", "*.sql"],
    max_results=50,
)

context = harness.build_context(
    query="Como a agenda de consultores é montada?",
    max_tokens=12_000,
)
```

A classe `CodeHarness` será uma fachada. Ela não implementará as buscas diretamente.

Internamente:

```text
CodeHarness
    └── ApplicationContainer
            ├── SearchTextTool
            ├── FindSymbolTool
            ├── SemanticSearchTool
            └── BuildContextTool
```

A API pública deverá devolver objetos Python, não dicionários genéricos.

---

## 9. Interface de terminal

### 9.1 Comandos previstos

```powershell
code-harness init "C:\projetos\nbs"

code-harness index --mode full
code-harness index --mode incremental

code-harness status
code-harness doctor

code-harness files list
code-harness files search "AgendaService"

code-harness search text "montar_agenda_consultor"
code-harness search regex "public\s+void\s+setFilter"
code-harness search symbol "AgendaService"
code-harness search semantic "validação para encerrar uma OS"
code-harness search hybrid "como a agenda distribui serviços"

code-harness outline "src/service/AgendaService.java"
code-harness references "montarAgendaConsultor"

code-harness read "src/service/AgendaService.java"
code-harness read "src/service/AgendaService.java" --lines 120:220

code-harness context "Como a agenda do consultor funciona?" --max-tokens 12000

code-harness mcp serve
```

### 9.2 Formatos de saída

```text
--output text
--output table
--output json
--output jsonl
--output llm
```

`--output llm` poderá apresentar um formato compacto, mas será responsabilidade do renderer da CLI, não da tool.

### 9.3 Códigos de saída

- `0`: sucesso;
- `1`: resultado válido com warnings ou resultado vazio quando configurado como erro;
- `2`: argumento inválido;
- `3`: projeto ou arquivo não encontrado;
- `4`: dependência externa indisponível;
- `5`: falha do índice;
- `6`: falha interna.

---

## 10. Adaptador MCP

### 10.1 Responsabilidade

O adaptador MCP deverá apenas:

1. declarar o schema da tool;
2. validar a entrada do protocolo;
3. criar o DTO da aplicação;
4. executar a tool;
5. serializar o resultado;
6. mapear erros tipados.

Exemplo conceitual:

```python
@mcp.tool()
def search_text(
    query: str,
    include_globs: list[str] | None = None,
    max_results: int = 50,
) -> dict:
    request = SearchTextRequest(
        query=query,
        include_globs=include_globs,
        max_results=max_results,
    )
    result = container.search_text.execute(request)
    return serialize_tool_result(result)
```

### 10.2 O que não pode existir no MCP

Não colocar em `interfaces/mcp`:

- chamadas diretas ao Ripgrep;
- consultas SQL;
- acesso direto a arquivos;
- chunking;
- geração de embeddings;
- ranking;
- regras de ignore;
- decisão de qual busca executar;
- construção de contexto.

### 10.3 Tools MCP iniciais

Expor inicialmente:

- `list_files`;
- `search_files`;
- `search_text`;
- `search_regex`;
- `read_file`;
- `read_range`;
- `get_file_outline`;
- `find_symbol`;
- `find_references`;
- `semantic_search`;
- `search_code`;
- `build_context`;
- `get_repository_map`;
- `get_index_status`.

`index_project` poderá ser exposta opcionalmente, controlada por configuração.

---

## 11. Descoberta de arquivos

### 11.1 Regras de exclusão

Combinar:

1. padrões internos seguros;
2. `.gitignore`;
3. arquivo de configuração do projeto;
4. parâmetros da chamada.

Ignorar por padrão:

```text
.git
.idea
.vscode
.venv
venv
__pycache__
node_modules
target
build
dist
coverage
logs
*.class
*.jar
*.war
*.zip
*.exe
*.dll
*.so
*.png
*.jpg
*.pdf
.env
*.pem
*.key
```

O usuário poderá sobrescrever regras não relacionadas a segurança.

### 11.2 Proteção de caminhos

`PathGuard` deverá:

- resolver caminhos absolutos;
- impedir `../` para fora da raiz;
- detectar escape por symlink;
- validar novamente antes da leitura;
- trabalhar internamente com caminhos normalizados;
- devolver caminhos relativos nos resultados.

### 11.3 Arquivos grandes

Configurar:

- tamanho máximo para indexação;
- tamanho máximo para leitura;
- quantidade máxima de linhas;
- política para arquivos minificados;
- política para código gerado.

Arquivos grandes poderão permanecer disponíveis para busca textual direta sem serem semanticamente indexados.

---

## 12. Pipeline de indexação

### 12.1 Etapas

```text
Descobrir arquivos
        ↓
Aplicar ignore e segurança
        ↓
Detectar tipo e linguagem
        ↓
Calcular hash
        ↓
Comparar com índice atual
        ↓
Ler conteúdo alterado
        ↓
Analisar estrutura em processo isolado
        ↓
Criar chunks
        ↓
Persistir arquivo, símbolos e referências
        ↓
Atualizar índice textual
        ↓
Gerar embeddings necessários
        ↓
Atualizar índice vetorial
        ↓
Registrar relatório da execução
```

### 12.2 Modos

#### Full

Percorre todos os arquivos, mas ainda reutiliza resultados quando:

- hash não mudou;
- versão do parser não mudou;
- configuração de chunk não mudou;
- modelo de embedding não mudou.

`full` significa validar todo o projeto, não recalcular tudo cegamente.

#### Incremental

Processa somente:

- arquivos novos;
- arquivos alterados;
- arquivos removidos;
- arquivos cujo parser mudou;
- arquivos cujo modelo ou estratégia de chunk mudou.

#### Verify

Confere integridade sem necessariamente reprocessar conteúdo.

### 12.3 Identidade do arquivo

Cada arquivo deverá possuir:

- `project_id`;
- caminho relativo normalizado;
- hash SHA-256;
- tamanho;
- timestamp observado;
- linguagem;
- encoding;
- versão do parser;
- estado do parse;
- último erro;
- data da última indexação.

O timestamp não será usado como única fonte de detecção de mudança.

### 12.4 Estado do índice

```text
not_initialized
indexing
ready
ready_with_warnings
failed
repairing
```

Falhas isoladas de parser deverão resultar em `ready_with_warnings`, desde que a busca textual permaneça operacional.

---

## 13. Estratégia de parsing

### 13.1 Registro de parsers

```python
class StructuralAnalyzer(Protocol):
    name: str
    version: str

    def supports(self, language: str) -> bool: ...
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult: ...
```

O registro escolherá o parser com base em:

- linguagem;
- extensão;
- configuração;
- disponibilidade;
- circuit breaker.

### 13.2 Um parse por arquivo

Uma operação estrutural completa deverá:

1. converter a fonte uma única vez;
2. construir uma árvore uma única vez;
3. extrair símbolos;
4. extrair imports;
5. extrair chamadas;
6. criar chunks a partir da mesma árvore.

Não executar um novo parse para cada tipo de informação.

### 13.3 Isolamento obrigatório

O processo principal não deverá carregar diretamente objetos nativos do Tree-sitter.

Arquitetura:

```text
Processo principal
    │
    │ request_id + linguagem + conteúdo
    ▼
Supervisor
    │
    ▼
Worker subprocess
    │
    ├── carrega gramática
    ├── executa parse
    └── devolve resultado serializável
```

### 13.4 Proteções do supervisor

Implementar:

- timeout por arquivo;
- detecção de processo morto;
- reinicialização do worker;
- encerramento com `terminate`, `join` e `kill` quando necessário;
- health check da gramática;
- circuit breaker;
- limite de falhas consecutivas;
- cache de payloads problemáticos;
- shutdown idempotente.

A identidade de um payload problemático deverá incluir:

- linguagem;
- operação;
- caminho;
- hash do conteúdo;
- versão do parser.

Se o arquivo mudar, ele poderá ser testado novamente.

### 13.5 Fallback

Em falha estrutural:

- não criar símbolos inventados no índice principal;
- registrar o erro;
- criar chunks textuais seguros;
- manter busca textual;
- manter leitura direta;
- indicar warning no status.

---

## 14. Estratégia de chunking

### 14.1 Ordem de preferência

1. chunk sintático;
2. chunk por símbolo;
3. chunk por bloco reconhecido;
4. janela textual com sobreposição.

### 14.2 Unidades desejadas

Java:

- classe;
- método;
- construtor;
- interface;
- enum;
- record.

PL/SQL:

- package specification;
- package body;
- procedure;
- function;
- trigger;
- declaração de cursor;
- bloco SQL relevante.

Python:

- módulo;
- classe;
- função;
- método.

### 14.3 Limites

Cada chunk terá:

- tamanho mínimo;
- tamanho alvo;
- tamanho máximo;
- pequena sobreposição quando textual;
- referência opcional ao chunk pai.

Símbolos muito grandes serão subdivididos preservando:

- assinatura;
- comentários de documentação;
- início do bloco;
- contexto pai.

### 14.4 Identidade do chunk

O `chunk_id` deverá ser estável enquanto o conteúdo permanecer igual.

A chave poderá considerar:

```text
project_id
path
symbol_id ou range
chunking_version
content_hash
```

Embeddings serão reutilizados pelo hash do conteúdo do chunk.

---

## 15. Persistência

### 15.1 SQLite como fonte de verdade

Tabelas iniciais:

```text
projects
index_runs
files
chunks
symbols
references
imports
calls
embeddings
parser_failures
settings_snapshot
```

Índices:

```text
files(project_id, path)
chunks(file_id, start_line, end_line)
symbols(project_id, name)
symbols(project_id, qualified_name)
references(target_name)
embeddings(model_id, content_hash)
```

### 15.2 Índice textual

Criar tabela virtual FTS para:

- conteúdo dos chunks;
- nomes de símbolos;
- caminhos;
- assinaturas;
- comentários associados.

A busca textual direta via Ripgrep continuará existindo. O FTS será usado para consultas repetidas e ranking lexical.

### 15.3 Índice vetorial

Definir contrato independente:

```python
class VectorIndex(Protocol):
    def upsert(self, records: Sequence[VectorRecord]) -> None: ...
    def delete(self, ids: Sequence[str]) -> None: ...
    def search(self, vector: Vector, limit: int) -> Sequence[VectorHit]: ...
```

A primeira implementação poderá armazenar vetores localmente e fazer busca por similaridade em memória.

Uma implementação especializada poderá ser adicionada depois sem alterar `semantic_search`.

### 15.4 Migrações

O banco deverá possuir:

- versão explícita de schema;
- migrações ordenadas;
- backup antes de migração destrutiva;
- comando `doctor`;
- comando `repair-index`.

Nunca depender de `CREATE TABLE IF NOT EXISTS` como único mecanismo de evolução.

---

## 16. Embeddings

### 16.1 Contrato

```python
class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Vector]: ...
    def embed_query(self, text: str) -> Vector: ...
```

### 16.2 Informações persistidas

Cada embedding deverá registrar:

- `model_id`;
- dimensão;
- versão do provider;
- hash do conteúdo;
- estratégia de normalização;
- data de geração.

Troca de modelo não poderá misturar vetores incompatíveis.

### 16.3 Provider local e remoto

A arquitetura suportará:

- provider local;
- provider remoto;
- provider fake para testes.

O provider local será a opção padrão recomendada.

### 16.4 Cache

Antes de gerar embedding:

1. calcular hash do chunk;
2. procurar embedding do mesmo modelo;
3. reutilizar quando existir;
4. gerar somente os ausentes;
5. processar em lotes limitados.

---

## 17. Estratégia de busca

### 17.1 Classificação inicial da consulta

Não usar uma LLM para isso na primeira versão.

Aplicar heurísticas determinísticas.

Consulta exata provável:

- contém texto entre aspas;
- parece nome de classe ou método;
- contém underscore em maiúsculas;
- contém mensagem de erro;
- contém caminho ou extensão;
- usuário escolheu modo textual.

Consulta conceitual provável:

- frase longa em linguagem natural;
- pergunta iniciada por “como”, “onde”, “qual rotina”;
- não contém identificador forte;
- usuário escolheu modo semântico.

### 17.2 Geração de candidatos

`search_code` poderá executar em paralelo:

```text
Ripgrep
FTS
Busca de símbolos
Busca de referências
Busca semântica
Busca por caminho
```

Cada mecanismo devolverá candidatos normalizados.

### 17.3 Ranking híbrido

Primeira implementação:

- normalizar scores de cada fonte;
- aplicar Reciprocal Rank Fusion ou combinação ponderada;
- remover duplicatas;
- aplicar boosts;
- limitar resultados por arquivo;
- validar o trecho no disco.

Boosts previstos:

- match exato no nome do símbolo;
- match exato no conteúdo;
- definição antes de referência, dependendo da consulta;
- caminho mencionado pelo usuário;
- linguagem filtrada;
- assinatura correspondente;
- chunk contendo implementação;
- teste associado quando a consulta mencionar teste.

Penalidades:

- arquivo gerado;
- arquivo muito grande;
- documentação distante quando a consulta pede implementação;
- muitos resultados equivalentes do mesmo arquivo;
- resultado desatualizado.

### 17.4 Diversidade

Não devolver apenas dez trechos do mesmo arquivo.

Configurar:

- máximo por arquivo;
- máximo por símbolo;
- diversidade por diretório;
- preservação do resultado de maior score.

### 17.5 Justificativa

Cada resultado híbrido deverá explicar de forma curta:

```text
Definição exata do método solicitado.
Encontrado por nome de símbolo e ocorrência textual.
Trecho semanticamente relacionado à validação de encerramento.
```

A justificativa deverá ser baseada nas estratégias executadas, não inventada por uma LLM.

---

## 18. Construção de contexto

### 18.1 Objetivo

`build_context` não deverá simplesmente devolver os primeiros resultados da busca.

Fluxo:

```text
Interpretar consulta
        ↓
Executar busca híbrida
        ↓
Selecionar resultados principais
        ↓
Expandir definições relevantes
        ↓
Adicionar imports ou chamadas necessárias
        ↓
Remover sobreposição
        ↓
Aplicar orçamento de tokens
        ↓
Ler novamente o conteúdo atual
        ↓
Montar pacote estruturado
```

### 18.2 Expansão controlada

A expansão poderá adicionar:

- definição do símbolo;
- classe ou package pai;
- chamadas diretas;
- imports relevantes;
- teste correspondente;
- configuração relacionada.

Limites:

- profundidade máxima;
- quantidade máxima de expansões;
- orçamento de tokens;
- máximo por arquivo;
- prevenção de ciclos.

### 18.3 Orçamento

Parâmetros:

- `max_tokens`;
- `reserved_tokens`;
- `max_files`;
- `max_snippets`;
- `max_expansion_depth`.

Prioridade:

1. definição principal;
2. implementação;
3. chamadas diretamente relacionadas;
4. testes;
5. documentação;
6. resultados semânticos complementares.

### 18.4 Formato interno

```python
@dataclass(frozen=True)
class ContextBundle:
    query: str
    snippets: tuple[ContextSnippet, ...]
    omitted_results: int
    estimated_tokens: int
    warnings: tuple[str, ...]
```

Renderização MCP, CLI e Python será feita fora do builder.

---

## 19. Configuração

Arquivo sugerido:

```yaml
project:
  root: .
  index_path: .code-harness/index.db

files:
  use_gitignore: true
  max_file_size_bytes: 2000000
  ignored_patterns:
    - "**/target/**"
    - "**/node_modules/**"
    - "**/.git/**"

ripgrep:
  executable: rg
  timeout_seconds: 10

parsers:
  enabled: true
  timeout_seconds: 3
  startup_timeout_seconds: 10
  failure_threshold: 3

semantic:
  enabled: false
  provider: local
  model: null
  batch_size: 16

search:
  max_results: 50
  max_results_per_file: 5

context:
  default_max_tokens: 12000
  max_expansion_depth: 2

mcp:
  expose_index_commands: false
```

Precedência:

```text
argumentos da chamada
    ↓
variáveis de ambiente
    ↓
configuração do projeto
    ↓
configuração global
    ↓
valores padrão
```

---

## 20. Observabilidade

### 20.1 Logs

Logs estruturados com:

- operação;
- projeto;
- duração;
- quantidade de arquivos;
- quantidade de resultados;
- estratégia executada;
- cache hit;
- warning;
- código de erro.

Nunca registrar conteúdo completo de arquivos por padrão.

### 20.2 Estatísticas do índice

`get_index_status` deverá retornar:

- estado;
- quantidade de arquivos;
- quantidade de chunks;
- quantidade de símbolos;
- quantidade de embeddings;
- arquivos ignorados;
- arquivos com warning;
- falhas por parser;
- duração da última execução;
- configuração relevante;
- versão de schema;
- versão dos parsers;
- modelo de embedding ativo.

### 20.3 Diagnóstico

`doctor` verificará:

- acesso ao projeto;
- disponibilidade do Ripgrep;
- banco SQLite;
- migrações;
- integridade do índice;
- processo de parser;
- health check das gramáticas;
- provider de embedding;
- permissões de escrita;
- configuração do MCP.

---

## 21. Segurança

A primeira versão será somente leitura em relação à base de código.

Requisitos:

- impedir acesso fora da raiz;
- impedir escape por symlink;
- não executar código do repositório;
- não executar comandos fornecidos pelo usuário;
- limitar tamanho de entrada e saída;
- não indexar segredos óbvios por padrão;
- respeitar `.gitignore`;
- esconder caminhos absolutos quando não forem necessários;
- não expor configuração sensível no status;
- validar todos os caminhos novamente no momento da leitura;
- usar timeout em subprocessos;
- encerrar processos filhos no shutdown.

O MCP não deverá permitir que o cliente altere arbitrariamente a raiz do projeto depois da inicialização, salvo configuração explícita.

---

## 22. Estratégia de testes

### 22.1 Testes unitários

Cobrir:

- modelos;
- validações;
- normalização de caminhos;
- regras de ignore;
- criação de comandos Ripgrep;
- parsing da saída do Ripgrep;
- ranking;
- deduplicação;
- orçamento de tokens;
- detecção incremental;
- serialização de respostas;
- mapeamento de erros.

### 22.2 Testes de integração

Com componentes reais:

- Ripgrep;
- SQLite;
- FTS;
- sistema de arquivos temporário;
- parser em subprocesso;
- provider fake de embeddings;
- índice vetorial local.

### 22.3 Testes do parser isolado

Simular deterministicamente:

- resposta normal;
- exceção Python;
- timeout;
- processo morto;
- pipe quebrado;
- resposta inválida;
- reinicialização;
- circuit breaker;
- payload problemático;
- arquivo alterado após uma falha;
- shutdown repetido;
- parser desabilitado.

Não provocar segfault real na suíte unitária.

### 22.4 Testes de contrato

Garantir que:

- API Python;
- CLI JSON;
- MCP;

representem o mesmo resultado lógico.

### 22.5 Testes end-to-end

Criar um repositório fixture contendo:

- Java;
- Python;
- package PL/SQL;
- SQL;
- referências cruzadas;
- arquivos ignorados;
- arquivo grande;
- arquivo com encoding diferente;
- sintaxe inválida;
- nomes duplicados;
- testes relacionados.

Cenários:

1. inicializar projeto;
2. indexar;
3. pesquisar texto;
4. localizar símbolo;
5. executar busca semântica;
6. construir contexto;
7. alterar arquivo;
8. executar indexação incremental;
9. confirmar atualização;
10. executar via MCP.

### 22.6 Testes de performance

Medir:

- descoberta de arquivos;
- busca Ripgrep direta;
- busca FTS;
- indexação inicial;
- indexação sem alterações;
- indexação de um único arquivo alterado;
- parse por linguagem;
- geração de embeddings;
- busca vetorial;
- construção de contexto.

Os benchmarks deverão registrar baseline, não falhar inicialmente por limites arbitrários.

### 22.7 Matriz de CI

Executar pelo menos:

- Windows;
- Linux;
- instalação básica;
- instalação com parsers;
- instalação completa;
- semantic search desabilitada;
- parser desabilitado.

---

## 23. Fases de implementação

### Fase 0 — Bootstrap do repositório

**Status: concluída localmente.**

#### Entregas

- [x] estrutura `src`;
- [x] `pyproject.toml`;
- [x] configuração de lint, type-check e testes;
- [x] README inicial;
- [x] ADRs iniciais;
- [x] CI Windows/Linux;
- [x] pacote importável e marcado com `py.typed`;
- [x] comando `code-harness --version`.

#### Critério de saída

```powershell
python -m code_harness --version
code-harness --help
pytest
```

devem funcionar em ambiente limpo.

**Evidência local:** versão e help executados; Ruff e Mypy aprovados; 40 testes
aprovados com 89,11% de cobertura. A matriz remota da CI deverá continuar sendo
confirmada a cada mudança.

---

### Fase 1 — Core de arquivos e busca textual

**Status: concluída localmente.**

#### Entregas

- [x] modelos de domínio;
- [x] contratos;
- [x] `PathGuard`;
- [x] regras de ignore;
- [x] catálogo de arquivos;
- [x] leitura de arquivo;
- [x] leitura por linhas;
- [x] adaptador Ripgrep;
- [x] tool `list_files`;
- [x] tool `search_files`;
- [x] tool `search_text`;
- [x] tool `search_regex`;
- [x] tool `read_file`;
- [x] tool `read_range`;
- [x] API Python;
- [x] CLI;
- [x] saída JSON.

#### Critério de saída

O sistema deverá ser útil via terminal sem índice, parser, embedding ou MCP.

**Evidência local:** listagem, busca literal, busca regex e leitura foram
executadas pela CLI contra o repositório de fixture, sem índice ou componentes
opcionais. A API Python e a CLI usam as mesmas application tools.

---

### Fase 2 — Persistência e indexação incremental

**Status: concluída localmente.**

#### Entregas

- [x] SQLite;
- [x] migrações;
- [x] tabelas de projeto, arquivos e execuções;
- [x] cálculo de hash persistido para indexação;
- [x] detector de mudanças;
- [x] índice FTS;
- [x] estados do índice;
- [x] `index_project`;
- [x] `get_index_status`;
- [x] integrar persistência ao comando `init` existente;
- [x] comando `index`;
- [x] comando `status`;
- [x] comando `doctor`.

#### Critério de saída

Uma segunda indexação sem alterações não deverá reler, parsear ou reprocessar todos os arquivos.

**Evidência local:** teste com leitor instrumentado confirmou que a segunda
indexação incremental classificou todos os arquivos como inalterados, com zero
leituras e zero reprocessamentos. Migrações, rollback, FTS validado no disco,
modificação, remoção, verify, corrupção e fallback lexical possuem cobertura
automatizada.

---

### Fase 3 — Análise estrutural

**Status: concluída localmente.**

#### Entregas

- [x] registro de parsers;
- [x] supervisor de processo nativo;
- [x] worker isolado;
- [x] timeout;
- [x] circuit breaker;
- [x] parser Java;
- [x] parser Python;
- [x] parser PL/SQL;
- [x] extração de símbolos;
- [x] outline;
- [x] chunks sintáticos;
- [x] tool `get_file_outline`;
- [x] tool `find_symbol`;
- [x] tool `find_definition`;
- [x] tool `find_references`.

#### Critério de saída

Uma falha nativa de parser não poderá encerrar:

- CLI;
- indexação;
- servidor;
- busca textual.

**Evidência local:** worker executado por subprocesso sem shell; testes cobrem
resposta normal, timeout, resposta inválida, circuit breaker e shutdown
idempotente. Crash simulado durante a indexação terminou em
`ready_with_warnings`, persistiu chunks textuais e preservou FTS/Ripgrep. Java,
Python e PL/SQL possuem extração e consultas estruturais cobertas. Uma segunda
indexação incremental sem mudanças executou zero parses.

---

### Fase 4 — Busca semântica

**Status: concluída localmente.**

#### Entregas

- [x] contrato de embedding;
- [x] provider local;
- [x] provider fake;
- [x] cache de embeddings;
- [x] armazenamento de vetores;
- [x] busca por similaridade;
- [x] reuso por hash;
- [x] tool `semantic_search`;
- [x] configuração funcional para ativar e desativar.

#### Critério de saída

A instalação básica continuará funcionando sem dependências semânticas.

**Evidência local:** o extra FastEmbed permanece opcional; testes com provider
fake cobrem geração, cache, troca de modelo sem releitura, falha degradada,
validação no arquivo atual, API Python e CLI. O provider real passou a executar
em subprocesso supervisionado, com trust store nativo, cache persistente,
`models prepare`, `doctor --deep` e recuperação de indexação interrompida. O
smoke real ranqueou `src/agenda.py` em primeiro para uma consulta conceitual sem
match literal. A suíte completa passou com 87 testes e 85,37% de cobertura.

---

### Fase 5 — Busca híbrida e contexto

**Status: concluída localmente em 20 de julho de 2026.**

#### Entregas

- [x] classificador heurístico de consulta;
- [x] execução paralela de estratégias;
- [x] normalização de scores;
- [x] ranking híbrido;
- [x] deduplicação;
- [x] diversidade;
- [x] validação no arquivo atual para resultados indexados;
- [x] expansão de contexto;
- [x] orçamento de tokens;
- [x] tool `search_code`;
- [x] tool `build_context`;
- [x] tool `get_repository_map`.

#### Critério de saída

Consultas por identificador deverão priorizar matches exatos, enquanto perguntas conceituais deverão aproveitar resultados semânticos sem perder definições exatas relacionadas.

**Evidência local:** testes unitários cobrem classificação, weighted RRF, boosts,
deduplicação, diversidade e orçamento. Testes de integração confirmam prioridade
de `AgendaService`, combinação de evidências estruturais e semânticas, descarte
de estrutura stale, expansão conservadora, mapa validado e os três comandos da
CLI. A suíte completa passou com 114 testes e 86,98% de cobertura.

---

### Fase 6 — Adaptador MCP

**Status: próxima.**

#### Entregas

- [ ] dependência MCP opcional;
- [ ] servidor;
- [ ] schemas;
- [ ] handlers finos;
- [ ] serializers;
- [ ] mapeamento de erros;
- [ ] teste de contrato funcional;
- [ ] comando `code-harness mcp serve`.

#### Critério de saída

Nenhum módulo fora de `interfaces/mcp` ou `bootstrap` poderá importar o SDK MCP.

Excluir a pasta MCP deverá manter API Python, CLI e testes do core funcionais.

---

### Fase 7 — Hardening

**Status: não iniciada; existem artefatos preliminares.**

#### Entregas

- [ ] benchmarks de indexação e busca;
- [ ] recuperação de índice;
- [ ] comando de reparo;
- [ ] documentação final de troubleshooting;
- [ ] logs estruturados;
- [ ] limites de memória;
- [ ] testes com projetos grandes;
- [ ] smoke test Windows;
- [ ] revisão de segurança;
- [ ] empacotamento de release.

O benchmark lexical e o guia preliminar de troubleshooting existentes são
insumos da fase, mas ainda não satisfazem suas entregas finais.

#### Critério de saída

O sistema deverá operar com degradação segura diante de:

- parser com crash;
- embedding indisponível;
- índice incompleto;
- arquivo removido;
- encoding inválido;
- Ripgrep ausente;
- resultado excessivamente grande.

---

## 24. Sequência recomendada de commits

O checklist representa capacidades acumuladas, não exige que o histórico Git
tenha exatamente um commit por linha.

1. [x] `chore: bootstrap python project and ci`
2. [x] `docs: add architecture decisions`
3. [x] `feat: add domain models and protocols`
4. [x] `feat: add filesystem catalog and path guard`
5. [x] `feat: add source reader and range reader`
6. [x] `feat: add ripgrep text search adapter`
7. [x] `feat: expose lexical tools through python api`
8. [x] `feat: add cli lexical commands`
9. [x] `feat: add sqlite schema and migrations`
10. [x] `feat: add incremental file indexing`
11. [x] `feat: add sqlite fts search`
12. [x] `feat: add native parser supervisor`
13. [x] `feat: add structural analyzer registry`
14. [x] `feat: add java structural analysis`
15. [x] `feat: add python structural analysis`
16. [x] `feat: add plsql structural analysis`
17. [x] `feat: add symbol and reference tools`
18. [x] `feat: add chunking pipeline`
19. [x] `feat: add embedding provider abstraction`
20. [x] `feat: add local semantic index`
21. [x] `feat: add semantic search tool`
22. [x] `feat: add hybrid ranking`
23. [x] `feat: add context builder`
24. [x] `feat: add repository map`
25. [ ] **PRÓXIMO:** `feat: add mcp adapter`
26. [ ] `test: add end-to-end repository scenarios`
27. [ ] `perf: add indexing and search benchmarks`
28. [ ] `docs: finalize user and troubleshooting guides`

Cada commit deverá manter a suíte passando.

---

## 25. Critérios globais de aceite

O projeto completo estará funcional quando todos os itens estiverem marcados.
Itens concluídos abaixo possuem cobertura automatizada ou validação local
reproduzível; critérios dependentes de fases futuras permanecem abertos.

1. [x] a CLI pesquisar uma base sem índice;
2. [x] a API Python usar exatamente as mesmas tools da CLI;
3. [x] o índice incremental ignorar arquivos inalterados;
4. [x] resultados incluírem caminho e linhas;
5. [x] o conteúdo devolvido for relido do arquivo atual;
6. [x] o parser rodar fora do processo principal;
7. [x] uma falha do parser preservar a busca textual;
8. [x] embeddings forem implementados como opcionais;
9. [x] busca híbrida combinar resultados semânticos e exatos;
10. [x] `build_context` respeitar o orçamento;
11. [ ] o MCP apenas adaptar chamadas;
12. [ ] remover o MCP implementado não afetar o core;
13. [ ] a suíte passar em execuções confirmadas de CI no Windows e Linux;
14. [x] caminhos fora da raiz serem rejeitados;
15. [x] nenhum comando da base de código ser executado;
16. [x] o estado do índice refletir warnings parciais;
17. [ ] o índice poder ser reconstruído integralmente;
18. [x] tools retornarem objetos estruturados;
19. [ ] CLI e MCP apresentarem resultados equivalentes;
20. [ ] documentação final descrever arquitetura, configuração e diagnóstico.

**Progresso global comprovado: 12 de 20 critérios.**

---

## 26. Riscos principais

### Tree-sitter e bibliotecas nativas

**Risco:** crash, timeout ou incompatibilidade de gramática.

**Mitigação:** subprocesso isolado, health check, timeout, circuit breaker, cache de falha e fallback textual.

### PL/SQL complexo

**Risco:** gramáticas incompletas ou parsing inconsistente.

**Mitigação:** contrato de parser substituível, extrator dedicado e fallback por boundaries de `procedure`, `function`, `package` e `trigger`.

### Índice desatualizado

**Risco:** devolver código que não corresponde ao disco.

**Mitigação:** hash, validação antes da resposta e reindexação incremental.

### Embeddings inadequados

**Risco:** busca semântica com baixa precisão.

**Mitigação:** provider configurável, benchmark com consultas reais, combinação com lexical e estrutural.

### Resultados grandes

**Risco:** consumir a janela da LLM.

**Mitigação:** limites em todas as tools, deduplicação, diversidade e orçamento central de tokens.

### Acoplamento ao MCP

**Risco:** lógica de aplicação migrar para handlers.

**Mitigação:** regras de importação, testes arquiteturais e handlers mínimos.

### Complexidade prematura

**Risco:** tentar entregar busca semântica, AST, MCP e indexação de uma vez.

**Mitigação:** tornar cada fase utilizável isoladamente e começar pela CLI lexical.

---

## 27. Primeira entrega prática

A primeira entrega deverá terminar com este fluxo:

```powershell
git clone <novo-repositorio>
cd code-harness

python -m venv .venv
.venv\Scripts\activate

pip install -e .

code-harness init "C:\projetos\sample_project"

code-harness files list

code-harness search text "AgendaService"

code-harness search regex "public\s+void\s+setFilter"

code-harness read "src\AgendaService.java" --lines 100:180
```

Neste ponto:

- não haverá MCP;
- não haverá embeddings;
- não haverá dependência de Tree-sitter;
- a arquitetura completa já estará preparada;
- a CLI já será útil para análise real de código.

Somente após essa base estar estável deverão entrar índice estrutural, semântico e MCP.

---

## 28. Decisão final de arquitetura

A arquitetura oficial será:

```text
                        ┌─────────────────┐
                        │      CLI        │
                        ├─────────────────┤
                        │   Python API    │
                        ├─────────────────┤
                        │      MCP        │
                        └────────┬────────┘
                                 │
                     ┌───────────▼───────────┐
                     │  Application Tools    │
                     │                       │
                     │ search / read / index │
                     │ rank / build context  │
                     └───────────┬───────────┘
                                 │
                    ┌────────────▼────────────┐
                    │         Domain          │
                    │ models / protocols      │
                    │ errors / invariants     │
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼────────────────────────┐
         │                       │                        │
┌────────▼────────┐   ┌──────────▼─────────┐   ┌─────────▼─────────┐
│ Files/Ripgrep   │   │ Parsers/Symbols    │   │ Embeddings/Vectors│
└────────┬────────┘   └──────────┬─────────┘   └─────────┬─────────┘
         │                       │                        │
         └───────────────────────┼────────────────────────┘
                                 │
                        ┌────────▼────────┐
                        │ SQLite / Cache  │
                        └─────────────────┘
```

O MCP será uma porta de entrada. As tools e o mecanismo de investigação serão o produto principal.

A etapa seguinte é iniciar a **Fase 6**, expondo as application tools por um
adaptador MCP fino e opcional.
