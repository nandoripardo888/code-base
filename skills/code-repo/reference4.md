# Code Repo — referência

Complemento de [SKILL.md](SKILL.md).

Usar sob demanda; não substitui as descriptions do MCP.

# Plugins

A nomenclatura oficial utilizada nesta documentação é:

```text
@code-project → código, arquivos, shell, projetos e mutações
@db-data      → Oracle, fontes PL/SQL e consultas ao banco
```

O nome **code-harness** pode aparecer quando a referência for especificamente ao backend/servidor utilizado pelo `@code-project`.

# Multi-project

## Modelo mental

O `@code-project` possui projetos configurados pelo servidor.

A skill mantém apenas um **contexto conversacional** para decidir qual projeto enviar no argumento `project`.

```text
@code-project
├── projeto default
├── projeto A
├── projeto B
└── projeto C

Chat atual
└── projeto ativo
```

O projeto ativo da conversa não altera o `default` do servidor.

## Precedência

Resolver o projeto nesta ordem:

| Prioridade | Situação | Projeto usado | Altera projeto ativo? |
|---|---|---|---|
| 1 | Usuário pede explicitamente para trocar | Projeto informado | Sim |
| 2 | Pedido cita projeto e ainda não existe ativo | Projeto informado | Sim |
| 3 | Pedido cita projeto diferente do ativo | Projeto informado | Não, salvo pedido de troca |
| 4 | Pedido não cita projeto | Projeto ativo | Não |
| 5 | Não existe projeto ativo | Default do servidor | Não |

## Primeiro projeto da conversa

Exemplo:

```text
Usuário:
@code-project use o projeto crmservice e investigue FrmAgendaConsultorA
```

Primeiro validar o nome através de:

```text
ListProjects
```

Depois:

```text
projeto ativo = crmservice
```

Todas as tools relacionadas ao pedido recebem:

```text
project="crmservice"
```

Pedido seguinte:

```text
Usuário:
onde esse método é chamado?
```

Mesmo sem repetir o projeto:

```text
Grep(..., project="crmservice")
Read(..., project="crmservice")
```

## Novo chat

O projeto ativo é escopo da conversa.

Portanto:

```text
novo chat
    ↓
nenhum projeto ativo
    ↓
usuário informou projeto?
    ├─ sim → validar e tornar ativo
    └─ não → usar default do servidor
```

Não transportar automaticamente o projeto ativo de outro chat.

## Uso pontual de outro projeto

Contexto:

```text
projeto ativo = crmservice
```

Pedido:

```text
no projeto code-base veja onde o tunnel-client é configurado
```

Executar:

```text
Grep(..., project="code-base")
Read(..., project="code-base")
```

Ao concluir:

```text
projeto ativo continua = crmservice
```

Pedido seguinte:

```text
agora veja quem chama esse método
```

volta a utilizar:

```text
project="crmservice"
```

Isso impede que uma consulta auxiliar troque silenciosamente o contexto principal do chat.

## Troca permanente durante o chat

Contexto:

```text
projeto ativo = crmservice
```

Pedido:

```text
a partir de agora vamos trabalhar no code-base
```

Validar `code-base`.

Depois:

```text
projeto ativo = code-base
```

Todos os pedidos posteriores sem projeto explícito passam a utilizar:

```text
project="code-base"
```

Outras frases equivalentes:

```text
mude para code-base
troque para freedom
vamos continuar no banco
use crmservice daqui pra frente
```

## Default

Descobrir através de:

```text
ListProjects
```

Exemplo conceitual:

```text
mode = named
default = <nome>
projects = [...]
```

Não documentar nem codificar um nome fixo como default.

O administrador pode alterar a configuração do servidor.

## Validação

Quando um projeto for explicitamente mencionado e ainda não estiver confirmado na conversa:

```text
ListProjects
```

Verificar se existe.

Quando informações adicionais forem necessárias:

```text
ProjectInfo(project="<nome>")
```

Não usar `ProjectInfo()` sem projeto como mecanismo para sobrescrever o projeto ativo da conversa.

Sem argumento, ele representa o comportamento/default do servidor, não o estado conversacional mantido pela skill.

## Projeto inexistente

Pedido:

```text
use o projeto foo e investigue essa classe
```

Se `foo` não estiver em `ListProjects`:

```text
não executar no default
não escolher projeto parecido
não inferir pelo filesystem
```

