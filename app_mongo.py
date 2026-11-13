"""MongoDB Atlas backend for EcoBlack V16 · plan access, Bmax Pro and precise map groups.

The route response contracts intentionally match static/js/app.js.
Production configuration comes from environment variables; never commit secrets.
"""
from __future__ import annotations

import os
import time
import secrets
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, jsonify, request, session
from pymongo import MongoClient, ReturnDocument, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import generate_password_hash, check_password_hash

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "ecoblack").strip() or "ecoblack"
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI no está configurada. Usa app.py en local o configura MongoDB Atlas.")

app = Flask(__name__)
if (os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID")) and not os.getenv("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY es obligatoria en Render. Configúrala en Environment.")
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "local-development-only-change-me"),
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID")),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
)

client = MongoClient(
    MONGODB_URI,
    appname="EcoBlack",
    serverSelectionTimeoutMS=12000,
    connectTimeoutMS=12000,
    retryWrites=True,
    w="majority",
)
db = client[MONGODB_DB]
users = db["users"]
modules = db["modules"]
module_sessions = db["module_sessions"]
balance_transactions = db["balance_transactions"]
admin_account = db["admin_account"]
admin_revenue_transactions = db["admin_revenue_transactions"]
counters = db["counters"]
bmax_config = db["bmax_config"]
bmax_reservations = db["bmax_reservations"]
bmax_purchases = db["bmax_purchases"]

WELCOME_BALANCE = 100.0
BMAX_DEFAULT_SLOTS = 10
BMAX_DEFAULT_PRICE = 99.0  # Precio inicial editable desde el panel de administración.
BMAX_RESERVATION_SECONDS = 300
DEFAULT_MODULES = [
    {"id": "EB-001", "name": "EcoBlack Centro", "lat": 21.1619, "lng": -86.8515, "city": "Cancún", "state": "Quintana Roo", "status": "available", "power": "30 W", "connectors": ["USB-C", "Lightning", "Inalámbrico"], "created_by": "system"},
    {"id": "EB-002", "name": "EcoBlack Plaza", "lat": 21.1638, "lng": -86.8460, "city": "Cancún", "state": "Quintana Roo", "status": "available", "power": "45 W", "connectors": ["USB-C", "Lightning"], "created_by": "system"},
    {"id": "EB-003", "name": "EcoBlack Zona Hotelera", "lat": 21.1357, "lng": -86.7416, "city": "Cancún", "state": "Quintana Roo", "status": "available", "power": "65 W", "connectors": ["USB-C", "Inalámbrico"], "created_by": "system"},
    {"id": "EB-004", "name": "EcoBlack Puerto", "lat": 21.1743, "lng": -86.8234, "city": "Cancún", "state": "Quintana Roo", "status": "available", "power": "25 W", "connectors": ["USB-C", "Micro-USB"], "created_by": "system"},
]
PACKAGES = {
    "basic": {"id": "basic", "name": "Básico", "minutes": 15, "price": 15.0, "max_watts": 10, "description": "Hasta 15 min de sesión y potencia limitada a 10 W."},
    "fast": {"id": "fast", "name": "Rápido", "minutes": 30, "price": 25.0, "max_watts": 20, "description": "Hasta 30 min de sesión y potencia limitada a 20 W."},
    "power": {"id": "power", "name": "Power", "minutes": 45, "price": 40.0, "max_watts": 45, "description": "Hasta 45 min de sesión y potencia limitada a 45 W."},
    "custom": {"id": "custom", "name": "Personalizado", "minutes": 25, "price": 25.0, "max_watts": 30, "description": "Elige una duración de 10 a 120 minutos, con un límite de 30 W."},
    "bmax_pro": {"id": "bmax_pro", "name": "Bmax Pro", "minutes": 0, "price": 0.0, "max_watts": 100, "description": "Acceso ilimitado: sesiones estimadas hasta el 100% de batería, con potencia máxima de 100 W y limitada por tu celular, módulo y conector."},
}
CONNECTOR_WATT_LIMITS = {"USB-C": 240, "Lightning": 20, "Micro-USB": 12, "Inalámbrico": 15}
CONNECTOR_EFFICIENCY = {"USB-C": 0.78, "Lightning": 0.75, "Micro-USB": 0.70, "Inalámbrico": 0.68}

class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def next_id(name: str, mongo_session=None) -> int:
    doc = counters.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=ReturnDocument.AFTER, session=mongo_session,
    )
    return int(doc["seq"])



def release_expired_bmax_reservations():
    """Return expired 5-minute holds to the shared Bmax inventory, exactly once."""
    now = time.time()
    expired = list(bmax_reservations.find({"status": "active", "expires_at": {"$lte": now}}, {"_id": 1}))
    for item in expired:
        try:
            with client.start_session() as mongo_session:
                with mongo_session.start_transaction():
                    changed = bmax_reservations.update_one(
                        {"_id": item["_id"], "status": "active", "expires_at": {"$lte": now}},
                        {"$set": {"status": "expired", "updated_at": now}}, session=mongo_session,
                    )
                    if changed.modified_count:
                        bmax_config.update_one(
                            {"_id": "bmax_pro", "reserved_count": {"$gte": 1}},
                            {"$inc": {"reserved_count": -1}, "$set": {"updated_at": now}}, session=mongo_session,
                        )
        except PyMongoError:
            app.logger.exception("No se pudo liberar un apartado vencido de Bmax Pro")


def bmax_status_for_user(user):
    release_expired_bmax_reservations()
    config = bmax_config.find_one({"_id": "bmax_pro"}) or {
        "total_slots": BMAX_DEFAULT_SLOTS, "reserved_count": 0, "sold_count": 0, "price_mxn": BMAX_DEFAULT_PRICE,
    }
    username = (user or {}).get("username")
    purchase = bmax_purchases.find_one({"username": username, "status": "active"}) if username else None
    reservation = bmax_reservations.find_one({"username": username, "status": "active", "expires_at": {"$gt": time.time()}}) if username else None
    total = int(config.get("total_slots", BMAX_DEFAULT_SLOTS))
    sold = int(config.get("sold_count", 0))
    reserved = int(config.get("reserved_count", 0))
    return {
        "package_id": "bmax_pro", "name": "Bmax Pro", "total_slots": total,
        "sold_count": sold, "reserved_count": reserved,
        "available_slots": max(0, total - sold - reserved),
        "price_mxn": round(float(config.get("price_mxn", BMAX_DEFAULT_PRICE)), 2),
        "has_purchased": bool(purchase),
        "purchase": {"purchased_at": purchase.get("purchased_at"), "price_mxn": purchase.get("price_mxn")} if purchase else None,
        "reservation": {"id": reservation.get("_id"), "expires_at": reservation.get("expires_at"), "created_at": reservation.get("created_at")} if reservation else None,
        "reservation_seconds": max(0, int(reservation["expires_at"] - time.time())) if reservation else 0,
        "sold_out": (total - sold - reserved) <= 0 and not purchase,
    }


