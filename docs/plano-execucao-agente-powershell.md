# Plano de Implementação — Execução de Comandos para Agentes

## 0. Controle do documento

Última atualização: **23 de julho de 2026** (revisão local Grok 4 contra o clone).

Clone validado: repositório Git `code-base` (`origin`: `nandoripardo888/code-base`), pacote Python `code-harness` em `src/code_harness/`, branch `main` em `2434c8e`, working tree limpa.

Este documento define a introdução de uma capacidade opcional de execução de comandos no `code-harness`, com foco inicial em Windows e PowerShell.

O plano **não altera o comportamento atual do produto**: enquanto nenhuma fase for implementada e habilitada, o `code-harness` continua sendo uma ferramenta local-first e somente leitura para recuperação de contexto.

A execução deverá ser entregue como capacidade separada, desabilitada por padrão e removível sem afetar busca, leitura, indexação, análise estrutural, busca semântica, construção de contexto ou o MCP de recuperação.

### 0.1 Estado atual relevante (confirmado no clone)

O repositório já possui:

- arquitetura com dependências direcionadas para dentro (`docs/adr/0001-layered-architecture.md`);
- application tools compartilhadas pela API Python, CLI e MCP;
- composição manual em `src/code_harness/bootstrap/container.py` (`ApplicationContainer` + `build_container`);
- DTOs imutáveis em `application/dto/requests.py` e erros tipados em `domain/errors.py` + `ErrorCode` em `domain/enums.py`;
- acesso a arquivos protegido por `PathGuard` (`infrastructure/filesystem/path_guard.py`);
- processos isolados para parsers e embeddings (workers nativos via `subprocess`);
- SQLite versionado para o índice (`schema_migrations` + `PRAGMA user_version` em `infrastructure/persistence/`);
- handlers MCP finos em `interfaces/mcp/handlers.py`, gate `mcp_expose_index_commands`;
- serialização compartilhada em `interfaces/serialization.py`;
- CLI Typer monolítica em `interfaces/cli/main.py` com sub-apps `files` / `search` / `models` / `mcp`;
- testes arquiteturais em `tests/contract/test_architecture.py`;
- cobertura mínima (`fail_under = 85` em `pyproject.toml`).

**Ainda não existe** nenhum módulo `infrastructure/execution/`, `bootstrap/execution.py`, tool de execução, extra `execution` no `pyproject.toml`, nem campos de execução em `Settings`.

A nova capacidade deverá preservar esses padrões.

### 0.2 Nova fronteira de confiança

O plano principal atual declara que nenhum código do repositório analisado é executado. Esta funcionalidade cria uma nova fronteira de confiança.

A arquitetura oficial deverá distinguir:

```text
Code Harness
├── Retrieval
│   ├── leitura
│   ├── busca
│   ├── indexação
│   ├── análise estrutural
│   └── construção de contexto
│
└── Agent Execution
    ├── inspeção de comandos
    ├── política de capacidades
    ├── aprovação
    ├── execução supervisionada
    ├── cancelamento
    ├── auditoria
    └── isolamento opcional (backend windows_sandbox)
```

A instalação básica continuará somente leitura.

### 0.3 Pré-requisitos da máquina de desenvolvimento (clone local)

Validados em 23/07/2026 neste host:

| Item | Estado local | Impacto no plano |
|------|--------------|------------------|
| Python 3.12 + venv | OK (3.12.5) | — |
| PowerShell 7 (`pwsh`) | **Ausente** (só Windows PowerShell 5.1) | Bloqueia E3 e parte de E0 (runner/`pwsh --version`); AST de inspeção pode usar o Parser do 5.1 só para smoke, mas o produto exige `pwsh` |
| `System.Management.Automation.Language.Parser` | Disponível via Windows PowerShell 5.1 | Útil para fixtures de AST; não substitui requisito de PS7 na execução |
| APIs Job Object (`CreateJobObjectW`, `AssignProcessToJobObject`, `CreateProcessW`, `ResumeThread`, …) | Disponíveis via `ctypes` | E1 viável sem pywin32 obrigatório |
| `pywin32` (312) | Presente no `.venv`, **não** declarado em `pyproject.toml` | Tratar como opcional no extra `execution`; preferir `ctypes` para Job Object |
| Windows Sandbox (`Containers-DisposableClientVM`) | **Disabled** | E7 não validável neste host até habilitar o feature |
| Sessão elevada (Administrador) | **Sim** (`IsInRole(Administrator)=True`) | Risco: testes de execução sob admin ampliam blast radius; rodar testes E1+ em sessão não elevada |
| `.code-harness/` | gitignored | Adequado para `index.db` e futuro `execution.db` |

---

## 1. Objetivo

Construir um subsistema local que permita a agentes solicitar comandos de forma controlada e rastreável, inicialmente no Windows, oferecendo:

- execução estruturada de programas sem shell;
- execução explícita de scripts PowerShell;
- análise prévia de risco;
- inferência determinística de capacidades;
- aprovação vinculada ao comando exato;
- timeout e cancelamento;
- contenção da árvore de processos;
- limitação e captura de saída;
- sanitização de ambiente;
- auditoria;
- API Python;
- CLI;
- exposição MCP opcional e restrita;
- backend futuro com isolamento real.

Fluxo:

