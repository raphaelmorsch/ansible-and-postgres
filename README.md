# AAP + EDA — Automação PostgreSQL no OpenShift

Demonstração do **Ansible Automation Platform (AAP)** e **Event-Driven Ansible (EDA)** orquestrando operações PostgreSQL em OpenShift, com dois cenários:

1. **Manutenção preventiva** — health check, ticket ITSM, VACUUM ANALYZE, validação e fechamento
2. **Resposta a deadlock** — detecção via webhook, diagnóstico, contenção OpenShift, aprovação humana, remediação e validação

Componentes auxiliares:

- **Mock Vault** — credenciais fora de playbook/Git/logs
- **Mock ITSM** — abertura, atualização e encerramento de tickets
- **4 aplicações Flask** — gravadores no banco + app que provoca deadlock e notifica o EDA

---

## Arquitetura

### Manutenção preventiva

```
AAP Scheduler / Launch manual
        │
        ▼
WF — Manutenção Preventiva PostgreSQL
        │
        ├── JT01 Health Check ──► Mock Vault
        ├── JT02 Abrir Ticket  ──► Mock ITSM
        ├── JT03 VACUUM ANALYZE ──► PostgreSQL
        ├── JT04 Validar
        └── JT05 Encerrar ticket
                │
                └── (falha) JT06 Evidências → JT07 Ticket pendente
```

### Resposta a deadlock (EDA + workflow)

```
app-delta-deadlock (/deadlock)
        │  POST webhook JSON
        ▼
EDA Activation (rulebook webhook :5000)
        │  run_job_template
        ▼
AAP WF — Resposta a Deadlock PostgreSQL
        │
        ├── JT08 Diagnosticar + abrir ticket
        ├── JT09 Conter app (scale Deployment → 0)
        ├── Aprovação humana (ação destrutiva)
        ├── JT10 Remediar (terminate + VACUUM ANALYZE)
        ├── JT11 Validar ambiente
        └── JT12 Encerrar ticket
                │
                └── (falha) JT06 → JT07
```

> **Como o EDA “detecta” deadlock:** o PostgreSQL aborta a transação em conflito; a aplicação `app-delta-deadlock` captura o erro `"deadlock detected"` e envia um webhook com `event_type: postgres_deadlock_detected`. O EDA não consulta o banco diretamente.

---

## Pré-requisitos

| Item | Detalhe |
|---|---|
| Cluster OpenShift | API e `oc` configurados |
| AAP instalado | Controller + EDA no namespace `aap` |
| Ferramentas locais | `oc`, `curl`, `python3`, `git` |
| Repositório | Este Git publicado (GitHub recomendado para o projeto EDA) |

Variáveis padrão do cluster **d796h** (ajuste conforme seu ambiente):

| Variável | Valor padrão |
|---|---|
| `AAP_URL` | `https://aap-aap.apps.cluster-d796h.dyn.redhatworkshops.io` |
| `AAP_USERNAME` / `AAP_PASSWORD` | credenciais admin do AAP |
| `PG_NAMESPACE` | `databases` |
| `PG_HOST` | `postgresql.databases.svc.cluster.local` |
| `SCM_URL` (Controller) | Git interno ou GitHub (ver nota abaixo) |
| `EDA_PROJECT_URL` | `https://github.com/raphaelmorsch/ansible-and-postgres.git` |

---

## Passo a passo — montar todo o cenário

### 0. Clonar e preparar

```bash
git clone https://github.com/raphaelmorsch/ansible-and-postgres.git
cd ansible-and-postgres
chmod +x scripts/*.sh scripts/*.py
```

### 1. Login no OpenShift

```bash
oc login https://api.cluster-d796h.dyn.redhatworkshops.io:6443 \
  -u admin -p '<senha>' --insecure-skip-tls-verify=true
```

### 2. Deploy base (mocks, Git interno, AAP)

```bash
./scripts/deploy_d796h.sh
```

Esse script:

- publica **Mock Vault** e **Mock ITSM**
- sobe o **Git interno** (`demo-git`) para o projeto do Controller
- cria o **Container Group** `openshift-ee` (execução de jobs no cluster)
- configura inventário, projeto, Job Templates e workflows via `setup_aap_workflow.py`

### 3. Instalar PostgreSQL no namespace `databases`

No AAP Controller:

1. **Templates → Workflows → `WF — Instalar PostgreSQL (OpenShift)`**
2. **Launch** e aguarde conclusão com sucesso

Isso cria o namespace (se necessário) e instala o PostgreSQL via manifests em `openshift/postgresql-databases.yaml`.

### 4. (Opcional) Gerar tuplas mortas — demo de manutenção preventiva

```bash
./scripts/generate_dead_tuples.sh
```

Ou lance o Job Template **`JT00 — Gerar Tuplas Mortas`** no AAP.

### 5. Deploy das aplicações gravadores + app de deadlock

```bash
./scripts/deploy_db_writers.sh
```

Aplicações criadas no namespace `databases`:

