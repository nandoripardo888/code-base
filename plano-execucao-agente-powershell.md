# Plano de Implementação — Execução de Comandos para Agentes

## 0. Controle do documento

Última atualização: **23 de julho de 2026**.

Este documento define a introdução de uma capacidade opcional de execução de comandos no `code-harness`, com foco inicial em Windows e PowerShell.

O plano **não altera o comportamento atual do produto**: enquanto nenhuma fase for implementada e habilitada, o `code-harness` continua sendo uma ferramenta local-first e somente leitura para recuperação de contexto.

A execução deverá ser entregue como capacidade separada, desabilitada por padrão e removível sem afetar busca, leitura, indexação, análise estrutural, busca semântica, construção de contexto ou o MCP de recuperação.

### 0.1 Estado atual relevante

O repositório já possui:

- arquitetura com dependências direcionadas para dentro;
- application tools compartilhadas pela API Python, CLI e MCP;
- composição manual em `bootstrap/container.py`;
- DTOs imutáveis e erros tipados;
- acesso a arquivos protegido pela raiz do projeto;
- processos isolados para parsers e embeddings;
- SQLite versionado para o índice;
- handlers MCP finos;
- testes arquiteturais, de contrato e cobertura mínima.

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
    └── sandbox opcional
```

A instalação básica continuará somente leitura.

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
- exposição de uma tool MCP capaz de aprovar a própria solicitação.

---

## 3. Princípios obrigatórios

### 3.1 Desabilitado por padrão

```text
execution_enabled = false
mcp_expose_execution = false
mcp_expose_powershell = false
```

Nenhuma dependência Windows deverá ser importada quando a execução estiver desabilitada.

### 3.2 Inspeção separada da execução

`inspect_command` não poderá executar o comando, carregar perfil PowerShell, importar módulos do projeto ou iniciar scripts do repositório.

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

Se uma solicitação exigir isolamento não suportado, deverá falhar com erro tipado.

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
20. comandos aparentemente de leitura que executam código do projeto.

---

## 5. Decisão arquitetural

Manter:

```text
interfaces ───▶ application ───▶ domain
                       ▲
                       │
               infrastructure
```

O MCP continuará somente como adaptador.

### 5.1 Composição opcional

```python
@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    # tools atuais
    execution: ExecutionContainer | None = None
```

```text
build_container(settings)
    ├── monta retrieval/indexação normalmente
    └── se execution_enabled:
            build_execution_container(settings, project, guard)
```

### 5.2 Persistência separada

Não adicionar execução ao `RepositoryStore`, que representa o índice.

Criar:

```text
ExecutionStore
ApprovalStore
ExecutionArtifactStore
```

Banco padrão:

```text
.code-harness/execution.db
```

O índice permanece em:

```text
.code-harness/index.db
```

---

## 6. Estrutura proposta

```text
src/code_harness/
├── domain/
│   ├── models/
│   │   ├── command_execution.py
│   │   ├── command_inspection.py
│   │   ├── execution_approval.py
│   │   └── execution_backend.py
│   ├── protocols/
│   │   ├── command_analyzer.py
│   │   ├── command_policy.py
│   │   ├── command_runner.py
│   │   ├── execution_store.py
│   │   ├── approval_store.py
│   │   └── execution_artifact_store.py
│   └── enums.py
│
├── application/
│   ├── dto/
│   │   └── execution_requests.py
│   └── tools/
│       ├── inspect_command.py
│       ├── run_process.py
│       ├── run_powershell.py
│       ├── get_execution.py
│       └── terminate_execution.py
│
├── infrastructure/
│   └── execution/
│       ├── analysis/
│       │   ├── process_analyzer.py
│       │   ├── powershell_ast_analyzer.py
│       │   ├── powershell_parser.ps1
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
│       │   ├── job_object.py
│       │   ├── process_factory.py
│       │   └── acl.py
│       ├── sandbox/
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
│   └── execution.py
│
└── interfaces/
    ├── cli/
    │   └── execution_commands.py
    ├── mcp/
    │   └── execution_handlers.py
    └── python_api/
        └── harness.py
