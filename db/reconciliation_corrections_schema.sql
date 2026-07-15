-- =========================================================================
-- Couche de CORRECTIONS opérateur (réconciliation transport).
-- Principe : le registre factures reste IMMUABLE ; les corrections manuelles
-- (éditer / supprimer une ligne réconciliée, forcer la validation d'une
-- facture) sont stockées ICI, tracées, et ré-appliquées PAR-DESSUS le snapshot
-- après chaque synchro. L'opérateur garde le contrôle sans casser l'audit.
-- =========================================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Correction d'une LIGNE réconciliée (clé = n° de commande EasyBeer).
-- Un champ NULL = pas de surcharge (on garde la valeur d'origine).
CREATE TABLE IF NOT EXISTS reconciliation_line_corrections (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  order_number   INT  NOT NULL,
  deleted        BOOLEAN NOT NULL DEFAULT false,   -- ligne masquée par l'opérateur
  client         TEXT,
  poids_eb       NUMERIC,
  poids_sofripa  NUMERIC,
  cout_transport NUMERIC,
  montant_ht     NUMERIC,
  reason         TEXT,
  updated_by     TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, order_number)
);

-- Validation FORCÉE d'une facture (l'opérateur sait qu'elle est OK).
CREATE TABLE IF NOT EXISTS facture_status_overrides (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  id_facture_source TEXT NOT NULL,
  forced_status     TEXT NOT NULL,                 -- 'OK' (validée à la main)
  reason            TEXT,
  updated_by        TEXT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id_facture_source)
);

-- Journal des corrections (qui / quand / avant-après).
CREATE TABLE IF NOT EXISTS reconciliation_correction_audit (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  cible        TEXT NOT NULL,               -- 'ligne <numero>' | 'facture <SOxxx>'
  action       TEXT NOT NULL,               -- EDIT | DELETE | RESET | VALIDATE
  details      JSONB NOT NULL DEFAULT '{}',
  changed_by   TEXT,
  changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rca_tenant ON reconciliation_correction_audit(tenant_id, changed_at);

-- ── RLS multi-tenant + grants (même motif que les autres schémas) ─────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['reconciliation_line_corrections',
                           'facture_status_overrides',
                           'reconciliation_correction_audit']
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

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shark') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE
      ON reconciliation_line_corrections, facture_status_overrides,
         reconciliation_correction_audit TO shark;
  END IF;
END $$;
