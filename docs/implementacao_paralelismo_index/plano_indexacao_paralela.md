# Plano de Implementação — Indexação Estrutural Paralela

## 1. Objetivo

Reduzir significativamente o tempo de indexação estrutural de projetos grandes, principalmente bases Java no Windows, eliminando dois gargalos atuais

1. criação de um subprocesso de parser para cada arquivo;
2. leitura, hashing, análise e construção de chunks executados sequencialmente.

A solução deverá preservar

 isolamento do Tree-sitter em subprocessos;
 timeout por arquivo;
 encerramento forçado de processos travados;
 circuit breaker;
 cache de payloads problemáticos;
 fallback textual por arquivo;
 persistência SQLite serializada;
 resultado estrutural equivalente ao atual;
 indexação incremental praticamente sem custo quando nada mudou.

---

# 2. Diagnóstico validado

## 2.1 Supervisor atual

O `NativeParserSupervisor` executa, para cada arquivo

```text
Popen
  ↓
envio de todo o JSON por stdin
  ↓
communicate()
  ↓
espera o processo terminar
  ↓
parse da resposta JSON
```

Portanto, cada arquivo Java ou Python paga novamente por

 inicialização do Python;
 importação do módulo worker;
 importação do Tree-sitter;
 importação da gramática;
 construção do objeto `Language`;
 construção do `Parser`;
 encerramento do processo.

O worker atual lê todo o `stdin` até EOF, responde uma vez e termina.

## 2.2 Coordenador atual

O `IndexCoordinator` possui dois loops sequenciais

 arquivos novos;
 arquivos candidatos a alteração.

Em ambos, ele executa sequencialmente

```text
reader.load
  ↓
comparação de hash
  ↓
analyzer.analyze
  ↓
build_chunks
  ↓
validação
  ↓
append em updates
```

## 2.3 Persistência

A persistência já está concentrada no processo principal

```text
store.commit_files(...)
store.commit_embeddings(...)
store.complete_run(...)
```

Essa parte deve continuar serializada.

---

# 3. Arquitetura-alvo

```text
IndexCoordinator
       │
       │ ThreadPoolExecutor limitado
       ▼
┌───────────────────────────────────┐
│ tarefas de leitura e análise      │
│                                   │
│ arquivo A ──▶ Parser Pool         │
│ arquivo B ──▶ Parser Pool         │
│ arquivo C ──▶ Parser Pool         │
└────────────────┬──────────────────┘
                 │
                 ▼
        NativeParserSupervisor
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
     Worker 1 Worker 2 Worker 3
     persist. persist. persist.
                 │
                 ▼
         resultados em memória
                 │
         ordenação por path
                 │
                 ▼
        commit SQLite serial
```

O `NativeParserSupervisor` continuará sendo o `StructuralAnalyzer` exposto ao restante da aplicação.

Internamente, passará a administrar vários slots de workers persistentes.

---

# 4. Decisões arquiteturais

## 4.1 Apenas um componente controla o pool

Não criar vários `NativeParserSupervisor` completos, cada um com circuit breaker próprio.

Separar internamente

```text
NativeParserSupervisor
    ├── estado global de circuit breaker
    ├── payloads problemáticos
    ├── fila de slots disponíveis
    └── _PersistentWorkerSlot[]
```

Cada `_PersistentWorkerSlot` será responsável somente por

 iniciar um processo;
 enviar uma requisição;
 receber a resposta correspondente;
 aplicar timeout;
 reiniciar seu próprio processo;
 armazenar o trecho recente de `stderr`;
 encerrar seu processo.

O supervisor ficará responsável por

 escolher o slot;
 controlar falhas por linguagem;
 controlar payloads problemáticos;
 abrir e fechar circuitos;
 expor `analyze`, `health_check` e `shutdown`.

## 4.2 Concorrência via threads no processo principal

Usar `ThreadPoolExecutor`.

Não usar `ProcessPoolExecutor`, pois o trabalho nativo já está isolado nos subprocessos dos parsers.

