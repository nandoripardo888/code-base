# Progresso — Indexação Estrutural Paralela

Acompanhar implementação de [`plano_indexacao_paralela.md`](plano_indexacao_paralela.md).

Legenda: `[ ]` pendente · `[~]` em andamento · `[x]` concluída

---

## Resumo

| Fase | Nome | Status |
|------|------|--------|
| 0 | Baseline e instrumentação | [x] |
| 1 | Protocolo persistente | [x] |
| 2 | Pool paralelo + coordinator + lifecycle | [x] |
| 3 | Indexação parcial por path/glob | [x] |

Hardening final (Etapa 8 do plano): [ ]

---

## Fase 0 — Baseline e instrumentação

Status: [x]

- [x] Timings estruturais no fluxo de indexação (`IndexTimings` em `IndexReport`)
- [x] Script/benchmark reproduzível (`scripts/benchmark_repository.py index|lexical`)
- [x] Métricas: descoberta, leitura/hash, init workers, análise, chunks, SQLite, embeddings, total
- [x] Baseline one-shot serial registrado (`baselines/one_shot_serial_100.json`)
- [~] Cenários medidos: one-shot 100 · persistent 100/500/1000 · ~4000 pendente

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

Status: [x]

### Protocolo e worker
- [x] Criar `native_protocol.py` (NDJSON, request/response, versões)
- [x] Worker com `--loop` (modo persistente)
- [x] Manter modo one-shot compatível
- [x] Cache real de parsers por linguagem no worker

### Slot persistente
- [x] `_PersistentWorkerSlot`
- [x] Reader thread de stdout + fila com timeout
- [x] Drain thread de stderr (buffer limitado)
- [x] Timeout, restart, morte inesperada, resposta inválida
- [x] Shutdown gracioso e idempotente

### Supervisor com 1 slot
- [x] Supervisor usa um slot persistente
- [x] `ANALYSIS_VERSION` inalterada pela troca de transporte (`"4"`)
- [x] Equivalência estrutural vs one-shot
- [x] Testes do protocolo (PID, Unicode, stderr, timeout, shutdown)

Notas:

```
Arquivos:
- src/code_harness/infrastructure/parsers/native_protocol.py
- src/code_harness/infrastructure/parsers/native_worker.py (--loop + cache)
- src/code_harness/infrastructure/parsers/native_supervisor.py (_PersistentWorkerSlot)
- tests/unit/test_native_protocol.py
- tests/unit/test_native_parser_supervisor.py

Versões:
- ANALYSIS_VERSION = "4" (inalterada)
- PROTOCOL_VERSION = 1
- WORKER_IMPLEMENTATION_VERSION = "2"

Benchmark 100 Java — persistent vs one-shot (Windows, 2026-07-24):

| métrica              | one-shot | persistent | delta            |
|----------------------|----------|------------|------------------|
| total_ms             | 91045    | 2232       | ~41x mais rápido |
| files_per_second     | 1.098    | 44.803     | ~41x             |
| processes_created    | 100      | 1          | -99              |
| distinct_pids        | 89       | 1          | reutilização     |
| worker_restarts      | 0        | 0          | —                |
| worker_timeouts      | 0        | 0          | —                |
| parser_cache_hits    | n/a      | 99         | 1 miss + 99 hits |
| analysis_ms_p50      | 900.5    | 1.45       | —                |
| peak_rss_bytes       | ~32 MB   | ~32 MB     | estável          |

Baseline: baselines/persistent_serial_100.json

Escalabilidade persistent serial (Windows, 2026-07-24):

| N    | total_ms | files/s | analysis_ms | read_hash_ms | discovery_ms | commit_ms | analysis % do total |
|------|----------|---------|-------------|--------------|--------------|-----------|---------------------|
| 100  | 2232     | 44.8    | 1219        | 559          | 260          | 135       | ~55%                |
| 500  | 2972     | 168.2   | 1325        | 902          | 478          | 221       | ~45%                |
| 1000 | 5390     | 185.5   | 1720        | 1924         | 988          | 689       | ~32%                |

Observações (Fase 1 → entrada da Fase 2):
- 1 processo, 0 restarts/timeouts em todos os tamanhos; cache hits = N-1.
- analysis_ms_p50 fica ~1 ms; o tempo de análise cresce pouco com N (overhead de IPC amortizado).
- Em N=1000, read_hash (~1.9 s) já supera analysis (~1.7 s); discovery+commit (~1.7 s) também pesam.
- Paralelizável na Fase 2 (read+hash+analyze+chunks) ≈ 68% do total em N=1000;
  serial restante (discovery + worker_init + commit) ≈ 31%.
- Baselines: persistent_serial_500.json, persistent_serial_1000.json
```

