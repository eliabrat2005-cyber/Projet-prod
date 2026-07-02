-- =========================================================================
-- Intake factures Pennylane/SOFRIPA — registre IMMUABLE (CDC Phase 1)
-- Fichier séparé le temps que db/migrate.sql soit démêlé ; à fusionner ensuite.
-- 3 tables : processed_invoices, processed_invoice_lines, invoice_processing_audit
-- Règles CDC : immuable (aucun UPDATE), unicité id_facture_source, audit complet.
-- =========================================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Table 1 : une facture = 1 record (jamais modifié après insertion)
CREATE TABLE IF NOT EXISTS processed_invoices (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  id_facture_source  TEXT NOT NULL,                 -- ex. "SO014744"
  date_facture       DATE,
  date_traitement    TIMESTAMPTZ NOT NULL DEFAULT now(),
  montant_ht         NUMERIC,
  montant_tva        NUMERIC,
  montant_ttc        NUMERIC,
  maj_go             NUMERIC,
  maj_gnr            NUMERIC,
  maj_total          NUMERIC,
  nb_lignes          INT NOT NULL DEFAULT 0,
  status             TEXT NOT NULL,                 -- 'OK' | 'REJECTED'
  error_log          TEXT,
  facture_data_json  JSONB NOT NULL DEFAULT '{}',   -- snapshot complet du PDF parsé
  created_by         UUID REFERENCES users(id) ON DELETE SET NULL,
  -- Unicité : une facture n'est jamais traitée 2 fois (par tenant)
  UNIQUE (tenant_id, id_facture_source)
);

-- Table 2 : une ligne de transport = 1 record
CREATE TABLE IF NOT EXISTS processed_invoice_lines (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  invoice_id            UUID NOT NULL REFERENCES processed_invoices(id) ON DELETE CASCADE,
  id_facture_source     TEXT NOT NULL,
  ligne_index           INT NOT NULL,
  exp_date              TEXT,
  jour                  TEXT,
  num_ordre_transport   TEXT,                        -- N° OT SOFRIPA
  num_piece             TEXT,                        -- N° pièce (clé matching)
  expediteur            TEXT,
  destinataire          TEXT,
  poids                 NUMERIC,
  unite                 TEXT,                        -- KGS | PAL | FO
  transport             NUMERIC,
  frais_admin           NUMERIC,
  montant_brut          NUMERIC,                     -- avant gasoil
  surtaxe_gasoil        NUMERIC,
  montant_final         NUMERIC,                     -- après gasoil
  id_commande_easybeer  INT,
  client_easybeer       TEXT,
  statut_match          TEXT                         -- OK | sans_piece | commande_absente | non_livree
);
CREATE INDEX IF NOT EXISTS idx_pil_invoice ON processed_invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS idx_pil_piece ON processed_invoice_lines(tenant_id, num_piece);

-- Table 3 : journal d'audit (toutes les actions)
CREATE TABLE IF NOT EXISTS invoice_processing_audit (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  id_facture_source  TEXT,
  action             TEXT NOT NULL,                 -- PARSE|VALIDATE|LINK|STORE|SKIP|ERROR
  details            JSONB NOT NULL DEFAULT '{}',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ipa_facture ON invoice_processing_audit(tenant_id, id_facture_source);

-- ── IMMUABILITÉ : bloquer tout UPDATE sur les 2 tables de données ─────────
CREATE OR REPLACE FUNCTION _bloquer_update_immuable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Table immuable (%): UPDATE interdit (CDC intake factures)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_immuable_invoices ON processed_invoices;
CREATE TRIGGER trg_immuable_invoices BEFORE UPDATE ON processed_invoices
  FOR EACH ROW EXECUTE FUNCTION _bloquer_update_immuable();

DROP TRIGGER IF EXISTS trg_immuable_lines ON processed_invoice_lines;
CREATE TRIGGER trg_immuable_lines BEFORE UPDATE ON processed_invoice_lines
  FOR EACH ROW EXECUTE FUNCTION _bloquer_update_immuable();

-- ── RLS multi-tenant ──────────────────────────────────────────────────────
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['processed_invoices','processed_invoice_lines','invoice_processing_audit']
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
    GRANT SELECT, INSERT, DELETE ON processed_invoices, processed_invoice_lines,
                                    invoice_processing_audit TO shark;
  END IF;
END $$;
