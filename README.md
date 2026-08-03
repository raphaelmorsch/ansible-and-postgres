# AAP — Manutenção Preventiva de PostgreSQL

Demonstração do Ansible Automation Platform orquestrando uma rotina corporativa de manutenção preventiva do PostgreSQL, com:

- **Mock Vault** para credenciais (senha fora do playbook/Git/logs)
- **Mock ITSM** para abertura, atualização e encerramento de tickets
- **Workflow AAP** com Job Templates JT01–JT07

## Arquitetura

```
AAP Scheduler → Workflow → Job Templates
        │              │
        ▼              ▼
   Mock Vault      ITSM Mock
        │
        ▼
   PostgreSQL (VACUUM / ANALYZE / validação)
```

## Job Templates

| JT | Função |
|----|--------|
| JT01 | Health check + classificação de tuplas mortas |
| JT02 | Abrir ticket no ITSM Mock |
| JT03 | Safety checks + VACUUM ANALYZE |
| JT04 | Validação pós-manutenção |
| JT05 | Encerrar ticket com relatório |
| JT06 | Coletar evidências (caminho de falha) |
| JT07 | Marcar ticket como pendente |

## Deploy no cluster

Pré-requisitos: `oc` logado no cluster, AAP acessível, PostgreSQL em `postgresql.postgresql.svc.cluster.local`.

```bash
chmod +x scripts/*.sh scripts/*.py
./scripts/deploy_all.sh
python3 scripts/setup_container_group.py   # necessário quando não há execution nodes
```

## URLs do ambiente atual

| Componente | URL |
|---|---|
| AAP Gateway | https://aap-aap.apps.cluster-bfd7z-1.dyn.redhatworkshops.io |
| Workflow | Templates → **WF — Manutenção Preventiva PostgreSQL** |
| Mock Vault | https://mock-vault-mock-vault.apps.cluster-bfd7z-1.dyn.redhatworkshops.io |
| Mock ITSM UI | https://mock-itsm-mock-itsm.apps.cluster-bfd7z-1.dyn.redhatworkshops.io |
| Git SCM (interno) | `http://demo-git.demo-git.svc.cluster.local/ansible-and-postgres.git` |

## Política de tuplas mortas

- `< 10%`: nenhuma ação
- `10–20%`: VACUUM ANALYZE
- `> 20%`: VACUUM ANALYZE + avaliação de índice
- `≥ 70%`: requer aprovação do DBA (caminho JT06/JT07)

## Fluxo do Workflow

```
JT01 Health Check → JT02 Abrir Ticket → JT03 Manutenção → JT04 Validar → JT05 Encerrar
                                              ↓ (falha)
                                         JT06 Evidências → JT07 Ticket pendente
```

## Evidência da última execução bem-sucedida

- Ticket ITSM `DBA-1001` fechado automaticamente
- `public.itens_pedido`: 63.07% → 0% dead tuples
- `public.pagamentos`: 50.61% → 0% dead tuples
- Credenciais obtidas do Mock Vault (não gravadas no playbook)
