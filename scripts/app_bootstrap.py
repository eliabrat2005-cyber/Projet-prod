import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import text

from db.conn import engine


def run_file(path: str):
    if not os.path.exists(path):
        print(f"⏭️  {path} absent, ignoré")
        return
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    with engine().begin() as conn:
        conn.execute(text(sql))
    print(f"✅ Migration exécutée : {path}")

if __name__ == "__main__":
    run_file("db/migrate.sql")
    # Schémas de features en fichiers séparés (à fusionner dans migrate.sql
    # une fois celui-ci démêlé). Dépendent des tables de base (tenants, users).
    run_file("db/invoices_schema.sql")
    run_file("db/transport_allocation_schema.sql")
