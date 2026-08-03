#!/usr/bin/env bash
set -euo pipefail

NS="${POSTGRES_NAMESPACE:-postgresql}"
POD="$(oc get pods -n "$NS" -l name=postgresql -o jsonpath='{.items[0].metadata.name}')"
DB_USER="${PG_USER:-user1}"
DB_NAME="${PG_DATABASE:-sampledb}"

echo "Seeding demo data on pod $POD (db=$DB_NAME user=$DB_USER)"

oc exec -i -n "$NS" "$POD" -- psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
DROP TABLE IF EXISTS itens_pedido CASCADE;
DROP TABLE IF EXISTS pagamentos CASCADE;
DROP TABLE IF EXISTS pedidos CASCADE;
DROP TABLE IF EXISTS clientes CASCADE;

CREATE TABLE clientes (
  id serial PRIMARY KEY,
  nome text NOT NULL,
  criado_em timestamptz DEFAULT now()
);

CREATE TABLE pedidos (
  id serial PRIMARY KEY,
  cliente_id int REFERENCES clientes(id),
  valor numeric(12,2) NOT NULL,
  status text NOT NULL,
  criado_em timestamptz DEFAULT now()
);

CREATE TABLE pagamentos (
  id serial PRIMARY KEY,
  pedido_id int REFERENCES pedidos(id),
  valor numeric(12,2) NOT NULL,
  metodo text NOT NULL,
  criado_em timestamptz DEFAULT now()
);

CREATE TABLE itens_pedido (
  id serial PRIMARY KEY,
  pedido_id int REFERENCES pedidos(id),
  produto text NOT NULL,
  quantidade int NOT NULL,
  criado_em timestamptz DEFAULT now()
);

ALTER TABLE clientes SET (autovacuum_enabled = false);
ALTER TABLE pedidos SET (autovacuum_enabled = false);
ALTER TABLE pagamentos SET (autovacuum_enabled = false);
ALTER TABLE itens_pedido SET (autovacuum_enabled = false);

INSERT INTO clientes (nome)
SELECT 'cliente-' || g FROM generate_series(1, 5000) g;

INSERT INTO pedidos (cliente_id, valor, status)
SELECT (random()*4999)::int + 1, round((random()*500)::numeric, 2), 'aberto'
FROM generate_series(1, 20000);

INSERT INTO pagamentos (pedido_id, valor, metodo)
SELECT (random()*19999)::int + 1, round((random()*500)::numeric, 2), 'pix'
FROM generate_series(1, 12000);

INSERT INTO itens_pedido (pedido_id, produto, quantidade)
SELECT (random()*19999)::int + 1, 'produto-' || ((random()*100)::int), (random()*5)::int + 1
FROM generate_series(1, 8000);

-- Create dead tuples without breaking FKs:
-- 1) delete dependent rows for target pedidos
-- 2) delete those pedidos
-- 3) create additional dead tuples on child tables
DELETE FROM itens_pedido WHERE pedido_id % 5 = 0;
DELETE FROM pagamentos WHERE pedido_id % 5 = 0;
DELETE FROM pedidos WHERE id % 5 = 0;              -- ~20% dead on pedidos

DELETE FROM itens_pedido WHERE id % 3 = 0;         -- extra dead on itens
DELETE FROM pagamentos WHERE id % 4 = 0;           -- extra dead on pagamentos
UPDATE clientes SET nome = nome || '-x' WHERE id % 50 = 0;
DELETE FROM clientes WHERE id % 50 = 0 AND NOT EXISTS (
  SELECT 1 FROM pedidos p WHERE p.cliente_id = clientes.id
);

ANALYZE clientes;
ANALYZE pedidos;
ANALYZE pagamentos;
ANALYZE itens_pedido;

-- Give stats collector a moment, then re-read
SELECT pg_sleep(2);

SELECT
  schemaname,
  relname,
  n_live_tup,
  n_dead_tup,
  ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct
FROM pg_stat_user_tables
ORDER BY dead_pct DESC NULLS LAST;
SQL

echo "Seed completed."
