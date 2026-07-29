# Avaliação e plano revisado — aprovação host e code review

> **Status de implementação:** concluída — **Fase 6 — Ações de review**
>
> | Fase | Nome | Status |
> |------|------|--------|
> | 1 | Canal genérico de decisão | concluída |
> | 2 | Host loopback | concluída |
> | 3 | Integração MCP | concluída |
> | 4 | Change sets somente leitura | concluída |
> | 5 | Análise de impacto e contexto | concluída |
> | 6 | Ações de review | **em andamento** |

## 1. Veredito

O plano faz sentido e é compatível com a arquitetura atual.

O projeto já possui:

* inspeção separada da execução;
* aprovação vinculada ao digest exato do comando;
* consumo único da aprovação;
* auditoria;
* aprovação MCP vinculada à sessão;
* lifecycle centralizado;
* teste arquitetural impedindo tools MCP de aprovação.

O MCP também já foi concebido como um adapter fino sobre as ferramentas de aplicação, e não como o lugar onde a lógica de negócio deve residir. Isso está alinhado tanto com a documentação atual quanto com o desenho original do harness.

Portanto, o fluxo abaixo é válido:

```text
MCP solicita execução
        ↓
política exige aprovação
        ↓
UI local é aberta no host
        ↓
humano aprova ou recusa
        ↓
aprovação exata é persistida
        ↓
execução continua
```

A principal ressalva é que o plano atual mistura três responsabilidades:

```text
canal de interação humana
persistência de aprovação
execução de comandos
```

Essas responsabilidades devem permanecer separadas.

---

## 2. O que não deveria ser implementado exatamente como está

### 2.1 `HostApprovalPrompt` não deve receber `ExecutionApproval`

O protocolo proposto:

```python
class HostApprovalPrompt(Protocol):
    async def prompt(
        self,
        approval: ExecutionApproval,
        *,
        timeout_seconds: float,
    ) -> ApprovalState: ...
```

já nasce acoplado ao domínio de execução.

Isso dificulta reutilizá-lo futuramente para:

* aplicar um patch;
* sobrescrever um arquivo;
* executar testes sobre mudanças;
* criar um commit;
* publicar comentários em um pull request;
* aprovar uma migração;
* confirmar uma operação Git.

O canal deveria trabalhar com uma representação genérica:

```python
@dataclass(frozen=True, slots=True)
class HumanDecisionRequest:
    request_id: str
    subject_kind: str
    subject_digest: str
    title: str
    summary: str
    details: tuple[DecisionDetail, ...]
    risks: tuple[RiskFinding, ...]
    options: tuple[DecisionOption, ...]
    expires_at: str
```

E retornar:

```python
@dataclass(frozen=True, slots=True)
class HumanDecisionResult:
    outcome: str
    source: str
    reason: str | None = None
```

Possíveis outcomes:

```text
approved
denied
cancelled
timed_out
unavailable
```

`timeout` e `unavailable` não devem ser representados como `ApprovalState`, pois não são decisões humanas.

---

### 2.2 O servidor HTTP não deve chamar diretamente `ApprovalAdminTool`

No plano original, o handler HTTP dentro de `infrastructure` chamaria:

```python
ApprovalAdminTool.decide(...)
```

Isso acopla a infraestrutura HTTP à camada de aplicação.

O desenho mais limpo é:

```text
HostLoopbackDecisionChannel
        ↓ retorna decisão
InteractiveApprovalService
        ↓ persiste
ApprovalAdminTool / ApprovalStore
```

Estrutura sugerida:

```text
domain/
  models/
    human_decision.py
  protocols/
    human_decision_channel.py

application/
  approvals/
    interactive_approval_service.py
    execution_approval_presenter.py

infrastructure/
  interaction/
    host_loopback_channel.py

interfaces/
  mcp/
    mcp_elicitation_channel.py
```

Protocolo:

