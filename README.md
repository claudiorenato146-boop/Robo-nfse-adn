# Robô NFS-e ADN

Baixa as NFS-e de cada empresa direto da API do governo (ADN) com o certificado
A1 do próprio contribuinte e entrega, por competência:

- **4 ZIPs** de XML nas pastas de importação do Domínio
- **2 planilhas** de conferência de 38 colunas por empresa
- **1 planilha mensal** consolidada, com uma aba por competência

---

## Instalação

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

```bash
copy .env.example .env
```

Abra o `.env` e preencha os caminhos da sua rede e a senha dos certificados. O
arquivo é ignorado pelo Git — nenhum valor real dele chega ao repositório.

---

## Uso

```bash
.venv\Scripts\python conferir.py
```

Roda **antes** da carteira: confere se cada certificado abre com a senha da
planilha, se está no prazo, e se a pasta de cada cliente existe nas três raízes.

```bash
.venv\Scripts\python main.py -m 082026
```

| Comando | O que faz |
|---|---|
| `main.py` | pergunta a competência e roda a carteira inteira |
| `main.py -c 25,438,752` | só essas empresas (código Domínio) |
| `main.py -m 082026` | competência sem perguntar |
| `main.py --somente-erros -m 072026` | **só as que falharam** na última rodada |
| `main.py --listar` | mostra a carteira e sai |
| `main.py -m 082026 --sim` | sem confirmação (para o agendador) |
| `conferir.py --so-pastas` | checagem rápida, sem abrir certificado |
| `conferir.py --csv saida.csv` | grava a checagem num CSV |

A competência aceita `082026`, `08/2026`, `7/2026` ou `2026-08`.

---

## Saída

```
{RAIZ_EMITIDAS}/11-PADARIA DO EXEMPLO/082026/
    xml_nfse_emitidas_99900001000150.zip
    xml_nfse_emitidas_canceladas_99900001000150.zip

{RAIZ_RECEBIDAS}/11-PADARIA DO EXEMPLO/082026/
    xml_nfse_recebidas_99900001000150.zip
    xml_nfse_recebidas_canceladas_99900001000150.zip

{RAIZ_RELATORIOS}/11-PADARIA DO EXEMPLO/082026/
    nfses_emitidas_99900001000150.xlsx
    nfses_recebidas_99900001000150.xlsx

{RAIZ_RELATORIOS}/PLANILHA RELATORIO MENSAL (API).xlsx
```

- XMLs **soltos na raiz** do ZIP. Nenhuma pasta, nenhuma subpasta.
- A nota cancelada vai no ZIP `_canceladas` **junto com o XML do evento** de
  cancelamento.
- Categoria sem nota não gera ZIP — e apaga o ZIP daquela categoria se tiver
  sobrado de uma rodada anterior.

### Regra das pastas

**O robô nunca cria pasta de cliente.** Ele localiza a pasta pelo **prefixo do
código** (`25-`, aceitando também `25 - ` e `25- `), porque o nome pode estar
desatualizado em relação ao apelido do Domínio. Se não existir, registra aviso
e segue.

**A pasta da competência** (`082026`) é criada quando falta e ignorada quando
já existe.

---

## As planilhas

### Por empresa — 38 colunas

Mesmo layout do robô antigo, coluna por coluna. Os cabeçalhos são reproduzidos
**literalmente**, inclusive com os erros de digitação do modelo original
(`Cpd Municipio Interm`, `Num Cep Intern`, `Vlr Sev.`) — mudar isso quebraria
quem consome a planilha hoje.

A coluna 38 `Status Apuracao Nfse` traz `CANCELADA` ou `APURAVEL`.

O nome do município não vem no XML, só o código IBGE. A tradução usa
`data/municipios.csv` (5.571 municípios, da API de localidades do IBGE).

### Mensal consolidada

Uma aba por competência (`082026`), uma linha por empresa:

| N° | RAZÃO SOCIAL | CNPJ | XML - PRESTADOS | XML - TOMADOS | OBSERVAÇÃO | IMPORTADO | VALIDADE CERT |
|---|---|---|---|---|---|---|---|

`XML - PRESTADOS` e `XML - TOMADOS` são **fluxos independentes** e têm três
valores: `OK` (teve nota), `SEM MOV` (rodou certo, a empresa não teve nota
naquele fluxo) e `ERRO` (a consulta nem chegou a rodar). Ter emitida e não ter
recebida — ou o contrário — é normal, não é problema.

`VALIDADE CERT` traz o vencimento do certificado usado, para você acompanhar as
renovações sem abrir outro relatório.

```
Emitidos: OK (488 XML, 3 canceladas); Recebidos: OK (12 XML, 0 canceladas)
```

> **Arquivo separado de propósito.** A `PLANILHA RELATORIO MENSAL.xlsx` (sem o
> sufixo) é do robô do ISS Digital. Este robô escreve na versão **(API)** e se
> recusa a gravar numa aba que não tenha a coluna `IMPORTADO` — sinal de que a
> aba é de outro processo.

A planilha é atualizada **por empresa**: rodar com `-c` ou `--somente-erros` NÃO
apaga as empresas que ficaram de fora — as linhas delas permanecem como estavam.

**A coluna IMPORTADO é sua.** O robô deixa em branco e você marca depois de
importar no Domínio. Quando o robô roda de novo, ele **lê o que você digitou e
regrava igual** — sua marcação não se perde.

