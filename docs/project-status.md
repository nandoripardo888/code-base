# Status do projeto

Última atualização: **22 de julho de 2026**.

Este documento é a fotografia operacional do `code-harness`. O
[plano de implementação](../plano-implementacao.md) continua sendo a referência
para escopo e decisões futuras; este status registra somente o que já existe e
foi verificado no repositório.

## Resumo executivo

O projeto concluiu localmente as **Fases 0, 1, 2, 3, 4, 5 e 6**. O produto oferece
pela CLI, pela API Python e pelo adaptador MCP opcional busca lexical direta,
persistência SQLite, FTS, indexação incremental, análise estrutural isolada,
busca semântica local opcional, ranking híbrido e contexto com orçamento.

O ponto exato da implementação é:

- itens 1 a 25 da sequência recomendada concluídos;
- suíte local com testes unitários/contrato verdes após a rodada de melhorias MCP
  (capabilities, degradação estrutural, outline compacto, assinaturas canônicas,
  expansão lexical, contexto por query, paginação e truncamento);
- próximo item: **26 — cenários end-to-end**;
- próxima fase: **Fase 7 — Hardening**.

## Progresso por fase

| Fase | Estado | Entregue | Próximo critério |
|---|---|---|---|
| 0 — Bootstrap | Concluída localmente | Pacote, configuração, documentação, CI, versão e quality gates | Confirmar a matriz CI remota em Windows e Linux |
| 1 — Core lexical | Concluída localmente | Descoberta, leitura, busca textual/regex, API Python, CLI e JSON | Preservar o fallback durante as fases seguintes |
| 2 — Persistência | Concluída localmente | SQLite versionado, FTS5, hashes, incremental/full/verify, status e doctor | Preservar o critério incremental durante a Fase 3 |
| 3 — Estrutural | Concluída localmente | Worker isolado, supervisor, circuit breaker, Java/Python/PLSQL, símbolos, referências, chunks e tools | Preservar degradação segura nas fases seguintes |
| 4 — Semântica | Concluída localmente | FastEmbed opcional e isolado, cache persistente, preparação, diagnóstico profundo, SQLite vetorial, API e CLI | Preservar degradação segura na busca híbrida |
| 5 — Híbrida/contexto | Concluída localmente | Classificação, execução paralela, ranking, diversidade, contexto por orçamento e mapa | Preservar degradação segura no adaptador MCP |
| 6 — MCP | Concluída localmente | Extra opcional, handlers finos, serializers compartilhados, `mcp serve` e contratos | Preservar isolamento do SDK MCP |
| 7 — Hardening | Próxima | Benchmark lexical e troubleshooting preliminares | Validar recuperação, limites e projetos grandes |

Artefatos preparatórios de fases futuras não significam que os respectivos
critérios de saída estejam satisfeitos.

## Capacidades implementadas

### Arquitetura e domínio

- pacote Python em layout `src`;
- dependências direcionadas para dentro e verificadas por teste arquitetural;
- modelos imutáveis para projeto, arquivo, localização, trecho e resultado;
- erros tipados e códigos estáveis;
- protocolos de catálogo, leitura e busca textual;
- composição manual das dependências no bootstrap;
- pacote público marcado como tipado por `py.typed`.

### Arquivos e segurança

- descoberta de arquivos com exclusões seguras e suporte a `.gitignore`;
- filtros de inclusão e exclusão por glob;
- detecção de linguagem por extensão;
- rejeição de travessia de diretório e symlinks fora da raiz;
- rejeição de arquivos binários, grandes ou com encoding não suportado;
- leitura integral e por intervalo inclusivo de linhas;
- limites de caracteres e linhas com indicação de truncamento;
- hash SHA-256 calculado a partir do conteúdo atual.

### Busca lexical

- busca de arquivos por nome e caminho;
- busca literal com Ripgrep;
- busca por expressão regular com Ripgrep;
- execução do Ripgrep com lista de argumentos, sem shell;
- parsing, normalização, limite e deduplicação dos resultados;
- releitura do trecho no arquivo atual antes de devolvê-lo;
- resultados com caminho, linhas, hash, score, motivo e termos encontrados.

### Persistência e indexação

- SQLite local com versão explícita e migrações transacionais;
- tabelas de projetos, arquivos e execuções de índice;
- FTS5 por arquivo como índice derivado;
- detecção de arquivos novos, alterados, removidos e inalterados;
- modos `incremental`, `full` e `verify`;
- segunda indexação incremental sem reler arquivos inalterados;
- estados `not_initialized`, `indexing`, `ready`, `ready_with_warnings` e `failed`;
- validação de candidatos FTS no arquivo atual e fallback automático para Ripgrep;
- diagnóstico de projeto, escrita, Ripgrep, integridade SQLite, schema e FTS5.

### Análise estrutural

- worker descartável em subprocesso com protocolo JSON;
- timeout, detecção de crash e resposta inválida, encerramento forçado,
  reinicialização, cache de payload problemático e circuit breaker por linguagem;
- Tree-sitter opcional para Java e Python, carregado somente no worker;
- extratores isolados compatíveis para Java, Python e PL/SQL;
- símbolos, referências e chunks persistidos atomicamente por arquivo;
- invalidação incremental por hash, versão do parser e versão do chunking;
- fallback por janelas textuais quando parser estiver desabilitado ou falhar;
- remoção de estrutura antiga quando um arquivo alterado não puder ser parseado;
- validação de hash contra o arquivo atual antes de devolver ranges estruturais;
- `get_file_outline`, `find_symbol`, `find_definition` e `find_references`;
- referências textuais via Ripgrep quando o índice estrutural não estiver pronto;
- status com contadores estruturais e diagnóstico de saúde do worker.

