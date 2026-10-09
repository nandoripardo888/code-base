# Casos Labs: conferência de fontes e alinhamento de métodos

Coletado somente por `@labs`, no projeto `crmservice`, em 2026-09-25. Nenhuma entrada foi enviada ao Jev nesta subtarefa. Os blocos abaixo são saídas literais relevantes do MCP, sem credenciais ou dados de clientes.

## Consultas Labs

```json
{"tool":"labs_grep","args":{"project":"crmservice","pattern":"filtrarTotalHojeAtrasado","output_mode":"references","head_limit":40}}
{"tool":"labs_read","args":{"project":"crmservice","path":"src/main/java/freedom/util/CrmServiceAgendaFiltro.java","offset":200,"limit":58}}
{"tool":"labs_read","args":{"project":"crmservice","path":"src/main/java/freedom/bytecode/rn/RecepcaoRNA.java","offset":300,"limit":28}}
{"tool":"labs_read","args":{"project":"crmservice","path":"src/main/java/freedom/bytecode/rn/PainelRNA.java","offset":35,"limit":28}}
{"tool":"labs_read","args":{"project":"crmservice","path":"src/main/java/freedom/bytecode/form/FrmRecepcaoA.java","offset":660,"limit":24}}
{"tool":"labs_grep","args":{"project":"crmservice","path":"src/main/java/freedom/bytecode/rn/RecepcaoRNA.java","pattern":"CrmServiceAgendaFiltro agenda|agenda =|String codEmpresas","output_mode":"content","context_lines":2,"head_limit":20}}
{"tool":"labs_grep","args":{"project":"crmservice","path":"src/main/java/freedom/bytecode/rn/PainelRNA.java","pattern":"CrmServiceAgendaFiltro agenda|agenda =|int codEmpresa","output_mode":"content","context_lines":1,"head_limit":20}}
{"tool":"labs_grep","args":{"project":"crmservice","path":"src/main/java/freedom/bytecode/form/FrmRecepcaoA.java","pattern":"codEmpresasSel","output_mode":"content","context_lines":1,"head_limit":12}}
```

## Saída de `references`

```text
6 syntactic occurrences in 4 files

src/main/java/freedom/bytecode/rn/RecepcaoRNA.java
  314:17 [definition in RecepcaoRNA] public void filtrarTotalHojeAtrasado(String codEmpresas, String usuario, String placa,
  317:16 [call in RecepcaoRNA.filtrarTotalHojeAtrasado] agenda.filtrarTotalHojeAtrasado(tbTotalizarAgendados, codEmpresas, usuario,

src/main/java/freedom/util/CrmServiceAgendaFiltro.java
  217:17 [definition in CrmServiceAgendaFiltro] public void filtrarTotalHojeAtrasado(TOTALIZAR_AGENDADOS tbTotalizarAgendados,
  242:17 [definition in CrmServiceAgendaFiltro] public void filtrarTotalHojeAtrasado(TOTALIZAR_AGENDADOS tbTotalizarAgendados,

src/main/java/freedom/bytecode/form/FrmRecepcaoA.java
  672:16 [call in FrmRecepcaoA.filtrarTotalAgendaHojeAtrasado] rn.filtrarTotalHojeAtrasado(codEmpresasSel, consultorSel, placaVeiculoSel, statusAgenda, nomeCliente,

src/main/java/freedom/bytecode/rn/PainelRNA.java
  52:16 [call in PainelRNA.filtrarHojeAtrasado] agenda.filtrarTotalHojeAtrasado(tbTotalizarAgendadosAtrasadosHj, codEmpresa, usuario, "",
```

## Saídas de `Read` usadas como evidência

`src/main/java/freedom/util/CrmServiceAgendaFiltro.java`:

```text
   217|    public void filtrarTotalHojeAtrasado(TOTALIZAR_AGENDADOS tbTotalizarAgendados,
   218|                                         int codEmpresa, String usuario, String placa, EnStatusAgenda statusAgenda,
   219|                                         String nomeCliente, int codTecnico) throws DataException {
   220|        /*Atrasados Hoje*/
   221|        tbTotalizarAgendados.close();
   222|        tbTotalizarAgendados.clearFilters();
   223|        //tbTotalizarAgendados.addFilter("HOJE_ATRASADO");
   224|        tbTotalizarAgendados.addFilter("AGENDADA_HOJE");
   225|        tbTotalizarAgendados.addParam("COD_EMPRESA", codEmpresa);
   226|        filtroComumAgenda(tbTotalizarAgendados, codEmpresa, usuario, placa, statusAgenda,
   227|                nomeCliente, codTecnico);
   228|        tbTotalizarAgendados.open();
   229|    }
   230|
   231|    /**
   232|     * Sobrecarga para Empresas Multi-Seleção
   233|     * @param tbTotalizarAgendados
   234|     * @param codEmpresas
   235|     * @param usuario
   236|     * @param placa
   237|     * @param statusAgenda
   238|     * @param nomeCliente
   239|     * @param codTecnico
   240|     * @throws DataException
   241|     */
   242|    public void filtrarTotalHojeAtrasado(TOTALIZAR_AGENDADOS tbTotalizarAgendados,
   243|                                         String codEmpresas, String usuario, String placa, EnStatusAgenda statusAgenda,
   244|                                         String nomeCliente, int codTecnico) throws DataException {
   245|        /*Atrasados Hoje*/
   246|        tbTotalizarAgendados.close();
   247|        tbTotalizarAgendados.clearFilters();
   248|        //tbTotalizarAgendados.addFilter("HOJE_ATRASADO");
   249|        tbTotalizarAgendados.addFilter("AGENDADA_HOJE");
   250|        //tbTotalizarAgendados.addParam("COD_EMPRESA", codEmpresa);
   251|        filtroComumAgenda(tbTotalizarAgendados, codEmpresas, usuario, placa, statusAgenda,
   252|                nomeCliente, codTecnico);
   253|        tbTotalizarAgendados.open();
   254|    }
```