```text
Modelo solicita comando
        ↓
Comando é normalizado e inspecionado
        ↓
Policy Engine decide: allow / approval_required / deny
        ↓
Backend verifica se consegue aplicar as garantias solicitadas
        ↓
Processo é executado e supervisionado
        ↓
Resultado estruturado volta ao agente
```

---

## 2. Não objetivos da primeira entrega

A primeira entrega não incluirá:

- terminal interativo persistente;
- pseudoterminal;
- sessão PowerShell com estado entre comandos;
- execução administrativa;
- autoaprovação ampla;
- acesso automático a credenciais;
- acesso livre à rede;
- `git push`, publicação ou deploy automáticos;
- execução fora do projeto ativo;
- suporte genérico a Linux e macOS;
- classificação de risco por LLM;
- alegação de sandbox forte no backend de host;
- execução implícita de `.bat` ou `.cmd` por shell;
- exposição de uma tool MCP capaz de aprovar a própria solicitação;
- uso de Windows PowerShell 5.1 como runner de produção (somente `pwsh` 7+).

---

## 3. Princípios obrigatórios

### 3.1 Desabilitado por padrão

```text
execution_enabled = false
mcp_expose_execution = false
mcp_expose_powershell = false
```

Nenhuma dependência Windows de execução deverá ser importada no caminho frio quando a execução estiver desabilitada. Em concreto: `build_container` **não** importa `code_harness.infrastructure.execution*` no topo do módulo; o import fica dentro de `build_execution_container` chamado só se `execution_enabled`.

Nota: o repositório já usa `subprocess` em ripgrep/parsers/embeddings; a restrição nova é sobre módulos do *subsistema de execução* e extras Windows (`pywin32`), não sobre banir `subprocess` em toda a infra.

### 3.2 Inspeção separada da execução

`inspect_process` / `inspect_powershell` não poderão executar o comando do usuário, carregar perfil PowerShell, importar módulos do projeto ou iniciar scripts do repositório analisado. O worker AST interno é código constante do harness, não código do projeto.

### 3.3 Menor capacidade

Cada solicitação declara capacidades. A política pode reduzir ou negar, mas nunca conceder silenciosamente uma capacidade adicional.

### 3.4 Política determinística

A primeira versão utilizará regras explícitas e testáveis. Nenhuma LLM decidirá se um comando é seguro.

### 3.5 Aprovação não substitui contenção

Aprovação humana autoriza; ela não cria isolamento.

### 3.6 Sem `shell=True`

`run_process` chamará um executável com uma lista de argumentos. Não poderá concatenar uma linha e entregá-la ao `cmd.exe`.

### 3.7 PowerShell livre é privilegiado

No backend de host, `run_powershell` sempre exigirá aprovação.

### 3.8 Política protegida

Arquivos de política, banco de auditoria, aprovações e código do executor não poderão ser alterados por comandos autoaprovados.

### 3.9 Sem falsa garantia

O backend deverá declarar o que realmente aplica.

```text
host_supervised
├── árvore de processos: aplicada
├── timeout: aplicado
├── limite de saída: aplicado
├── filesystem: não isolado
├── rede: não isolada
└── credenciais: apenas mitigação parcial
```

Se uma solicitação exigir isolamento não suportado, deverá falhar com erro tipado (`backend_capability_unavailable`).

**Nomenclatura:** o backend inicial chama-se `host_supervised`, nunca “sandbox”. Isolamento real fica em `windows_sandbox`. Pastas de código usam `backends/`, não `sandbox/` para o host.

### 3.10 Saída não confiável

`stdout` e `stderr` são dados externos. O agente não deve tratar o conteúdo como instrução.

---

## 4. Modelo de ameaças

Cobrir ao menos:

1. destruição de arquivos ou trabalho Git não commitado;
2. leitura fora do projeto;
3. acesso a `.env`, chaves, tokens e credenciais;
4. exfiltração por rede;
5. código malicioso no repositório;
6. scripts de build, plugins e subprocessos;
7. PowerShell dinâmico;
8. aliases, funções, perfis e módulos;
9. escape por filhos;
10. processos órfãos após timeout;
11. saída excessiva;
12. prompt injection em arquivos e logs;
13. alteração da própria política;
14. aprovação para um comando e execução de outro;
15. repetição infinita do mesmo comando bloqueado;
16. concorrência de escrita;
17. vazamento pela auditoria;
18. symlink e normalização de caminho;
19. diferenças entre PowerShell 7 e Windows PowerShell 5.1;
20. comandos aparentemente de leitura que executam código do projeto;
21. execução sob sessão Administrador (blast radius ampliado neste host);
22. reutilização acidental de `pywin32` não pinado do venv local.

---

## 5. Decisão arquitetural

Manter:

```text
interfaces ───▶ application ───▶ domain
                       ▲
                       │
               infrastructure
```

O MCP continuará somente como adaptador (`docs/adr/0004-mcp-as-adapter.md`).

### 5.1 Composição opcional

Estado atual (`ApplicationContainer`, linhas 49–71 de `bootstrap/container.py`): todos os campos são tools/retrieval obrigatórios; **não** há campo opcional.

Ajuste mínimo compatível (campo novo **no final**, com default):

```python
@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    # ... tools atuais inalterados ...
    prepare_semantic_model: PrepareSemanticModelTool
    execution: ExecutionContainer | None = None
```

```text
build_container(settings)
    ├── monta retrieval/indexação normalmente (como hoje)
    └── se settings.execution_enabled:
            execution = build_execution_container(settings, project, guard)
        senão:
            execution = None
```