def init_db():
    client.admin.command("ping")
    users.create_index([("username", ASCENDING)], unique=True, name="username_unique")
    modules.create_index([("id", ASCENDING)], unique=True, name="module_id_unique")
    modules.create_index([("status", ASCENDING), ("lat", ASCENDING), ("lng", ASCENDING)], name="module_status_coords")
    module_sessions.create_index([("username", ASCENDING), ("active", ASCENDING), ("id", DESCENDING)], name="session_user_active")
    module_sessions.create_index([("module_id", ASCENDING), ("active", ASCENDING)], name="session_module_active")
    # Stop two concurrent requests from opening simultaneous sessions for one user.
    duplicates = list(module_sessions.aggregate([
        {"$match": {"active": True}},
        {"$sort": {"id": -1}},
        {"$group": {"_id": "$username", "ids": {"$push": "$id"}}},
        {"$match": {"ids.1": {"$exists": True}}},
    ]))
    for duplicate in duplicates:
        stale_ids = duplicate["ids"][1:]
        stale = list(module_sessions.find({"id": {"$in": stale_ids}}, {"module_id": 1}))
        for item in stale:
            modules.update_one({"id": item["module_id"], "occupied_by": duplicate["_id"]}, {"$set": {"status": "available", "occupied_by": None, "occupied_until": None}})
        module_sessions.update_many({"id": {"$in": stale_ids}}, {"$set": {"active": False, "ended_at": time.time()}})
    module_sessions.create_index([("username", ASCENDING)], unique=True, partialFilterExpression={"active": True}, name="one_active_session_per_user")
    balance_transactions.create_index([("username", ASCENDING), ("id", DESCENDING)], name="balance_user_id")
    admin_revenue_transactions.create_index([("id", DESCENDING)], name="revenue_id_desc")
    admin_revenue_transactions.create_index([("session_id", ASCENDING)], unique=True, partialFilterExpression={"session_id": {"$type": "number"}}, name="revenue_session_unique")
    admin_revenue_transactions.create_index([("collected", ASCENDING), ("collected_at", DESCENDING)], name="revenue_collected_date")
    bmax_reservations.create_index([("username", ASCENDING)], unique=True, partialFilterExpression={"status": "active"}, name="one_active_bmax_reservation_per_user")
    bmax_reservations.create_index([("status", ASCENDING), ("expires_at", ASCENDING)], name="bmax_reservation_expiry")
    bmax_purchases.create_index([("username", ASCENDING)], unique=True, name="one_bmax_purchase_per_user")
    bmax_purchases.create_index([("purchased_at", DESCENDING)], name="bmax_purchase_date")
    bmax_config.update_one(
        {"_id": "bmax_pro"},
        {"$setOnInsert": {"total_slots": BMAX_DEFAULT_SLOTS, "reserved_count": 0, "sold_count": 0, "price_mxn": BMAX_DEFAULT_PRICE, "created_at": time.time()}, "$set": {"updated_at": time.time()}},
        upsert=True,
    )

    admin_username = os.getenv("ADMIN_USERNAME", "tragic").strip().lower() or "tragic"
    admin_password = os.getenv("ADMIN_PASSWORD", "69")
    if (os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID")) and not os.getenv("ADMIN_PASSWORD") and not users.find_one({"username": admin_username}):
        raise RuntimeError("Configura ADMIN_PASSWORD en Render antes del primer despliegue; no se creará un admin con contraseña predecible.")
    if not users.find_one({"username": admin_username}):
        users.insert_one({
            "_id": admin_username, "id": next_id("users"), "username": admin_username,
            "password_hash": generate_password_hash(admin_password), "role": "admin",
            "usage": "Administración de módulos", "balance": 0.0, "discount_percent": 0.0,
            "blocked": False, "avatar_data": None, "display_name": admin_username,
            "active_plan": None, "active_plan_status": "none", "active_plan_minutes": 0,
            "plan_price_mxn": 0.0, "plan_purchased_at": None,
            "admin_service_theme": "wine", "created_at": now_iso(),
        })
    users.update_one(
        {"username": admin_username, "$or": [{"admin_service_theme": {"$exists": False}}, {"admin_service_theme": None}, {"admin_service_theme": ""}]},
        {"$set": {"admin_service_theme": "wine"}},
    )
    admin_account.update_one(
        {"_id": 1},
        {"$setOnInsert": {"owner_username": admin_username, "balance": 0.0, "updated_at": time.time()}},
        upsert=True,
    )
    if modules.count_documents({}) == 0:
        for item in DEFAULT_MODULES:
            doc = {**item, "_id": item["id"], "created_at": now_iso(), "occupied_by": None, "occupied_until": None}
            modules.insert_one(doc)
    # Backfill city/state metadata for built-in modules created by earlier versions.
    # Preserve any values already entered by the administrator.
    for item in DEFAULT_MODULES:
        modules.update_one(
            {"id": item["id"], "$or": [{"city": {"$exists": False}}, {"city": None}, {"city": ""}]},
            {"$set": {"city": item["city"]}},
        )
        modules.update_one(
            {"id": item["id"], "$or": [{"state": {"$exists": False}}, {"state": None}, {"state": ""}]},
            {"$set": {"state": item["state"]}},
        )
    # Initialize counters from documents so imported SQLite records don't collide.
    for collection_name, collection in (
        ("users", users), ("module_sessions", module_sessions),
        ("balance_transactions", balance_transactions),
        ("admin_revenue_transactions", admin_revenue_transactions),
        ("bmax_purchases", bmax_purchases),
    ):
        max_id = collection.find_one(sort=[("id", DESCENDING)])
        if max_id and isinstance(max_id.get("id"), int):
            counters.update_one({"_id": collection_name}, {"$max": {"seq": max_id["id"]}}, upsert=True)


def release_expired_modules():
    now = time.time()
    expired = list(modules.find(
        {"status": "busy", "occupied_until": {"$ne": None, "$lte": now}},
        {"id": 1},
    ))
    ids = [item["id"] for item in expired]
    if not ids:
        return
    modules.update_many(
        {"id": {"$in": ids}, "status": "busy", "occupied_until": {"$lte": now}},
        {"$set": {"status": "available", "occupied_by": None, "occupied_until": None}},
    )
    module_sessions.update_many(
        {"module_id": {"$in": ids}, "active": True},
        {"$set": {"active": False, "ended_at": now}},
    )


def user_public(user):
    if not user:
        return None
    result = {key: value for key, value in user.items() if key not in ("_id", "password_hash")}
    result["balance"] = round(float(result.get("balance") or 0), 2)
    result["discount_percent"] = round(float(result.get("discount_percent") or 0), 2)
    result["blocked"] = bool(result.get("blocked", False))
    return result


def current_user():
    username = session.get("username")
    if not username:
        return None
    user = users.find_one({"username": username})
    if not user or (user.get("blocked") and user.get("role") != "admin"):
        session.clear()
        return None
    return user_public(user)


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            return jsonify({"ok": False, "error": "Necesitas iniciar sesión."}), 401
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"ok": False, "error": "Necesitas iniciar sesión."}), 401
        if user.get("role") != "admin":
            return jsonify({"ok": False, "error": "Solo el administrador puede hacer esto."}), 403
        return fn(*args, **kwargs)
    return wrapped


@app.put("/api/admin/service-theme")
@admin_required
def update_admin_service_theme():
    data = request.get_json(silent=True) or {}
    theme = str(data.get("theme", "wine")).strip().lower()
    allowed = {"wine", "basic", "fast", "power", "custom", "bmax_pro"}
    if theme not in allowed:
        return jsonify({"ok": False, "error": "Tema de servicio no válido."}), 400
    user = current_user()
    users.update_one({"username": user["username"]}, {"$set": {"admin_service_theme": theme}})
    return jsonify({"ok": True, "theme": theme})


def module_payload(row, admin=False):
    if not row:
        return None
    item = {key: value for key, value in row.items() if key != "_id"}
    connectors = item.get("connectors") or "USB-C"
    if isinstance(connectors, str):
        connectors = [value.strip() for value in connectors.split(",") if value.strip()]
    item["connectors"] = connectors
    # Older EcoBlack modules predate the Bmax module type; treat them as standard.
    item["module_type"] = item.get("module_type") if item.get("module_type") in ("standard", "bmax") else "standard"
    item["city"] = str(item.get("city") or "").strip()
    item["state"] = str(item.get("state") or "").strip()
    item["remaining_seconds"] = max(0, int(item["occupied_until"] - time.time())) if item.get("occupied_until") else 0
    if not admin:
        item.pop("occupied_by", None)
    return item