As threads serão responsáveis por

 leitura dos arquivos;
 hashing;
 espera pela resposta dos workers;
 construção e validação dos chunks.

## 4.3 Persistência continua serial

Nenhuma thread de análise poderá escrever no SQLite.

Somente a thread do coordenador executará

```python
store.commit_files(...)
store.commit_embeddings(...)
store.complete_run(...)
```

## 4.4 Workers iniciados sob demanda

A criação do container não deverá iniciar todos os processos.

Cada slot inicia seu worker somente quando receber sua primeira análise.

Consequência

 indexação incremental sem alterações não inicia Tree-sitter;
 comandos de busca não iniciam Tree-sitter;
 o MCP não mantém processos nativos desnecessariamente sem que alguma análise seja executada.

## 4.5 Quantidade de workers

Adicionar

```text
CODE_HARNESS_PARSER_WORKERS
```

Default

```python
min(4, os.cpu_count() or 1)
```

Limites

```text
mínimo 1
máximo 8
```

Validação deverá ocorrer em `Settings.__post_init__`.

Não usar quantidade ilimitada baseada somente no número de CPUs, pois cada processo carrega

 runtime Python;
 Tree-sitter;
 gramática JavaPython;
 conteúdo do arquivo analisado;
 árvore sintática;
 resultado estrutural.

---

# 5. Fase 0 — Baseline e instrumentação

Antes de alterar o transporte, criar um benchmark reproduzível.

## 5.1 Script

Criar ou estender

```text
scriptsbenchmark_repository.py
```

Registrar separadamente

 descoberta;
 leitura e hashing;
 inicialização dos workers;
 análise estrutural;
 construção de chunks;
 persistência SQLite;
 geração de embeddings;
 duração total.

## 5.2 Cenários

Executar com

```text
100 arquivos Java
500 arquivos Java
1.000 arquivos Java
projeto real com aproximadamente 4.000 arquivos Java
```

Medir

1. implementação atual, one-shot e serial;
2. worker persistente, um worker;
3. worker persistente, dois workers;
4. worker persistente, quatro workers;
5. worker persistente, oito workers.

## 5.3 Métricas adicionais

Registrar

 arquivos por segundo;
 processos criados;
 PIDs distintos;
 quantidade de restarts;
 quantidade de timeouts;
 pico aproximado de memória;
 tamanho médio dos arquivos;
 tempo médio e percentis por arquivo.

## 5.4 Critério

Não definir inicialmente “dez vezes mais rápido” como requisito rígido.

Usar como metas

 worker persistente com um slot deve eliminar a maior parte do custo de startup;
 quatro workers devem superar claramente um worker em cold index;
 workers adicionais não podem degradar significativamente o desempenho;
 a indexação incremental sem mudanças deve permanecer praticamente igual.

---

# 6. Fase 1 — Protocolo persistente

## 6.1 Arquivos

Alterar

```text
srccode_harnessinfrastructureparsersnative_worker.py
srccode_harnessinfrastructureparsersnative_supervisor.py
```

Criar, preferencialmente

```text
srccode_harnessinfrastructureparsersnative_protocol.py
```

## 6.2 Protocolo

Usar NDJSON

 uma requisição JSON por linha;
 uma resposta JSON por linha;
 nenhuma mensagem multilinha fora do JSON.

Formato da requisição

```json
{
  protocol_version 1,
  request_id worker-2-000001,
  operation analyze,
  payload {
    path srcAgendaService.java,
    language java,
    content ...,
    content_hash ...
  }
}
```

Formato de sucesso

```json
{
  protocol_version 1,
  request_id worker-2-000001,
  ok true,
  result {
    parser_name tree-sitter-java,
    parser_version 4,
    state ready,
    symbols [],
    references [],
    chunks [],
    warnings []
  }
}
```

Formato de erro tratável

```json
{
  protocol_version 1,
  request_id worker-2-000001,
  ok false,
  error {
    kind analysis_error,
    type ValueError,
    message ...
  }
}
```