`ExecutionContainer` vive em `bootstrap/execution.py` e agrupa só tools/stores de execução. Imports Windows ficam lá, lazy.

### 5.2 Persistência separada

Não adicionar execução ao `RepositoryStore` / `SQLiteRepositoryStore`, que representa o índice (`index.db`).

Criar stores próprios, protocolos em `domain/protocols/`, implementação em `infrastructure/execution/persistence/`:

```text
ExecutionStore
ApprovalStore
ExecutionArtifactStore
```

Banco padrão (espelhando `CODE_HARNESS_INDEX_PATH`):

```text
.code-harness/execution.db
```

via `CODE_HARNESS_EXECUTION_STORE_PATH` (relativo à raiz do projeto se não absoluto).

O índice permanece em:

```text
.code-harness/index.db
```

Migrations da execução: **mesmo padrão** do índice (`apply_migrations` + tabela `schema_migrations` + `PRAGMA user_version`), mas em arquivo/DB separado. Nome da tabela pode ser `schema_migrations` *dentro* de `execution.db` (não misturar com `index.db`). Evitar o nome confuso `execution_schema_migrations` a menos que se documente a razão; o isolamento já é por arquivo.

---

## 6. Estrutura proposta (alinhada ao layout atual)

Caminhos relativos a `src/code_harness/`. Itens marcados com *(estender)* já existem.

```text
src/code_harness/
├── domain/
│   ├── models/
│   │   ├── command_execution.py          # novo
│   │   ├── command_inspection.py         # novo
│   │   ├── execution_approval.py         # novo
│   │   └── execution_backend.py          # novo
│   ├── protocols/
│   │   ├── command_analyzer.py           # novo
│   │   ├── command_policy.py             # novo
│   │   ├── command_runner.py             # novo
│   │   ├── execution_store.py            # novo
│   │   ├── approval_store.py             # novo
│   │   └── execution_artifact_store.py   # novo
│   ├── enums.py                          # *(estender)* StrEnums de execução
│   └── errors.py                         # *(estender)* classes de erro
│
├── application/
│   ├── dto/
│   │   ├── requests.py                   # *(inalterado)*
│   │   └── execution_requests.py         # novo
│   └── tools/
│       ├── inspect_process.py            # novo
│       ├── inspect_powershell.py         # novo
│       ├── run_process.py                # novo
│       ├── run_powershell.py             # novo
│       ├── get_execution.py              # novo
│       └── terminate_execution.py        # novo
│
├── infrastructure/
│   └── execution/
│       ├── analysis/
│       │   ├── process_analyzer.py
│       │   ├── powershell_ast_analyzer.py
│       │   ├── powershell_parser.ps1     # worker constante do harness
│       │   └── risk_rules.py
│       ├── policy/
│       │   ├── deterministic_policy.py
│       │   ├── capability_inference.py
│       │   ├── protected_paths.py
│       │   └── executable_rules.py
│       ├── runners/
│       │   ├── supervised_process_runner.py
│       │   ├── powershell_runner.py
│       │   ├── process_registry.py
│       │   └── output_collector.py
│       ├── windows/
│       │   ├── job_object.py             # preferir ctypes
│       │   ├── process_factory.py
│       │   └── acl.py                    # pywin32 opcional
│       ├── backends/                      # NÃO chamar de sandbox/
│       │   ├── host_supervised_backend.py
│       │   └── windows_sandbox_backend.py
│       ├── persistence/
│       │   ├── sqlite_execution_store.py
│       │   ├── schema.py
│       │   └── migrations.py
│       ├── approvals/
│       │   ├── sqlite_approval_store.py
│       │   └── digest.py
│       └── redaction/
│           └── output_redactor.py
│
├── bootstrap/
│   ├── container.py                      # *(estender)* campo + wire opcional
│   ├── settings.py                       # *(estender)* flags/env
│   └── execution.py                      # novo: ExecutionContainer + build_*
│
└── interfaces/
    ├── cli/
    │   ├── main.py                       # *(estender)* add_typer(exec_app)
    │   └── execution_commands.py         # novo: Typer sub-app "exec"
    ├── mcp/
    │   ├── handlers.py                   # *(estender)* delegar registro opcional
    │   ├── execution_handlers.py         # novo: handlers finos
    │   └── server.py                     # *(estender)* instructions se execução exposta
    ├── python_api/
    │   └── harness.py                    # *(estender)* métodos tipados
    ├── serialization.py                  # *(reusar)*; estender se necessário
    └── cli/renderers/output.py           # *(estender)* render text de ExecutionResult
```

ADR novo: `docs/adr/0005-execution-trust-boundary.md`.

---

## 7. Modelos e enums

**Estender** `domain/enums.py` (não criar segundo arquivo de enums):

```python
class CommandKind(StrEnum):
    PROCESS = "process"
    POWERSHELL = "powershell"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"


class ExecutionState(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    SANDBOX_VIOLATION = "sandbox_violation"


class ExecutionCapability(StrEnum):
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    GIT_READ = "git_read"
    GIT_WRITE = "git_write"
    EXECUTE_REPOSITORY_CODE = "execute_repository_code"
    PROCESS_SPAWN = "process_spawn"
    NETWORK_OUTBOUND = "network_outbound"
    CREDENTIAL_ACCESS = "credential_access"
    HOST_FILESYSTEM_READ = "host_filesystem_read"
    HOST_FILESYSTEM_WRITE = "host_filesystem_write"
    REGISTRY_READ = "registry_read"
    REGISTRY_WRITE = "registry_write"
    SERVICE_CONTROL = "service_control"
    ADMIN = "admin"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class ExecutionBackendKind(StrEnum):
    HOST_SUPERVISED = "host_supervised"
    WINDOWS_SANDBOX = "windows_sandbox"
```