def calculate_charging_plan(package_id, battery_start, capacity_mah, device_max_watts, module_power, connector, package_limit_minutes):
    """Estimate a session using the package, module, phone and connector caps.

    This is a software estimate, not control of physical charger hardware. The
    returned session_minutes is the exact server-side occupied time; the target
    estimate can exceed that package's maximum duration.
    """
    if package_id not in PACKAGES:
        raise ValueError('Selecciona un paquete válido.')
    package = PACKAGES[package_id]
    battery = max(1, min(99, int(battery_start)))
    capacity = max(1000, min(30000, int(capacity_mah)))
    phone_watts = max(3, min(300, int(device_max_watts)))
    module_watts = max(3.0, min(300.0, float(module_power)))
    package_watts = int(package['max_watts'])
    connector = connector if connector in CONNECTOR_WATT_LIMITS else 'USB-C'
    effective_watts = max(3.0, min(package_watts, phone_watts, module_watts, CONNECTOR_WATT_LIMITS[connector]))
    efficiency = CONNECTOR_EFFICIENCY[connector]
    # Average-rate/taper factor is lower at higher state of charge.
    taper = 0.78 if battery < 50 else (0.68 if battery < 75 else 0.56)
    capacity_wh = capacity * 3.85 / 1000.0
    target_battery = 100 if package_id == 'bmax_pro' else 90
    energy_needed_wh = capacity_wh * max(0, target_battery - battery) / 100.0
    if energy_needed_wh <= 0:
        target_minutes = 1
        session_minutes = 1
        session_limit = 1 if package_id == 'bmax_pro' else (max(10, min(120, int(package_limit_minutes))) if package_id == 'custom' else int(package['minutes']))
        end_battery = battery
    elif package_id == 'bmax_pro':
        # Conservative estimate in 1% increments to reflect charging taper near full.
        import math
        target_minutes_float = 0.0
        level = float(battery)
        while level < 100:
            next_level = min(100.0, level + 1.0)
            midpoint = (level + next_level) / 2.0
            taper_factor = 0.78 if midpoint < 50 else (0.68 if midpoint < 75 else (0.48 if midpoint < 90 else (0.30 if midpoint < 97 else 0.18)))
            step_wh = capacity_wh * (next_level - level) / 100.0
            target_minutes_float += (step_wh / max(0.1, effective_watts * efficiency * taper_factor)) * 60.0
            level = next_level
        target_minutes = max(1, int(math.ceil(target_minutes_float + 3.0)))
        session_limit = target_minutes  # No fixed package duration; this is a device-based estimate to 100%.
        session_minutes = target_minutes
        end_battery = 100
    else:
        battery_watts = max(0.1, effective_watts * efficiency * taper)
        target_minutes = max(1, int(__import__('math').ceil((energy_needed_wh / battery_watts) * 60.0 + 2.0)))
        try:
            session_limit = max(10, min(120, int(package_limit_minutes))) if package_id == 'custom' else int(package['minutes'])
        except (TypeError, ValueError):
            session_limit = int(package['minutes'])
        session_minutes = max(1, min(target_minutes, session_limit))
        charge_minutes = max(0, session_minutes - 2)
        delivered_wh = (charge_minutes / 60.0) * battery_watts
        end_battery = min(90, max(battery, int(battery + delivered_wh / capacity_wh * 100)))
    return {
        'package_max_watts': package_watts,
        'module_max_watts': round(module_watts, 1),
        'device_max_watts': phone_watts,
        'effective_watts': round(effective_watts, 1),
        'minutes_to_target': target_minutes,
        'session_minutes': session_minutes,
        'package_limit_minutes': session_limit if energy_needed_wh > 0 else (1 if package_id == 'bmax_pro' else (max(10, min(120, int(package_limit_minutes))) if package_id == 'custom' else int(package['minutes']))),
        'target_battery': target_battery,
        'battery_end_estimate': end_battery,
        'target_reached_in_session': target_minutes <= (session_limit if energy_needed_wh > 0 else 1),
    }


def calculate_price(package_id, minutes, discount_percent):
    if package_id not in PACKAGES:
        raise ValueError('Selecciona un paquete válido.')
    package = PACKAGES[package_id]
    if package_id == 'bmax_pro':
        return 0, 0.0, 0.0, 0.0
    if package_id == 'custom':
        try:
            duration = max(10, min(120, int(minutes)))
        except (TypeError, ValueError):
            duration = package['minutes']
        base = float(duration)  # $1 MXN per minute for the custom package
    else:
        duration, base = int(package['minutes']), float(package['price'])
    discount = min(90.0, max(0.0, float(discount_percent or 0)))
    final_price = round(base * (1 - discount / 100), 2)
    return duration, round(base, 2), discount, final_price


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    try:
        client.admin.command("ping")
        return jsonify({"ok": True, "database": "connected"})
    except PyMongoError:
        return jsonify({"ok": False, "database": "disconnected"}), 503


@app.get("/api/network/ping")
def network_ping():
    return jsonify({"ok": True, "server_time": time.time()})


@app.get("/api/modules")
def get_modules():
    release_expired_modules()
    user = current_user() or {}
    rows = list(modules.find().sort("name", ASCENDING))
    return jsonify([module_payload(row, user.get("role") == "admin") for row in rows])


@app.get("/api/packages")
@login_required
def get_packages():
    user = current_user()
    bmax = bmax_status_for_user(user)
    active_plan_id = user.get("active_plan") or ("bmax_pro" if bmax["has_purchased"] else None)
    items = []
    for package in PACKAGES.values():
        if package["id"] == "bmax_pro" and not bmax["has_purchased"]:
            continue
        minutes, base, discount, final = calculate_price(package["id"], package["minutes"], user["discount_percent"])
        items.append({**package, "minutes": minutes, "base_price": base, "discount_percent": discount, "final_price": final, "purchased": package["id"] == "bmax_pro"})
    active_package = PACKAGES.get(active_plan_id) if active_plan_id else None
    current_plan = None
    if active_package:
        current_plan = {
            "id": active_plan_id, "name": active_package["name"],
            "minutes": int(user.get("active_plan_minutes") or active_package["minutes"] or 0),
            "max_watts": active_package["max_watts"],
            "status": "active" if active_plan_id == "bmax_pro" else (user.get("active_plan_status") or "ready"),
            "permanent": active_plan_id == "bmax_pro",
            "price_mxn": round(float(user.get("plan_price_mxn") or (bmax.get("purchase") or {}).get("price_mxn") or 0), 2),
        }
    return jsonify({"packages": items, "balance": user["balance"], "discount_percent": user["discount_percent"], "bmax": bmax, "current_plan": current_plan})