| App | Função |
|---|---|
| `app-alpha` | grava eventos no PostgreSQL |
| `app-beta` | grava eventos no PostgreSQL |
| `app-gamma-stress` | stress de conexões (não usado no fluxo de deadlock) |
| `app-delta-deadlock` | provoca deadlock e notifica o EDA |

Obter URLs externas:

```bash
oc get route -n databases
```

### 6. Publicar playbooks atualizados no GitHub

O projeto **EDA** exige repositório Git “smart” (GitHub funciona; o Git interno `demo-git` é HTTP dumb e falha no EDA).

```bash
git add -A
git commit -m "Atualiza playbooks e automação de deadlock"
git push origin main
```

### 7. Apontar o projeto do Controller para o GitHub (recomendado)

No AAP Controller → **Projects → postgresql-preventive-maintenance**:

- **SCM URL:** `https://github.com/raphaelmorsch/ansible-and-postgres.git`
- **SCM Branch:** `main`
- **Update** (sincronizar)

> Sem este passo, o Controller pode continuar usando uma revisão antiga do Git interno.

### 8. Configurar automação de deadlock (workflow + EDA)

```bash
export AAP_URL="https://aap-aap.apps.cluster-d796h.dyn.redhatworkshops.io"
export AAP_USERNAME="admin"
export AAP_PASSWORD="<senha>"
export EDA_PROJECT_URL="https://github.com/raphaelmorsch/ansible-and-postgres.git"

python3 scripts/setup_deadlock_automation.py
```

Esse script idempotente cria/atualiza:

- Job Templates **JT08–JT12**
- Workflow **`WF — Resposta a Deadlock PostgreSQL`** (com nó de aprovação humana)
- Job Template shim **`WF — Resposta a Deadlock PostgreSQL`** (launcher do EDA)
- Decision Environment, projeto EDA, credencial AAP no EDA e **Activation Deadlock AutoResponse v2**

Aguarde a activation ficar **`running`** na UI do EDA.

### 9. Configurar webhook na app de deadlock

Obtenha a URL do webhook da activation no EDA (**Automation Decisions → Activations → Activation Deadlock AutoResponse v2 → Endpoint**).

Ou via API:

```bash
curl -sk -u 'admin:<senha>' \
  "${AAP_URL}/api/eda/v1/activations/?name=Activation%20Deadlock%20AutoResponse%20v2" \
  | python3 -m json.tool
```

Configure a app:

```bash
./scripts/configure_deadlock_webhook.sh 'https://<endpoint-da-activation>'
```

O script define `EDA_WEBHOOK_URL` e `APP_NAMESPACE` no Deployment `app-delta-deadlock` e aguarda o rollout.

### 10. Smoke test final

```bash
# Mocks
curl -sk "https://$(oc get route mock-vault -n mock-vault -o jsonpath='{.spec.host}')/secrets/postgresql-prod?mask=1"
curl -sk "https://$(oc get route mock-itsm -n mock-itsm -o jsonpath='{.spec.host}')/healthz"

# App deadlock (deve retornar deadlock_detected e eda_notification.sent=true)
curl -sk "https://$(oc get route app-delta-deadlock -n databases -o jsonpath='{.spec.host}')/deadlock" | python3 -m json.tool
```

No AAP, confirme que um novo **Workflow Job** de `WF — Resposta a Deadlock PostgreSQL` foi criado.

---

## Demonstração — Manutenção preventiva

1. Gere tuplas mortas (`generate_dead_tuples.sh` ou JT00)
2. No AAP: **Launch → `WF — Manutenção Preventiva PostgreSQL`**
3. Acompanhe:
   - JT01 classifica tabelas por % de tuplas mortas
   - JT02 abre ticket no Mock ITSM
   - JT03 executa VACUUM ANALYZE (com safety checks)
   - JT04 valida resultado
   - JT05 encerra ticket com relatório

### Política de tuplas mortas (JT01)

| % dead tuples | Ação |
|---|---|
| `< 10%` | nenhuma |
| `10–20%` | VACUUM ANALYZE |
| `> 20%` | VACUUM ANALYZE + avaliação de índice |
| `≥ 70%` | requer aprovação DBA → JT06/JT07 |

---

## Demonstração — Deadlock automático (EDA)

### Na UI (roteiro sugerido)

1. **OpenShift** — abra a route de `app-delta-deadlock` e clique em **Gerar deadlock**
2. **EDA** — em **Activations**, confirme incremento do **Fire Count** em `Activation Deadlock AutoResponse v2`
3. **AAP Controller** — abra o **Workflow Job** recém-criado de `WF — Resposta a Deadlock PostgreSQL`
4. Acompanhe:
   - **JT08** — diagnóstico + ticket ITSM
   - **JT09** — scale `app-delta-deadlock` → 0 réplicas
   - **Aprovação** — aprove a remediação destrutiva no banco
   - **JT10** — terminate sessions + `VACUUM (ANALYZE) deadlock_lab`
   - **JT11** — valida banco + contenção mantida
   - **JT12** — fecha ticket

### Payload enviado ao EDA