Operações

```text
health
analyze
shutdown
```

## 6.3 Compatibilidade one-shot

Manter o comportamento one-shot para

 testes;
 diagnóstico;
 rollback;
 execução manual do módulo.

Sugestão

```text
python -m code_harness.infrastructure.parsers.native_worker
```

continua executando uma única requisição.

O supervisor passa a usar

```text
python -u -m code_harness.infrastructure.parsers.native_worker --loop
```

A flag `-u` garante streams sem buffering adicional.

## 6.4 Loop do worker

No modo persistente

```python
for line in sys.stdin
    if not line.strip()
        continue

    request = json.loads(line)
    response = process_request(request)

    sys.stdout.write(json.dumps(response, ensure_ascii=True) + n)
    sys.stdout.flush()

    if request[operation] == shutdown
        break
```

Nunca usar `sys.stdin.read()` no modo persistente.

## 6.5 Cache real de parsers

Apenas manter o processo vivo não é suficiente.

Atualmente, a análise Tree-sitter ainda constrói `Language` e `Parser` dentro da função de análise.

Criar cache no worker

```python
_PARSER_CACHE dict[str, object] = {}
```

Exemplo conceitual

```python
def _get_tree_sitter_parser(language str)
    parser = _PARSER_CACHE.get(language)
    if parser is not None
        return parser

    tree_sitter = importlib.import_module(tree_sitter)
    grammar = importlib.import_module(ftree_sitter_{language})

    language_object = tree_sitter.Language(grammar.language())
    parser = tree_sitter.Parser(language_object)

    _PARSER_CACHE[language] = parser
    return parser
```

Cada worker processará somente uma requisição por vez, portanto o parser do slot não precisa ser compartilhado entre threads.

## 6.6 Leitura assíncrona de stdout

Não fazer diretamente

```python
process.stdout.readline()
```

e esperar conseguir aplicar timeout portátil.

No Windows, `select` não funciona de forma geral para pipes de subprocessos.

Cada slot deverá possuir uma thread leitora

```text
stdout do subprocesso
        ↓
reader thread
        ↓
Queue de respostas
        ↓
request() chama queue.get(timeout=...)
```

Fluxo

1. adquirir o lock do slot;
2. verificar ou iniciar o processo;
3. escrever a requisição;
4. executar `flush`;
5. esperar na fila com timeout;
6. validar `request_id`;
7. devolver o resultado.

## 6.7 Drenagem de stderr

Criar uma segunda thread para consumir continuamente `stderr`.

Sem isso, um worker que escreva muito em `stderr` poderá preencher o buffer do pipe e bloquear.

Manter somente um buffer limitado

```text
últimas 50 linhas
ou
últimos 32 KB
```

Esse conteúdo poderá ser anexado ao erro de crash.

Nunca manter `stderr` ilimitado em memória.

## 6.8 Morte inesperada

Se o processo morrer

 a thread leitora detecta EOF;
 qualquer requisição pendente recebe sinal de falha;
 o slot é marcado como morto;
 o próximo uso inicia outro processo.

## 6.9 Timeout

Quando uma requisição ultrapassar o timeout

1. marcar o payload como problemático;
2. encerrar apenas o processo daquele slot;
3. fechar os pipes;
4. aguardar brevemente as threads leitoras;
5. liberar o slot;
6. lançar `ParserTimeoutError`.

A próxima requisição no mesmo slot inicia um novo worker.

## 6.10 Resposta inválida

Tratar como erro de protocolo

 JSON inválido;
 `request_id` diferente;
 versão de protocolo incompatível;
 resposta sem `ok`;
 resultado sem campos obrigatórios;
 mensagem espontânea sem request pendente.

Nesses casos

1. matar o worker;
2. registrar trecho de `stderr`;
3. lançar `ParserCrashError`;
4. permitir restart no próximo uso.

## 6.11 Shutdown

O encerramento normal deve tentar

```text
enviar operation=shutdown
aguardar resposta
aguardar saída do processo
```