### Interfaces

- API Python `CodeHarness`;
- CLI `code-harness` e `python -m code_harness`;
- registro e seleção explícita de projeto;
- comandos `init`, `index`, `status`, `doctor`, `files list`, `files search`,
  `search text`, `search regex`, `search hybrid`, `context`, `map`, `read` e
  `mcp serve`;
- renderização em `text`, `table`, `json`, `jsonl` e `llm`;
- envelopes estruturados de sucesso e erro;
- códigos de saída estáveis.

### Busca semântica

- contratos de provider e índice vetorial independentes da infraestrutura;
- provider FastEmbed lazy no extra opcional `semantic` e provider fake determinístico;
- carregamento, download e inferência isolados em subprocesso com timeout e contenção de crash;
- trust store nativo, CA corporativa configurável e cache permanente do modelo;
- modelo multilíngue local configurável, batching e janelas com média L2;
- schema SQLite v4 com cache por provider, modelo, estratégia e hash, PID da execução
  e recuperação de indexações interrompidas;
- vetores float32, similaridade cosseno em memória e vínculos com chunks vivos;
- reuso incremental sem releitura, inclusive após troca de modelo;
- validação de hash e releitura do arquivo antes de devolver resultados;
- `semantic_search` na API Python e em `code-harness search semantic`;
- `models prepare`, `doctor --deep` e smoke test real do modelo multilíngue;
- falhas de dependência, download ou inferência degradam para `ready_with_warnings`.

### Busca híbrida e contexto

- classificação heurística determinística para consultas exatas, conceituais e mistas;
- execução paralela de busca lexical, símbolos, referências, caminhos e semântica opcional;
- weighted Reciprocal Rank Fusion, boosts rastreáveis, deduplicação e diversidade;
- evidências estruturadas com rank, score normalizado e contribuição por estratégia;
- releitura e validação de hash central antes de devolver resultados híbridos;
- degradação para estratégias disponíveis quando índice ou embeddings faltam;
- expansão conservadora de definições, símbolos pais e referências diretas;
- prevenção de ciclos, limites de arquivos/snippets e orçamento local conservador;
- mapa hierárquico do catálogo atual com símbolos enriquecidos somente após validação;
- `search_code`, `build_context` e `get_repository_map` na API Python e CLI.

### Adaptador MCP

- extra opcional `mcp` com o SDK oficial;
- servidor FastMCP em stdio via `code-harness mcp serve`;
- handlers finos que só traduzem protocolo → DTO → application tool → JSON;
- serializers compartilhados com a CLI (`to_primitive` / envelopes de erro);
- tools iniciais da seção 10.3 do plano; `index_project` opcional via
  `CODE_HARNESS_MCP_EXPOSE_INDEX`;
- SDK restrito a `interfaces/mcp`, verificado por teste arquitetural;
- import lazy do adaptador na CLI para o core continuar sem o extra.

## Baseline de validação

Validação local executada no Windows com Python 3.12 em 21 de julho de 2026:

| Gate | Resultado |
|---|---|
| `ruff check .` | Passou |
| `ruff format --check .` | Passou — 139 arquivos formatados |
| `mypy` | Passou — 108 arquivos sem problemas |
| `pytest --cov --cov-report=term-missing` | Passou — 126 testes |
| Cobertura total | 86,28% |
| Cobertura mínima configurada | 85% |
| `python -m code_harness --version` | Passou — versão 0.1.0 |
| `code-harness --help` | Passou |
| Fluxos lexical e incremental pela CLI/API | Passaram |
| Contratos MCP e isolamento do SDK | Passaram |

A workflow de CI está configurada para Ubuntu e Windows. Esta fotografia não
afirma o estado de uma execução remota específica; registra apenas a validação
local e a existência da matriz.

## Critérios globais já atendidos

Dos 20 critérios globais do plano, 15 estão comprovadamente atendidos:

1. a CLI pesquisa uma base sem índice;
2. API Python e CLI usam as mesmas application tools;
3. resultados incluem caminho e linhas;
4. o conteúdo devolvido é relido do arquivo atual;
5. caminhos fora da raiz são rejeitados;
6. nenhum código do repositório analisado é executado;
7. as tools devolvem objetos estruturados.
8. o índice incremental ignora arquivos inalterados;
9. o estado do índice reflete warnings parciais.
10. o parser roda fora do processo principal;
11. timeout, crash ou indisponibilidade do parser preservam a busca textual.
12. embeddings são opcionais e sua indisponibilidade preserva o core.
13. a busca híbrida combina resultados semânticos e exatos.
14. `build_context` respeita o orçamento local estimado.
15. o MCP apenas adapta chamadas; remover o adaptador não afeta o core; CLI e MCP
    apresentam resultados equivalentes para as tools cobertas.

Os demais critérios dependem de CI remota, repair/rebuild ou documentação final.

## Funcionalidades ainda não implementadas

- comando dedicado de repair e hardening para grandes projetos;
- documentação final consolidada de troubleshooting e release.

## Próximo marco: Fase 7

A Fase 7 deverá começar pelo item 26 do plano: cenários end-to-end, seguido de
benchmarks, recuperação de índice e limites para projetos grandes.

## Regra de atualização

Ao concluir uma entrega:

1. atualizar o checklist da fase no plano;
2. registrar neste documento a capacidade e sua evidência;
3. executar lint, format, type-check e testes com cobertura;
4. atualizar o baseline somente com resultados efetivamente executados;
5. mover o marco atual apenas quando o critério de saída da fase estiver
   comprovado.
