-- =========================================================================
-- Repartition des couts de transport Ferment Station <-> Spraga (PRD v1.1)
-- Fichier separe le temps que db/migrate.sql soit demele ; a fusionner ensuite.
-- 2 tables : transport_allocation (coeur), allocation_audit (journal).
--
-- Source du cout : reconciliation (Pennylane SOFRIPA -> commande EasyBeer).
-- Cle de repartition : poids des lignes par entite (FS vs Spraga).
--
-- Cycle de vie : PROVISOIRE (recalcule a chaque sync, ecrasable)
--                VALIDEE   (fige par l'operateur, JAMAIS ecrase par la sync)
--                ANOMALIE  (poids nul / reference non classee / cout absent)
-- Le gel resulte UNIQUEMENT de la validation operateur (cf. PRD sect. 6).
-- =========================================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Table 1 : une commande = 1 repartition (upsert par sync, sauf si VALIDEE)
CREATE TABLE IF NOT EXISTS transport_allocation (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_number          INT  NOT NULL,                 -- N° commande EasyBeer
  order_client          TEXT,
  order_status          TEXT,                          -- EN_COURS | EN_ATTENTE_STOCK | LIVREE (info)
  invoice_number        TEXT,                          -- N° piece/facture SOFRIPA rattache
  period_month          TEXT,                          -- 'YYYY-MM' (filtre UI)
  weight_fs_kg          NUMERIC,
  weight_spraga_kg      NUMERIC,
  weight_total_kg       NUMERIC,
  pct_fs                NUMERIC,
  pct_spraga            NUMERIC,
  transport_cost_total  NUMERIC,                        -- snapshot (Pennylane)
  cost_fs               NUMERIC,
  cost_spraga           NUMERIC,
  allocation_status     TEXT NOT NULL DEFAULT 'PROVISOIRE', -- PROVISOIRE | VALIDEE | ANOMALIE
  is_manual_override    BOOLEAN NOT NULL DEFAULT false,     -- VALIDEE apres modification manuelle
  anomaly_reason        TEXT,                               -- NULL si RAS
  lines_json            JSONB NOT NULL DEFAULT '[]',         -- snapshot des lignes classees
  validated_at          TIMESTAMPTZ,
  validated_by          TEXT,                                -- email/id operateur
  last_synced_at        TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Une commande = une seule repartition par tenant
  UNIQUE (tenant_id, order_number)
);
CREATE INDEX IF NOT EXISTS idx_ta_tenant_period
  ON transport_allocation(tenant_id, period_month);
CREATE INDEX IF NOT EXISTS idx_ta_tenant_status
  ON transport_allocation(tenant_id, allocation_status);

-- Table 2 : journal d'audit (validation + modifications manuelles)
CREATE TABLE IF NOT EXISTS allocation_audit (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_number   INT,
  action         TEXT NOT NULL,                 -- VALIDATE | EDIT | RESET | SYNC
  field          TEXT,                          -- champ modifie (NULL si validation simple)
  value_before   TEXT,
  value_after    TEXT,
  reason         TEXT,
  changed_by     TEXT,
  changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_aa_order
  ON allocation_audit(tenant_id, order_number);

-- ── RLS multi-tenant (meme motif que invoices_schema.sql) ─────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['transport_allocation','allocation_audit']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($f$CREATE POLICY tenant_isolation ON %I USING (
        current_setting('app.current_tenant_id', true) IS NULL
        OR current_setting('app.current_tenant_id', true) = ''
        OR tenant_id = current_setting('app.current_tenant_id', true)::uuid)
      WITH CHECK (
        current_setting('app.current_tenant_id', true) IS NULL
        OR current_setting('app.current_tenant_id', true) = ''
        OR tenant_id = current_setting('app.current_tenant_id', true)::uuid)$f$, t);
  END LOOP;
END $$;

-- ── Droits pour l'utilisateur applicatif "shark" (prod) ───────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shark') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE
      ON transport_allocation, allocation_audit TO shark;
  END IF;
END $$;
