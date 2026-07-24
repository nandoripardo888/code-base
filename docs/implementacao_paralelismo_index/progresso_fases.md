# Progresso — Indexação Estrutural Paralela

Acompanhar implementação de [`plano_indexacao_paralela.md`](plano_indexacao_paralela.md).

Legenda: `[ ]` pendente · `[~]` em andamento · `[x]` concluída

---

## Resumo

| Fase | Nome | Status |
|------|------|--------|
| 0 | Baseline e instrumentação | [x] |
| 1 | Protocolo persistente | [ ] |
| 2 | Pool paralelo + coordinator + lifecycle | [ ] |
| 3 | Indexação parcial por path/glob | [ ] |

Hardening final (Etapa 8 do plano): [ ]

---

## Fase 0 — Baseline e instrumentação

Status: [x]

- [x] Timings estruturais no fluxo de indexação (`IndexTimings` em `IndexReport`)
- [x] Script/benchmark reproduzível (`scripts/benchmark_repository.py index|lexical`)
- [x] Métricas: descoberta, leitura/hash, init workers, análise, chunks, SQLite, embeddings, total
- [x] Baseline one-shot serial registrado (`baselines/one_shot_serial_100.json`)
- [~] Cenários medidos (quando aplicável): 100 feito · 500 / 1000 / ~4000 pendentes (mesma ferramenta)

Notas:

```
Instrumentação:
- IndexCoordinator acumula fases e percentis por arquivo
- NativeParserSupervisor expõe metrics_snapshot (processos, PIDs, timeouts, spawn/request)
- StructuralAnalyzerRegistry agrega metrics_snapshot dos analyzers filhos

Scripts:
- scripts/benchmark_repository.py index <repo> --wipe-index --output <json>
- scripts/build_java_benchmark_repo.py <dest> --count N

Baseline one-shot 100 Java (Windows, 2026-07-24):
- total_ms ≈ 91045 (~91 s)
- files_per_second ≈ 1.1
- processes_created = 100 (1 processo por arquivo)
- analysis_ms_p50 ≈ 900 ms/arquivo
- worker_init_ms = custo de Popen; startup Python/Tree-sitter ainda entra em analysis_ms no one-shot
- peak_rss_bytes ≈ 32 MB (processo principal)

Para 500/1000/~4000:
  .\.venv\Scripts\python.exe scripts\build_java_benchmark_repo.py %TEMP%\bench-N --count N
  .\.venv\Scripts\python.exe scripts\benchmark_repository.py index %TEMP%\bench-N --mode full --wipe-index --label one-shot-serial-N --output docs\implementacao_paralelismo_index\baselines\one_shot_serial_N.json
```

---

## Fase 1 — Protocolo persistente

Status: [ ]

### Protocolo e worker
- [ ] Criar `native_protocol.py` (NDJSON, request/response, versões)
- [ ] Worker com `--loop` (modo persistente)
- [ ] Manter modo one-shot compatível
- [ ] Cache real de parsers por linguagem no worker

### Slot persistente
- [ ] `_PersistentWorkerSlot`
- [ ] Reader thread de stdout + fila com timeout
- [ ] Drain thread de stderr (buffer limitado)
- [ ] Timeout, restart, morte inesperada, resposta inválida
- [ ] Shutdown gracioso e idempotente

### Supervisor com 1 slot
- [ ] Supervisor usa um slot persistente
- [ ] `ANALYSIS_VERSION` inalterada pela troca de transporte
- [ ] Equivalência estrutural vs one-shot
- [ ] Testes do protocolo (PID, Unicode, stderr, timeout, shutdown)

Notas:

```
```

---

## Fase 2 — Pool paralelo + coordinator + lifecycle

Status: [ ]

### Pool (Etapas 4 do plano)
- [ ] `parser_workers` em Settings (`CODE_HARNESS_PARSER_WORKERS`, 1–8)
- [ ] Pool de slots + fila de disponíveis
- [ ] Circuit breaker e payload cache centralizados no supervisor
- [ ] Testes de concorrência, crash isolado, shutdown
- [ ] Benchmark com 2 / 4 / 8 workers

### IndexCoordinator paralelo (Etapa 5)
- [ ] `_IndexJob` + `_BuildOutcome` imutável
- [ ] Remover mutação compartilhada de warnings
- [ ] Unificar loops new/candidates
- [ ] `ThreadPoolExecutor` limitado (em voo ≤ `parser_workers * 2`)
- [ ] Progresso só na thread principal
- [ ] Ordenação determinística antes do commit
- [ ] Writes SQLite somente na thread do coordenador
- [ ] Bancos equivalentes `workers=1` vs `workers=N`

### Lifecycle (Etapa 6)
- [ ] `shutdown` / contexto no container
- [ ] Shutdown na CLI (`finally`)
- [ ] Shutdown no MCP
- [ ] Sem processos órfãos

Notas:

```
```

---

## Fase 3 — Indexação parcial por path/glob

Status: [ ]

- [ ] `IndexProjectRequest` com `include_globs` / `exclude_globs`
- [ ] Helper `IndexScope`
- [ ] Discovery filtrada + remoção só dentro do escopo
- [ ] Sem filtros = comportamento atual
- [ ] Relatório com escopo parcial
- [ ] CLI `--include` / `--exclude`
- [ ] MCP schema (quando `mcp_expose_index_commands`)
- [ ] Testes de remoção parcial (cenários 1–7 do plano)

Notas:

```
```

---

## Hardening e fechamento (Etapa 8)

Status: [ ]

- [ ] Suíte Windows
- [ ] Suíte Linux (se disponível)
- [ ] Benchmarks finais + memória
- [ ] Validação em projeto real (~4000 arquivos Java)
- [ ] Observabilidade (métricas do §12)
- [ ] Docs: `docs/indexing.md`, ADR 0003, `config.example.yaml`, `README.md`

Notas:

```
```

---

## Histórico

| Data | Fase | O que foi feito |
|------|------|-----------------|
| 2026-07-24 | 0 | Timings estruturais, métricas do supervisor, benchmark index, fixture Java, baseline 100 one-shot |