Responder indicando que `foo` não existe entre os projetos configurados.

Quando útil, apresentar os nomes retornados por `ListProjects`.

# Propagação de project

Depois de resolver:

```text
target_project = crmservice
```

usar o mesmo valor em todo o fluxo.

Correto:

```text
Glob(..., project="crmservice")
Grep(..., project="crmservice")
Read(..., project="crmservice")
StrReplace(..., project="crmservice")
```

Incorreto:

```text
Glob(..., project="crmservice")
Grep(...)
Read(...)
```

As chamadas sem `project` podem voltar ao default do servidor e misturar projetos.

## Shell

O mesmo vale para:

```text
Shell(
    command="...",
    project="crmservice"
)
```

Não depender de `working_directory` para selecionar projeto.

`working_directory` é relativo ao contexto selecionado; não substitui `project`.

## Jobs

Se um `Shell` gerar um job assíncrono:

```text
Shell(..., project="crmservice")
```

acompanhar usando o mesmo projeto:

```text
GetJobStatus(
    job_id="...",
    project="crmservice"
)
```

## Mutações

Manter o projeto em:

```text
Write
StrReplace
ApplyPatch
Delete
RollbackPatch
OpenPatchReview
```

quando a assinatura da tool oferecer `project`.

Exemplo:

```text
StrReplace(
    ...,
    project="crmservice"
)
```

O `review_url` pertence à alteração daquele contexto e deve ser devolvido normalmente ao usuário.

# Cruzamento entre projetos

Às vezes a mesma investigação precisa consultar projetos diferentes.

Exemplo:

```text
crmservice
    Java da aplicação

banco
    scripts Oracle versionados

code-base
    implementação do MCP
```

Nesse caso, não existe um único `target_project` global para toda a resposta.

Usar explicitamente:

```text
Grep(..., project="crmservice")
Grep(..., project="banco")
Grep(..., project="code-base")
```

Preservar a origem da evidência ao raciocinar.

Não assumir que:

```text
src/foo.java
```

encontrado em um projeto também exista em outro.

## Projeto principal em investigação cruzada

Mesmo consultando projetos auxiliares, o projeto ativo continua sendo o contexto principal da conversa.

Exemplo:

```text
ativo = crmservice

consulta auxiliar = banco
```

A consulta no projeto `banco` não altera automaticamente:

```text
ativo = crmservice
```

# Relação com @db-data

`project` pertence ao `@code-project`.

O `@db-data` possui contexto próprio.

Exemplo:

```text
@code-project
project="crmservice"
```

não autoriza inferir automaticamente:

```text
@db-data
schema/conexão = crmservice
```

Validar o banco conforme as ferramentas e regras específicas do `@db-data`.

# Guia rápido: pedido → primeira ação

| Tipo de pedido | Primeira ação | Por quê |
|---|---|---|
| Primeiro pedido do chat informa projeto | `ListProjects` para validar → usar `project` em todas as tools | Estabelece o projeto ativo |
| Pedido sem projeto e já existe ativo | Reutilizar projeto ativo | Evita repetir seleção |
| Pedido sem projeto e não existe ativo | Descobrir/utilizar default | Comportamento padrão |
| Pedido cita outro projeto pontualmente | Usar esse projeto somente no pedido | Preserva o contexto principal |
| Usuário pede para mudar de projeto | Validar → atualizar projeto ativo | Troca intencional |
| "onde a classe `OsCancelamentoService` faz X" | `symbols` direto no arquivo; Glob somente se path desconhecido | Âncora exata |
| "por que dá `ORA-02291` ao salvar orçamento" | `Grep content` com `ORA-02291` | Mensagem rara é ótima âncora |
| "onde grava o status ao cancelar a OS" | Ver funil abaixo | Sem âncora sintática |
| "muda a validação de agendamento pra aceitar sábado" | Localizar regra → confirmar arquivo/método → `StrReplace` | Mutação ainda exige descoberta |
| "por que `PKG_OS_AGENDA_CONSULTOR.reservar_horario`..." | `@db-data`: `grep_source` direto | Package Oracle já é âncora |
| "como funciona o módulo de faturamento" | 1 `Glob`/`count`; se ruído alto, pedir âncora | Escopo genérico demais |

# Exemplo de funil sem âncora sintática

Pedido:

```text
onde a OS grava o status ao cancelar?
```

Projeto já resolvido:

```text
project="crmservice"
```

1. `Glob` com:

```text
**/*Cancel*Os*.java
**/*Os*Cancel*.java
```

2. `Grep count` / `files_with_matches` com padrão composto.

3. No arquivo candidato:

```text
Grep output_mode=symbols
```

4. Escolher método e usar:

```text
Grep output_mode=references
```

5. `content` + `Read` no trecho que chama package/cursor.

6. Ao chegar ao Oracle, usar `@db-data`:

```text
grep_source
read_source
```

7. Responder com o fluxo confirmado.

# Exemplo de funil com âncora Oracle direta

Pedido:

```text
por que PKG_OS_AGENDA_CONSULTOR.reservar_horario
está travando com dois consultores?
```

1. Pular Java.

2. Usar `@db-data`:

```text
grep_source
```

em `PKG_OS_AGENDA_CONSULTOR`.

3. `read_source` no procedimento.

4. Consultar `outline` se a package for grande.

5. Se envolver lock/concorrência, checar DDL da tabela envolvida antes de formular hipótese.

6. Só voltar ao `@code-project` se houver evidência concreta de participação Java.

7. Responder com a causa confirmada na procedure.

# Paralelização

Quando duas checagens não dependerem uma da outra, disparar juntas.

Exemplos:

- `Glob` de dois padrões de classe;
- `Grep count` em módulos diferentes;
- consulta em `@code-project` + `@db-data`;
- busca independente em dois projetos.

Exemplo multi-project:

```text
Grep(..., project="crmservice")
Grep(..., project="banco")
```

podem ser paralelos se uma busca não depender do resultado da outra.

Não paralelizar quando o passo seguinte depende do anterior.

Exemplo:

```text
symbols
    ↓
nome exato do método
    ↓
references
```

# Anti-padrões

| Evitar | Fazer em vez disso |
|---|---|
| Omitir `project` depois que um projeto já foi selecionado | Propagar o projeto resolvido |
| Hardcode de qualquer projeto como default | Descobrir através de `ListProjects` |
| Trocar ativo ao consultar outro projeto pontualmente | Preservar ativo salvo pedido explícito |
| Cair no default quando projeto solicitado não existe | Informar projeto inválido |
| Inferir projeto pelo path | Resolver pelo nome configurado |
| Transportar projeto ativo para outro chat | Novo chat começa sem ativo |
| Assumir que `working_directory` seleciona projeto | Usar `project` |
| Misturar paths encontrados em projetos diferentes | Manter origem de cada evidência |
| Tratar `@db-data` como projeto do `@code-project` | Manter contextos independentes |
| `Grep content` com termos genéricos globais | `count` → restringir → `files_with_matches` |
| Buscar `salvar` ou `continuar` globalmente | Achar classe → `references` |
| Ler `*RowType.java` na primeira passagem | Excluir salvo necessidade |
| Inventar `group_id` | Omitir + `group_title`; reutilizar id retornado |
| Afirmar "o sistema usa X" por uma tela | "Neste fluxo, classe Y usa X" |
| Continuar no Java após achar `PKG_FOO.bar` | Ir ao `@db-data` |
| Rodar suíte inteira após ajuste pontual | Teste mais próximo |
| Refinar `files_with_matches` >30 repetidamente | Pedir contexto |
| Serializar buscas independentes | Paralelizar |

# Lembrete de API

## @code-project

### Seleção de projeto

```text
ListProjects()
```

Lista os contextos configurados sem expor roots do filesystem.

```text
ProjectInfo(project?)
```

Retorna metadados não sensíveis do projeto selecionado.

O argumento:

```text
project
```

está disponível nas principais tools do `@code-project`.

### Pesquisa

`Grep output_mode`:

```text
content
files_with_matches
count
symbols
references
```

Não existe:

```text
mode=files
```

Usar:

```text
output_mode=files_with_matches
```

### Mutações

Principais tools:

```text
Write
StrReplace
ApplyPatch
Delete
RollbackPatch
```

Nas mutações:

- usar `description`;
- `group_title` para iniciar agrupamento;
- reutilizar `group_id` retornado;
- preservar `project`;
- devolver `review_url` quando fornecido.

## @db-data

Fluxo:

```text
grep_source
    ↓
read_source
```

Modos relevantes:

```text
source
outline
errors
ddl
```

`run_sql`:

```text
SELECT / WITH
```

por padrão nesta skill.