```

---

## 7. Modelos e enums

Adicionar:

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
```

Capacidades:

```python
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
```

Aprovação:

```python
class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"
    EXPIRED = "expired"
```

Backends:

```python
class ExecutionBackendKind(StrEnum):
    HOST_SUPERVISED = "host_supervised"
    WINDOWS_SANDBOX = "windows_sandbox"
```

Garantias:

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
```

Solicitação normalizada:

```python
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
```

Inspeção:

```python
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
```

Resultado:

```python
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

---

## 8. Erros tipados

Adicionar em `ErrorCode`:

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

---

## 9. DTOs

Criar `application/dto/execution_requests.py`.

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

Validar:

- timeout positivo e abaixo do máximo;
- limite de saída positivo;
- `cwd` não vazio;
- script não vazio;
- tamanho máximo do script;
- quantidade e tamanho dos argumentos;
- capabilities sem duplicação.

---

## 10. Application tools

### `InspectCommandTool`

1. normaliza `cwd`;
2. localiza executável;
3. analisa programa/argumentos ou AST PowerShell;
4. infere capacidades;
5. detecta paths protegidos;
6. consulta guarantees do backend;
7. executa policy;
8. calcula digest;
9. retorna `ToolResult[CommandInspection]`.

Nunca inicia o comando.

### `RunProcessTool`

```text
validar request
    ↓
inspect_command
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

- exige `powershell_enabled`;
- usa executável resolvido;
- `-NoLogo -NoProfile -NonInteractive`;
- script temporário controlado;
- ACL restrita;
- limpeza em `finally`;
- aprovação obrigatória no host;
- proíbe `EncodedCommand`;
- não reutiliza sessão.

### `GetExecutionTool`

Retorna estado e saída coletada, sem aceitar caminho arbitrário.

### `TerminateExecutionTool`

Encerra a árvore inteira e é idempotente.

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

O Python chama um script interno constante. O script do usuário entra como dado e vira JSON, sem ser executado.

Extrair:

- comandos;
- pipelines;
- redirecionamentos;
- call operator;
- dot sourcing;
- invocações dinâmicas;
- acesso a membros .NET;
- `Add-Type`;
- importação de módulos;
- caminhos literais;
- cmdlets de rede;
- registro;
- serviços;
- tarefas agendadas;
- criação de processos;
- remoção e sobrescrita;
- encoded commands;
- comando definido por variável.

Dinâmico:

```powershell
& $comando
Invoke-Expression $texto
. $arquivo
Import-Module $caminhoDinamico
Start-Process $programa
```

Comportamento desconhecido nunca vira `allow`.

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
8. backend suporta as garantias?
9. regra permite autoexecução?
10. aprovação necessária?
```

Decisões:

```text
ALLOW
APPROVAL_REQUIRED
DENY
```

### Hard deny inicial

- elevação administrativa;
- `RunAs`;
- desativação de antivírus/firewall;
- acesso a processos de credenciais;
- alteração da policy/auditoria;
- alteração de hooks de segurança;
- formatação e partições;
- criação de administrador;
- leitura de chaves privadas conhecidas;
- execução fora da raiz;
- `EncodedCommand`;
- perfil PowerShell;
- shell interativo.

### Autoallow inicial

Conjunto pequeno:

```text
git status
git diff
git diff --stat
git log com limites
rg
python --version
pwsh --version
mvn --version
ant -version
```

Builds e testes executam código do repositório; no host exigem aprovação.

### Paths protegidos

```text
.git/config
.git/hooks/**
.code-harness/**
.env
.env.*
**/*.pem
**/*.key
**/*credential*
**/*secret*
configuração da policy
scripts internos do executor
```

A policy publica:

```text
policy_name
policy_version
ruleset_hash
```

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

Estados:

```text
pending → approved → consumed
        ↘ denied
        ↘ expired
```