```python
class HumanDecisionChannel(Protocol):
    @property
    def source(self) -> str: ...

    async def request_decision(
        self,
        request: HumanDecisionRequest,
        *,
        timeout_seconds: float,
    ) -> HumanDecisionResult: ...

    def shutdown(self) -> None: ...
```

Implementações:

```text
McpElicitationDecisionChannel
HostLoopbackDecisionChannel
CLIInteractiveDecisionChannel    # possível futuramente
NativeHostDecisionChannel        # possível futuramente
```

---

## 3. O que deve continuar específico de execução

Não é necessário transformar imediatamente toda a persistência em um sistema universal de autorização.

Hoje, `ExecutionApproval` e `ApprovalStore` possuem campos explicitamente relacionados a comandos:

* `command_kind`;
* `command_summary`;
* `required_capabilities`;
* `backend`;
* política de execução.

Isso aparece tanto no modelo quanto no protocolo da store.

Minha recomendação é:

```text
agora:
    generalizar o canal de decisão humana

depois:
    generalizar a autorização de ações quando surgir
    a primeira operação não relacionada a execução
```

Assim, evita-se uma abstração excessiva antes de haver um segundo consumidor real.

Quando surgirem `apply_patch`, `commit_changes` ou `publish_review`, será possível evoluir de:

```text
ExecutionApproval
```

para:

```text
ActionApproval
```

com campos:

```text
subject_kind
subject_digest
subject_summary
required_capabilities
policy_name
policy_version
ruleset_hash
```

Exemplos de `subject_kind`:

```text
process_execution
powershell_execution
apply_patch
create_commit
publish_review
push_branch
```

---

## 4. Configuração mais genérica

Atualmente existem:

```text
CODE_HARNESS_MCP_EXECUTION_ELICITATION
CODE_HARNESS_MCP_EXECUTION_ELICITATION_TRUST_MODE
```

E o código aceita somente:

```text
disabled
local_interactive
```

Além disso, quando a elicitação está habilitada, a validação exige obrigatoriamente `local_interactive`.

O termo `elicitation` não descreve corretamente `host_loopback`, porque nesse modo não existe elicitação MCP.

A configuração mais clara seria uma única variável:

```text
CODE_HARNESS_MCP_EXECUTION_APPROVAL_CHANNEL
```

Valores:

```text
disabled
mcp_elicitation
host_loopback
```

Exemplo:

```powershell
$env:CODE_HARNESS_MCP_EXECUTION_APPROVAL_CHANNEL = "host_loopback"
```

Isso elimina a combinação redundante:

```text
enabled=true
trust_mode=host_loopback
```

Para preservar compatibilidade:

1. verificar primeiro a nova variável;
2. se ausente, interpretar as variáveis antigas;
3. emitir warning de depreciação;
4. remover as antigas apenas em uma versão maior.

No domínio, utilizar enums em vez de strings:

```python
class ApprovalChannel(str, Enum):
    DISABLED = "disabled"
    MCP_ELICITATION = "mcp_elicitation"
    HOST_LOOPBACK = "host_loopback"


class ApprovalDecisionSource(str, Enum):
    LOCAL_ADMIN = "local_admin"
    MCP_ELICITATION = "mcp_elicitation"
    HOST_LOOPBACK = "host_loopback"
```

Hoje `decision_source` é validado por comparações de strings tanto na aplicação quanto no SQLite store.

---

## 5. Orquestração sugerida

O atual `_run_with_optional_elicitation` concentra:

* detecção da capability MCP;
* criação da elicitação;
* decisão;
* persistência;
* session binding;
* nova tentativa de execução.

Isso é adequado para o primeiro caso, mas ficará difícil de estender.

Substituir conceitualmente por:

```python
async def _run_with_optional_approval(
    *,
    approval_channel: HumanDecisionChannel | None,
    approval_service: InteractiveApprovalService,
    inspect: Callable[[], CommandInspection],
    run: Callable[[str | None, str | None], ToolResult[Any]],
) -> dict[str, Any]:
    ...
```

Fluxo:

```text
1. inspecionar ação
2. procurar aprovação reutilizável para o digest
3. tentar executar
4. receber ExecutionApprovalRequiredError
5. selecionar o canal configurado
6. apresentar a solicitação
7. persistir approve/deny
8. executar novamente com approval_id
```

O adapter MCP apenas seleciona o canal:

```python
match settings.approval_channel:
    case ApprovalChannel.MCP_ELICITATION:
        channel = McpElicitationDecisionChannel(ctx)

    case ApprovalChannel.HOST_LOOPBACK:
        channel = execution.host_loopback_channel

    case _:
        channel = None
```

---

## 6. Melhorias necessárias na store

### 6.1 Não procurar aprovação listando 200 registros

Atualmente `_approved_for_digest` lista até 200 aprovações aprovadas e procura o digest em memória.

Adicionar ao protocolo:

```python
def find_reusable_approval(
    self,
    project_id: str,
    *,
    digest: str,
    allowed_sources: tuple[str, ...],
) -> ExecutionApproval | None: ...
```

Consulta:

```sql
SELECT *
  FROM approval_requests
 WHERE project_id = ?
   AND digest = ?
   AND state = 'approved'
   AND expires_at > ?
   AND decision_source IN (...)
 ORDER BY decided_at DESC
 LIMIT 1
```

Índice sugerido:

```sql
CREATE INDEX IX_APPROVAL_REUSABLE_DIGEST
    ON approval_requests (
        project_id,
        digest,
        state,
        expires_at
    );
```

### 6.2 Binding não deve depender de `if source == ...`

Hoje o session binding é aplicado apenas quando:

```text
decision_source == mcp_elicitation
```

Para crescer de forma limpa:

```python
class ApprovalBinding(str, Enum):
    NONE = "none"
    MCP_SESSION = "mcp_session"
    HOST_INSTANCE = "host_instance"
```

Assim, o consumo verifica a estratégia de binding, e não o nome do canal.

Para `host_loopback`, inicialmente:

```text
binding = none
```

ou, preferencialmente:

```text
binding = host_instance
source_instance_id = service_instance_id
```

Isso impede que uma aprovação criada por outra instância antiga do serviço seja confundida com a instância atual, quando essa restrição for desejável.

---

## 7. Segurança do loopback

### 7.1 Limite real da proteção

`127.0.0.1 + CSRF` protege contra:

* agente MCP remoto tentando aprovar diretamente;
* formulário cross-site simples;
* exposição acidental pela rede;
* tunnel remoto que não tenha acesso arbitrário ao host.

Mas não protege contra:

* processo malicioso executando no mesmo usuário;
* malware local;
* outro processo local capaz de acessar a página e extrair o token;
* automação local que controle o navegador.

Portanto, o ADR deve declarar:

> O canal loopback confirma presença por meio de uma interação local, mas não estabelece identidade forte e não protege contra processos maliciosos executando sob o mesmo usuário.

Se “não confiar no tunnel-client” significar apenas que ele não é um canal de confirmação confiável, o plano atende.

Se significar que o `tunnel-client` deve ser tratado como um processo local ativamente malicioso, apenas loopback e CSRF não são suficientes.

Nesse segundo threat model seria necessário algo fora do HTTP acessível ao processo, por exemplo:

* confirmação nativa do sistema operacional;
* código de uso único exibido em console local e digitado manualmente;
* named pipe com ACL e uma UI separada;
* serviço local executando sob outra identidade.

### 7.2 Hardening HTTP obrigatório

Além do token CSRF:

```text
bind exclusivo em 127.0.0.1
validação estrita do header Host
validação de Origin quando presente
token individual por approval_id
compare_digest para tokens
token de uso único
limite pequeno para body
Content-Type permitido
Cache-Control: no-store
Pragma: no-cache
Content-Security-Policy
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
frame-ancestors 'none'
HTML sempre escapado
```

CSP sugerida:

```text
default-src 'none';
style-src 'unsafe-inline';
form-action 'self';
frame-ancestors 'none';
base-uri 'none'
```

Não colocar o token no payload MCP, logs estruturados ou mensagens de erro.