Se não terminar

```text
terminate
aguardar
kill
```

O shutdown deve ser idempotente.

## 6.12 Versionamento

Separar três conceitos

```text
ANALYSIS_VERSION
PROTOCOL_VERSION
WORKER_IMPLEMENTATION_VERSION
```

### `ANALYSIS_VERSION`

Controla invalidação do índice.

Somente alterar quando mudar

 extração de símbolos;
 referências;
 assinaturas;
 ranges;
 chunks estruturais;
 semântica do resultado.

### `PROTOCOL_VERSION`

Controla compatibilidade entre supervisor e worker.

Alterar quando mudar o envelope ou wire format.

### `WORKER_IMPLEMENTATION_VERSION`

Somente diagnóstico.

A troca one-shot → persistente não deve alterar `ANALYSIS_VERSION`.

## 6.13 Aceite da fase 1

 cem análises sequenciais reutilizam o mesmo PID;
 Java e Python criam o parser uma única vez por processo;
 conteúdo Unicode e multilinha faz round trip corretamente;
 um worker que escreve muito em `stderr` não bloqueia;
 timeout encerra o processo;
 a análise seguinte cria outro PID e funciona;
 resposta inválida causa restart;
 shutdown não deixa processo filho ativo;
 resultados estruturais são equivalentes à implementação one-shot;
 nenhuma reindexação completa é provocada somente pela troca do transporte.

---

# 7. Fase 2 — Pool paralelo

## 7.1 Estrutura interna

O supervisor deverá manter

```python
self._slots tuple[_PersistentWorkerSlot, ...]
self._available_slots Queue[_PersistentWorkerSlot]
```

`analyze()`

```python
slot = self._available_slots.get()

try
    return slot.request(...)
finally
    self._available_slots.put(slot)
```

Nenhum slot poderá processar duas requisições simultaneamente.

## 7.2 Estado compartilhado

Mover para o supervisor

 circuit breaker;
 payloads problemáticos;
 contagem de falhas;
 métricas de restart;
 locks de estado.

Não manter circuit breaker independente por slot.

## 7.3 Identidade do payload problemático

Usar

```text
language
operation
path
content_hash
analysis_version
```

Não usar somente linguagem, caminho e hash.

Quando o conteúdo ou a versão da análise mudar, o arquivo poderá ser testado novamente.

## 7.4 Circuit breaker

Não abrir o circuito por repetidas tentativas do mesmo arquivo.

Abrir somente após falhas de payloads distintos dentro de uma janela temporal.

Exemplo

```text
failure_threshold = 3
failure_window_seconds = 60
circuit_reset_seconds = 60
```

Falhas consideradas

 crash nativo;
 timeout;
 corrupção do protocolo;
 morte anormal do processo.

Erros normais de análise que retornem uma resposta JSON válida não devem necessariamente matar o worker nem abrir o circuito.

## 7.5 Falha isolada de slot

Quando um slot falhar

 somente seu processo é encerrado;
 os outros slots continuam;
 o payload recebe fallback textual;
 o slot permanece disponível e reinicia no próximo uso.

Um slot ruim não pode derrubar automaticamente todo o pool.

## 7.6 Configuração

Alterar

```text
srccode_harnessbootstrapsettings.py
srccode_harnessbootstrapcontainer.py
```

Adicionar

```python
parser_workers int
```

Configuração

```python
parser_workers=int(
    os.environ.get(
        CODE_HARNESS_PARSER_WORKERS,
        str(min(4, os.cpu_count() or 1)),
    )
)
```

Validação

```text
1 = parser_workers = 8
```

Passar a quantidade ao supervisor no bootstrap.

---

# 8. Paralelização no IndexCoordinator

## 8.1 Problema da implementação atual

`_build_update` recebe

```python
warnings list[str]
```

e altera essa lista diretamente.

Isso não pode ser mantido com execução paralela.

## 8.2 Resultado imutável por tarefa

Criar modelo interno