---

## Fase 2 — Pool paralelo + coordinator + lifecycle

Status: [x]

### Pool (Etapas 4 do plano)
- [x] `parser_workers` em Settings (`CODE_HARNESS_PARSER_WORKERS`, 1–8)
- [x] Pool de slots + fila de disponíveis
- [x] Circuit breaker e payload cache centralizados no supervisor
- [x] Testes de concorrência, crash isolado, shutdown
- [x] Benchmark com 2 / 4 / 8 workers

### IndexCoordinator paralelo (Etapa 5)
- [x] `_IndexJob` + `_BuildOutcome` imutável
- [x] Remover mutação compartilhada de warnings
- [x] Unificar loops new/candidates
- [x] `ThreadPoolExecutor` limitado (em voo ≤ `parser_workers * 2`)
- [x] Progresso só na thread principal
- [x] Ordenação determinística antes do commit
- [x] Writes SQLite somente na thread do coordenador
- [x] Bancos equivalentes `workers=1` vs `workers=N`

### Lifecycle (Etapa 6)
- [x] `shutdown` / contexto no container
- [x] Shutdown na CLI (`finally` / `call_on_close`)
- [x] Shutdown no MCP (lifespan + `atexit` de proteção)
- [x] Sem processos órfãos

Notas:

```
Arquivos principais:
- native_supervisor.py (pool de _PersistentWorkerSlot + fila)
- index_coordinator.py (_IndexJob/_BuildOutcome + ThreadPoolExecutor)
- settings.py (parser_workers, failure_window)
- container.py / cli / mcp / CodeHarness (shutdown idempotente)
- docs/adr/0003-native-parser-isolation.md (atualizado: pool persistente + config)

Circuit breaker:
- chave = (language, operation, path, content_hash, analysis_version)
- abre só com N payloads distintos na janela
- erros analysis_error (JSON válido) não abrem circuito

Config:
- CODE_HARNESS_PARSER_WORKERS
- default = min(4, cpu_count)
- limites = 1–8

Validação:
- 216 testes passando
- ruff + mypy limpos
- equivalência lógica workers=1/2/4
- progresso monotônico single-threaded
- SQLite writes só na thread do coordenador

Benchmark 100 Java — série (warmup descartado + 5 medições; Windows, 2026-07-24):

| workers | total_ms median | min | max | p95 | files/s median | processes | restarts/timeouts |
|---------|-----------------|-----|-----|-----|----------------|-----------|-------------------|
| 1       | 1097            |1067 |1107 |1105 | 91.2           | 1         | 0 / 0             |
| 2       | 1157            |1089 |1225 |1213 | 86.4           | 2         | 0 / 0             |
| 4       | 1281            |1245 |1917 |1799 | 78.1           | 4         | 0 / 0             |
| 8       | 2157            |2053 |2661 |2573 | 46.4           | 8         | 0 / 0             |

Conclusão no fixture N=100:
- workers=1 é o mais rápido e estável (menor mediana e menor dispersão).
- 2/4/8 não amortizam o custo de spawn + carga de gramática Java por processo.
- O run único anterior (que apontava workers=4) era ruído de cache/AV/warmup.
- Default permanece 4 por prudência em repositórios maiores (memória/estabilidade);
  8 continua disponível via env. Confirmar em N=500/1000/~4000 no hardening.

Baselines série: persistent_pool_{1,2,4,8}_100_series.json
Script: benchmark_repository.py index --repeats 5 --discard-first
```