```json
{
  "event_type": "postgres_deadlock_detected",
  "app_name": "app-delta-deadlock",
  "database": "sampledb",
  "namespace": "databases",
  "timestamp": 1787164103,
  "result": { "worker_a": "...", "worker_b": "...", "deadlock_detected": true }
}
```

Rulebook (`rulebooks/postgres_deadlock_webhook.yml`) dispara quando `event.payload.event_type == "postgres_deadlock_detected"`.

---

## Job Templates

### Manutenção preventiva

| JT | Playbook | Função |
|---|---|---|
| JT00 | `jt00_generate_dead_tuples.yml` | Gera tuplas mortas para demo |
| JT01 | `jt01_health_check.yml` | Health check + classificação |
| JT02 | `jt02_open_ticket.yml` | Abrir ticket ITSM |
| JT03 | `jt03_execute_maintenance.yml` | Safety checks + VACUUM ANALYZE |
| JT04 | `jt04_validate_result.yml` | Validação pós-manutenção |
| JT05 | `jt05_close_ticket.yml` | Encerrar ticket |
| JT06 | `jt06_collect_evidence.yml` | Evidências (falha) |
| JT07 | `jt07_mark_ticket_pending.yml` | Ticket pendente (falha) |

### Resposta a deadlock

| JT | Playbook | Função |
|---|---|---|
| JT08 | `jt08_deadlock_diagnose.yml` | Diagnóstico + abrir ticket |
| JT09 | `jt09_contain_application.yml` | Scale Deployment → 0 no OpenShift |
| JT10 | `jt10_remediate_deadlock.yml` | Terminate sessions + VACUUM ANALYZE |
| JT11 | `jt11_validate_deadlock_recovery.yml` | Validar banco + contenção |
| JT12 | `jt12_close_deadlock_ticket.yml` | Fechar ticket com relatório |

### Workflows

| Workflow | Descrição |
|---|---|
| `WF — Instalar PostgreSQL (OpenShift)` | Provisiona namespace + PostgreSQL |
| `WF — Manutenção Preventiva PostgreSQL` | Rotina JT01–JT07 |
| `WF — Resposta a Deadlock PostgreSQL` | Rotina JT08–JT12 + aprovação humana |

---

## URLs do ambiente (d796h)

Substitua pelo host do seu cluster:

| Componente | URL |
|---|---|
| AAP Gateway | `https://aap-aap.apps.cluster-d796h.dyn.redhatworkshops.io` |
| Mock Vault | `https://mock-vault-mock-vault.apps.cluster-d796h.dyn.redhatworkshops.io` |
| Mock ITSM | `https://mock-itsm-mock-itsm.apps.cluster-d796h.dyn.redhatworkshops.io` |
| Git interno (Controller) | `http://demo-git.demo-git.svc.cluster.local/ansible-and-postgres.git` |
| GitHub (EDA + recomendado) | `https://github.com/raphaelmorsch/ansible-and-postgres.git` |

Routes das apps:

```bash
oc get route -n databases
oc get route -n mock-vault
oc get route -n mock-itsm
```

---

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---|---|---|
| EDA Fire Count sobe, workflow não dispara | Job Template shim ausente ou rulebook antigo | Rode `setup_deadlock_automation.py`; confirme projeto EDA no GitHub |
| Activation EDA `failed` | Rulebook com `run_playbook` sem inventário, ou credencial AAP errada | Verifique logs do pod `aap-eda-activation-worker`; credencial EDA deve usar host `.../api/controller/` |
| JT10 falha no VACUUM | `autocommit: false` | Sincronize projeto do Controller com GitHub (commit com `autocommit: true`) |
| JT11 falha no k8s | Credencial OpenShift não anexada ao JT11 | Rode `setup_deadlock_automation.py` (anexa credencial ao JT09 e JT11) |
| `eda_notification.sent: false` | `EDA_WEBHOOK_URL` não configurada | Rode `configure_deadlock_webhook.sh` com endpoint da activation |
| Projeto EDA import failed (dumb http) | Git interno não suporta shallow clone | Use `EDA_PROJECT_URL` apontando para GitHub |

Logs úteis:

```bash
# EDA activation worker
oc logs -n aap -l app.kubernetes.io/component=eda-activation-worker --tail=100

# App deadlock
oc logs -n databases deployment/app-delta-deadlock --tail=50
```

---

## Collections Ansible

Definidas em `collections/requirements.yml`:

- `edb.epas_postgresql` — queries e manutenção PostgreSQL
- `community.general` — utilitários gerais
- `kubernetes.core` — operações OpenShift (JT09, JT11)

---

## Estrutura do repositório

```
ansible-and-postgres/
├── inventory/           # Inventário AAP (host PostgreSQL)
├── playbooks/           # JT00–JT12 + install + EDA launcher
├── rulebooks/           # postgres_deadlock_webhook.yml (EDA)
├── openshift/           # Manifests OpenShift (mocks, PG, apps)
├── mocks/               # Código Mock Vault e Mock ITSM
├── scripts/             # Deploy e setup automatizado
└── sql/                 # Scripts SQL auxiliares
```