```python
@dataclass(frozen=True, slots=True)
class _BuildOutcome
    path str
    status str
    update FileIndexUpdate  None = None
    warnings tuple[str, ...] = ()
    changed bool = False
    newly_discovered bool = False
    remove_stale bool = False
```

Possíveis status

```text
new
changed
unchanged
metadata_only
unreadable_new
unreadable_stale
failed_with_fallback
```

Cada tarefa constrói seus próprios warnings.

A thread principal agrega os resultados.

## 8.3 Unificação dos loops

Substituir os loops separados de `plan.new` e `plan.candidates` por uma coleção de trabalhos.

Exemplo conceitual

```python
jobs = [
    _IndexJob.new(source_file)
    for source_file in plan.new
]

jobs.extend(
    _IndexJob.candidate(source_file, previous)
    for source_file, previous in plan.candidates
)
```

Cada job

1. lê o arquivo;
2. calcula o hash;
3. decide se houve alteração;
4. executa `_build_update` quando necessário;
5. devolve `_BuildOutcome`.

## 8.4 Executor limitado

Usar

```python
ThreadPoolExecutor(max_workers=parser_workers)
```

Não submeter resultados ilimitados sem controle.

Manter no máximo

```text
parser_workers  2
```

trabalhos em voo.

Isso evita

 milhares de futures;
 crescimento desnecessário de memória;
 muitos conteúdos completos aguardando processamento;
 pressão excessiva no sistema de arquivos.

## 8.5 Progresso

As threads não devem chamar diretamente o renderer de progresso.

Somente a thread principal emitirá eventos quando receber um outcome concluído.

O contador será naturalmente serial

```text
1N
2N
3N
...
NN
```

O `path` exibido será o arquivo que acabou de ser processado, não necessariamente a ordem alfabética.

## 8.6 Determinismo

A conclusão das tarefas será fora de ordem.

Antes de gerar relatório e persistir

```python
updates.sort(key=lambda item item.source.path)
removed_paths = sorted(set(removed_paths))
warnings = sorted(...)
```

Preferencialmente ordenar warnings por

```text
path
código
mensagem
```

Isso garante

 testes reprodutíveis;
 banco final equivalente;
 relatórios estáveis;
 diffs previsíveis.

## 8.7 Validação e fallback

A lógica atual deverá permanecer por arquivo

```text
analyze
  ↓
build_chunks
  ↓
_validate_analysis
  ↓
resultado válido
```

Em caso de erro

```text
warning
  ↓
textual_fallback
  ↓
FileIndexUpdate válido
```

Nenhuma falha individual deve encerrar a indexação inteira.

## 8.8 Persistência

Depois que todos os outcomes forem agregados

```python
store.commit_files(...)
prepare_embeddings(...)
store.commit_embeddings(...)
store.complete_run(...)
```

Os writes continuam na thread principal.

## 8.9 Cancelamento

Se ocorrer falha global inesperada

1. cancelar futures ainda não iniciadas;
2. aguardar encerramento controlado do executor;
3. não iniciar commit parcial;
4. marcar a execução como failed;
5. preservar o índice anterior.

## 8.10 Aceite da fase 2

 `workers=1` e `workers=4` produzem o mesmo estado lógico no banco;
 símbolos, referências e chunks são equivalentes;
 ordem física de execução pode variar;
 relatório final permanece determinístico;
 callbacks de progresso são monotônicos;
 SQLite é acessado para escrita somente pela thread principal;
 no máximo N análises nativas executam simultaneamente;
 um worker travado não impede os outros de concluírem;
 shutdown encerra todos os processos;
 indexação sem arquivos alterados não inicia workers;
 não há deadlocks após repetidos crashes e restarts.

---

# 9. Lifecycle da aplicação

## 9.1 Problema

Na CLI, os processos normalmente terminam junto com o processo pai.

No MCP, o container pode permanecer ativo por bastante tempo.

O servidor atual cria o container, mas não possui lifecycle explícito para encerrar supervisores persistentes.

## 9.2 Mudança