Evitar também token permanente no query string. O HTML pode receber um nonce por approval, mantido somente na memória do processo.

---

## 8. Espera da decisão

O método não deveria se chamar long-poll internamente. A chamada MCP estará suspensa aguardando um evento local.

Criar um broker:

```python
class ApprovalDecisionBroker:
    def register(self, approval_id: str) -> DecisionWaiter: ...
    def publish(self, approval_id: str, decision: HumanDecisionResult) -> bool: ...
    def unregister(self, approval_id: str) -> None: ...
```

Cuidados:

1. verificar a store antes de registrar o waiter;
2. registrar o waiter;
3. verificar novamente a store;
4. aguardar;
5. remover em `finally`.

Isso evita perder uma decisão ocorrida entre a primeira consulta e a criação do evento.

Modos futuros:

```text
blocking
deferred
auto
```

* `blocking`: espera dentro da mesma chamada MCP;
* `deferred`: retorna imediatamente `approval_required`;
* `auto`: espera até um limite seguro e depois retorna pending.

Para a primeira versão:

```text
auto
```

com timeout configurado é o comportamento mais resiliente.

Em timeout:

* não negar;
* não expirar prematuramente;
* manter a aprovação pending;
* permitir aprovação posterior;
* permitir que uma nova chamada reutilize a aprovação pelo digest.

---

## 9. Lifecycle

Hoje `ExecutionContainer.shutdown()` encerra somente o registry.  O `ApplicationContainer` chama esse shutdown antes dos parsers e embeddings.

Adicionar o canal:

```python
@dataclass(frozen=True, slots=True)
class ExecutionContainer:
    ...
    approval_channel: HumanDecisionChannel | None
    _registry: ExecutionRegistry

    def shutdown(self) -> None:
        if self.approval_channel is not None:
            self.approval_channel.shutdown()
        self._registry.shutdown()
```

Melhor ainda, introduzir um pequeno registro genérico:

```python
class LifecycleRegistry:
    def add(self, component: SupportsShutdown) -> None: ...
    def shutdown(self) -> None: ...
```

Isso atenderia também futuros:

* Git watchers;
* servidores auxiliares;
* worker pools;
* cache managers;
* review providers.

---

# 10. Preparação para code review de código alterado

A aprovação host e o code review se relacionam apenas quando o review executa alguma ação com efeito colateral.

Uma revisão somente de leitura não deve pedir aprovação.

```text
ler diff               → sem aprovação
analisar símbolos      → sem aprovação
buscar referências     → sem aprovação
montar contexto        → sem aprovação
executar testes        → execução supervisionada
aplicar correção       → aprovação exata
criar commit           → aprovação exata
publicar comentários   → aprovação exata
push                   → aprovação exata
```

## 10.1 A abstração central deve ser `ChangeSet`

```python
@dataclass(frozen=True, slots=True)
class ChangeSet:
    change_set_id: str
    source_kind: str
    repository_id: str
    base_ref: str | None
    base_sha: str | None
    head_ref: str | None
    head_sha: str | None
    diff_sha256: str
    files: tuple[ChangedFile, ...]
    created_at: str
```

Possíveis fontes:

```text
working_tree
staged
commit
commit_range
branch_compare
pull_request
patch_file
```

`change_set_id` deve representar o estado exato analisado.

Para working tree, considerar:

* SHA do HEAD;
* conteúdo do index;
* hash dos arquivos modificados;
* arquivos não rastreados incluídos;
* diff digest.

Isso evita produzir uma revisão de uma versão e depois aplicar uma correção sobre outra.

---

## 10.2 Provider independente

```python
class ChangeProvider(Protocol):
    def create_change_set(
        self,
        request: ChangeSetRequest,
    ) -> ChangeSet: ...

    def read_diff(
        self,
        change_set: ChangeSet,
    ) -> ChangeDiff: ...
```

Implementações:

```text
LocalGitChangeProvider
PatchFileChangeProvider
GitHubPullRequestChangeProvider
```