A aprovação:

- expira;
- é de uso único;
- pertence ao projeto e digest;
- é consumida atomicamente;
- registra origem quando possível.

CLI:

```powershell
code-harness exec approvals list
code-harness exec approvals show <approval-id>
code-harness exec approvals approve <approval-id>
code-harness exec approvals deny <approval-id>
```

---

## 14. Backend `host_supervised`

O nome oficial não será `sandbox`.

### 14.1 Processo suspenso

No Windows:

1. criar processo suspenso;
2. atribuir ao Job Object;
3. retomar thread principal.

Isso evita filhos antes da contenção.

`pywin32` poderá ser usado no extra Windows, após validação de versão.

### 14.2 Job Object

- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`;
- limite de processos;
- limite de memória opcional;
- encerramento da árvore;
- cleanup idempotente.

### 14.3 Ambiente por allowlist

Manter apenas o necessário:

```text
SystemRoot
WINDIR
TEMP/TMP controlados
PATH sanitizado
PATHEXT
HOME/USERPROFILE quando indispensável
```

Não herdar automaticamente:

```text
AWS_*
AZURE_*
GOOGLE_*
GITHUB_TOKEN
GH_TOKEN
NPM_TOKEN
DOCKER_*
KUBECONFIG
SSH_AUTH_SOCK
variáveis internas
```

### 14.4 Rede e credenciais

O host não garante bloqueio de rede nem isolamento de credenciais. Portanto:

- `network_outbound` nunca é autoaprovada;
- garantia de rede bloqueada exige backend isolado;
- resultado declara `network_isolated=false`;
- `credential_access` é risco elevado.

---

## 15. PowerShell Runner

Executar:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -File <script-controlado.ps1>
```

Requisitos:

- PowerShell 7;
- caminho absoluto resolvido;
- script UTF-8;
- nada interpolado em outra linha PowerShell;
- ACL restrita;
- `cwd` definido pelo processo;
- stdout e stderr separados;
- encoding normalizado;
- timeout;
- Job Object;
- limpeza em `finally`;
- hash do script;
- script completo não persistido por padrão;
- sem stdin interativo na primeira versão.

---

## 16. Saída

Aplicar limites separados para stdout, stderr e combinado.

- ler ambos continuamente;
- impedir deadlock;
- marcar truncamento;
- registrar bytes;
- preservar início/final quando configurado;
- redigir tokens, Authorization headers, URLs com credenciais e private keys;
- não persistir saída completa por padrão.

A primeira versão pode ser síncrona. A fase assíncrona adiciona:

```text
wait=false
get_execution
terminate_execution
```

---

## 17. Persistência e auditoria

Schema próprio:

```text
execution_schema_migrations
executions
execution_capabilities
execution_events
approval_requests
```

Campos principais de `executions`:

```text
execution_id
project_id
command_kind
command_digest
command_display_redacted
script_hash
cwd
state
policy_decision
policy_name
policy_version
ruleset_hash
backend
backend_guarantees_json
requested_capabilities_json
required_capabilities_json
approval_id
started_at
finished_at
elapsed_ms
exit_code
stdout_bytes
stderr_bytes
stdout_hash
stderr_hash
stdout_truncated
stderr_truncated
error_code
error_message_redacted
```

Eventos:

```text
created
inspection_completed
approval_requested
approval_granted
approval_consumed
process_starting
process_started
output_truncated
timeout_requested
termination_requested
process_finished
cleanup_finished
```

Não persistir por padrão:

- script completo;
- segredos em argumentos;
- valores do ambiente;
- stdout/stderr completos;
- material secreto de aprovação.

---

## 18. Configuração