Adicionar ao container

```python
def shutdown(self) - None
    ...
```

ou implementar contexto gerenciado

```python
with build_container(settings) as container
    ...
```

O shutdown deverá encerrar

 parser supervisor;
 embedding supervisor;
 futuros componentes com subprocessos.

## 9.3 CLI

Executar shutdown em bloco `finally`.

## 9.4 MCP

Registrar encerramento do container no lifecycle do servidor.

Usar `atexit` somente como proteção adicional, não como mecanismo principal.

---

# 10. Fase 3 — Indexação parcial por pathglob

## 10.1 Request

Alterar

```text
srccode_harnessapplicationdtorequests.py
```

De

```python
@dataclass(frozen=True, slots=True)
class IndexProjectRequest
    mode IndexMode = IndexMode.INCREMENTAL
```

Para

```python
@dataclass(frozen=True, slots=True)
class IndexProjectRequest
    mode IndexMode = IndexMode.INCREMENTAL
    include_globs tuple[str, ...] = ()
    exclude_globs tuple[str, ...] = ()

    @property
    def partial(self) - bool
        return bool(self.include_globs or self.exclude_globs)
```

## 10.2 Discovery

Executar

```python
discovered = catalog.list_files(
    include_globs=request.include_globs,
    exclude_globs=request.exclude_globs,
)
```

O `FileCatalog` já suporta esses argumentos.

## 10.3 Escopo explícito

Criar helper

```text
IndexScope
```

Responsabilidade

 compilar `include_globs`;
 compilar `exclude_globs`;
 informar se um path pertence ao escopo solicitado pelo usuário.

```python
scope.matches(path)
```

## 10.4 Regra de remoção

Para indexação parcial

```python
stored_in_scope = tuple(
    item for item in stored
    if scope.matches(item.path)
)
```

Executar detecção de mudanças somente com

```text
discovered filtrado
stored_in_scope
```

Consequência

 arquivos armazenados fora do escopo são preservados;
 arquivos dentro do escopo que desapareceram são removidos;
 arquivos excluídos pelo usuário não são removidos;
 uma indexação de `src` não afeta `docs`.

## 10.5 Regras de ignore

O escopo de remoção deve considerar os filtros explícitos do usuário, não reutilizar cegamente as regras atuais de `.gitignore`.

Exemplo

1. um arquivo estava indexado;
2. passou a ser ignorado pelo `.gitignore`;
3. uma indexação completa é executada.

Nesse caso ele deve ser removido.

Para uma indexação parcial, ele deve ser removido apenas quando seu path estiver dentro dos globs explicitamente solicitados.

## 10.6 Sem filtros

Quando não houver `include_globs` nem `exclude_globs`

 comportamento atual preservado;
 todos os arquivos ausentes são removidos;
 mudanças de `.gitignore` refletem no índice;
 nenhuma semântica parcial é aplicada.

## 10.7 Relatório

Adicionar ao resultado, sem necessariamente exigir migração imediata

```json
{
  partial true,
  include_globs [src],
  exclude_globs [generated],
  scoped_discovered_files 120,
  preserved_out_of_scope_files 830
}
```

Isso impede que o usuário interprete uma indexação parcial como validação completa do projeto.

## 10.8 CLI

Alterar o comando

```text
code-harness index
```

Adicionar

```text
--include
--exclude
```

Exemplo

```powershell
code-harness index --include src --exclude generated
```

## 10.9 MCP

Quando `mcp_expose_index_commands=true`, expor

```python
def index_project(
    mode str = incremental,
    include_globs list[str]  None = None,
    exclude_globs list[str]  None = None,
)
```

## 10.10 Aceite da fase 3

Fixture

```text
srcA.java
srcB.java
docsguide.md
generatedC.java
```

Cenários obrigatórios