Modelos em arquivos novos sob `domain/models/` (dataclasses `frozen=True, slots=True`, padrão do projeto):

```python
@dataclass(frozen=True, slots=True)
class BackendGuarantees:
    process_tree_containment: bool
    timeout_enforced: bool
    output_limits_enforced: bool
    filesystem_isolated: bool
    network_isolated: bool
    credentials_isolated: bool
    workspace_write_isolated: bool


@dataclass(frozen=True, slots=True)
class NormalizedCommand:
    kind: CommandKind
    executable: str | None
    args: tuple[str, ...]
    script: str | None
    cwd: str
    timeout_seconds: float
    max_output_bytes: int
    requested_capabilities: tuple[ExecutionCapability, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class CommandInspection:
    normalized: NormalizedCommand
    required_capabilities: tuple[ExecutionCapability, ...]
    decision: PolicyDecision
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    dynamic_features: tuple[str, ...]
    protected_path_matches: tuple[str, ...]
    backend_requirements: tuple[str, ...]
    approval_digest: str


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: str
    state: ExecutionState
    inspection: CommandInspection
    backend: ExecutionBackendKind
    backend_guarantees: BackendGuarantees
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    elapsed_ms: int
    started_at: str | None
    finished_at: str | None
    warnings: tuple[str, ...]
```

**API surface:** tools e `CodeHarness` retornam `ToolResult[CommandInspection]` / `ToolResult[ExecutionResult]`, alinhado ao restante da API (não retornar o DTO “nu”).

---

## 8. Erros tipados

Adicionar valores em `ErrorCode` (`domain/enums.py`) e classes espelho em `domain/errors.py`, seguindo o padrão `CodeHarnessError` (code, message, details, recoverable, capability, remediation):

```text
execution_disabled
execution_backend_unavailable
backend_capability_unavailable
command_analysis_failed
command_blocked
approval_required
approval_not_found
approval_invalid
approval_expired
approval_consumed
execution_not_found
execution_already_finished
execution_timeout
execution_cancelled
process_start_failed
powershell_unavailable
output_limit_exceeded
```

Os erros não devem carregar script completo, token ou segredo.

Estender `_exit_code` em `interfaces/cli/main.py` para mapear novos códigos (ex.: `approval_required` → 2; `command_blocked` → 2; `powershell_unavailable` → 4; `execution_timeout` → 6).

Estender `_ERROR_CAPABILITIES` / `_DEFAULT_REMEDIATIONS` / `_RECOVERABLE_CODES` conforme fizer sentido (`powershell_unavailable` recuperável; `command_blocked` não).

---

## 9. DTOs

Criar `application/dto/execution_requests.py` (não misturar em `requests.py`).

```python
@dataclass(frozen=True, slots=True)
class InspectProcessRequest:
    executable: str
    args: tuple[str, ...] = ()
    cwd: str = "."
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    requested_capabilities: tuple[ExecutionCapability, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InspectPowerShellRequest:
    script: str
    cwd: str = "."
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    requested_capabilities: tuple[ExecutionCapability, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RunProcessRequest(InspectProcessRequest):
    approval_id: str | None = None
    wait: bool = True


@dataclass(frozen=True, slots=True)
class RunPowerShellRequest(InspectPowerShellRequest):
    approval_id: str | None = None
    wait: bool = True


@dataclass(frozen=True, slots=True)
class GetExecutionRequest:
    execution_id: str
    include_output: bool = True


@dataclass(frozen=True, slots=True)
class TerminateExecutionRequest:
    execution_id: str
    reason: str | None = None
```

Validar em `__post_init__` com o helper `_require_positive` (mesmo padrão de `requests.py`):

- timeout positivo e abaixo do máximo (máximo vem das Settings na tool, não hardcode no DTO além de sanity);
- limite de saída positivo;
- `cwd` não vazio;
- script não vazio;
- tamanho máximo do script;
- quantidade e tamanho dos argumentos;
- capabilities sem duplicação.

---

## 10. Application tools

Duas tools de inspeção (não um único `InspectCommandTool` ambíguo):

### `InspectProcessTool` / `InspectPowerShellTool`

1. normaliza `cwd` via extensão de `PathGuard` (ver §10.1);
2. localiza executável / valida `pwsh`;
3. analisa programa/argumentos ou AST PowerShell;
4. infere capacidades;
5. detecta paths protegidos;
6. consulta guarantees do backend;
7. executa policy;
8. calcula digest;
9. retorna `ToolResult[CommandInspection]`.

Nunca inicia o comando do usuário.

### `RunProcessTool`

```text
validar request
    ↓
inspect_process
    ↓
deny → CommandBlockedError
    ↓
approval_required → validar aprovação
    ↓
verificar backend
    ↓
registrar execução
    ↓
iniciar runner sem shell
    ↓
capturar resultado
    ↓
concluir auditoria
```

### `RunPowerShellTool`