Adicionar em `Settings`:

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
execution_store_path: Path
execution_artifacts_path: Path
execution_approval_ttl_seconds: int = 600
execution_keep_artifacts: bool = False
mcp_expose_execution: bool = False
mcp_expose_powershell: bool = False
```

Variáveis:

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

Combinações inseguras devem falhar no `__post_init__`.

---

## 19. Empacotamento

```toml
[project.optional-dependencies]
execution = [
  # dependências Windows validadas
]
execution-sandbox = [
  # dependências futuras
]
```

Não incluir `execution` em `all` até a CI Windows e a revisão de segurança estarem estáveis.

---

## 20. API Python

```python
inspection = harness.inspect_process(
    executable="git",
    args=("status", "--short"),
)

result = harness.run_process(
    executable="git",
    args=("status", "--short"),
)

inspection = harness.inspect_powershell(
    script="Get-ChildItem -Recurse",
)
```

A API retorna objetos tipados.

---

## 21. CLI

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

Scripts grandes devem vir por arquivo ou stdin, evitando histórico do shell.

---

## 22. MCP

Registrar execução apenas quando:

```text
execution_enabled=true
mcp_expose_execution=true
```

PowerShell exige também:

```text
execution_powershell_enabled=true
mcp_expose_powershell=true
```

Tools:

```text
inspect_process
inspect_powershell
run_process
run_powershell
get_execution
terminate_execution
```

Não expor:

```text
approve_execution
deny_execution
alter_policy
alter_protected_paths
alter_backend
```

Handlers apenas traduzem protocolo → DTO → tool → JSON.

---

## 23. Concorrência e worktree

Default:

```text
execution_max_concurrent = 1
```

Locks por projeto e workspace gravável.

Fase futura:

```text
.code-harness/worktrees/<execution-id>/
```

Antes de usar worktree, validar Git, alterações locais, submódulos, LFS e arquivos ignorados necessários ao build.

---

## 24. Windows Sandbox

Fase de isolamento forte:

```text
projeto original       → somente leitura
worktree temporário    → leitura e escrita
saída controlada       → leitura e escrita
rede                    → desabilitada
clipboard               → desabilitado
credenciais do host     → não compartilhadas
```

A policy poderá exigir esse backend para:

- PowerShell livre de alto risco;
- build não confiável;
- garantia de rede bloqueada;
- isolamento de credenciais;
- escrita automática.

---

## 25. Doctor

Adicionar diagnóstico:

```text
execution enabled
backend
sistema operacional
PowerShell e versão
parser AST
Job Object
banco de auditoria
pasta de artefatos
policy e ruleset hash
Windows Sandbox
exposição MCP
```

O doctor não executa código do projeto.

---

## 26. Testes

### Unitários

- DTOs;
- normalização;
- digest;
- capabilities;
- hard deny;
- autoallow;
- paths protegidos;
- policy por backend;
- approvals;
- redaction;
- limite de saída;
- transições.

### AST PowerShell

Fixtures:

```powershell
Get-ChildItem
Get-Content .\arquivo.txt
Remove-Item -Recurse -Force .\pasta
Invoke-WebRequest https://example.com
& $comando
Invoke-Expression $texto
Start-Process powershell
Set-ItemProperty HKLM:\...
Get-Service
Stop-Service
Add-Type -TypeDefinition $codigo
Import-Module .\modulo.psm1
```

Confirmar que o parser não executa.

### Runner

- stdout/stderr;
- exit code;
- timeout;
- cancelamento;
- filho e neto;
- Job Object encerra árvore;
- saída excessiva;
- processo que não fecha pipes;
- Unicode;
- cleanup;
- limite de concorrência.

### Segurança

- `cwd` fora da raiz;
- symlink;
- executável não permitido;
- `git reset --hard`;
- `git clean -fdx`;
- `git push`;
- PowerShell dinâmico;
- `EncodedCommand`;
- alteração de `.code-harness`;
- digest diferente;
- aprovação expirada/reutilizada;
- MCP tentando aprovar;
- token no ambiente do pai não chegando ao filho.

### Arquitetura

- MCP somente em `interfaces/mcp`;
- domain sem infraestrutura;
- application sem subprocess/pywin32/FastMCP;
- execução não importada quando desabilitada;
- `RepositoryStore` sem execução;
- aprovação ausente das tools MCP.

### CI

```text
Ubuntu sem execution
Windows sem execution
Windows com execution
Windows AST smoke
Windows Job Object integration
```

Windows Sandbox em job separado e condicional.

---

## 27. Fases

### E0 — Contratos e inspeção sem execução

- ADR;
- modelos/enums/errors;
- DTOs;
- protocolos;
- analyzer de processo;
- AST PowerShell;
- policy;
- digest;
- CLI de inspeção;
- testes.

**Aceite:** classifica e gera digest sem executar nada.

### E1 — `run_process` supervisionado síncrono

- extra execution;
- backend host;
- executável seguro;
- sem shell;
- Job Object;
- timeout;
- stdout/stderr;
- ambiente;
- tool/API/CLI.

**Aceite:** `git status --short` funciona; destrutivo é bloqueado antes do processo.

### E2 — Aprovação e auditoria

- `execution.db`;
- stores;
- digest de uso único;
- expiração;
- CLI;
- eventos;
- redaction.

**Aceite:** aprovar `mvn test` não autoriza `mvn deploy` nem outro `cwd`.

### E3 — PowerShell livre

- runner PowerShell;
- `NoProfile`;
- script protegido;
- AST obrigatória;
- aprovação obrigatória no host;
- cleanup.

**Aceite:** nenhum PowerShell livre é autoallow no host.

### E4 — Assíncrono e cancelamento

- registry;
- `wait=false`;
- get/terminate;
- logs limitados;
- recovery;
- concorrência;
- shutdown cleanup.

**Aceite:** cancelamento não deixa filhos.

### E5 — MCP opcional

- handlers finos;
- flags;
- sem aprovação MCP;
- contratos;
- instruções de saída não confiável.

**Aceite:** cliente MCP não consegue se autoaprovar.

### E6 — Worktree

- manager;
- locks;
- diff;
- rollback;
- políticas de gerados.

**Aceite:** execuções graváveis não compartilham checkout.

### E7 — Windows Sandbox

- detecção;
- configuração;
- mapeamentos;
- rede;
- worker;
- guarantees.

**Aceite:** não lê perfil nem acessa rede quando bloqueados.

### E8 — Hardening

- benchmarks;
- fuzzing;
- projetos grandes;
- documentação;
- threat review;
- retenção;
- dependências;
- release opt-in.

---

## 28. Sequência de commits

1. `docs: add execution trust-boundary ADR`
2. `feat: add execution domain models and errors`
3. `feat: add execution request DTOs`
4. `feat: add command analyzer protocols`
5. `feat: add deterministic execution policy`
6. `feat: add PowerShell AST inspection worker`
7. `feat: expose command inspection through python api`
8. `feat: add execution inspection CLI`
9. `test: add command inspection security cases`
10. `build: add optional Windows execution extra`
11. `feat: add Windows Job Object containment`
12. `feat: add supervised process runner`
13. `feat: add output limits and environment sanitization`
14. `feat: add run process application tool`
15. `test: add supervised runner integration tests`
16. `feat: add execution database and migrations`
17. `feat: add digest-bound approval lifecycle`
18. `feat: add local approval CLI`
19. `feat: add execution audit events and redaction`
20. `feat: add supervised PowerShell runner`
21. `test: add PowerShell execution security scenarios`
22. `feat: add asynchronous execution registry`
23. `feat: add execution status and termination tools`
24. `feat: add optional MCP execution handlers`
25. `test: add execution API CLI MCP contracts`
26. `feat: add isolated Git worktree execution`
27. `feat: add Windows Sandbox backend`
28. `perf: add command execution benchmarks`
29. `docs: finalize execution operations and security guide`

---

## 29. Critérios globais

1. desabilitada por padrão;
2. instalação básica sem dependências Windows;
3. inspeção não executa;
4. `run_process` sem shell;
5. PowerShell livre exige aprovação no host;
6. aprovação ligada ao digest;
7. aprovação expira e é de uso único;
8. MCP sem tool de aprovação;
9. `cwd` externo rejeitado;
10. árvore encerrada no timeout;
11. saída limitada;
12. ambiente sem tokens por padrão;
13. auditoria com redaction;
14. backend declara guarantees reais;
15. garantia não suportada falha;
16. policy determinística/versionada;
17. bloqueado não inicia processo;
18. PowerShell sem perfil/interatividade;
19. cancelamento sem órfãos;
20. auditoria com hashes/transições;
21. stores separados;
22. SDK MCP restrito;
23. equivalência API/CLI/MCP;
24. retrieval sem extra;
25. limitações do host documentadas;
26. Sandbox exigida para isolamento real;
27. testes Windows com/sem extra;
28. core Linux preservado;
29. policy/auditoria protegidas;
30. nenhuma falsa alegação de segurança.

---

## 30. Riscos principais

### Processo suspenso e Job Object

Atribuir depois de iniciar cria janela de escape. Criar suspenso, atribuir e retomar.

### Deadlock em pipes

Coletar stdout/stderr concorrentemente.

### Allowlist ampla

Regra por executável, subcomando, argumentos e capabilities.

### PowerShell dinâmico

Desconhecido nunca é autoallow.

### Credenciais no host

Ambiente sanitizado não isola Credential Manager ou perfil. Exigir aprovação/sandbox.

### Build executa código

`mvn test`, `pytest`, `npm test` recebem `execute_repository_code`.

### Auditoria como vazamento

Persistir metadados/hashes, aplicar redaction e retenção.

### Complexidade

Começar por E0; não implementar tudo em paralelo.

---

## 31. Ordem recomendada

Começar por E0 e revisar antes de E1.

Não iniciar `run_process` antes de:

- modelos e erros estáveis;
- policy com testes negativos;
- AST validada como análise sem execução;
- digest canônico;
- guarantees modeladas.

Não expor MCP antes de:

- CLI/API estáveis;
- aprovação local;
- cancelamento;
- Job Object validado;
- erros/redaction testados.

---

## 32. Prompt para o Grok 4 no clone local

```text
Revise o arquivo docs/plano-execucao-agente-powershell.md contra o clone local
atual do repositório code-harness.