1. indexação completa registra todos os arquivos permitidos;
2. remover `srcA.java` e indexar `src` remove apenas `srcA.java`;
3. remover `docsguide.md` e indexar `src` preserva a entrada de docs;
4. excluir `srcB.java` via `--exclude` preserva sua entrada atual;
5. sem filtros, arquivos ausentes são removidos normalmente;
6. `VERIFY` parcial não altera o banco;
7. indexação parcial informa explicitamente o escopo no relatório.

---

# 11. Testes obrigatórios

## 11.1 Protocolo persistente

 reutilização do PID;
 request ID correto;
 resposta fora de ordem rejeitada;
 resposta inválida;
 EOF inesperado;
 shutdown;
 timeout;
 restart;
 Unicode;
 conteúdo com quebras de linha;
 payload grande;
 worker escrevendo muito em `stderr`;
 processo encerrando antes da resposta.

## 11.2 Cache do parser

Instrumentar worker de teste para confirmar

 gramática Java carregada uma vez;
 parser Java criado uma vez;
 gramática Python carregada uma vez;
 parser Python criado uma vez;
 cada processo possui seu próprio cache.

## 11.3 Pool

 máximo de N PIDs trabalhando ao mesmo tempo;
 nenhum slot processa duas requests simultaneamente;
 distribuição entre slots;
 crash de um slot;
 restart de um slot;
 outros slots continuam;
 shutdown com slots nunca iniciados;
 shutdown com slots ativos;
 chamadas repetidas a shutdown.

## 11.4 Circuit breaker

 mesmo payload falhando várias vezes conta uma vez;
 três payloads distintos abrem o circuito;
 sucesso isolado não esconde falhas concorrentes;
 circuito fecha após reset;
 mudança do conteúdo libera o payload;
 mudança da analysis version libera o payload.

## 11.5 Coordenador

Comparar bancos gerados por

```text
workers=1
workers=2
workers=4
```

Comparar

 arquivos;
 hashes;
 parse states;
 símbolos;
 assinaturas;
 referências;
 chunks;
 FTS;
 warnings;
 relatório.

Não comparar somente a ordem de retorno.

## 11.6 Persistência serial

Usar store fake que registre o ID da thread.

Falhar o teste caso qualquer escrita seja executada fora da thread do coordenador.

## 11.7 Progresso

Validar

 `current` começa em 1;
 nunca diminui;
 nunca ultrapassa `total`;
 evento final possui `current == total`;
 nenhum callback é executado simultaneamente.

## 11.8 Indexação parcial

Cobrir

 include;
 exclude;
 combinação include + exclude;
 arquivo removido dentro do escopo;
 arquivo removido fora do escopo;
 mudança de `.gitignore`;
 modo full;
 modo incremental;
 modo verify.

---

# 12. Observabilidade

Adicionar ao relatório ou aos logs

```text
parser_workers
worker_processes_started
worker_restarts
worker_timeouts
worker_crashes
parser_queue_wait_ms
parser_analysis_ms
source_read_ms
chunk_build_ms
commit_ms
```

Também registrar por execução

```text
max_concurrent_analyses
```

Não registrar conteúdo dos arquivos.

---

# 13. Documentação

Atualizar

```text
docsindexing.md
docsadr0003-native-parser-isolation.md
config.example.yaml
README.md
```

## 13.1 ADR 0003

Alterar a decisão implementada para informar que

 subprocessos podem ser persistentes;
 cada worker processa uma requisição por vez;
 o supervisor pode administrar um pool;
 processos continuam isolados do processo principal;
 timeout encerra apenas o worker afetado;
 circuit breaker e payload cache ficam no supervisor;
 resultados continuam totalmente serializáveis.

A persistência do worker não viola a decisão original de isolamento.

## 13.2 Indexing

Documentar

 quantidade de workers;
 consumo de memória;
 default;
 variável de ambiente;
 indexação parcial;
 semântica de remoção;
 diferença entre ignore permanente e filtro de uma execução.

---

# 14. Ordem exata de implementação

## Etapa 1 — Baseline

1. adicionar timings estruturais;
2. criar benchmark one-shot;
3. registrar baseline do projeto real.

## Etapa 2 — Protocolo

