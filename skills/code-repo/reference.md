# Code Repo — referência

Complemento de [SKILL.md](SKILL.md). Usar sob demanda; não substitui as descriptions do MCP.

## Exemplo de funil

Pedido: “onde a OS grava o status ao cancelar?”

1. `Glob` com `**/*Cancel*Os*.java` e `**/*Os*Cancel*.java` em `crmservice/src/main/java` (ajustar módulo ao projeto).
2. `Grep` `count` / `files_with_matches` com padrão composto (`cancelar` + identificador de status), `glob=*.java`, `exclude` de wizard/RowType.
3. No arquivo candidato: `Grep` `symbols` (sem pattern) → escolher método.
4. `Grep` `references` no nome do método/classe com `reference_kind` adequado.
5. `content` + `Read` no trecho que chama package/cursor.
6. db-base: `grep_source` no package/procedure → `read_source` no intervalo.
7. Responder com o fluxo confirmado; se houver outro entrypoint, mencionar como lacuna ou investigar.

## Anti-padrões

| Evitar | Fazer em vez disso |
|--------|-------------------|
| `Grep content` com `termo1\|termo2` em todo o repo | `count` → restringir path/glob → `files_with_matches` |
| Buscar `salvar` / `continuar` globalmente | Achar a classe, depois `references` no método |
| Ler `*RowType.java` na 1ª passagem | Excluir; incluir só se precisar do binding |
| Inventar `group_id` | Omitir + `group_title`; reutilizar o id retornado |
| Chamar `OpenPatchReview` após toda mudança | Só se `review_url` faltou ou o usuário pediu |
| Afirmar “o sistema usa X” por uma tela | “Neste fluxo (classe Y) usa X” |
| Continuar no Java após achar `PKG_FOO.bar` | `grep_source` / `read_source` no db-base |

## Lembrete de API (não exaustivo)

code-harness:

- `Grep` `output_mode`: `content` \| `files_with_matches` \| `count` \| `symbols` \| `references` (não existe `mode=files`).
- Mutações: `description` obrigatória; `group_title` / `group_id`; surface `review_url` (portal fixo, default `http://127.0.0.1:8765`).

db-base:

- `grep_source` (`text` \| `references`) → `read_source` (`source` \| `outline` \| `errors` \| `ddl`).
- `run_sql`: SELECT/WITH apenas.