---

## Fase 3 — Indexação parcial por path/glob

Status: [x]

- [x] `IndexProjectRequest` com `include_globs` / `exclude_globs`
- [x] Helper `IndexScope`
- [x] Discovery filtrada + remoção só dentro do escopo
- [x] Sem filtros = comportamento atual
- [x] Relatório com escopo parcial
- [x] CLI `--include` / `--exclude`
- [x] MCP schema (quando `mcp_expose_index_commands`)
- [x] Testes de remoção parcial (cenários 1–7 do plano)

Notas:

```
Arquivos:
- application/indexing/index_scope.py (IndexScope)
- application/dto/requests.py (IndexProjectRequest.partial)
- application/indexing/index_coordinator.py (discovery + stored filtrados)
- domain/models/index_report.py (partial, globs, scoped_*, preserved_*)
- tools/index_project.py, CLI, API Python, MCP handlers
- tests/unit/test_partial_indexing.py
- docs/indexing.md (semântica parcial)

Semântica:
- include/exclude são filtros temporários da execução (não misturar com .gitignore)
- remoção só para stored paths com scope.matches(path)
- path excluído explicitamente é fora de escopo → entrada preservada
- VERIFY parcial não altera a tabela files
- ANALYSIS_VERSION permanece "4"

Validação:
- 229 testes passando
- ruff + mypy limpos
- workers 1 e 4 no cenário include
- writes SQLite só na thread do coordenador
- VERIFY não aplica updates nem removals em commit_files
- CLI manual: full indexa 4 arquivos; --include src remove só A.java e preserva docs/guide.md ausente
```

---

## Hardening e fechamento (Etapa 8)

Status: [ ]

- [ ] Suíte Windows
- [ ] Suíte Linux (se disponível)
- [ ] Benchmarks finais + memória
- [ ] Série pool em 500 (1/2/4/8) e 1000 (1/2/4/8)
- [ ] Validação em projeto real (~4000 arquivos Java; 1/4/8)
- [ ] Observabilidade (métricas do §12)
- [x] ADR 0003 atualizado (pool persistente + `CODE_HARNESS_PARSER_WORKERS`)
- [ ] Docs: `docs/indexing.md`, `config.example.yaml`, `README.md`

Notas:

```
Em N≈4000 o custo inicial de 8 workers deve amortizar melhor que em N=100.
Mesmo se 8 vencer no projeto real, manter default=4; 8 fica opt-in via env.
```

---

## Histórico

| Data | Fase | O que foi feito |
|------|------|-----------------|
| 2026-07-24 | 0 | Timings estruturais, métricas do supervisor, benchmark index, fixture Java, baseline 100 one-shot |
| 2026-07-24 | 1 | Protocolo NDJSON, worker `--loop`, cache Language/Parser, slot persistente (timeout/stderr/restart), equivalência estrutural, baseline persistent 100 (~41x vs one-shot) |
| 2026-07-24 | 1 | Benchmarks persistent serial 500 (2.97 s, 168 files/s) e 1000 (5.39 s, 186 files/s); read_hash passa a dominar análise em N=1000 |
| 2026-07-24 | 2 | Pool 1–8 workers, coordinator paralelo, circuit breaker por payload distinto, lifecycle/shutdown |
| 2026-07-24 | 2 | Série warmup+5 em N=100: mediana favorece workers=1; ADR 0003 atualizado; default 4 mantido para N maior |
| 2026-07-24 | 3 | IndexScope + include/exclude em request/CLI/API/MCP; remoção só no escopo; relatório parcial; docs/indexing.md |