@app.post("/api/plans/purchase")
@login_required
def purchase_plan():
    user = current_user()
    data = request.get_json(silent=True) or {}
    package_id = str(data.get("package_id", "")).strip()
    if package_id not in PACKAGES or package_id == "bmax_pro":
        return jsonify({"ok": False, "error": "Selecciona un plan estándar válido. Bmax Pro se compra desde su apartado limitado."}), 400
    try:
        requested_minutes = data.get("minutes", PACKAGES[package_id]["minutes"])
        discount = float(user.get("discount_percent") or 0)
        minutes, base, discount_pct, price = calculate_price(package_id, requested_minutes, discount)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "La duración del plan no es válida."}), 400
    if price <= 0:
        return jsonify({"ok": False, "error": "El precio del plan no es válido."}), 400
    now = time.time()
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                live = users.find_one({"username": user["username"]}, session=mongo_session)
                if not live or live.get("blocked"):
                    raise ApiError("Tu cuenta no puede comprar planes.", 403)
                if live.get("role") != "admin":
                    if live.get("active_plan"):
                        raise ApiError("Ya tienes un plan activo. Úsalo antes de comprar otro.", 409)
                    if bmax_purchases.find_one({"username": user["username"], "status": "active"}, session=mongo_session):
                        raise ApiError("Tu cuenta ya tiene Bmax Pro permanente; no necesitas otro plan.", 409)
                    debited = users.update_one(
                        {"username": user["username"], "balance": {"$gte": price}, "active_plan": {"$in": [None, ""]}, "blocked": {"$ne": True}},
                        {"$inc": {"balance": -price}, "$set": {"active_plan": package_id, "active_plan_status": "ready", "active_plan_minutes": minutes, "plan_price_mxn": price, "plan_purchased_at": now, "active_plan_discount_percent": discount_pct}},
                        session=mongo_session,
                    )
                    if debited.modified_count != 1:
                        live = users.find_one({"username": user["username"]}, session=mongo_session) or {}
                        if live.get("active_plan"):
                            raise ApiError("Ya tienes un plan activo. Úsalo antes de comprar otro.", 409)
                        raise ApiError(f"Saldo insuficiente. Tienes ${float(live.get('balance') or 0):.2f} y {PACKAGES[package_id]['name']} cuesta ${price:.2f}.", 402)
                    fresh = users.find_one({"username": user["username"]}, session=mongo_session)
                    balance_after = round(float(fresh.get("balance") or 0), 2)
                    tx_id = next_id("balance_transactions", mongo_session)
                    balance_transactions.insert_one({"_id": tx_id, "id": tx_id, "username": user["username"], "kind": "plan_purchase", "amount": -price, "balance_after": balance_after, "description": f"Compra del plan {PACKAGES[package_id]['name']} · {minutes} min", "created_at": now_iso()}, session=mongo_session)
                    revenue_id = next_id("admin_revenue_transactions", mongo_session)
                    admin_revenue_transactions.insert_one({"_id": revenue_id, "id": revenue_id, "session_id": None, "payer_username": user["username"], "module_id": f"PLAN-{package_id.upper()}", "module_name": f"Plan {PACKAGES[package_id]['name']}", "package_name": PACKAGES[package_id]["name"], "gross_amount": base, "discount_amount": max(0, round(base-price, 2)), "amount": price, "payment_method": "wallet", "payment_status": "paid_wallet", "collected": True, "created_at": now, "collected_at": now, "received_by": os.getenv("ADMIN_USERNAME", "tragic")}, session=mongo_session)
                    admin_account.update_one({"_id": 1}, {"$inc": {"balance": price}, "$set": {"updated_at": now}}, session=mongo_session)
                else:
                    users.update_one({"username": user["username"]}, {"$set": {"active_plan": package_id, "active_plan_status": "ready", "active_plan_minutes": minutes, "plan_price_mxn": 0.0, "plan_purchased_at": now, "active_plan_discount_percent": 0}}, session=mongo_session)
                    balance_after = round(float(live.get("balance") or 0), 2)
    except ApiError as error:
        return jsonify({"ok": False, "error": error.message}), error.status
    except PyMongoError:
        app.logger.exception("Error comprando plan EcoBlack")
        return jsonify({"ok": False, "error": "No se pudo completar la compra del plan."}), 503
    refreshed = current_user()
    return jsonify({"ok": True, "balance": balance_after, "user": refreshed, "plan": {"id": package_id, "name": PACKAGES[package_id]["name"], "minutes": minutes, "max_watts": PACKAGES[package_id]["max_watts"], "status": "ready", "permanent": False}, "message": f"Plan {PACKAGES[package_id]['name']} comprado. Ya puedes conectar un módulo normal."})


@app.get("/api/auth/me")
def auth_me():
    user = current_user()
    return jsonify({"authenticated": bool(user), "user": user})


@app.post("/api/auth/login")
def auth_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    user = users.find_one({"username": username})
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return jsonify({"ok": False, "error": "Usuario o contraseña incorrectos."}), 401
    if user.get("blocked") and user.get("role") != "admin":
        return jsonify({"ok": False, "error": "Tu cuenta está bloqueada. Contacta al administrador."}), 403
    session["username"] = username
    session.permanent = True
    return jsonify({"ok": True, "user": current_user()})