4. criar `native_protocol.py`;
5. adaptar worker para `--loop`;
6. manter modo one-shot;
7. criar cache de parser por linguagem;
8. implementar `_PersistentWorkerSlot`;
9. implementar reader thread de stdout;
10. implementar drain thread de stderr;
11. implementar timeout e restart;
12. implementar shutdown gracioso;
13. adicionar testes do protocolo.

## Etapa 3 — Supervisor persistente único

14. fazer o supervisor usar um slot;
15. manter `ANALYSIS_VERSION`;
16. executar testes estruturais existentes;
17. comparar banco one-shot versus persistente;
18. executar benchmark com um worker.

## Etapa 4 — Pool

19. adicionar `parser_workers` em Settings;
20. transformar supervisor em pool;
21. centralizar circuit breaker;
22. centralizar payload cache;
23. adicionar testes de concorrência e falhas;
24. executar benchmark com 2, 4 e 8 workers.

## Etapa 5 — Coordinator paralelo

25. criar `_IndexJob`;
26. criar `_BuildOutcome`;
27. remover mutação compartilhada de warnings;
28. unificar loops de novos e candidatos;
29. adicionar executor limitado;
30. emitir progresso somente na thread principal;
31. ordenar outcomes antes do commit;
32. garantir writes SQLite seriais;
33. comparar bancos workers=1 e workers=N.

## Etapa 6 — Lifecycle

34. adicionar shutdown ao container;
35. ligar shutdown na CLI;
36. ligar shutdown no MCP;
37. validar ausência de processos órfãos.

## Etapa 7 — Escopo parcial

38. estender `IndexProjectRequest`;
39. criar `IndexScope`;
40. filtrar discovery;
41. filtrar stored somente para detect_changes;
42. preservar paths fora do escopo;
43. adicionar CLI;
44. adicionar schema MCP;
45. adicionar relatório de escopo;
46. documentar semântica;
47. executar testes de remoção parcial.

## Etapa 8 — Hardening

48. executar suíte Windows;
49. executar suíte Linux;
50. executar benchmarks;
51. validar memória;
52. validar projeto real de aproximadamente 4.000 arquivos;
53. atualizar ADR e documentação.

---

# 15. Critérios finais de sucesso

A implementação estará aprovada quando

1. o Tree-sitter continuar fora do processo principal;
2. workers forem reutilizados entre arquivos;
3. gramáticas e parsers forem reutilizados dentro de cada worker;
4. nenhuma indexação incremental vazia iniciar workers;
5. quatro workers analisarem arquivos simultaneamente;
6. SQLite continuar com escrita serial;
7. workers=1 e workers=N produzirem índice equivalente;
8. crash de um worker não encerrar o processo principal;
9. timeout afetar somente o slot correspondente;
10. circuit breaker permanecer funcional em concorrência;
11. warnings e relatórios permanecerem determinísticos;
12. não existirem processos órfãos após CLI ou MCP;
13. indexação parcial não remover arquivos fora do escopo;
14. indexação completa preservar o comportamento atual;
15. o cold full index do projeto real apresentar melhora mensurável;
16. o second incremental sem alterações permanecer praticamente no-op.

---

# 16. O que não fazer nesta implementação

 não carregar Tree-sitter no processo principal;
 não compartilhar objetos nativos entre processos;
 não usar um único pipe simultaneamente por várias threads;
 não executar duas requisições concorrentes no mesmo worker;
 não deixar `stderr` sem consumidor;
 não usar `select` sobre pipes como solução obrigatória para Windows;
 não alterar a analysis version apenas pela troca de transporte;
 não criar circuit breaker independente por worker;
 não alterar listas compartilhadas dentro das tarefas;
 não escrever no SQLite pelas threads;
 não iniciar todos os workers durante o bootstrap;
 não paralelizar embeddings nesta rodada;
 não misturar indexação parcial com limpeza completa do índice;
 não depender apenas da ordem de conclusão das futures;
 não definir quantidade de workers sem limite superior.