Estrutura:

```text
domain/
  models/
    change_set.py
    review.py
  protocols/
    change_provider.py

application/
  review/
    get_change_set.py
    get_changed_symbols.py
    find_change_impacts.py
    build_review_context.py
    suggest_validation_plan.py

infrastructure/
  git/
    local_git_change_provider.py
    unified_diff_parser.py
```

O provider Git deve executar apenas comandos estruturados e de leitura, sem `shell=True`.

Não utilizar a tool genérica `run_process` para cada leitura Git. Um adapter Git dedicado entrega:

* argumentos controlados;
* parsing estruturado;
* erros tipados;
* proteção contra option injection;
* melhor performance;
* sem aprovações desnecessárias.

---

## 10.3 Tools iniciais de review

### `get_change_set`

Obtém o snapshot das alterações.

```json
{
  "source": "working_tree",
  "base": "HEAD",
  "include_untracked": true
}
```

### `list_changed_files`

Retorna:

```text
added
modified
deleted
renamed
copied
binary
```

### `read_diff`

Retorna hunks estruturados, não apenas texto bruto.

### `get_changed_symbols`

Relaciona cada hunk a:

* classe;
* método;
* função;
* procedure;
* package;
* configuração.

### `find_change_impacts`

Usa o índice existente para localizar:

* callers;
* referências;
* implementações;
* testes relacionados;
* arquivos de configuração;
* contratos afetados.

### `build_review_context`

Monta contexto priorizando:

1. diff;
2. símbolo alterado completo;
3. definições relacionadas;
4. callers;
5. testes;
6. documentação relevante.

### `suggest_validation_plan`

Produz um plano determinístico de validação:

```text
testes candidatos
comandos conhecidos do projeto
módulos afetados
checagens estáticas
limitações da análise
```

O harness não precisa incorporar uma LLM para fazer o review. Ele prepara contexto verificável; o agente conectado ao MCP produz a análise, preservando o princípio original do projeto.

---

## 10.4 Modelo de finding

Para reviews determinísticos ou findings recebidos do agente:

```python
@dataclass(frozen=True, slots=True)
class ReviewFinding:
    finding_id: str
    fingerprint: str
    category: str
    severity: str
    confidence: float
    path: str
    side: str
    start_line: int
    end_line: int
    title: str
    explanation: str
    evidence: tuple[str, ...]
    related_symbols: tuple[str, ...]
    suggested_fix: str | None
```

O `fingerprint` deve considerar:

```text
regra
arquivo
símbolo
linhas normalizadas
trecho relevante
change_set digest
```

Isso permite:

* deduplicar findings;
* comparar reviews;
* marcar finding resolvido;
* evitar repetir o mesmo comentário;
* verificar se o finding ainda pertence à versão atual.

---

## 10.5 Operações com efeito colateral

### `validate_change_set`

Pode executar:

* testes;
* linters;
* compilação;
* análise estática.

Deve reutilizar o subsystem de execução.

O digest da aprovação deve incluir não apenas o comando, mas também:

```text
change_set_id
workspace_snapshot_digest
comando
cwd
limites
backend
```

Sem isso, o usuário poderia aprovar `pytest` sobre um estado do projeto e o comando ser executado posteriormente sobre outro estado.

### `apply_review_fix`

A aprovação deve estar vinculada a:

```text
patch exato
change_set_id base
hash anterior de cada arquivo
arquivos que serão modificados
```

Antes de aplicar:

1. recalcular hashes;
2. comparar com o snapshot;
3. abortar em divergência;
4. aplicar atomicamente;
5. devolver novo change set.

### `publish_review`

A aprovação deve incluir:

```text
repositório
PR
head SHA
comentários exatos
linhas e lados do diff
```

Assim, uma aprovação não pode ser reutilizada para publicar comentários diferentes.

---

# 11. Plano de implementação revisado

## Fase 1 — Canal genérico de decisão — **concluída**