`src/main/java/freedom/bytecode/rn/RecepcaoRNA.java`:

```text
   314|    public void filtrarTotalHojeAtrasado(String codEmpresas, String usuario, String placa,
   315|                                         EnStatusAgenda statusAgenda, String nomeCliente, int codTecnico) throws DataException {
   316|        /*Atrasados Hoje*/
   317|        agenda.filtrarTotalHojeAtrasado(tbTotalizarAgendados, codEmpresas, usuario,
   318|                placa, statusAgenda, nomeCliente, codTecnico);
   319|    }
```

`src/main/java/freedom/bytecode/rn/PainelRNA.java`:

```text
    50|    public void filtrarHojeAtrasado(int codEmpresa, String usuario) throws DataException {
    51|        /*Atrasados Hoje*/
    52|        agenda.filtrarTotalHojeAtrasado(tbTotalizarAgendadosAtrasadosHj, codEmpresa, usuario, "",
    53|                EnStatusAgenda.TODOS, "", 0);
    54|    }
```

`src/main/java/freedom/bytecode/form/FrmRecepcaoA.java`:

```text
    46|    private String codEmpresasSel = "";
   670|    private void filtrarTotalAgendaHojeAtrasado() {
   671|        try {
   672|            rn.filtrarTotalHojeAtrasado(codEmpresasSel, consultorSel, placaVeiculoSel, statusAgenda, nomeCliente,
   673|                    codTecnicoSel);
   674|            lblAgendaHojeAtrasado.setCaption(tbTotalizarAgendados.getTOTAL().asString());
   675|            vBoxAgendaHojeAtrasado.invalidate();
   676|        } catch (DataException ex) {
   677|            CrmServiceUtil.showError(ex);
   678|        }
   679|    }
```

As duas classes de RNA declaram `private final CrmServiceAgendaFiltro agenda = new CrmServiceAgendaFiltro();` em `RecepcaoRNA.java:26` e `PainelRNA.java:21` (consultas `labs_grep` acima). A saída de `references` não resolve a sobrecarga por si só.

## Casos de conferência de afirmação versus fonte

1. **Sustentada.** Afirmação: “A sobrecarga de `filtrarTotalHojeAtrasado` que recebe `String codEmpresas` ativa `AGENDADA_HOJE`.” Fonte: `CrmServiceAgendaFiltro.java:242-253`. Gabarito: **sustenta**, pela chamada ativa na linha 249.
2. **Contradita.** Afirmação: “A sobrecarga que recebe `String codEmpresas` ativa `HOJE_ATRASADO`.” Mesma fonte. Gabarito: **contradiz**; `HOJE_ATRASADO` só aparece comentado na linha 248, e a linha 249 ativa `AGENDADA_HOJE`.
3. **Insuficiente.** Afirmação: “O filtro do serviço usado no contador `lblAgendaHojeAtrasado` é `AGENDADA_HOJE`.” Fonte isolada: `FrmRecepcaoA.java:670-679`. Gabarito: **não demonstra**; a UI chama a RNA e atualiza o rótulo, mas a condição aparece no serviço. A afirmação é verdadeira à luz de outras fontes, porém a citação isolada não a comprova.

## Caso de alinhamento de entidades/métodos

Pergunta para eventual Jev: “Dado o contexto de chamada da recepção, qual das duas definições de `CrmServiceAgendaFiltro.filtrarTotalHojeAtrasado` corresponde à chamada? Compare assinatura e fluxo; pode haver duas entidades semanticamente parecidas.”

- Candidato A: `CrmServiceAgendaFiltro.java:217-229`, segundo argumento `int codEmpresa`, adiciona `COD_EMPRESA` explicitamente.
- Candidato B: `CrmServiceAgendaFiltro.java:242-254`, segundo argumento `String codEmpresas`, delega o filtro comum com múltiplas empresas.
- Contexto da recepção: `FrmRecepcaoA.java:46,670-674` usa `String codEmpresasSel`; `RecepcaoRNA.java:314-318` repassa `String codEmpresas`; logo a chamada da recepção alinha com **B**.
- Controle negativo: `PainelRNA.java:50-54` recebe `int codEmpresa`; a chamada do painel alinha com **A**.

Gabarito humano: as duas definições têm quase o mesmo nome/corpo e mesmo filtro ativo, mas são sobrecargas distintas para contexto de empresas diferente. Um alinhador puramente semântico pode considerá-las equivalentes quando a pergunta exige vínculo exato de chamada. Conferência por tipo do argumento é determinística e deve prevalecer sobre pontuação do Jev.