Objetivo: validar e corrigir o plano, sem implementar nenhuma alteração.

Para cada seção técnica relevante:

1. confirme os arquivos, classes, protocolos e convenções atuais afetados;
2. identifique divergências entre o plano e o código local;
3. localize alterações não commitadas ou arquivos locais relevantes;
4. confirme se os caminhos propostos seguem a organização atual;
5. proponha ajustes mínimos quando houver incompatibilidade;
6. confirme os testes existentes que devem ser ampliados;
7. verifique como os testes arquiteturais atuais restringem imports;
8. verifique a composição atual do ApplicationContainer e CodeHarness;
9. verifique serializers, renderers e tratamento de erros atuais;
10. verifique o sistema de migrations SQLite existente;
11. verifique disponibilidade local de PowerShell 7 e APIs Windows necessárias;
12. aponte riscos específicos da máquina e do clone;
13. não execute scripts do repositório para validar segurança;
14. não implemente código;
15. não faça commit.

Entregue:

A. resumo executivo;
B. divergências encontradas, com arquivo e linha;
C. arquivos exatos a criar e alterar por fase;
D. ajustes recomendados no plano;
E. riscos ainda não cobertos;
F. uma versão revisada completa do plano em Markdown.

Preserve as decisões centrais:

- execução opcional e desabilitada por padrão;
- MCP como adaptador fino;
- aprovação não disponível ao cliente MCP;
- stores de índice e execução separados;
- host_supervised não deve ser chamado de sandbox;
- run_process sem shell;
- PowerShell livre exige aprovação no host;
- nenhuma falsa garantia de filesystem, rede ou credenciais.
```

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

O backend inicial será um **executor supervisionado**, não uma sandbox. Isolamento real será capability separada, fornecida por backend específico e reportada explicitamente.