* [x] criar `HumanDecisionRequest`;
* [x] criar `HumanDecisionResult`;
* [x] criar `HumanDecisionChannel`;
* [x] criar enums para channel, source e outcome;
* [x] extrair a orquestração de aprovação do handler MCP;
* [x] manter `ExecutionApproval` e store atuais;
* [x] criar `find_reusable_approval`;
* [x] adicionar testes de arquitetura.

## Fase 2 — Host loopback — **concluída**

* [x] implementar `HostLoopbackDecisionChannel`;
* [x] bind fixo em `127.0.0.1`;
* [x] porta efêmera ou configurada;
* [x] broker concorrente por `approval_id`;
* [x] CSRF por approval;
* [x] headers de segurança;
* [x] Host/Origin validation;
* [x] fila de pendências;
* [x] abertura deduplicada do navegador;
* [x] fallback para URL em stderr;
* [x] shutdown idempotente;
* [x] integração ao lifecycle.

## Fase 3 — Integração MCP — **concluída**

* [x] substituir `_run_with_optional_elicitation` (orquestra via `InteractiveApprovalService`);
* [x] selecionar o canal configurado (`mcp_elicitation` / `host_loopback`);
* [x] manter session binding para elicitação MCP;
* [x] usar host binding para loopback (`service_instance_id` via `session_id`);
* [x] preservar fallback `execution_approval_required`;
* [x] não expor nenhuma tool administrativa.

O teste arquitetural atual que proíbe aprovação via MCP deve continuar.

## Fase 4 — Change sets somente leitura — **concluída**

* [x] `ChangeSet`;
* [x] `ChangedFile`;
* [x] `ChangedHunk`;
* [x] `ChangeProvider`;
* [x] `LocalGitChangeProvider`;
* [x] parser de unified diff;
* [x] `get_change_set`;
* [x] `list_changed_files`;
* [x] `read_diff`;
* [x] `get_changed_symbols`.

## Fase 5 — Análise de impacto e contexto — **concluída**

* [x] `find_change_impacts`;
* [x] integração com símbolos e referências;
* [x] descoberta de testes relacionados;
* [x] `build_review_context`;
* [x] `suggest_validation_plan`;
* [x] orçamento específico para diff;
* [x] cobertura e limitações no resultado.

## Fase 6 — Ações de review — **concluída**

* [x] `validate_change_set`;
* [x] `apply_review_fix`;
* [x] worktree isolado;
* [x] autorização vinculada ao snapshot;
* [x] criação de commit opt-in;
* [x] publicação de review opt-in;
* [x] evolução de `ExecutionApproval` para `ActionApproval`, caso os casos reais justifiquem.
  *(decisão v1: manter `ExecutionApproval` + digest `canonical_version: 2` / `compute_review_action_digest`; sem `ActionApproval` ainda)*

---

# 12. Decisão recomendada

Implementaria `host_loopback`, mas com estas mudanças obrigatórias:

1. chamar o conceito de **approval channel**, não de elicitation trust mode;
2. tornar o protocolo independente de `ExecutionApproval`;
3. impedir que o servidor HTTP dependa de `ApprovalAdminTool`;
4. usar enums para source/channel/outcome;
5. adicionar consulta direta de aprovação por digest;
6. declarar claramente o threat model do loopback;
7. incluir Host validation e headers de segurança;
8. preparar um lifecycle genérico;
9. tratar code review por meio de `ChangeSet`;
10. exigir aprovação apenas para validação executável ou efeitos colaterais.

A arquitetura resultante fica:

```text
                         MCP / CLI / Python API
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
             Retrieval tools             Review tools
                    │                           │
                    └─────────────┬─────────────┘
                                  │
                           Application layer
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
             Execution subsystem         Change subsystem
                    │                           │
                    └─────────────┬─────────────┘
                                  │
                       Human approval service
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
       MCP elicitation      Host loopback       Local CLI/native
```

Essa solução atende ao problema imediato sem transformar o loopback em uma dependência específica do MCP e cria uma base adequada para revisão, validação, aplicação e publicação de mudanças.
