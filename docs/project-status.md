# Status do projeto

Última atualização: **20 de julho de 2026**.

Este documento é a fotografia operacional do `code-harness`. O
[plano de implementação](../plano-implementacao.md) continua sendo a referência
para escopo e decisões futuras; este status registra somente o que já existe e
foi verificado no repositório.

## Resumo executivo

O projeto concluiu localmente as **Fases 0, 1, 2, 3, 4 e 5**. O produto oferece pela
CLI e pela API Python busca lexical direta, persistência SQLite, FTS, indexação
incremental, análise estrutural isolada, busca semântica local opcional, ranking
híbrido e contexto com orçamento. MCP ainda não foi implementado.

O ponto exato da implementação é:

- itens 1 a 24 da sequência recomendada concluídos;
- suíte local com 114 testes passando e 86,98% de cobertura;
- próximo item: **25 — adaptador MCP**;
- próxima fase: **Fase 6 — Adaptador MCP**.

## Progresso por fase

| Fase | Estado | Entregue | Próximo critério |
|---|---|---|---|
| 0 — Bootstrap | Concluída localmente | Pacote, configuração, documentação, CI, versão e quality gates | Confirmar a matriz CI remota em Windows e Linux |
| 1 — Core lexical | Concluída localmente | Descoberta, leitura, busca textual/regex, API Python, CLI e JSON | Preservar o fallback durante as fases seguintes |
| 2 — Persistência | Concluída localmente | SQLite versionado, FTS5, hashes, incremental/full/verify, status e doctor | Preservar o critério incremental durante a Fase 3 |
| 3 — Estrutural | Concluída localmente | Worker isolado, supervisor, circuit breaker, Java/Python/PLSQL, símbolos, referências, chunks e tools | Preservar degradação segura nas fases seguintes |
| 4 — Semântica | Concluída localmente | FastEmbed opcional e isolado, cache persistente, preparação, diagnóstico profundo, SQLite vetorial, API e CLI | Preservar degradação segura na busca híbrida |
| 5 — Híbrida/contexto | Concluída localmente | Classificação, execução paralela, ranking, diversidade, contexto por orçamento e mapa | Preservar degradação segura no adaptador MCP |
| 6 — MCP | Próxima | ADR e teste arquitetural preventivo | Criar adaptador opcional e handlers finos |
| 7 — Hardening | Não iniciada | Benchmark lexical e troubleshooting preliminares | Validar recuperação, limites e projetos grandes |

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
  `search text`, `search regex`, `search hybrid`, `context`, `map` e `read`;
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

## Baseline de validação

Validação local executada no Windows com Python 3.12 em 20 de julho de 2026:

| Gate | Resultado |
|---|---|
| `ruff check .` | Passou |
| `ruff format --check .` | Passou — 132 arquivos formatados |
| `mypy` | Passou — 103 arquivos sem problemas |
| `pytest --cov --cov-report=term-missing` | Passou — 114 testes |
| Cobertura total | 86,98% |
| Cobertura mínima configurada | 85% |
| `python -m code_harness --version` | Passou — versão 0.1.0 |
| `code-harness --help` | Passou |
| Fluxos lexical e incremental pela CLI/API | Passaram |

A workflow de CI está configurada para Ubuntu e Windows. Esta fotografia não
afirma o estado de uma execução remota específica; registra apenas a validação
local e a existência da matriz.

## Critérios globais já atendidos

Dos 20 critérios globais do plano, 14 estão comprovadamente atendidos:

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

Os demais critérios dependem de MCP, CI remota ou documentação final.

## Funcionalidades ainda não implementadas

- servidor e adaptador MCP;
- comando dedicado de repair e hardening para grandes projetos.

## Próximo marco: Fase 6

A Fase 6 deverá começar pelo item 25 do plano: adaptador MCP opcional. Os handlers
deverão apenas traduzir chamadas para as application tools já validadas, sem
mover busca, ranking, acesso a arquivos ou construção de contexto para a camada
de interface.

## Regra de atualização

Ao concluir uma entrega:

1. atualizar o checklist da fase no plano;
2. registrar neste documento a capacidade e sua evidência;
3. executar lint, format, type-check e testes com cobertura;
4. atualizar o baseline somente com resultados efetivamente executados;
5. mover o marco atual apenas quando o critério de saída da fase estiver
   comprovado.