- exige `execution_powershell_enabled`;
- usa executável resolvido (`execution_powershell_executable`, default `pwsh`);
- `-NoLogo -NoProfile -NonInteractive`;
- script temporário controlado;
- ACL restrita;
- limpeza em `finally`;
- aprovação obrigatória no host;
- proíbe `EncodedCommand`;
- não reutiliza sessão.

### `GetExecutionTool` / `TerminateExecutionTool`

Como no plano original (estado/saída; encerrar árvore; idempotente).

### 10.1 PathGuard

Hoje só existe `resolve_file` (exige arquivo). Para `cwd` e paths de policy, adicionar método mínimo, por exemplo:

```python
def resolve_within_root(self, path: str, *, must_exist: bool = False) -> tuple[Path, str]:
    ...
```

que rejeita escape da raiz (`PathOutsideProjectError`), sem exigir que seja arquivo.

---

## 11. Análise de comandos

### 11.1 Processos estruturados

Classificar executável, subcomando e argumentos.

```text
git status        → git_read
git diff          → git_read
git add           → git_write + workspace_read
git reset --hard  → git_write + workspace_write + destructive
git push          → git_write + network_outbound + credential_access
pytest            → execute_repository_code + process_spawn
mvn test          → execute_repository_code + process_spawn
npm install       → workspace_write + execute_repository_code + network_outbound
```

Não analisar somente o primeiro token.

### 11.2 AST PowerShell

Usar:

```text
System.Management.Automation.Language.Parser
```

O Python chama um script interno constante (`powershell_parser.ps1`) via `pwsh` quando disponível. O script do usuário entra como dado e vira JSON, sem ser executado.

Em hosts sem `pwsh` (como este clone hoje): inspeção PowerShell falha com `powershell_unavailable` de forma tipada; não cair silenciosamente para Windows PowerShell 5.1 na API de produto. Testes de unidade do parser podem invocar 5.1 só em fixtures marcadas, se necessário, até PS7 ser instalado.

Extrair: comandos, pipelines, redirecionamentos, call operator, dot sourcing, invocações dinâmicas, membros .NET, `Add-Type`, módulos, caminhos literais, rede, registro, serviços, tarefas, processos, remoção/sobrescrita, encoded commands, comando por variável.

Dinâmico / desconhecido nunca vira `allow`.

---

## 12. Policy Engine

Ordem:

```text
1. execução habilitada?
2. tipo de comando habilitado?
3. cwd pertence ao projeto?
4. executável bloqueado?
5. hard deny?
6. path protegido?
7. capabilities requeridas?
8. backend suporte as garantias?
9. regra permite autoexecução?
10. aprovação necessária?
```

Decisões: `ALLOW` | `APPROVAL_REQUIRED` | `DENY`.

Hard deny / autoallow / paths protegidos: manter lista do plano original (§12 do documento anterior), incluindo `.code-harness/**`, policy, hooks, secrets.

A policy publica `policy_name`, `policy_version`, `ruleset_hash`.

---

## 13. Aprovação

O agente não pode aprovar a própria solicitação.

- aprovação disponível por CLI/API local;
- não exposta como tool MCP;
- MCP cria solicitação pendente;
- usuário aprova fora da tool;
- agente repete a mesma solicitação com `approval_id`.

Digest SHA-256 sobre forma canônica de:

```text
project_id
command_kind
executable resolvido
args
script_hash
cwd
timeout
max_output
capabilities
backend
policy_version
ruleset_hash
```

Estados: `pending → approved → consumed` (ou `denied` / `expired`).

A aprovação expira, é de uso único, pertence ao projeto e digest, é consumida atomicamente.

CLI (Typer sub-app `exec`):

```powershell
code-harness exec approvals list
code-harness exec approvals show <approval-id>
code-harness exec approvals approve <approval-id>
code-harness exec approvals deny <approval-id>
```

---

## 14. Backend `host_supervised`

O nome oficial não será `sandbox`. Implementação em `infrastructure/execution/backends/host_supervised_backend.py`.

### 14.1 Processo suspenso

No Windows:

1. criar processo suspenso (`CREATE_SUSPENDED`);
2. atribuir ao Job Object;
3. retomar thread principal.

Preferência de implementação: **ctypes** contra `kernel32` (já validado neste host). `pywin32` só se necessário para ACL (`win32security`) no extra opcional, com versão pinada.

### 14.2 Job Object

- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
- limite de processos;
- limite de memória opcional;
- encerramento da árvore;
- cleanup idempotente.

### 14.3 Ambiente por allowlist

Manter apenas o necessário (`SystemRoot`, `WINDIR`, `TEMP`/`TMP` controlados, `PATH` sanitizado, `PATHEXT`, `HOME`/`USERPROFILE` quando indispensável).

Não herdar automaticamente tokens cloud/CI (`AWS_*`, `AZURE_*`, `GITHUB_TOKEN`, `GH_TOKEN`, `NPM_TOKEN`, `DOCKER_*`, `KUBECONFIG`, `SSH_AUTH_SOCK`, variáveis internas do harness).

### 14.4 Rede e credenciais

O host não garante bloqueio de rede nem isolamento de credenciais. Declarar `network_isolated=false` / `credentials_isolated=false`. Capacidades correspondentes nunca são autoallow no host.

---

