"""One-time optional migration from local ecoblack.db to MongoDB Atlas.

Run from the project root after installing requirements and setting MONGODB_URI.
The SQLite file is deliberately not included in the deployment ZIP or Git repository.
"""
import os
import sqlite3
import sys
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError

BASE = Path(__file__).resolve().parent
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", str(BASE / "ecoblack.db")))
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "ecoblack").strip() or "ecoblack"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "tragic").strip().lower() or "tragic"

if not MONGODB_URI:
    print("ERROR: Configura la variable MONGODB_URI con tu cadena de conexión de Atlas.")
    sys.exit(2)
if not SQLITE_PATH.exists():
    print(f"ERROR: No encuentro {SQLITE_PATH}. Copia tu ecoblack.db antiguo junto a este script o define SQLITE_PATH.")
    sys.exit(2)

MAPPING = {
    "users": ("users", "username"),
    "modules": ("modules", "id"),
    "module_sessions": ("module_sessions", "id"),
    "balance_transactions": ("balance_transactions", "id"),
    "admin_account": ("admin_account", "id"),
    "admin_revenue_transactions": ("admin_revenue_transactions", "id"),
}
COUNTER_COLLECTIONS = ("users", "module_sessions", "balance_transactions", "admin_revenue_transactions")


def row_docs(connection, table):
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in existing:
        return []
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def main():
    sqlite_connection = sqlite3.connect(str(SQLITE_PATH))
    sqlite_connection.row_factory = sqlite3.Row
    mongo_client = MongoClient(MONGODB_URI, appname="EcoBlack-Migration", serverSelectionTimeoutMS=15000, retryWrites=True, w="majority")
    mongo_client.admin.command("ping")
    database = mongo_client[MONGODB_DB]
    counts = {}
    try:
        for table, (collection_name, key_name) in MAPPING.items():
            docs = row_docs(sqlite_connection, table)
            collection = database[collection_name]
            count = 0
            for old in docs:
                document = dict(old)
                key = document.get(key_name)
                if key is None:
                    continue
                # Mongo uses natural _id keys for users/modules and integer IDs for ledgers/sessions.
                document["_id"] = str(key) if table in ("users", "modules") else int(key)
                if table == "users":
                    document["blocked"] = bool(document.get("blocked", 0))
                    document.setdefault("balance", 100.0 if document.get("role") != "admin" else 0.0)
                    document.setdefault("discount_percent", 0.0)
                    document.setdefault("display_name", document.get("username"))
                    document.setdefault("avatar_data", None)
                    # Keep the Atlas admin password chosen for deployment; never overwrite
                    # it with the old local password (often the demo password "69").
                    existing_admin = database["users"].find_one({"username": ADMIN_USERNAME}) if str(key).lower() == ADMIN_USERNAME else None
                    if existing_admin and str(key).lower() == ADMIN_USERNAME:
                        document["password_hash"] = existing_admin.get("password_hash", document.get("password_hash"))
                        document["role"] = "admin"
                        document["blocked"] = False
                elif table == "modules":
                    document.setdefault("occupied_by", None)
                    document.setdefault("occupied_until", None)
                    connector_value = document.get("connectors") or "USB-C"
                    document["connectors"] = [value.strip() for value in connector_value.split(",") if value.strip()] if isinstance(connector_value, str) else connector_value
                elif table == "module_sessions":
                    document["active"] = bool(document.get("active", 0))
                elif table == "admin_revenue_transactions":
                    document["collected"] = bool(document.get("collected", 0))
                collection.replace_one({"_id": document["_id"]}, document, upsert=True)
                count += 1
            counts[table] = count
            print(f"{table}: {count} documentos copiados")

        # Seed sequence counters to avoid colliding with imported integer IDs.
        for collection_name in COUNTER_COLLECTIONS:
            max_id_doc = database[collection_name].find_one(sort=[("id", -1)])
            maximum = int(max_id_doc.get("id", 0)) if max_id_doc and isinstance(max_id_doc.get("id"), int) else 0
            if maximum:
                database["counters"].update_one({"_id": collection_name}, {"$max": {"seq": maximum}}, upsert=True)
        account = database["admin_account"].find_one({"_id": 1})
        if not account:
            database["admin_account"].insert_one({"_id": 1, "id": 1, "owner_username": "tragic", "balance": 0.0, "updated_at": 0})
        print("\nMigración completada. No borres el archivo SQLite hasta verificar usuarios, módulos y recaudación en Atlas.")
    finally:
        sqlite_connection.close()
        mongo_client.close()


if __name__ == "__main__":
    try:
        main()
    except (sqlite3.Error, PyMongoError, OSError) as error:
        print(f"ERROR de migración: {error}")
        sys.exit(1)