---

## Cadastro das empresas

`data/clientes.csv`, separado por `;`. O arquivo real **não é versionado**:
comece copiando `data/clientes.exemplo.csv`.

| coluna | conteúdo |
|---|---|
| `codigo_dominio` | código no Domínio — é ele que localiza a pasta |
| `apelido`, `razao_social` | identificação |
| `cnpj` | 14 dígitos, sem pontuação — vai no nome dos arquivos |
| `arquivo_certificado` | nome do arquivo dentro da pasta de certificados |
| `senha_certificado` | deixe **vazia** e use `SENHA_CERTIFICADO_PADRAO` no `.env`; preencha só quando uma empresa tiver senha própria |
| `ativo` | `sim` para entrar na rodada |

Os certificados ficam **todos numa pasta só**; o que amarra empresa e arquivo é
a coluna `arquivo_certificado`. Aceita `.pfx` e `.p12`.

> Se o CSV passar pelo Excel, **a coluna CNPJ é destruída** — 14 dígitos viram
> notação científica (`1,24E+13`). Edite como texto, ou reconstrua o CNPJ do
> export do Domínio.

---

## Como ele se comporta

| Situação | Comportamento |
|---|---|
| Certificado não abre / vencido | `SEM_CERTIFICADO`, segue para a próxima |
| Pasta do cliente não existe | Aviso na planilha; **não cria** |
| Pasta da competência não existe | Cria |
| HTTP 429 (throttling) | Espera e repete o **mesmo** NSU (respeita `Retry-After`) |
| Queda no meio | Ponteiro de NSU salvo a cada lote — retoma de onde parou |
| Nota já baixada | Ignorada (dedup por chave de acesso) |
| Arquivo já existe com o mesmo conteúdo | Mantido, não reescreve |
| Arquivo já existe com conteúdo diferente | Atualiza (é saída do próprio robô) |
| Arquivo de outro nome na pasta | Nunca tocado |
| Cancelamento em mês posterior | Tira a nota do ZIP de válidas retroativamente |
| Ctrl+C | Gera os relatórios do que já rodou |
| Erro numa empresa | Não derruba a rodada |

Na primeira rodada de cada empresa a fila inteira de NSU é lida (demora).
Depois o ponteiro fica no SQLite e só o que é novo trafega.

---

## Agendamento (Windows)

Agendador de Tarefas → Tarefa básica → Diário:

- Programa: `C:\caminho\.venv\Scripts\python.exe`
- Argumentos: `main.py -m 082026 --sim`
- Iniciar em: `C:\caminho\`

> A unidade de rede mapeada **não existe** na sessão do Agendador. Para agendar,
> troque os caminhos do `.env` pelo UNC (`\\servidor\compartilhamento\...`).

---

## Não versionar

```
.env
staging/
logs/
relatorios/
data/nfse_adn.db
```

## Segurança

A senha dos certificados **não fica mais no CSV**. Quando a coluna
`senha_certificado` está vazia — e é assim que o exemplo vem —, o robô lê
`SENHA_CERTIFICADO_PADRAO` do `.env`, que o `.gitignore` mantém fora do
repositório. Preencher a coluna continua funcionando e tem prioridade, para o
caso de uma empresa ter senha própria.

Isso resolve o vazamento, não o risco de fundo: quem tiver o `.env` **e** a
pasta dos `.pfx` assina como qualquer cliente da carteira. Duas coisas que
valem mais que este código:

- **uma senha diferente por certificado** — senha única para toda a carteira
  significa que um vazamento é o vazamento de todos;
- **nunca colocar a senha no nome do arquivo `.pfx`** — ela aparece em qualquer
  listagem de pasta, backup ou captura de tela.

Os certificados `.pfx`/`.p12`, o `.env` e o `data/clientes.csv` estão todos no
`.gitignore`. Confira com `git status` antes do primeiro commit.

---

## Testes

```bash
pip install pytest
pytest -q
```

28 testes. A maior parte cobre `xmlutil`, que é onde os erros custaram caro:

- **`dCompet` tem prioridade sobre `dhEmi`.** Uma nota com competência
  31/12 e emissão 02/01 pertence a dezembro. Ler a data de emissão mandava as
  notas de virada de ano para a pasta errada.
- **Sem data conhecida, a competência é `None` — nunca o mês atual.** Um
  fallback para "hoje" arquiva o documento em silêncio no mês em que o robô
  rodou, e ninguém percebe.
- **A leitura é agnóstica a namespace e prefixo**, porque foi um XPath com
  `local-name()` (não suportado pelo ElementTree) que causou o problema acima.
- Papel prestador / tomador / intermediário, e `INDEFINIDO` para eventos, que
  herdam o papel da NFS-e que referenciam.

O resto cobre o cadastro: colunas obrigatórias, empresa inativa, seleção por
código na ordem pedida, e a senha vinda do ambiente.

## Licença

MIT — veja [LICENSE](LICENSE). Use, copie e adapte à vontade.

## Aviso

Este repositório traz **só o código**. Nenhum cadastro de cliente, nenhuma
credencial e nenhum certificado estão aqui, e os caminhos de rede nos exemplos
são genéricos. Os arquivos `*.exemplo.*` existem para o projeto rodar sem
depender de dado real — copie, renomeie e preencha com os seus.