## 15. PowerShell Runner

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -File <script-controlado.ps1>
```

Requisitos: PowerShell 7+; caminho absoluto; UTF-8; sem interpolação em outra linha; ACL; cwd do processo; stdout/stderr separados; timeout; Job Object; cleanup; hash do script; script completo não persistido por padrão; sem stdin interativo na v1.

Se `pwsh` ausente → `PowerShellUnavailableError`.

---

## 16. Saída

Limites separados para stdout, stderr e combinado; leitura concorrente; truncamento marcado; redaction de tokens/headers/URLs/keys; não persistir saída completa por padrão.

Primeira versão síncrona; fase assíncrona adiciona `wait=false`, `get_execution`, `terminate_execution`.

Serialização: reutilizar `interfaces/serialization.to_primitive` / `serialize_tool_result` / `serialize_error`. Estender renderer CLI (`output.py`) para formato texto de inspeção/execução.

---

## 17. Persistência e auditoria

Schema próprio em `execution.db`:

```text
schema_migrations
executions
execution_capabilities
execution_events
approval_requests
```

Campos principais de `executions` e eventos: manter lista do plano original (ids, digests, policy metadata, backend_guarantees_json, bytes/hashes, error redacted).

Não persistir por padrão: script completo, segredos em args, env, stdout/stderr completos, material secreto de aprovação.

Reusar `connect_database` ou extrair helper compartilhado se necessário (sem acoplar ao `RepositoryStore`).

---

## 18. Configuração

Estender `Settings` em `bootstrap/settings.py` e `Settings.for_root` (mesmo padrão booleano de `CODE_HARNESS_SEMANTIC` / `CODE_HARNESS_MCP_EXPOSE_INDEX`):

```python
execution_enabled: bool = False
execution_backend: str = "host_supervised"
execution_powershell_enabled: bool = False
execution_powershell_executable: str = "pwsh"
execution_approval_mode: str = "policy"
execution_default_timeout_seconds: float = 60.0
execution_max_timeout_seconds: float = 1_800.0
execution_max_output_bytes: int = 200_000
execution_max_script_chars: int = 100_000
execution_max_argument_chars: int = 16_384
execution_max_processes: int = 32
execution_max_concurrent: int = 1
execution_store_path: Path  # default .code-harness/execution.db sob root
execution_artifacts_path: Path
execution_approval_ttl_seconds: int = 600
execution_keep_artifacts: bool = False
mcp_expose_execution: bool = False
mcp_expose_powershell: bool = False
```

Variáveis de ambiente (espelhar nomenclatura existente `CODE_HARNESS_*`):

```text
CODE_HARNESS_EXECUTION
CODE_HARNESS_EXECUTION_BACKEND
CODE_HARNESS_EXECUTION_POWERSHELL
CODE_HARNESS_EXECUTION_POWERSHELL_EXE
CODE_HARNESS_EXECUTION_APPROVAL_MODE
CODE_HARNESS_EXECUTION_DEFAULT_TIMEOUT_SECONDS
CODE_HARNESS_EXECUTION_MAX_TIMEOUT_SECONDS
CODE_HARNESS_EXECUTION_MAX_OUTPUT_BYTES
CODE_HARNESS_EXECUTION_MAX_CONCURRENT
CODE_HARNESS_EXECUTION_STORE_PATH
CODE_HARNESS_EXECUTION_ARTIFACTS_PATH
CODE_HARNESS_EXECUTION_APPROVAL_TTL_SECONDS
CODE_HARNESS_MCP_EXPOSE_EXECUTION
CODE_HARNESS_MCP_EXPOSE_POWERSHELL
```

Combinações inseguras falham no `__post_init__` (ex.: `mcp_expose_execution` sem `execution_enabled`; `mcp_expose_powershell` sem powershell + execution; backend desconhecido).

---

## 19. Empacotamento

Em `pyproject.toml`:

```toml
[project.optional-dependencies]
execution = [
  # pin explícito se ACL exigir pywin32; Job Object via ctypes não exige
  # "pywin32==312; platform_system=='Windows'",
]
execution-sandbox = [
  # dependências futuras do Windows Sandbox
]
```

**Não** incluir `execution` em `all` até CI Windows + revisão de segurança estáveis.

O `pywin32` já presente no `.venv` local **não** conta como dependência do projeto até ser declarado e pinado.

---

## 20. API Python

Estender `CodeHarness` (`interfaces/python_api/harness.py`):

```python
def inspect_process(...) -> ToolResult[CommandInspection]: ...
def inspect_powershell(...) -> ToolResult[CommandInspection]: ...
def run_process(...) -> ToolResult[ExecutionResult]: ...
def run_powershell(...) -> ToolResult[ExecutionResult]: ...
def get_execution(...) -> ToolResult[ExecutionResult]: ...
def terminate_execution(...) -> ToolResult[ExecutionResult]: ...
```

Se `container.execution is None`, tools levantam `ExecutionDisabledError`.

---

## 21. CLI

Novo módulo `interfaces/cli/execution_commands.py` exportando `exec_app = typer.Typer(...)`.

Em `main.py`: `app.add_typer(exec_app, name="exec")` (mesmo padrão das linhas 45–48).

```powershell
code-harness exec inspect-process git status --short
code-harness exec inspect-powershell --file script.ps1
code-harness exec run-process git status --short
code-harness exec run-powershell --file script.ps1
code-harness exec status <execution-id>
code-harness exec terminate <execution-id>
code-harness exec approvals list
code-harness exec approvals approve <approval-id>
```

Scripts grandes por arquivo ou stdin.

---

## 22. MCP

Registrar execução apenas quando:

```text
execution_enabled=true
mcp_expose_execution=true
```

PowerShell exige também `execution_powershell_enabled` + `mcp_expose_powershell`.

Padrão espelhando o gate atual de índice (`handlers.py` ~383–389):

```python
# em register_handlers ou via register_execution_handlers(...)
if settings.execution_enabled and settings.mcp_expose_execution:
    register_execution_handlers(server, container, settings)