@app.post("/api/auth/register")
def auth_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    usage = str(data.get("usage", "")).strip()
    if len(username) < 3 or len(username) > 30:
        return jsonify({"ok": False, "error": "El usuario debe tener entre 3 y 30 caracteres."}), 400
    if len(password) < 4:
        return jsonify({"ok": False, "error": "La contraseña debe tener al menos 4 caracteres."}), 400
    if not usage:
        return jsonify({"ok": False, "error": "Selecciona dónde usarás EcoBlack."}), 400
    try:
        user_id = next_id("users")
        users.insert_one({
            "_id": username, "id": user_id, "username": username,
            "password_hash": generate_password_hash(password), "role": "user", "usage": usage,
            "balance": WELCOME_BALANCE, "discount_percent": 0.0, "blocked": False,
            "avatar_data": None, "display_name": username,
            "active_plan": None, "active_plan_status": "none", "active_plan_minutes": 0,
            "plan_price_mxn": 0.0, "plan_purchased_at": None, "active_plan_discount_percent": 0.0,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        return jsonify({"ok": False, "error": "Ese usuario ya existe."}), 409
    tx_id = next_id("balance_transactions")
    balance_transactions.insert_one({
        "_id": tx_id, "id": tx_id,
        "username": username, "kind": "welcome", "amount": WELCOME_BALANCE,
        "balance_after": WELCOME_BALANCE, "description": "Saldo de bienvenida de EcoBlack", "created_at": now_iso(),
    })
    session["username"] = username
    session.permanent = True
    return jsonify({"ok": True, "user": current_user()})


@app.post("/api/auth/logout")
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/profile")
@login_required
def profile_get():
    user = current_user()
    transactions = list(balance_transactions.find({"username": user["username"]}).sort("id", DESCENDING).limit(25))
    for item in transactions:
        item.pop("_id", None)
    user["transactions"] = transactions
    return jsonify({"profile": user})


@app.put("/api/profile")
@login_required
def profile_update():
    user = current_user(); data = request.get_json(silent=True) or {}
    display_name = str(data.get("display_name", user.get("display_name") or user["username"])).strip()[:40]
    avatar = data.get("avatar_data", user.get("avatar_data"))
    if avatar is not None:
        if not isinstance(avatar, str) or (avatar and not avatar.startswith("data:image/")):
            return jsonify({"ok": False, "error": "La foto debe ser una imagen."}), 400
        if len(avatar) > 2_500_000:
            return jsonify({"ok": False, "error": "La imagen pesa demasiado; elige una más pequeña."}), 413
    users.update_one({"username": user["username"]}, {"$set": {"display_name": display_name or user["username"], "avatar_data": avatar}})
    return jsonify({"ok": True, "profile": user_public(users.find_one({"username": user["username"]}))})


@app.get("/api/profile/transactions")
@login_required
def profile_transactions():
    user = current_user(); rows = list(balance_transactions.find({"username": user["username"]}).sort("id", DESCENDING).limit(50))
    for item in rows: item.pop("_id", None)
    return jsonify({"transactions": rows})


@app.post("/api/modules")
@admin_required
def create_module():
    data = request.get_json(silent=True) or {}; name = str(data.get("name", "")).strip()
    city = str(data.get("city", "")).strip()[:80]; state_name = str(data.get("state", "")).strip()[:80]
    lat_raw, lng_raw = data.get("lat"), data.get("lng")
    if lat_raw is None or lng_raw is None or str(lat_raw).strip() == "" or str(lng_raw).strip() == "":
        return jsonify({"ok": False, "error": "Selecciona la ubicación del módulo en el mapa o escribe latitud y longitud."}), 400
    try:
        lat, lng = float(lat_raw), float(lng_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Latitud y longitud deben ser números válidos."}), 400
    if not name or not city or not state_name or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({"ok": False, "error": "Completa nombre, ciudad, estado y coordenadas dentro de los rangos válidos."}), 400
    if abs(lat) < 1e-9 and abs(lng) < 1e-9:
        return jsonify({"ok": False, "error": "La ubicación 0, 0 no es válida para este módulo. Elige un punto real en el mapa."}), 400
    connectors = data.get("connectors", ["USB-C"])
    if isinstance(connectors, str): connectors = [connectors]
    connectors = [str(value).strip() for value in connectors if str(value).strip()] or ["USB-C"]
    module_id = f"EB-{int(time.time()*1000)%100000:05d}"
    while modules.find_one({"id": module_id}): module_id = f"EB-{secrets.randbelow(90000)+10000}"
    status = data.get("status", "available")
    if status not in ("available", "busy"): status = "available"
    module_type = str(data.get("module_type", "standard")).strip().lower()
    if module_type not in ("standard", "bmax"):
        return jsonify({"ok": False, "error": "Tipo de módulo inválido."}), 400
    doc = {"_id": module_id, "id": module_id, "name": name, "lat": lat, "lng": lng,
           "status": status, "module_type": module_type, "power": str(data.get("power", "30 W")), "connectors": connectors,
           "city": city, "state": state_name,
           "created_by": session.get("username", "admin"), "occupied_by": None, "occupied_until": None, "created_at": now_iso()}
    try:
        modules.insert_one(doc)
    except DuplicateKeyError:
        return jsonify({"ok": False, "error": "No se pudo asignar un identificador único. Intenta de nuevo."}), 409
    except PyMongoError:
        app.logger.exception("Error guardando el módulo en MongoDB")
        return jsonify({"ok": False, "error": "MongoDB no pudo guardar el módulo. Revisa la conexión con Atlas e inténtalo de nuevo."}), 503
    return jsonify({"ok": True, "module": module_payload(doc, True)}), 201


@app.put("/api/modules/<module_id>")
@admin_required
def update_module(module_id):
    data = request.get_json(silent=True) or {}; release_expired_modules()
    row = modules.find_one({"id": module_id})
    if not row: return jsonify({"ok": False, "error": "Módulo no encontrado."}), 404
    if row.get("occupied_until") and row["occupied_until"] > time.time():
        return jsonify({"ok": False, "error": "Ese módulo está ocupado por una sesión activa."}), 409
    connectors = data.get("connectors", row.get("connectors", ["USB-C"]))
    if isinstance(connectors, str): connectors = [v.strip() for v in connectors.split(",") if v.strip()]
    try: lat, lng = float(data.get("lat", row["lat"])), float(data.get("lng", row["lng"]))
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Coordenadas inválidas."}), 400
    status = str(data.get("status", row.get("status", "available")))
    if status not in ("available", "busy"): status = row.get("status", "available")
    module_type = str(data.get("module_type", row.get("module_type", "standard"))).strip().lower()
    if module_type not in ("standard", "bmax"):
        return jsonify({"ok": False, "error": "Tipo de módulo inválido."}), 400
    modules.update_one({"id": module_id}, {"$set": {
        "name": str(data.get("name", row["name"])).strip(), "lat": lat, "lng": lng,
        "status": status, "module_type": module_type, "power": str(data.get("power", row["power"])),
        "connectors": connectors, "city": str(data.get("city", row.get("city", ""))).strip()[:80],
        "state": str(data.get("state", row.get("state", ""))).strip()[:80], "updated_at": now_iso(),
    }})
    return jsonify({"ok": True, "module": module_payload(modules.find_one({"id": module_id}), True)})


@app.delete("/api/modules/<module_id>")
@admin_required
def delete_module(module_id):
    row = modules.find_one({"id": module_id})
    if not row: return jsonify({"ok": False, "error": "Módulo no encontrado."}), 404
    if row.get("occupied_until") and row["occupied_until"] > time.time():
        return jsonify({"ok": False, "error": "No puedes quitar un módulo ocupado."}), 409
    modules.delete_one({"id": module_id}); module_sessions.delete_many({"module_id": module_id})
    return jsonify({"ok": True})


@app.post("/api/modules/<module_id>/connect")
@login_required
def connect_module(module_id):
    user = current_user()
    data = request.get_json(silent=True) or {}
    if not user or user.get("blocked"):
        return jsonify({"ok": False, "error": "Tu cuenta no puede iniciar sesiones."}), 403

    requested_package_id = str(data.get("package_id", "")).strip()
    is_admin = user.get("role") == "admin"
    active_plan_id = user.get("active_plan")
    if not active_plan_id and bmax_purchases.find_one({"username": user["username"], "status": "active"}):
        active_plan_id = "bmax_pro"  # Compatibilidad con compras Bmax anteriores a los planes de cuenta.
    if is_admin:
        package_id = requested_package_id if requested_package_id in PACKAGES else "basic"
    else:
        if not active_plan_id:
            return jsonify({"ok": False, "error": "No tienes un plan activo. Compra uno en Servicios para poder utilizar EcoBlack."}), 403
        package_id = active_plan_id
        if requested_package_id and requested_package_id != active_plan_id:
            return jsonify({"ok": False, "error": "El paquete solicitado no coincide con el plan activo de tu cuenta."}), 403
    raw_discount = data.get("use_discount", False)
    use_discount = raw_discount is True or str(raw_discount).strip().lower() in ("true", "1", "yes", "si", "sí")
    payment_method = "bmax_pro" if package_id == "bmax_pro" else ("admin" if is_admin else "plan")

    try:
        live_plan_minutes = int(user.get("active_plan_minutes") or PACKAGES.get(package_id, {}).get("minutes", 15))
        if package_id == "custom" and not is_admin:
            live_plan_minutes = max(10, min(120, live_plan_minutes))
        discount_to_apply = float(user.get("active_plan_discount_percent", 0) or 0) if not is_admin else 0.0
        package_limit_minutes, base_cost, discount_pct, payable = calculate_price(
            package_id, live_plan_minutes if package_id == "custom" else data.get("minutes", live_plan_minutes), discount_to_apply
        )
        if not is_admin:
            payable = 0.0  # The price was settled when the user purchased the plan.
        battery_start = max(0, min(100, int(data.get("battery_start", 35))))
        capacity_mah = max(1000, min(30000, int(data.get("device_capacity_mah", 4200))))
        device_max_watts = max(3, min(300, int(data.get("device_max_watts", 25))))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Revisa la configuración del dispositivo o el paquete."}), 400

    release_expired_modules()
    now = time.time()
    timestamp = now_iso()
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                live_user = users.find_one({"username": user["username"]}, session=mongo_session)
                if not live_user or live_user.get("blocked"):
                    raise ApiError("Tu cuenta no puede iniciar sesiones.", 403)
                live_plan_id = live_user.get("active_plan")
                if not live_plan_id and bmax_purchases.find_one({"username": user["username"], "status": "active"}, session=mongo_session):
                    live_plan_id = "bmax_pro"
                if live_user.get("role") != "admin" and (not live_plan_id or live_plan_id != package_id):
                    raise ApiError("Tu plan ya no está activo. Actualiza y compra otro plan.", 403)
                module = modules.find_one({"id": module_id}, session=mongo_session)
                if not module:
                    raise ApiError("Módulo no encontrado.", 404)
                if module.get("status") != "available":
                    raise ApiError("Este módulo está ocupado. Elige otro.", 409)
                module_is_bmax = module.get("module_type") == "bmax"
                if live_user.get("role") != "admin":
                    if module_is_bmax and package_id != "bmax_pro":
                        raise ApiError("Este es un módulo Bmax. Necesitas el plan Bmax Pro para conectarlo.", 403)
                    if not module_is_bmax and package_id == "bmax_pro":
                        raise ApiError("Bmax Pro solo funciona en módulos Bmax negros y violetas.", 403)

                requested_connector = str(data.get("connector", "USB-C")).strip()
                supported_connectors = module.get("connectors") or ["USB-C"]
                if isinstance(supported_connectors, str):
                    supported_connectors = [x.strip() for x in supported_connectors.split(",") if x.strip()]
                if requested_connector not in supported_connectors:
                    raise ApiError(
                        f"Este módulo no ofrece {requested_connector}. Conectores disponibles: {', '.join(supported_connectors)}.", 409
                    )
                if module_sessions.find_one({"username": user["username"], "active": True}, session=mongo_session):
                    raise ApiError("Ya tienes una sesión activa. Desconéctala antes de conectar otro módulo.", 409)

                try:
                    module_watts = float(str(module.get("power", "30 W")).lower().replace("w", "").strip().replace(",", "."))
                    charging_plan = calculate_charging_plan(
                        package_id, battery_start, capacity_mah, device_max_watts, module_watts,
                        requested_connector, package_limit_minutes,
                    )
                except (ValueError, TypeError) as exc:
                    raise ApiError(f"No se pudo calcular la carga: {exc}", 400)

                amount_paid = 0.0
                amount_due = 0.0
                payment_status = "included_bmax_pro" if package_id == "bmax_pro" else ("included_admin" if live_user.get("role") == "admin" else "included_plan")

                session_minutes = int(charging_plan["session_minutes"])
                until = now + session_minutes * 60
                reserved = modules.update_one(
                    {"id": module_id, "status": "available"},
                    {"$set": {"status": "busy", "occupied_by": user["username"], "occupied_until": until}},
                    session=mongo_session,
                )
                if reserved.modified_count != 1:
                    raise ApiError("Otro usuario acaba de ocupar este módulo. Actualiza el mapa e inténtalo de nuevo.", 409)

                session_id = next_id("module_sessions", mongo_session)
                session_payload = {
                    "_id": session_id, "id": session_id, "module_id": module_id,
                    "module_type": module.get("module_type", "standard"),
                    "username": user["username"], "module_name": module["name"],
                    "device": str(data.get("device", "Dispositivo"))[:80],
                    "connector": requested_connector, "package_name": PACKAGES[package_id]["name"],
                    "minutes": session_minutes, "started_at": now, "ended_at": None, "active": True,
                    "amount_paid": amount_paid, "amount_due": amount_due,
                    "battery_start": battery_start, "device_capacity_mah": capacity_mah,
                    "device_max_watts": device_max_watts, "estimated_minutes": charging_plan["minutes_to_target"],
                    "minutes_to_target": charging_plan["minutes_to_target"],
                    "package_max_watts": charging_plan["package_max_watts"],
                    "effective_watts": charging_plan["effective_watts"],
                    "battery_end_estimate": charging_plan["battery_end_estimate"],
                    "package_limit_minutes": charging_plan["package_limit_minutes"],
                    "target_reached_in_session": charging_plan["target_reached_in_session"],
                    "target_battery": charging_plan.get("target_battery", 90),
                    "package_id": package_id,
                    "payment_method": payment_method, "payment_status": payment_status,
                    "discount_percent": discount_pct, "use_discount": use_discount,
                    "occupied_until": until, "until": until, "created_at": timestamp,
                }
                module_sessions.insert_one(session_payload, session=mongo_session)
                if live_user.get("role") != "admin" and package_id != "bmax_pro":
                    consumed = users.update_one(
                        {"username": user["username"], "active_plan": package_id, "active_plan_status": "ready"},
                        {"$set": {"active_plan": None, "active_plan_status": "none", "active_plan_minutes": 0, "active_plan_discount_percent": 0.0, "plan_consumed_at": now}},
                        session=mongo_session,
                    )
                    if consumed.modified_count != 1:
                        raise ApiError("El plan ya fue utilizado en otra sesión. Actualiza tu cuenta.", 409)
                balance_doc = users.find_one({"username": user["username"]}, session=mongo_session)
                balance_after = round(float(balance_doc.get("balance") or 0), 2)


                updated_module = modules.find_one({"id": module_id}, session=mongo_session)
    except ApiError as error:
        current = users.find_one({"username": user["username"]}) or user
        return jsonify({"ok": False, "error": error.message, "balance": round(float(current.get("balance") or 0), 2), "price": payable if "payable" in locals() else 0}), error.status
    except DuplicateKeyError:
        return jsonify({"ok": False, "error": "Ya tienes una sesión activa o el módulo acaba de ocuparse. Actualiza el mapa e inténtalo de nuevo."}), 409
    except PyMongoError:
        app.logger.exception("Error en transacción MongoDB al conectar módulo")
        return jsonify({"ok": False, "error": "No se pudo completar la conexión. No se confirmó el cobro; inténtalo de nuevo."}), 503

    payload = {key: value for key, value in session_payload.items() if key != "_id"}
    payload["remaining_seconds"] = max(0, int(until - time.time()))
    return jsonify({
        "ok": True, "balance": balance_after, "charged": amount_paid, "amount_due": amount_due,
        "base_price": base_cost, "payable": payable, "payment_method": payment_method,
        "payment_status": payment_status, "discount_percent": discount_pct,
        "session": payload, "module": module_payload(updated_module, user.get("role") == "admin"),
    })

@app.post("/api/modules/<module_id>/disconnect")
@login_required
def disconnect_module(module_id):
    user = current_user(); row = modules.find_one({"id": module_id})
    if not row: return jsonify({"ok": False, "error": "Módulo no encontrado."}), 404
    if row.get("occupied_by") != user["username"] and user.get("role") != "admin":
        return jsonify({"ok": False, "error": "Solo la sesión que conectó el módulo puede desconectarlo."}), 403
    now = time.time()
    modules.update_one({"id": module_id}, {"$set": {"status": "available", "occupied_by": None, "occupied_until": None}})
    module_sessions.update_many({"module_id": module_id, "active": True}, {"$set": {"active": False, "ended_at": now}})
    updated = modules.find_one({"id": module_id})
    return jsonify({"ok": True, "module": module_payload(updated, user.get("role") == "admin")})


@app.get("/api/sessions/active")
@login_required
def active_session():
    user = current_user(); release_expired_modules()
    row = module_sessions.find_one({"username": user["username"], "active": True}, sort=[("id", DESCENDING)])
    if not row: return jsonify({"active": False})
    mod = modules.find_one({"id": row["module_id"]}) or {}
    payload = {key: value for key, value in row.items() if key != "_id"}
    payload["module_name"] = mod.get("name", row["module_id"]); payload["status"] = mod.get("status")
    payload["occupied_until"] = mod.get("occupied_until") or row.get("occupied_until")
    payload["remaining_seconds"] = max(0, int((payload.get("occupied_until") or time.time()) - time.time()))
    return jsonify({"active": True, "session": payload})



@app.get("/api/bmax/status")
@login_required
def bmax_status():
    return jsonify({"ok": True, "bmax": bmax_status_for_user(current_user())})


@app.post("/api/bmax/reserve")
@login_required
def bmax_reserve():
    user = current_user()
    if bmax_purchases.find_one({"username": user["username"], "status": "active"}):
        return jsonify({"ok": False, "error": "Tu cuenta ya tiene Bmax Pro activo."}), 409
    if user.get("active_plan"):
        return jsonify({"ok": False, "error": "Primero utiliza tu plan actual; después podrás apartar Bmax Pro."}), 409
    release_expired_bmax_reservations()
    now = time.time()
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                existing = bmax_reservations.find_one({"username": user["username"], "status": "active", "expires_at": {"$gt": now}}, session=mongo_session)
                if existing:
                    reservation = existing
                else:
                    config = bmax_config.find_one_and_update(
                        {"_id": "bmax_pro", "$expr": {"$gt": ["$total_slots", {"$add": ["$sold_count", "$reserved_count"]}]}},
                        {"$inc": {"reserved_count": 1}, "$set": {"updated_at": now}},
                        return_document=ReturnDocument.AFTER, session=mongo_session,
                    )
                    if not config:
                        raise ApiError("Bmax Pro está agotado por ahora. El administrador puede habilitar más cupos.", 409)
                    reservation = {"_id": uuid.uuid4().hex, "username": user["username"], "status": "active", "created_at": now, "expires_at": now + BMAX_RESERVATION_SECONDS}
                    bmax_reservations.insert_one(reservation, session=mongo_session)
    except ApiError as error:
        return jsonify({"ok": False, "error": error.message}), error.status
    except DuplicateKeyError:
        return jsonify({"ok": False, "error": "Ya tienes un apartado activo. Actualiza la página."}), 409
    except PyMongoError:
        app.logger.exception("Error apartando cupo Bmax Pro")
        return jsonify({"ok": False, "error": "No se pudo apartar el cupo. Inténtalo de nuevo."}), 503
    return jsonify({"ok": True, "bmax": bmax_status_for_user(user)})


@app.post("/api/bmax/cancel")
@login_required
def bmax_cancel():
    user = current_user(); now = time.time()
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                reservation = bmax_reservations.find_one({"username": user["username"], "status": "active"}, session=mongo_session)
                if reservation:
                    changed = bmax_reservations.update_one({"_id": reservation["_id"], "status": "active"}, {"$set": {"status": "cancelled", "updated_at": now}}, session=mongo_session)
                    if changed.modified_count:
                        bmax_config.update_one({"_id": "bmax_pro", "reserved_count": {"$gte": 1}}, {"$inc": {"reserved_count": -1}, "$set": {"updated_at": now}}, session=mongo_session)
    except PyMongoError:
        app.logger.exception("Error cancelando apartado Bmax Pro")
        return jsonify({"ok": False, "error": "No se pudo cancelar el apartado."}), 503
    return jsonify({"ok": True, "bmax": bmax_status_for_user(user)})


@app.post("/api/bmax/purchase")
@login_required
def bmax_purchase():
    user = current_user(); now = time.time()
    release_expired_bmax_reservations()
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                if bmax_purchases.find_one({"username": user["username"], "status": "active"}, session=mongo_session):
                    raise ApiError("Ya tienes Bmax Pro activo.", 409)
                live_user = users.find_one({"username": user["username"]}, session=mongo_session) or {}
                if live_user.get("active_plan"):
                    raise ApiError("Primero utiliza tu plan actual antes de comprar Bmax Pro.", 409)
                reservation = bmax_reservations.find_one({"username": user["username"], "status": "active", "expires_at": {"$gt": now}}, session=mongo_session)
                if not reservation:
                    raise ApiError("Tu apartado venció. Aparta un cupo nuevamente.", 409)
                config = bmax_config.find_one({"_id": "bmax_pro"}, session=mongo_session) or {}
                price = round(float(config.get("price_mxn", BMAX_DEFAULT_PRICE)), 2)
                if price <= 0:
                    raise ApiError("El precio de Bmax Pro todavía no está configurado.", 400)
                debited = users.update_one({"username": user["username"], "balance": {"$gte": price}, "blocked": {"$ne": True}}, {"$inc": {"balance": -price}}, session=mongo_session)
                if debited.modified_count != 1:
                    live = users.find_one({"username": user["username"]}, session=mongo_session) or {}
                    balance = round(float(live.get("balance") or 0), 2)
                    raise ApiError(f"Saldo insuficiente. Tienes ${balance:.2f} y Bmax Pro cuesta ${price:.2f}.", 402)
                changed = bmax_reservations.update_one({"_id": reservation["_id"], "status": "active", "expires_at": {"$gt": now}}, {"$set": {"status": "purchased", "purchased_at": now, "updated_at": now}}, session=mongo_session)
                if changed.modified_count != 1:
                    raise ApiError("El apartado ya no está disponible. Vuelve a intentarlo.", 409)
                config_update = bmax_config.update_one({"_id": "bmax_pro", "reserved_count": {"$gte": 1}}, {"$inc": {"reserved_count": -1, "sold_count": 1}, "$set": {"updated_at": now}}, session=mongo_session)
                if config_update.modified_count != 1:
                    raise ApiError("No se pudo confirmar el cupo. Inténtalo de nuevo.", 409)
                purchase_id = next_id("bmax_purchases", mongo_session)
                bmax_purchases.insert_one({"_id": purchase_id, "id": purchase_id, "username": user["username"], "status": "active", "price_mxn": price, "purchased_at": now, "reservation_id": reservation["_id"]}, session=mongo_session)
                users.update_one({"username": user["username"]}, {"$set": {"active_plan": "bmax_pro", "active_plan_status": "active", "active_plan_minutes": 0, "plan_price_mxn": price, "plan_purchased_at": now, "active_plan_discount_percent": 0.0}}, session=mongo_session)
                balance_doc = users.find_one({"username": user["username"]}, session=mongo_session)
                balance_after = round(float(balance_doc.get("balance") or 0), 2)
                tx_id = next_id("balance_transactions", mongo_session)
                balance_transactions.insert_one({"_id": tx_id, "id": tx_id, "username": user["username"], "kind": "bmax_purchase", "amount": -price, "balance_after": balance_after, "description": "Compra de acceso Bmax Pro", "created_at": now_iso()}, session=mongo_session)
                if user.get("role") != "admin":
                    revenue_id = next_id("admin_revenue_transactions", mongo_session)
                    admin_revenue_transactions.insert_one({"_id": revenue_id, "id": revenue_id, "session_id": None, "payer_username": user["username"], "module_id": "BMAX-PRO", "module_name": "Acceso Bmax Pro", "package_name": "Bmax Pro", "gross_amount": price, "discount_amount": 0.0, "amount": price, "payment_method": "wallet", "payment_status": "paid_wallet", "collected": True, "created_at": now, "collected_at": now, "received_by": os.getenv("ADMIN_USERNAME", "tragic")}, session=mongo_session)
                    admin_account.update_one({"_id": 1}, {"$inc": {"balance": price}, "$set": {"updated_at": now}}, session=mongo_session)
    except ApiError as error:
        return jsonify({"ok": False, "error": error.message}), error.status
    except PyMongoError:
        app.logger.exception("Error confirmando compra Bmax Pro")
        return jsonify({"ok": False, "error": "No se confirmó la compra. Revisa tu saldo e inténtalo de nuevo."}), 503
    return jsonify({"ok": True, "balance": balance_after, "bmax": bmax_status_for_user(user), "message": "Bmax Pro quedó activo en tu cuenta."})


@app.get("/api/admin/bmax")
@admin_required
def admin_bmax():
    release_expired_bmax_reservations()
    config = bmax_config.find_one({"_id": "bmax_pro"}) or {}
    purchases = list(bmax_purchases.find().sort("purchased_at", DESCENDING).limit(100))
    rows = []
    for row in purchases:
        buyer = users.find_one({"username": row.get("username")}, {"display_name": 1}) or {}
        rows.append({"username": row.get("username"), "display_name": buyer.get("display_name") or row.get("username"), "price_mxn": row.get("price_mxn"), "purchased_at": row.get("purchased_at"), "status": row.get("status", "active")})
    total = int(config.get("total_slots", BMAX_DEFAULT_SLOTS)); sold = int(config.get("sold_count", 0)); reserved = int(config.get("reserved_count", 0))
    return jsonify({"ok": True, "config": {"total_slots": total, "sold_count": sold, "reserved_count": reserved, "available_slots": max(0, total-sold-reserved), "price_mxn": round(float(config.get("price_mxn", BMAX_DEFAULT_PRICE)), 2)}, "purchases": rows})


@app.put("/api/admin/bmax/config")
@admin_required
def admin_bmax_config():
    release_expired_bmax_reservations()
    data = request.get_json(silent=True) or {}
    try:
        total_slots = int(data.get("total_slots"))
        price = round(float(data.get("price_mxn")), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Escribe un número entero de cupos y un precio válido."}), 400
    if not 0 <= total_slots <= 100000:
        return jsonify({"ok": False, "error": "Los cupos deben estar entre 0 y 100,000."}), 400
    if not 1 <= price <= 100000:
        return jsonify({"ok": False, "error": "El precio debe estar entre $1 y $100,000 MXN."}), 400
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                config = bmax_config.find_one({"_id": "bmax_pro"}, session=mongo_session) or {}
                minimum = int(config.get("sold_count", 0)) + int(config.get("reserved_count", 0))
                if total_slots < minimum:
                    raise ApiError(f"No puedes dejar menos de {minimum} cupos: ya están vendidos o apartados.", 409)
                bmax_config.update_one({"_id": "bmax_pro"}, {"$set": {"total_slots": total_slots, "price_mxn": price, "updated_at": time.time()}}, session=mongo_session)
    except ApiError as error:
        return jsonify({"ok": False, "error": error.message}), error.status
    except PyMongoError:
        app.logger.exception("Error actualizando configuración de Bmax Pro")
        return jsonify({"ok": False, "error": "No se pudo guardar la configuración."}), 503
    return jsonify({"ok": True, "bmax": bmax_status_for_user(current_user())})

@app.get("/api/admin/revenue")
@admin_required
def admin_revenue():
    account = admin_account.find_one({"_id": 1}) or {"owner_username": "tragic", "balance": 0}
    today = datetime.now(timezone.utc).date()
    start_of_day = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp()
    start_of_next_day = start_of_day + 86400
    pipeline = [{"$group": {"_id": None,
        "collected_total": {"$sum": {"$cond": ["$collected", {"$ifNull": ["$amount", 0]}, 0]}},
        "payments_total": {"$sum": {"$cond": ["$collected", 1, 0]}},
        "collected_today": {"$sum": {"$cond": [{"$and": ["$collected", {"$gte": ["$collected_at", start_of_day]}, {"$lt": ["$collected_at", start_of_next_day]}]}, {"$ifNull": ["$amount", 0]}, 0]}},
        "payments_today": {"$sum": {"$cond": [{"$and": ["$collected", {"$gte": ["$collected_at", start_of_day]}, {"$lt": ["$collected_at", start_of_next_day]}]}, 1, 0]}},
        "cash_pending": {"$sum": {"$cond": [{"$and": [{"$eq": ["$collected", False]}, {"$eq": ["$payment_status", "pending_cash"]}]}, {"$ifNull": ["$amount", 0]}, 0]}},
        "card_unprocessed": {"$sum": {"$cond": [{"$and": [{"$eq": ["$collected", False]}, {"$eq": ["$payment_status", "demo_not_processed"]}]}, {"$ifNull": ["$amount", 0]}, 0]}},
    }}]
    summary = next(db["admin_revenue_transactions"].aggregate(pipeline), {})
    keys = ("collected_today", "collected_total", "cash_pending", "card_unprocessed", "payments_today", "payments_total")
    normalized = {key: (round(float(summary.get(key) or 0), 2) if key in ("collected_today", "collected_total", "cash_pending", "card_unprocessed") else int(summary.get(key) or 0)) for key in keys}
    fields = ("id", "session_id", "payer_username", "module_id", "module_name", "package_name", "gross_amount", "discount_amount", "amount", "payment_method", "payment_status", "collected", "created_at", "collected_at", "received_by")
    rows = [{key: row.get(key) for key in fields} for row in admin_revenue_transactions.find().sort("id", DESCENDING).limit(50)]
    return jsonify({"ok": True, "account": {"owner_username": account.get("owner_username", "tragic"), "balance": round(float(account.get("balance") or 0), 2)}, "summary": normalized, "transactions": rows})


@app.post("/api/admin/revenue/<int:revenue_id>/collect")
@admin_required
def admin_collect_cash(revenue_id):
    row = admin_revenue_transactions.find_one({"id": revenue_id})
    if not row: return jsonify({"ok": False, "error": "Movimiento de cobro no encontrado."}), 404
    if row.get("collected"): return jsonify({"ok": False, "error": "Este pago ya fue confirmado."}), 409
    if row.get("payment_method") != "cash" or row.get("payment_status") != "pending_cash":
        return jsonify({"ok": False, "error": "Solo se pueden confirmar pagos en efectivo pendientes. La tarjeta necesita una pasarela real."}), 400
    now = time.time(); amount = round(float(row.get("amount") or 0), 2)
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                result = admin_revenue_transactions.update_one({"id": revenue_id, "collected": False, "payment_status": "pending_cash"}, {"$set": {"collected": True, "payment_status": "paid_cash", "collected_at": now, "received_by": session["username"]}}, session=mongo_session)
                if result.modified_count != 1: raise ApiError("Este pago ya fue confirmado por otra operación.", 409)
                admin_account.update_one({"_id": 1}, {"$inc": {"balance": amount}, "$set": {"updated_at": now}}, session=mongo_session)
                if row.get("session_id") is not None:
                    module_sessions.update_one({"id": row["session_id"]}, {"$set": {"payment_status": "paid_cash", "amount_paid": amount, "amount_due": 0}}, session=mongo_session)
    except ApiError as error:
        return jsonify({"ok": False, "error": error.message}), error.status
    except PyMongoError:
        app.logger.exception("Error confirmando pago en efectivo")
        return jsonify({"ok": False, "error": "No se pudo confirmar el pago. Inténtalo de nuevo."}), 503
    acc = admin_account.find_one({"_id": 1}) or {"balance": 0}
    return jsonify({"ok": True, "collected": amount, "admin_balance": round(float(acc.get("balance") or 0), 2)})


@app.get("/api/admin/users")
@admin_required
def admin_users():
    rows = list(users.find().sort("created_at", DESCENDING))
    return jsonify({"users": [user_public(row) for row in rows]})


@app.post("/api/admin/users/<username>/balance")
@admin_required
def admin_add_balance(username):
    data = request.get_json(silent=True) or {}
    try: amount = round(float(data.get("amount", 0)), 2)
    except (TypeError, ValueError): amount = 0
    if not 0 < amount <= 100000: return jsonify({"ok": False, "error": "El saldo debe ser mayor que $0 y no superar $100,000."}), 400
    try:
        with client.start_session() as mongo_session:
            with mongo_session.start_transaction():
                row = users.find_one({"username": username}, session=mongo_session)
                if not row: raise ApiError("Usuario no encontrado.", 404)
                users.update_one({"username": username}, {"$inc": {"balance": amount}}, session=mongo_session)
                fresh = users.find_one({"username": username}, session=mongo_session)
                tx_id = next_id("balance_transactions", mongo_session)
                balance_transactions.insert_one({"_id": tx_id, "id": tx_id, "username": username, "kind": "admin_topup", "amount": amount, "balance_after": round(float(fresh.get("balance") or 0), 2), "description": f"Saldo agregado por {session['username']}", "created_at": now_iso()}, session=mongo_session)
    except ApiError as error: return jsonify({"ok": False, "error": error.message}), error.status
    except PyMongoError:
        app.logger.exception("Error al agregar saldo")
        return jsonify({"ok": False, "error": "No se pudo agregar saldo."}), 503
    return jsonify({"ok": True, "username": username, "balance": round(float(fresh.get("balance") or 0), 2)})


@app.put("/api/admin/users/<username>/discount")
@admin_required
def admin_set_discount(username):
    data = request.get_json(silent=True) or {}
    try: percent = float(data.get("discount_percent", 0))
    except (TypeError, ValueError): percent = -1
    if not 0 <= percent <= 90: return jsonify({"ok": False, "error": "El descuento debe estar entre 0% y 90%."}), 400
    result = users.update_one({"username": username}, {"$set": {"discount_percent": round(percent, 2)}})
    if not result.matched_count: return jsonify({"ok": False, "error": "Usuario no encontrado."}), 404
    return jsonify({"ok": True, "username": username, "discount_percent": round(percent, 2)})


@app.put("/api/admin/users/<username>/blocked")
@admin_required
def admin_set_blocked(username):
    data = request.get_json(silent=True) or {}; blocked = bool(data.get("blocked", False))
    if username == os.getenv("ADMIN_USERNAME", "tragic").strip().lower(): return jsonify({"ok": False, "error": "La cuenta admin principal no se puede bloquear."}), 400
    row = users.find_one({"username": username})
    if not row: return jsonify({"ok": False, "error": "Usuario no encontrado."}), 404
    if row.get("role") == "admin": return jsonify({"ok": False, "error": "No se puede bloquear otra cuenta administradora."}), 400
    users.update_one({"username": username}, {"$set": {"blocked": blocked}})
    if blocked:
        now = time.time()
        active = list(module_sessions.find({"username": username, "active": True}, {"module_id": 1}))
        ids = [item["module_id"] for item in active]
        if ids: modules.update_many({"id": {"$in": ids}}, {"$set": {"status": "available", "occupied_by": None, "occupied_until": None}})
        module_sessions.update_many({"username": username, "active": True}, {"$set": {"active": False, "ended_at": now}})
    return jsonify({"ok": True, "username": username, "blocked": blocked})


@app.get("/manifest.webmanifest")
def manifest(): return app.send_static_file("manifest.webmanifest")

@app.get("/sw.js")
def service_worker(): return app.send_static_file("sw.js")

@app.errorhandler(413)
def file_too_large(_): return jsonify({"ok": False, "error": "El archivo es demasiado grande."}), 413


try:
    init_db()
except Exception:
    app.logger.exception("No se pudo conectar con MongoDB Atlas. Revisa MONGODB_URI y la lista de acceso IP.")
    raise

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