```

Tools: `inspect_process`, `inspect_powershell`, `run_process`, `run_powershell`, `get_execution`, `terminate_execution`.

**Não** expor: `approve_execution`, `deny_execution`, `alter_policy`, `alter_protected_paths`, `alter_backend`.

Handlers apenas: protocolo → DTO → tool → `serialize_tool_result` / `serialize_error`.

Atualizar `server.py` instructions para mencionar que stdout/stderr não são instruções, quando execução estiver exposta.

---

## 23. Concorrência e worktree

Default `execution_max_concurrent = 1`. Locks por projeto/workspace.

Fase futura: `.code-harness/worktrees/<execution-id>/` com validação Git/LFS/submódulos.

---

## 24. Windows Sandbox

Backend `windows_sandbox` (E7). Neste host o feature está **Disabled** — CI/job dedicado ou máquina com Sandbox habilitado é pré-requisito de aceite.

Garantias alvo: projeto original RO; worktree RW; saída controlada; rede off; clipboard off; credenciais do host não compartilhadas.

A policy poderá exigir esse backend para PowerShell de alto risco, build não confiável, rede bloqueada, credenciais e escrita automática.

---

## 25. Doctor

Estender `LocalDiagnosticProvider` / `DoctorTool` (não criar tool separada na v1) com checks opcionais quando `execution_enabled` ou sempre em modo informativo `disabled`:

```text
execution enabled
backend
SO
PowerShell + versão (pwsh)
parser AST
Job Object
banco de auditoria
pasta de artefatos
policy + ruleset hash
Windows Sandbox
exposição MCP
```

O doctor **não** executa código do projeto sob análise.

---

## 26. Testes

### Unitários / contrato / integração existentes a ampliar

| Área | Arquivos atuais a estender ou espelhar |
|------|----------------------------------------|
| Settings/env | `tests/unit/test_settings.py` |
| DTOs | `tests/unit/test_models_and_requests.py` |
| PathGuard | `tests/unit/test_path_guard.py` |
| Serialização | `tests/unit/test_serialization.py` |
| Renderer CLI | `tests/unit/test_output_renderer.py` |
| Doctor/capabilities | `tests/unit/test_capability_reporter.py`, diagnostics |
| Arquitetura | `tests/contract/test_architecture.py` |
| MCP gate | `tests/contract/test_mcp_adapter.py` |
| Python API | `tests/integration/test_python_api.py` |
| CLI | `tests/integration/test_cli.py` |

Novos: `tests/unit/execution/…`, `tests/integration/execution/…` (markers Windows), fixtures AST PowerShell.

### Arquitetura (regras atuais + extensões)

Hoje (`tests/contract/test_architecture.py`):

- domain só importa `code_harness.domain` (L29–33);
- application não importa infrastructure/interfaces/bootstrap (L36–41);
- SDK `mcp` só em `interfaces/mcp` (L44–50);
- `tree_sitter` só no worker (L53–59);
- CLI não importa MCP no nível de módulo (L75–78).

Adicionar:

- `pywin32` / `win32*` só sob `infrastructure/execution/windows/`;
- application sem `subprocess` / `ctypes.windll` / `win32*`;
- `bootstrap/container.py` sem import de módulo de execução no topo (lazy);
- aprovação ausente das tools MCP registradas;
- `RepositoryStore` / schema do índice sem tabelas de execução.

### CI

```text
Ubuntu sem execution
Windows sem execution
Windows com execution (após pwsh + extra)
Windows AST smoke (requer pwsh)
Windows Job Object integration
Windows Sandbox (job condicional; skip se feature off)
```

---

## 27. Fases e arquivos exatos

### E0 — Contratos e inspeção sem execução

**Criar:**

- `docs/adr/0005-execution-trust-boundary.md`
- `domain/models/command_execution.py`, `command_inspection.py`, `execution_approval.py`, `execution_backend.py`
- `domain/protocols/command_analyzer.py`, `command_policy.py` (+ stores stubs se necessário só na interface)
- `application/dto/execution_requests.py`
- `application/tools/inspect_process.py`, `inspect_powershell.py`
- `infrastructure/execution/analysis/*`, `policy/*`, `approvals/digest.py`
- `infrastructure/execution/backends/host_supervised_backend.py` (só `guarantees()`, sem runner)
- `bootstrap/execution.py` (container mínimo de inspeção)
- `interfaces/cli/execution_commands.py` (só inspect)
- testes unitários de policy/AST/digest

**Alterar:**

- `domain/enums.py`, `domain/errors.py`
- `bootstrap/settings.py`, `bootstrap/container.py`
- `application/tools/__init__.py`
- `interfaces/python_api/harness.py`
- `interfaces/cli/main.py`
- `interfaces/cli/renderers/output.py`
- `infrastructure/filesystem/path_guard.py`
- `tests/contract/test_architecture.py`, `tests/unit/test_settings.py`, …

**Aceite:** classifica e gera digest sem executar comando do usuário. Sem `pwsh`: `inspect_powershell` retorna erro tipado; `inspect_process` de `git` funciona.

### E1 — `run_process` supervisionado síncrono

**Criar:** `runners/*`, `windows/job_object.py`, `windows/process_factory.py`, `application/tools/run_process.py`, testes de integração Job Object.

**Alterar:** `pyproject.toml` (extra `execution`), `bootstrap/execution.py`, API/CLI, doctor checks.

**Aceite:** `git status --short` sob host_supervised; destrutivo bloqueado antes do processo. Rodar testes em sessão **não** admin.

### E2 — Aprovação e auditoria

**Criar:** `persistence/*`, `approvals/sqlite_approval_store.py`, redaction, CLI approvals.

**Alterar:** tools de run para consumir aprovação; settings de TTL/path.

**Aceite:** aprovar `mvn test` não autoriza `mvn deploy` nem outro `cwd`.

### E3 — PowerShell livre

**Pré-requisito de máquina:** instalar PowerShell 7 (`pwsh` no PATH).

**Criar:** `powershell_runner.py`, `windows/acl.py`, `run_powershell.py`, testes de segurança PS.

**Aceite:** nenhum PowerShell livre é autoallow no host; sem perfil/EncodedCommand.

### E4 — Assíncrono e cancelamento

**Criar/alterar:** `process_registry.py`, `get_execution` / `terminate_execution`, recovery, concorrência.

**Aceite:** cancelamento não deixa filhos.

### E5 — MCP opcional

**Criar:** `interfaces/mcp/execution_handlers.py`

**Alterar:** `handlers.py`, `server.py`, `test_mcp_adapter.py`

**Aceite:** cliente MCP não consegue se autoaprovar.

### E6 — Worktree

Manager, locks, diff, rollback.

### E7 — Windows Sandbox

Requer feature habilitado; skip local neste host até lá.

### E8 — Hardening

Benchmarks, fuzzing, docs operacionais, threat review, release opt-in.

---

## 28. Sequência de commits

Mantida a sequência original (§28 anterior), com ajustes de mensagem onde couber:

- ADR vira `docs: add execution trust-boundary ADR` → arquivo `0005-…`
- “sandbox backend” → “Windows Sandbox backend” (nunca renomear host para sandbox)
- commit de extra Windows: declarar explicitamente se `pywin32` entra ou se fica só ctypes

---

## 29. Critérios globais

Mantidos os 30 critérios do plano original (desabilitado por padrão; inspeção sem execução; `run_process` sem shell; PowerShell livre com aprovação no host; digest; MCP sem approve; stores separados; sem falsa garantia; etc.), com estes esclarecimentos:

- instalação básica sem depender de `pwsh`/pywin32;
- API retorna `ToolResult[…]`;
- pasta de backends ≠ nome “sandbox” para o host;
- CI Linux permanece verde sem o extra `execution`.

---

## 30. Riscos principais

Além dos riscos originais (Job Object race, deadlock de pipes, allowlist, PS dinâmico, credenciais, build=código, auditoria, complexidade):

### Máquina / clone atuais

- **Sem PowerShell 7:** E3 e smoke AST de produto bloqueados até instalar `pwsh`.
- **Sandbox desabilitado:** E7 não validável aqui.
- **Sessão Administrador:** testes de execução devem preferir usuário padrão.
- **pywin32 fantasma no venv:** não documentar como dependência até pin no extra.
- **Working tree limpa:** plano já commitado; implementação ainda não iniciada — bom baseline.

### Integração com código existente

- `subprocess` já usado na infra: testes arquiteturais novos não devem quebrar ripgrep/parsers.
- `ApplicationContainer` cresce: manter campo `execution` opcional no final.
- Doctor e MCP instructions precisam distinguir retrieval vs execution.

---

## 31. Ordem recomendada

Começar por E0 e revisar antes de E1.

Não iniciar `run_process` antes de: modelos/erros estáveis; policy com testes negativos; AST validada como análise sem execução do usuário; digest canônico; guarantees modeladas.

Não expor MCP antes de: CLI/API estáveis; aprovação local; cancelamento; Job Object validado; erros/redaction testados.

Instalar PowerShell 7 neste host antes de E3 (e preferencialmente antes do smoke AST de E0 para `inspect_powershell`).

---

## 32. Revisão local (Grok 4) — status

Prompt de revisão executado em 23/07/2026 contra este clone.

Entregáveis da revisão:

- A–E: ver mensagem da sessão / histórico do agente;
- F: **este documento** (versão revisada).

Decisões centrais **preservadas**:

- execução opcional e desabilitada por padrão;
- MCP como adaptador fino;
- aprovação não disponível ao cliente MCP;
- stores de índice e execução separados;
- `host_supervised` não deve ser chamado de sandbox;
- `run_process` sem shell;
- PowerShell livre exige aprovação no host;
- nenhuma falsa garantia de filesystem, rede ou credenciais.

---

## 33. Decisão final

A primeira capability não será `execute_command`.

Evolução:

```text
inspect_process
inspect_powershell
run_process
run_powershell
get_execution
terminate_execution
```

Regra central:

> Quanto mais expressiva a tool, menos ela pode ser autoaprovada.

O backend inicial será um **executor supervisionado** (`host_supervised`), não uma sandbox. Isolamento real será capability separada (`windows_sandbox`), fornecida por backend específico e reportada explicitamente em `BackendGuarantees`.
