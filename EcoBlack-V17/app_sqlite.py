from flask import Flask, render_template, jsonify, request, session
from pathlib import Path
import sqlite3
import time
import uuid
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'ecoblack.db'
app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-ecoblack-secret-before-deployment'
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024
WELCOME_BALANCE = 100.0
BMAX_DEFAULT_SLOTS = 10
BMAX_DEFAULT_PRICE = 99.0
BMAX_RESERVATION_SECONDS = 300

DEFAULT_MODULES = [
    ('EB-001', 'EcoBlack Centro', 21.1619, -86.8515, 'available', '30 W', 'USB-C,Lightning,Inalámbrico'),
    ('EB-002', 'EcoBlack Plaza', 21.1638, -86.8460, 'available', '45 W', 'USB-C,Lightning'),
    ('EB-003', 'EcoBlack Zona Hotelera', 21.1357, -86.7416, 'available', '65 W', 'USB-C,Inalámbrico'),
    ('EB-004', 'EcoBlack Puerto', 21.1743, -86.8234, 'available', '25 W', 'USB-C,Micro-USB'),
]
PACKAGES = {
    # minutes is the maximum reservation window; max_watts caps estimated power.
    'basic': {'id': 'basic', 'name': 'Básico', 'minutes': 15, 'price': 15.0, 'max_watts': 10, 'description': 'Hasta 15 min de sesión y potencia limitada a 10 W.'},
    'fast': {'id': 'fast', 'name': 'Rápido', 'minutes': 30, 'price': 25.0, 'max_watts': 20, 'description': 'Hasta 30 min de sesión y potencia limitada a 20 W.'},
    'power': {'id': 'power', 'name': 'Power', 'minutes': 45, 'price': 40.0, 'max_watts': 45, 'description': 'Hasta 45 min de sesión y potencia limitada a 45 W.'},
    'custom': {'id': 'custom', 'name': 'Personalizado', 'minutes': 25, 'price': 25.0, 'max_watts': 30, 'description': 'Elige una duración de 10 a 120 minutos, con un límite de 30 W.'},
    'bmax_pro': {'id': 'bmax_pro', 'name': 'Bmax Pro', 'minutes': 0, 'price': 0.0, 'max_watts': 100, 'description': 'Acceso ilimitado: sesiones estimadas hasta el 100% de batería, con potencia máxima de 100 W y limitada por tu celular, módulo y conector.'},
}
CONNECTOR_WATT_LIMITS = {'USB-C': 240, 'Lightning': 20, 'Micro-USB': 12, 'Inalámbrico': 15}
CONNECTOR_EFFICIENCY = {'USB-C': 0.78, 'Lightning': 0.75, 'Micro-USB': 0.70, 'Inalámbrico': 0.68}


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def table_columns(conn, table):
    return {row['name'] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}


def add_column(conn, table, name, declaration):
    if name not in table_columns(conn, table):
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        return True
    return False



def release_expired_bmax_reservations(conn=None):
    own = conn is None
    conn = conn or db()
    was_in_transaction = conn.in_transaction
    now = time.time()
    expired = conn.execute("SELECT id FROM bmax_reservations WHERE status='active' AND expires_at<=?", (now,)).fetchall()
    for item in expired:
        cur = conn.execute("UPDATE bmax_reservations SET status='expired' WHERE id=? AND status='active' AND expires_at<=?", (item['id'], now))
        if cur.rowcount:
            conn.execute("UPDATE bmax_config SET reserved_count=MAX(0,reserved_count-1),updated_at=? WHERE id=1", (now,))
    if own:
        conn.commit(); conn.close()
    elif not was_in_transaction:
        conn.commit()


def bmax_status_for_user(user, conn=None):
    own = conn is None
    conn = conn or db()
    release_expired_bmax_reservations(conn)
    config = conn.execute("SELECT * FROM bmax_config WHERE id=1").fetchone()
    purchase = conn.execute("SELECT * FROM bmax_purchases WHERE username=? AND status='active'", (user['username'],)).fetchone() if user else None
    reservation = conn.execute("SELECT * FROM bmax_reservations WHERE username=? AND status='active' AND expires_at>?", (user['username'], time.time())).fetchone() if user else None
    total = int(config['total_slots']) if config else BMAX_DEFAULT_SLOTS
    sold = int(config['sold_count']) if config else 0
    reserved = int(config['reserved_count']) if config else 0
    result = {
        'package_id': 'bmax_pro', 'name': 'Bmax Pro', 'total_slots': total, 'sold_count': sold,
        'reserved_count': reserved, 'available_slots': max(0, total-sold-reserved),
        'price_mxn': round(float(config['price_mxn']), 2) if config else BMAX_DEFAULT_PRICE,
        'has_purchased': bool(purchase),
        'purchase': {'purchased_at': purchase['purchased_at'], 'price_mxn': purchase['price_mxn']} if purchase else None,
        'reservation': {'id': reservation['id'], 'expires_at': reservation['expires_at'], 'created_at': reservation['created_at']} if reservation else None,
        'reservation_seconds': max(0, int(reservation['expires_at'] - time.time())) if reservation else 0,
        'sold_out': (total-sold-reserved) <= 0 and not purchase,
    }
    if own:
        conn.close()
    return result


def init_db():
    conn = db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', usage TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS modules (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'available', power TEXT NOT NULL DEFAULT '30 W',
            connectors TEXT NOT NULL DEFAULT 'USB-C', created_by TEXT, occupied_by TEXT,
            occupied_until REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS module_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, module_id TEXT NOT NULL, username TEXT NOT NULL,
            device TEXT, connector TEXT, package_name TEXT, minutes INTEGER NOT NULL,
            started_at REAL NOT NULL, ended_at REAL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS balance_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, kind TEXT NOT NULL,
            amount REAL NOT NULL, balance_after REAL NOT NULL, description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admin_account (
            id INTEGER PRIMARY KEY CHECK(id=1), owner_username TEXT NOT NULL DEFAULT 'tragic',
            balance REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admin_revenue_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER UNIQUE,
            payer_username TEXT NOT NULL,
            module_id TEXT NOT NULL,
            module_name TEXT NOT NULL,
            package_name TEXT NOT NULL,
            gross_amount REAL NOT NULL DEFAULT 0,
            discount_amount REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            payment_method TEXT NOT NULL,
            payment_status TEXT NOT NULL,
            collected INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            collected_at REAL,
            received_by TEXT
        );
        CREATE TABLE IF NOT EXISTS bmax_config (
            id INTEGER PRIMARY KEY CHECK(id=1), total_slots INTEGER NOT NULL DEFAULT 10,
            reserved_count INTEGER NOT NULL DEFAULT 0, sold_count INTEGER NOT NULL DEFAULT 0,
            price_mxn REAL NOT NULL DEFAULT 99, updated_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bmax_reservations (
            id TEXT PRIMARY KEY, username TEXT NOT NULL, status TEXT NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL, purchased_at REAL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_bmax_reservation_per_user
            ON bmax_reservations(username) WHERE status='active';
        CREATE INDEX IF NOT EXISTS bmax_reservation_expiry ON bmax_reservations(status, expires_at);
        CREATE TABLE IF NOT EXISTS bmax_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', price_mxn REAL NOT NULL, purchased_at REAL NOT NULL,
            reservation_id TEXT
        );
    ''')
    conn.execute("INSERT OR IGNORE INTO admin_account(id,owner_username,balance,updated_at) VALUES(1,'tragic',0,?)", (time.time(),))
    conn.execute("INSERT OR IGNORE INTO bmax_config(id,total_slots,reserved_count,sold_count,price_mxn,updated_at) VALUES(1,?,?,?, ?,?)", (BMAX_DEFAULT_SLOTS, 0, 0, BMAX_DEFAULT_PRICE, time.time()))

    add_column(conn, 'modules', 'occupied_by', 'TEXT')
    add_column(conn, 'modules', 'occupied_until', 'REAL')
    add_column(conn, 'modules', 'module_type', "TEXT NOT NULL DEFAULT 'standard'")
    add_column(conn, 'module_sessions', 'module_type', "TEXT NOT NULL DEFAULT 'standard'")
    add_column(conn, 'module_sessions', 'amount_paid', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'module_sessions', 'battery_start', 'INTEGER NOT NULL DEFAULT 35')
    add_column(conn, 'module_sessions', 'device_capacity_mah', 'INTEGER NOT NULL DEFAULT 4200')
    add_column(conn, 'module_sessions', 'device_max_watts', 'INTEGER NOT NULL DEFAULT 25')
    add_column(conn, 'module_sessions', 'estimated_minutes', 'INTEGER NOT NULL DEFAULT 20')
    add_column(conn, 'module_sessions', 'package_max_watts', 'INTEGER NOT NULL DEFAULT 10')
    add_column(conn, 'module_sessions', 'effective_watts', 'REAL NOT NULL DEFAULT 10')
    add_column(conn, 'module_sessions', 'battery_end_estimate', 'INTEGER NOT NULL DEFAULT 90')
    add_column(conn, 'module_sessions', 'package_limit_minutes', 'INTEGER NOT NULL DEFAULT 15')
    add_column(conn, 'module_sessions', 'payment_method', "TEXT NOT NULL DEFAULT 'wallet'")
    add_column(conn, 'module_sessions', 'payment_status', "TEXT NOT NULL DEFAULT 'paid_wallet'")
    add_column(conn, 'module_sessions', 'amount_due', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'module_sessions', 'discount_percent', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'module_sessions', 'use_discount', 'INTEGER NOT NULL DEFAULT 1')

    balance_added = add_column(conn, 'users', 'balance', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'users', 'discount_percent', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'users', 'blocked', 'INTEGER NOT NULL DEFAULT 0')
    add_column(conn, 'users', 'avatar_data', 'TEXT')
    add_column(conn, 'users', 'display_name', 'TEXT')
    add_column(conn, 'users', 'active_plan', 'TEXT')
    add_column(conn, 'users', 'active_plan_status', "TEXT NOT NULL DEFAULT 'none'")
    add_column(conn, 'users', 'active_plan_minutes', 'INTEGER NOT NULL DEFAULT 0')
    add_column(conn, 'users', 'plan_price_mxn', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'users', 'plan_purchased_at', 'REAL')
    add_column(conn, 'users', 'active_plan_discount_percent', 'REAL NOT NULL DEFAULT 0')
    add_column(conn, 'users', 'plan_consumed_at', 'REAL')
    add_column(conn, 'users', 'admin_service_theme', "TEXT NOT NULL DEFAULT 'wine'")

    add_column(conn, 'modules', 'city', 'TEXT')
    add_column(conn, 'modules', 'state', 'TEXT')
    # Backfill city/state for the four built-in locations from older local databases.
    for module in DEFAULT_MODULES:
        conn.execute(
            "UPDATE modules SET city=COALESCE(NULLIF(city,''),'Cancún'), state=COALESCE(NULLIF(state,''),'Quintana Roo') WHERE id=?",
            (module[0],),
        )

    if not conn.execute('SELECT id FROM users WHERE username=?', ('tragic',)).fetchone():
        conn.execute('''INSERT INTO users(username,password_hash,role,usage,balance,display_name)
                        VALUES(?,?,?,?,?,?)''',
                     ('tragic', generate_password_hash('69'), 'admin', 'Administración de módulos', 0, 'tragic'))

    # One-time welcome credit for accounts already in older database versions.
    if balance_added:
        existing_users = conn.execute("SELECT username FROM users WHERE role!='admin'").fetchall()
        conn.execute("UPDATE users SET balance=? WHERE role!='admin'", (WELCOME_BALANCE,))
        for row in existing_users:
            conn.execute('''INSERT INTO balance_transactions(username,kind,amount,balance_after,description)
                            VALUES(?,?,?,?,?)''',
                         (row['username'], 'welcome', WELCOME_BALANCE, WELCOME_BALANCE, 'Saldo inicial de bienvenida'))
    conn.execute("UPDATE users SET display_name=username WHERE display_name IS NULL OR display_name=''")

    # Backfill prior genuinely-paid sessions once; session_id is unique so they can never duplicate.
    historical = conn.execute(
        "SELECT s.id,s.username,s.module_id,m.name AS module_name,s.package_name,s.amount_paid,"
        "s.discount_percent,s.payment_method,s.payment_status,s.started_at,s.ended_at "
        "FROM module_sessions s LEFT JOIN modules m ON m.id=s.module_id LEFT JOIN users u ON u.username=s.username "
        "WHERE COALESCE(s.amount_paid,0)>0 AND s.payment_status IN ('paid_wallet','paid_cash') AND COALESCE(u.role,'user')!='admin'"
    ).fetchall()
    for item in historical:
        exists = conn.execute('SELECT id FROM admin_revenue_transactions WHERE session_id=?', (item['id'],)).fetchone()
        if exists:
            continue
        amount = round(float(item['amount_paid'] or 0), 2)
        discount_factor = max(0.01, 1 - float(item['discount_percent'] or 0) / 100)
        gross = round(amount / discount_factor, 2)
        collected_at = float(item['ended_at'] or item['started_at'] or time.time())
        conn.execute('''INSERT INTO admin_revenue_transactions(
                            session_id,payer_username,module_id,module_name,package_name,gross_amount,
                            discount_amount,amount,payment_method,payment_status,collected,created_at,collected_at,received_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                     (item['id'], item['username'], item['module_id'], item['module_name'] or item['module_id'],
                      item['package_name'] or 'Sesión', gross, max(0, round(gross-amount,2)), amount,
                      item['payment_method'] or 'wallet', item['payment_status'] or 'paid_wallet',
                      float(item['started_at'] or collected_at), collected_at, 'migration'))
        conn.execute('UPDATE admin_account SET balance=ROUND(balance+?,2),updated_at=? WHERE id=1', (amount, time.time()))

    if conn.execute('SELECT COUNT(*) FROM modules').fetchone()[0] == 0:
        conn.executemany('''INSERT INTO modules(id,name,lat,lng,status,power,connectors,created_by,city,state)
                            VALUES(?,?,?,?,?,?,?,?,?,?)''', [(*module, 'system', 'Cancún', 'Quintana Roo') for module in DEFAULT_MODULES])
    conn.commit()
    conn.close()


def release_expired_modules(conn=None):
    owns_connection = conn is None
    conn = conn or db()
    now = time.time()
    conn.execute("UPDATE modules SET status='available',occupied_by=NULL,occupied_until=NULL WHERE status='busy' AND occupied_until IS NOT NULL AND occupied_until<=?", (now,))
    conn.execute('UPDATE module_sessions SET active=0,ended_at=? WHERE active=1 AND started_at+(minutes*60)<=?', (now, now))
    if owns_connection:
        conn.commit()
        conn.close()


def user_public(row):
    if not row:
        return None
    result = dict(row)
    result['balance'] = round(float(result.get('balance') or 0), 2)
    result['discount_percent'] = round(float(result.get('discount_percent') or 0), 2)
    result['blocked'] = bool(result.get('blocked', 0))
    return result


def current_user():
    username = session.get('username')
    if not username:
        return None
    conn = db()
    row = conn.execute('''SELECT id,username,role,usage,balance,discount_percent,blocked,avatar_data,
                          display_name,created_at,active_plan,active_plan_status,active_plan_minutes,
                          plan_price_mxn,plan_purchased_at,active_plan_discount_percent,admin_service_theme FROM users WHERE username=?''', (username,)).fetchone()
    conn.close()
    if not row or (row['blocked'] and row['role'] != 'admin'):
        session.clear()
        return None
    return user_public(row)


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            return jsonify({'ok': False, 'error': 'Necesitas iniciar sesión.'}), 401
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({'ok': False, 'error': 'Necesitas iniciar sesión.'}), 401
        if user['role'] != 'admin':
            return jsonify({'ok': False, 'error': 'Solo el administrador puede hacer esto.'}), 403
        return fn(*args, **kwargs)
    return wrapped


def module_payload(row, admin=False):
    item = dict(row)
    item['connectors'] = [value for value in (item.get('connectors') or '').split(',') if value]
    item['module_type'] = item.get('module_type') if item.get('module_type') in ('standard', 'bmax') else 'standard'
    item['city'] = str(item.get('city') or '').strip()
    item['state'] = str(item.get('state') or '').strip()
    item['remaining_seconds'] = max(0, int(item['occupied_until'] - time.time())) if item.get('occupied_until') else 0
    if not admin:
        item.pop('occupied_by', None)
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
    battery = max(0, min(100, int(battery_start)))
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
        session_limit = target_minutes
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
        'package_limit_minutes': session_limit if energy_needed_wh > 0 else (max(10, min(120, int(package_limit_minutes))) if package_id == 'custom' else int(package['minutes'])),
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


@app.get('/')
def home():
    return render_template('index.html')


@app.get('/api/modules')
def get_modules():
    conn = db(); release_expired_modules(conn); conn.commit()
    rows = conn.execute('SELECT * FROM modules ORDER BY name').fetchall()
    user = current_user() or {}
    conn.close()
    return jsonify([module_payload(row, user.get('role') == 'admin') for row in rows])


@app.get('/api/packages')
@login_required
def get_packages():
    user = current_user()
    conn = db()
    bmax = bmax_status_for_user(user, conn)
    active_plan_id = user.get('active_plan') or ('bmax_pro' if bmax['has_purchased'] else None)
    packages = []
    for package in PACKAGES.values():
        if package['id'] == 'bmax_pro' and not bmax['has_purchased']:
            continue
        minutes, base, discount, final = calculate_price(package['id'], package['minutes'], user['discount_percent'])
        packages.append({**package, 'minutes': minutes, 'base_price': base, 'discount_percent': discount, 'final_price': final, 'purchased': package['id'] == 'bmax_pro'})
    active_package = PACKAGES.get(active_plan_id) if active_plan_id else None
    current_plan = None
    if active_package:
        current_plan = {'id': active_plan_id, 'name': active_package['name'],
                        'minutes': int(user.get('active_plan_minutes') or active_package['minutes'] or 0),
                        'max_watts': active_package['max_watts'],
                        'status': 'active' if active_plan_id == 'bmax_pro' else (user.get('active_plan_status') or 'ready'),
                        'permanent': active_plan_id == 'bmax_pro',
                        'price_mxn': round(float(user.get('plan_price_mxn') or (bmax.get('purchase') or {}).get('price_mxn') or 0), 2)}
    conn.close()
    return jsonify({'packages': packages, 'balance': user['balance'], 'discount_percent': user['discount_percent'], 'bmax': bmax, 'current_plan': current_plan})


@app.post('/api/plans/purchase')
@login_required
def purchase_plan():
    user = current_user(); data = request.get_json(silent=True) or {}
    package_id = str(data.get('package_id', '')).strip()
    if package_id not in PACKAGES or package_id == 'bmax_pro':
        return jsonify({'ok': False, 'error': 'Selecciona un plan estándar válido. Bmax Pro se compra desde su apartado limitado.'}), 400
    try:
        requested_minutes = data.get('minutes', PACKAGES[package_id]['minutes'])
        minutes, base, discount_pct, price = calculate_price(package_id, requested_minutes, float(user.get('discount_percent') or 0))
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'La duración del plan no es válida.'}), 400
    if price <= 0: return jsonify({'ok': False, 'error': 'El precio del plan no es válido.'}), 400
    conn = db(); conn.execute('BEGIN IMMEDIATE'); now = time.time()
    live = conn.execute('SELECT * FROM users WHERE username=?', (user['username'],)).fetchone()
    if not live or live['blocked']:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Tu cuenta no puede comprar planes.'}), 403
    if live['role'] != 'admin':
        if live['active_plan']:
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Ya tienes un plan activo. Úsalo antes de comprar otro.'}), 409
        if conn.execute("SELECT id FROM bmax_purchases WHERE username=? AND status='active'", (user['username'],)).fetchone():
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Tu cuenta ya tiene Bmax Pro permanente; no necesitas otro plan.'}), 409
        if float(live['balance'] or 0) < price:
            balance = float(live['balance'] or 0); conn.rollback(); conn.close()
            return jsonify({'ok': False, 'error': f'Saldo insuficiente. Tienes ${balance:.2f} y {PACKAGES[package_id]["name"]} cuesta ${price:.2f}.'}), 402
        conn.execute('''UPDATE users SET balance=ROUND(balance-?,2),active_plan=?,active_plan_status='ready',
                        active_plan_minutes=?,plan_price_mxn=?,plan_purchased_at=?,active_plan_discount_percent=?
                        WHERE username=? AND balance>=? AND active_plan IS NULL''',
                     (price, package_id, minutes, price, now, discount_pct, user['username'], price))
        if conn.execute('SELECT changes()').fetchone()[0] != 1:
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'No se pudo reservar este plan; actualiza tu cuenta.'}), 409
        balance_after = float(conn.execute('SELECT balance FROM users WHERE username=?', (user['username'],)).fetchone()['balance'] or 0)
        conn.execute('''INSERT INTO balance_transactions(username,kind,amount,balance_after,description)
                        VALUES(?,?,?,?,?)''', (user['username'], 'plan_purchase', -price, balance_after, f'Compra del plan {PACKAGES[package_id]["name"]} · {minutes} min'))
        conn.execute('''INSERT INTO admin_revenue_transactions(session_id,payer_username,module_id,module_name,package_name,
                        gross_amount,discount_amount,amount,payment_method,payment_status,collected,created_at,collected_at,received_by)
                        VALUES(NULL,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                     (user['username'], f'PLAN-{package_id.upper()}', f'Plan {PACKAGES[package_id]["name"]}', PACKAGES[package_id]['name'],
                      base, max(0, round(base-price,2)), price, 'wallet', 'paid_wallet', now, now, 'tragic'))
        conn.execute('UPDATE admin_account SET balance=ROUND(balance+?,2),updated_at=? WHERE id=1', (price, now))
    else:
        conn.execute("UPDATE users SET active_plan=?,active_plan_status='ready',active_plan_minutes=?,plan_price_mxn=0,plan_purchased_at=?,active_plan_discount_percent=0 WHERE username=?",
                     (package_id, minutes, now, user['username']))
        balance_after = float(live['balance'] or 0)
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'balance': round(balance_after,2), 'user': current_user(),
                    'plan': {'id': package_id, 'name': PACKAGES[package_id]['name'], 'minutes': minutes,
                             'max_watts': PACKAGES[package_id]['max_watts'], 'status': 'ready', 'permanent': False},
                    'message': f'Plan {PACKAGES[package_id]["name"]} comprado. Ya puedes conectar un módulo normal.'})


@app.get('/api/auth/me')
def auth_me():
    user = current_user()
    return jsonify({'authenticated': bool(user), 'user': user})


@app.post('/api/auth/login')
def auth_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip().lower()
    password = str(data.get('password', ''))
    conn = db(); user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone(); conn.close()
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'ok': False, 'error': 'Usuario o contraseña incorrectos.'}), 401
    if user['blocked'] and user['role'] != 'admin':
        return jsonify({'ok': False, 'error': 'Tu cuenta está bloqueada. Contacta al administrador.'}), 403
    session['username'] = username
    return jsonify({'ok': True, 'user': current_user()})


@app.post('/api/auth/register')
def auth_register():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username', '')).strip().lower()
    password = str(data.get('password', ''))
    usage = str(data.get('usage', '')).strip()
    if len(username) < 3 or len(username) > 30:
        return jsonify({'ok': False, 'error': 'El usuario debe tener entre 3 y 30 caracteres.'}), 400
    if len(password) < 4:
        return jsonify({'ok': False, 'error': 'La contraseña debe tener al menos 4 caracteres.'}), 400
    if not usage:
        return jsonify({'ok': False, 'error': 'Selecciona dónde usarás EcoBlack.'}), 400
    conn = db()
    try:
        conn.execute('''INSERT INTO users(username,password_hash,role,usage,balance,discount_percent,blocked,display_name)
                        VALUES(?,?,?,?,?,?,?,?)''',
                     (username, generate_password_hash(password), 'user', usage, WELCOME_BALANCE, 0, 0, username))
        conn.execute('''INSERT INTO balance_transactions(username,kind,amount,balance_after,description)
                        VALUES(?,?,?,?,?)''',
                     (username, 'welcome', WELCOME_BALANCE, WELCOME_BALANCE, 'Saldo de bienvenida de EcoBlack'))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close(); return jsonify({'ok': False, 'error': 'Ese usuario ya existe.'}), 409
    conn.close(); session['username'] = username
    return jsonify({'ok': True, 'user': current_user()})


@app.post('/api/auth/logout')
def auth_logout():
    session.clear()
    return jsonify({'ok': True})


@app.get('/api/profile')
@login_required
def profile_get():
    user = current_user(); conn = db()
    transactions = conn.execute('''SELECT id,kind,amount,balance_after,description,created_at
                                   FROM balance_transactions WHERE username=? ORDER BY id DESC LIMIT 25''',
                                (user['username'],)).fetchall()
    conn.close()
    user['transactions'] = [dict(row) for row in transactions]
    return jsonify({'profile': user})


@app.put('/api/profile')
@login_required
def profile_update():
    user = current_user(); data = request.get_json(silent=True) or {}
    display_name = str(data.get('display_name', user.get('display_name') or user['username'])).strip()[:40]
    avatar = data.get('avatar_data', user.get('avatar_data'))
    if avatar is not None:
        if not isinstance(avatar, str) or (avatar and not avatar.startswith('data:image/')):
            return jsonify({'ok': False, 'error': 'La foto debe ser una imagen.'}), 400
        if len(avatar) > 2_500_000:
            return jsonify({'ok': False, 'error': 'La imagen pesa demasiado; elige una más pequeña.'}), 413
    conn = db()
    conn.execute('UPDATE users SET display_name=?,avatar_data=? WHERE username=?',
                 (display_name or user['username'], avatar, user['username']))
    conn.commit()
    row = conn.execute('''SELECT id,username,role,usage,balance,discount_percent,blocked,avatar_data,display_name,created_at,
                          active_plan,active_plan_status,active_plan_minutes,plan_price_mxn,plan_purchased_at,active_plan_discount_percent,admin_service_theme
                          FROM users WHERE username=?''', (user['username'],)).fetchone()
    conn.close()
    return jsonify({'ok': True, 'profile': user_public(row)})


@app.put('/api/admin/service-theme')
@admin_required
def update_admin_service_theme():
    data = request.get_json(silent=True) or {}
    theme = str(data.get('theme', 'wine')).strip().lower()
    if theme not in {'wine', 'basic', 'fast', 'power', 'custom', 'bmax_pro'}:
        return jsonify({'ok': False, 'error': 'Tema de servicio no válido.'}), 400
    user = current_user()
    conn = db()
    conn.execute('UPDATE users SET admin_service_theme=? WHERE username=?', (theme, user['username']))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'theme': theme})


@app.get('/api/profile/transactions')
@login_required
def profile_transactions():
    user = current_user(); conn = db()
    rows = conn.execute('''SELECT id,kind,amount,balance_after,description,created_at
                           FROM balance_transactions WHERE username=? ORDER BY id DESC LIMIT 50''', (user['username'],)).fetchall()
    conn.close()
    return jsonify({'transactions': [dict(row) for row in rows]})


@app.post('/api/modules')
@admin_required
def create_module():
    data = request.get_json(silent=True) or {}; name = str(data.get('name', '')).strip()
    city = str(data.get('city', '')).strip()[:80]; state_name = str(data.get('state', '')).strip()[:80]
    lat_raw, lng_raw = data.get('lat'), data.get('lng')
    if lat_raw is None or lng_raw is None or str(lat_raw).strip() == '' or str(lng_raw).strip() == '':
        return jsonify({'ok': False, 'error': 'Selecciona la ubicación del módulo en el mapa o escribe latitud y longitud.'}), 400
    try:
        lat, lng = float(lat_raw), float(lng_raw)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'Latitud y longitud deben ser números válidos.'}), 400
    if not name or not city or not state_name or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'ok': False, 'error': 'Completa nombre, ciudad, estado y coordenadas dentro de los rangos válidos.'}), 400
    if abs(lat) < 1e-9 and abs(lng) < 1e-9:
        return jsonify({'ok': False, 'error': 'La ubicación 0, 0 no es válida para este módulo. Elige un punto real en el mapa.'}), 400
    connectors = data.get('connectors', ['USB-C'])
    if isinstance(connectors, str): connectors = [connectors]
    connector_text = ','.join(str(value).strip() for value in connectors if str(value).strip()) or 'USB-C'
    conn = db(); module_id = f"EB-{int(time.time()*1000)%100000:05d}"
    while conn.execute('SELECT 1 FROM modules WHERE id=?', (module_id,)).fetchone():
        module_id = f"EB-{(int(time.time()*1000)+1)%100000:05d}"
    status = data.get('status', 'available')
    if status not in ('available', 'busy'): status = 'available'
    module_type = str(data.get('module_type', 'standard')).strip().lower()
    if module_type not in ('standard', 'bmax'):
        conn.close(); return jsonify({'ok': False, 'error': 'Tipo de módulo inválido.'}), 400
    conn.execute('''INSERT INTO modules(id,name,lat,lng,status,power,connectors,created_by,module_type,city,state)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                 (module_id, name, lat, lng, status, data.get('power', '30 W'), connector_text, session.get('username', 'admin'), module_type,
                  city, state_name))
    conn.commit(); row = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone(); conn.close()
    return jsonify({'ok': True, 'module': module_payload(row, True)}), 201


@app.put('/api/modules/<module_id>')
@admin_required
def update_module(module_id):
    data = request.get_json(silent=True) or {}; conn = db(); release_expired_modules(conn)
    row = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone()
    if not row: conn.close(); return jsonify({'ok': False, 'error': 'Módulo no encontrado.'}), 404
    if row['occupied_until'] and row['occupied_until'] > time.time():
        conn.close(); return jsonify({'ok': False, 'error': 'Ese módulo está ocupado por una sesión activa.'}), 409
    connectors = data.get('connectors', row['connectors'])
    if isinstance(connectors, list): connectors = ','.join(str(value) for value in connectors)
    try: lat, lng = float(data.get('lat', row['lat'])), float(data.get('lng', row['lng']))
    except (TypeError, ValueError): conn.close(); return jsonify({'ok': False, 'error': 'Coordenadas inválidas.'}), 400
    status = str(data.get('status', row['status']))
    if status not in ('available', 'busy'): status = row['status']
    module_type = str(data.get('module_type', row['module_type'] or 'standard')).strip().lower()
    if module_type not in ('standard', 'bmax'):
        conn.close(); return jsonify({'ok': False, 'error': 'Tipo de módulo inválido.'}), 400
    conn.execute('''UPDATE modules SET name=?,lat=?,lng=?,status=?,power=?,connectors=?,module_type=?,city=?,state=? WHERE id=?''',
                 (str(data.get('name', row['name'])).strip(), lat, lng, status, str(data.get('power', row['power'])), connectors, module_type,
                  str(data.get('city', row['city'] or '')).strip()[:80], str(data.get('state', row['state'] or '')).strip()[:80], module_id))
    conn.commit(); updated = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone(); conn.close()
    return jsonify({'ok': True, 'module': module_payload(updated, True)})


@app.delete('/api/modules/<module_id>')
@admin_required
def delete_module(module_id):
    conn = db(); row = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone()
    if row and row['occupied_until'] and row['occupied_until'] > time.time():
        conn.close(); return jsonify({'ok': False, 'error': 'No puedes quitar un módulo ocupado.'}), 409
    cur = conn.execute('DELETE FROM modules WHERE id=?', (module_id,))
    conn.execute('DELETE FROM module_sessions WHERE module_id=?', (module_id,)); conn.commit(); conn.close()
    if not cur.rowcount: return jsonify({'ok': False, 'error': 'Módulo no encontrado.'}), 404
    return jsonify({'ok': True})


@app.post('/api/modules/<module_id>/connect')
@login_required
def connect_module(module_id):
    user = current_user()
    data = request.get_json(silent=True) or {}
    if not user or user['blocked']:
        return jsonify({'ok': False, 'error': 'Tu cuenta no puede iniciar sesiones.'}), 403

    requested_package_id = str(data.get('package_id', '')).strip()
    is_admin = user.get('role') == 'admin'
    active_plan_id = user.get('active_plan')
    if not active_plan_id:
        check_conn = db()
        has_bmax = bool(check_conn.execute("SELECT id FROM bmax_purchases WHERE username=? AND status='active'", (user['username'],)).fetchone())
        check_conn.close()
        if has_bmax: active_plan_id = 'bmax_pro'
    if is_admin:
        package_id = requested_package_id if requested_package_id in PACKAGES else 'basic'
    else:
        if not active_plan_id:
            return jsonify({'ok': False, 'error': 'No tienes un plan activo. Compra uno en Servicios para poder utilizar EcoBlack.'}), 403
        package_id = active_plan_id
        if requested_package_id and requested_package_id != active_plan_id:
            return jsonify({'ok': False, 'error': 'El paquete solicitado no coincide con el plan activo de tu cuenta.'}), 403
    raw_discount = data.get('use_discount', False)
    use_discount = raw_discount is True or str(raw_discount).strip().lower() in ('true', '1', 'yes', 'si', 'sí')
    payment_method = 'bmax_pro' if package_id == 'bmax_pro' else ('admin' if is_admin else 'plan')

    try:
        live_plan_minutes = int(user.get('active_plan_minutes') or PACKAGES.get(package_id, {}).get('minutes', 15))
        if package_id == 'custom' and not is_admin: live_plan_minutes = max(10, min(120, live_plan_minutes))
        discount_to_apply = float(user.get('active_plan_discount_percent') or 0) if not is_admin else 0.0
        package_limit_minutes, base_cost, discount_pct, payable = calculate_price(
            package_id, live_plan_minutes if package_id == 'custom' else data.get('minutes', live_plan_minutes), discount_to_apply)
        if not is_admin: payable = 0.0  # El plan ya se pagó al comprarlo.
        battery_start = max(0, min(100, int(data.get('battery_start', 35))))
        capacity_mah = max(1000, min(30000, int(data.get('device_capacity_mah', 4200))))
        max_watts = max(3, min(300, int(data.get('device_max_watts', 25))))
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Revisa la configuración del dispositivo o el paquete.'}), 400
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400

    now = time.time()
    conn = db()
    conn.execute('BEGIN IMMEDIATE')
    release_expired_modules(conn)
    row = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone()
    if not row:
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': 'Módulo no encontrado.'}), 404
    if live_account := conn.execute('SELECT active_plan_minutes,role FROM users WHERE username=?', (user['username'],)).fetchone():
        if package_id == 'custom' and live_account['role'] != 'admin': package_limit_minutes = max(10, min(120, int(live_account['active_plan_minutes'] or package_limit_minutes)))
    if row['status'] == 'busy':
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': 'Este módulo está ocupado. Elige otro.'}), 409
    live_account = conn.execute('SELECT active_plan,active_plan_status,active_plan_minutes,active_plan_discount_percent,role FROM users WHERE username=?', (user['username'],)).fetchone()
    if not live_account:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Cuenta no disponible.'}), 403
    if live_account['role'] != 'admin' and (live_account['active_plan'] != package_id or (package_id != 'bmax_pro' and live_account['active_plan_status'] != 'ready')):
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Tu plan ya no está activo. Actualiza y compra otro plan.'}), 403
    module_is_bmax = (row['module_type'] or 'standard') == 'bmax'
    if live_account['role'] != 'admin':
        if module_is_bmax and package_id != 'bmax_pro':
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Este es un módulo Bmax. Necesitas el plan Bmax Pro para conectarlo.'}), 403
        if not module_is_bmax and package_id == 'bmax_pro':
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Bmax Pro solo funciona en módulos Bmax negros y violetas.'}), 403
    requested_connector = str(data.get('connector', 'USB-C')).strip()
    supported_connectors = [value.strip() for value in (row['connectors'] or 'USB-C').split(',') if value.strip()]
    if requested_connector not in supported_connectors:
        conn.rollback(); conn.close()
        supported_text = ', '.join(supported_connectors) or 'USB-C'
        return jsonify({'ok': False, 'error': f'Este módulo no ofrece {requested_connector}. Conectores disponibles: {supported_text}.'}), 409
    try:
        module_power = float(str(row['power'] or '30').lower().replace('w', '').strip().replace(',', '.'))
        charging_plan = calculate_charging_plan(
            package_id, battery_start, capacity_mah, max_watts, module_power,
            requested_connector, package_limit_minutes,
        )
    except (ValueError, TypeError) as exc:
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': f'No se pudo calcular la carga: {exc}'}), 400
    session_minutes = charging_plan['session_minutes']
    estimated_minutes = charging_plan['minutes_to_target']
    battery_end_estimate = charging_plan['battery_end_estimate']
    package_max_watts = charging_plan['package_max_watts']
    effective_watts = charging_plan['effective_watts']
    package_limit_minutes = charging_plan['package_limit_minutes']
    if conn.execute('SELECT id FROM module_sessions WHERE username=? AND active=1', (user['username'],)).fetchone():
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': 'Ya tienes una sesión activa. Desconéctala antes de conectar otro módulo.'}), 409

    current_balance_row = conn.execute('SELECT balance FROM users WHERE username=?', (user['username'],)).fetchone()
    balance = float(current_balance_row['balance'] or 0) if current_balance_row else 0.0
    amount_paid = 0.0
    amount_due = 0.0
    payment_status = 'included_bmax_pro' if package_id == 'bmax_pro' else ('included_admin' if is_admin else 'included_plan')

    until = now + session_minutes * 60
    conn.execute("UPDATE modules SET status='busy',occupied_by=?,occupied_until=? WHERE id=?", (user['username'], until, module_id))
    conn.execute('''INSERT INTO module_sessions(
                    module_id,username,device,connector,package_name,minutes,started_at,active,module_type,
                    amount_paid,battery_start,device_capacity_mah,device_max_watts,estimated_minutes,
                    payment_method,payment_status,amount_due,discount_percent,use_discount,
                    package_max_watts,effective_watts,battery_end_estimate,package_limit_minutes)
                    VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (module_id, user['username'], str(data.get('device', 'Dispositivo'))[:80],
                  requested_connector[:40], PACKAGES[package_id]['name'], session_minutes, now, row['module_type'] or 'standard',
                  amount_paid, battery_start, capacity_mah, max_watts, estimated_minutes,
                  payment_method, payment_status, amount_due, discount_pct, 1 if use_discount else 0,
                  package_max_watts, effective_watts, battery_end_estimate, package_limit_minutes))
    session_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    if not is_admin and package_id != 'bmax_pro':
        cur = conn.execute("UPDATE users SET active_plan=NULL,active_plan_status='none',active_plan_minutes=0,active_plan_discount_percent=0,plan_consumed_at=? WHERE username=? AND active_plan=? AND active_plan_status='ready'",
                           (now, user['username'], package_id))
        if cur.rowcount != 1:
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'El plan ya fue utilizado en otra sesión. Actualiza tu cuenta.'}), 409
    balance = float(conn.execute('SELECT balance FROM users WHERE username=?', (user['username'],)).fetchone()['balance'] or 0)
    conn.commit()
    updated = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone()
    conn.close()

    session_payload = {
        'id': session_id, 'module_id': module_id, 'module_name': row['name'], 'module_type': row['module_type'] or 'standard', 'minutes': session_minutes,
        'until': until, 'started_at': now, 'device': data.get('device'), 'connector': requested_connector,
        'package_name': PACKAGES[package_id]['name'], 'battery_start': battery_start,
        'estimated_minutes': estimated_minutes, 'battery_end_estimate': battery_end_estimate,
        'package_limit_minutes': package_limit_minutes, 'package_max_watts': package_max_watts,
        'effective_watts': effective_watts, 'target_battery': charging_plan.get('target_battery', 90),
        'package_id': package_id,
        'amount_paid': amount_paid, 'amount_due': amount_due,
        'device_capacity_mah': capacity_mah, 'device_max_watts': max_watts,
        'payment_method': payment_method, 'payment_status': payment_status,
        'discount_percent': discount_pct, 'use_discount': use_discount,
    }
    return jsonify({'ok': True, 'balance': round(balance, 2), 'charged': amount_paid, 'amount_due': amount_due,
                    'base_price': base_cost, 'payable': payable, 'payment_method': payment_method,
                    'payment_status': payment_status, 'discount_percent': discount_pct,
                    'session': session_payload, 'module': module_payload(updated, user['role'] == 'admin')})


@app.post('/api/modules/<module_id>/disconnect')
@login_required
def disconnect_module(module_id):
    user = current_user(); conn = db(); conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone()
    if not row: conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Módulo no encontrado.'}), 404
    if row['occupied_by'] != user['username'] and user['role'] != 'admin':
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Solo la sesión que conectó el módulo puede desconectarlo.'}), 403
    now = time.time()
    conn.execute("UPDATE modules SET status='available',occupied_by=NULL,occupied_until=NULL WHERE id=?", (module_id,))
    conn.execute('UPDATE module_sessions SET active=0,ended_at=? WHERE module_id=? AND active=1', (now, module_id))
    conn.commit(); updated = conn.execute('SELECT * FROM modules WHERE id=?', (module_id,)).fetchone(); conn.close()
    return jsonify({'ok': True, 'module': module_payload(updated, user['role'] == 'admin')})


@app.get('/api/sessions/active')
@login_required
def active_session():
    user = current_user(); conn = db(); release_expired_modules(conn); conn.commit()
    row = conn.execute('''SELECT s.*,m.name AS module_name,m.status,m.occupied_until FROM module_sessions s
                          JOIN modules m ON m.id=s.module_id WHERE s.username=? AND s.active=1 ORDER BY s.id DESC LIMIT 1''',
                       (user['username'],)).fetchone()
    conn.close()
    if not row: return jsonify({'active': False})
    payload = dict(row); payload['remaining_seconds'] = max(0, int((payload.get('occupied_until') or time.time()) - time.time()))
    return jsonify({'active': True, 'session': payload})



@app.get('/api/bmax/status')
@login_required
def bmax_status():
    user = current_user(); conn = db(); result = bmax_status_for_user(user, conn); conn.close()
    return jsonify({'ok': True, 'bmax': result})


@app.post('/api/bmax/reserve')
@login_required
def bmax_reserve():
    user = current_user(); conn = db(); conn.execute('BEGIN IMMEDIATE')
    release_expired_bmax_reservations(conn); now = time.time()
    if conn.execute("SELECT id FROM bmax_purchases WHERE username=? AND status='active'", (user['username'],)).fetchone():
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Tu cuenta ya tiene Bmax Pro activo.'}), 409
    live_plan = conn.execute('SELECT active_plan FROM users WHERE username=?', (user['username'],)).fetchone()
    if live_plan and live_plan['active_plan']:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Primero utiliza tu plan actual; después podrás apartar Bmax Pro.'}), 409
    existing = conn.execute("SELECT * FROM bmax_reservations WHERE username=? AND status='active' AND expires_at>?", (user['username'], now)).fetchone()
    if not existing:
        config = conn.execute('SELECT * FROM bmax_config WHERE id=1').fetchone()
        if not config or int(config['total_slots']) <= int(config['sold_count']) + int(config['reserved_count']):
            conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Bmax Pro está agotado por ahora. El administrador puede habilitar más cupos.'}), 409
        reservation_id = uuid.uuid4().hex
        conn.execute('UPDATE bmax_config SET reserved_count=reserved_count+1,updated_at=? WHERE id=1', (now,))
        conn.execute("INSERT INTO bmax_reservations(id,username,status,created_at,expires_at) VALUES(?,?,'active',?,?)", (reservation_id, user['username'], now, now+BMAX_RESERVATION_SECONDS))
    conn.commit(); result = bmax_status_for_user(user, conn); conn.close()
    return jsonify({'ok': True, 'bmax': result})


@app.post('/api/bmax/cancel')
@login_required
def bmax_cancel():
    user = current_user(); conn = db(); conn.execute('BEGIN IMMEDIATE'); now = time.time()
    row = conn.execute("SELECT id FROM bmax_reservations WHERE username=? AND status='active'", (user['username'],)).fetchone()
    if row:
        changed = conn.execute("UPDATE bmax_reservations SET status='cancelled' WHERE id=? AND status='active'", (row['id'],))
        if changed.rowcount:
            conn.execute('UPDATE bmax_config SET reserved_count=MAX(0,reserved_count-1),updated_at=? WHERE id=1', (now,))
    conn.commit(); result = bmax_status_for_user(user, conn); conn.close()
    return jsonify({'ok': True, 'bmax': result})


@app.post('/api/bmax/purchase')
@login_required
def bmax_purchase():
    user = current_user(); conn = db(); conn.execute('BEGIN IMMEDIATE')
    release_expired_bmax_reservations(conn); now = time.time()
    if conn.execute("SELECT id FROM bmax_purchases WHERE username=? AND status='active'", (user['username'],)).fetchone():
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Ya tienes Bmax Pro activo.'}), 409
    live_plan = conn.execute('SELECT active_plan FROM users WHERE username=?', (user['username'],)).fetchone()
    if live_plan and live_plan['active_plan']:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Primero utiliza tu plan actual antes de comprar Bmax Pro.'}), 409
    reservation = conn.execute("SELECT * FROM bmax_reservations WHERE username=? AND status='active' AND expires_at>?", (user['username'], now)).fetchone()
    if not reservation:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Tu apartado venció. Aparta un cupo nuevamente.'}), 409
    config = conn.execute('SELECT * FROM bmax_config WHERE id=1').fetchone()
    price = round(float(config['price_mxn'] if config else BMAX_DEFAULT_PRICE), 2)
    live_user = conn.execute('SELECT balance FROM users WHERE username=?', (user['username'],)).fetchone()
    balance = float(live_user['balance'] or 0) if live_user else 0.0
    if balance < price:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': f'Saldo insuficiente. Tienes ${balance:.2f} y Bmax Pro cuesta ${price:.2f}.'}), 402
    conn.execute('UPDATE users SET balance=ROUND(balance-?,2) WHERE username=? AND balance>=?', (price, user['username'], price))
    changed = conn.execute("UPDATE bmax_reservations SET status='purchased',purchased_at=? WHERE id=? AND status='active' AND expires_at>?", (now, reservation['id'], time.time()))
    if changed.rowcount != 1:
        conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Tu apartado venció. Aparta un cupo nuevamente.'}), 409
    conn.execute('UPDATE bmax_config SET reserved_count=MAX(0,reserved_count-1),sold_count=sold_count+1,updated_at=? WHERE id=1', (now,))
    conn.execute("INSERT INTO bmax_purchases(username,status,price_mxn,purchased_at,reservation_id) VALUES(?,'active',?,?,?)", (user['username'], price, now, reservation['id']))
    conn.execute("UPDATE users SET active_plan='bmax_pro',active_plan_status='active',active_plan_minutes=0,plan_price_mxn=?,plan_purchased_at=?,active_plan_discount_percent=0 WHERE username=?", (price, now, user['username']))
    balance_after = float(conn.execute('SELECT balance FROM users WHERE username=?', (user['username'],)).fetchone()['balance'] or 0)
    conn.execute("INSERT INTO balance_transactions(username,kind,amount,balance_after,description) VALUES(?,?,?,?,?)", (user['username'], 'bmax_purchase', -price, balance_after, 'Compra de acceso Bmax Pro'))
    if user['role'] != 'admin':
        conn.execute('''INSERT INTO admin_revenue_transactions(session_id,payer_username,module_id,module_name,package_name,gross_amount,discount_amount,amount,payment_method,payment_status,collected,created_at,collected_at,received_by)
                        VALUES(NULL,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                     (user['username'], 'BMAX-PRO', 'Acceso Bmax Pro', 'Bmax Pro', price, 0, price, 'wallet', 'paid_wallet', now, now, 'tragic'))
        conn.execute('UPDATE admin_account SET balance=ROUND(balance+?,2),updated_at=? WHERE id=1', (price, now))
    conn.commit(); result = bmax_status_for_user(user, conn); conn.close()
    return jsonify({'ok': True, 'balance': round(balance_after,2), 'bmax': result, 'message': 'Bmax Pro quedó activo en tu cuenta.'})


@app.get('/api/admin/bmax')
@admin_required
def admin_bmax():
    conn = db(); release_expired_bmax_reservations(conn)
    config = conn.execute('SELECT * FROM bmax_config WHERE id=1').fetchone()
    purchases = conn.execute("SELECT p.username,p.price_mxn,p.purchased_at,p.status,COALESCE(u.display_name,p.username) AS display_name FROM bmax_purchases p LEFT JOIN users u ON u.username=p.username ORDER BY p.purchased_at DESC LIMIT 100").fetchall()
    total = int(config['total_slots']); sold = int(config['sold_count']); reserved = int(config['reserved_count'])
    payload = {'config': {'total_slots': total, 'sold_count': sold, 'reserved_count': reserved, 'available_slots': max(0,total-sold-reserved), 'price_mxn': round(float(config['price_mxn']),2)}, 'purchases': [dict(row) for row in purchases]}
    conn.commit(); conn.close(); return jsonify({'ok': True, **payload})


@app.put('/api/admin/bmax/config')
@admin_required
def admin_bmax_config():
    data = request.get_json(silent=True) or {}
    try: total_slots = int(data.get('total_slots')); price = round(float(data.get('price_mxn')),2)
    except (TypeError,ValueError): return jsonify({'ok':False,'error':'Escribe un número entero de cupos y un precio válido.'}),400
    if not 0 <= total_slots <= 100000: return jsonify({'ok':False,'error':'Los cupos deben estar entre 0 y 100,000.'}),400
    if not 1 <= price <= 100000: return jsonify({'ok':False,'error':'El precio debe estar entre $1 y $100,000 MXN.'}),400
    conn = db(); conn.execute('BEGIN IMMEDIATE'); release_expired_bmax_reservations(conn); config = conn.execute('SELECT * FROM bmax_config WHERE id=1').fetchone()
    minimum = int(config['sold_count']) + int(config['reserved_count'])
    if total_slots < minimum:
        conn.rollback(); conn.close(); return jsonify({'ok':False,'error':f'No puedes dejar menos de {minimum} cupos: ya están vendidos o apartados.'}),409
    conn.execute('UPDATE bmax_config SET total_slots=?,price_mxn=?,updated_at=? WHERE id=1', (total_slots,price,time.time()))
    conn.commit(); result = bmax_status_for_user(current_user(), conn); conn.close()
    return jsonify({'ok':True,'bmax':result})

@app.get('/api/admin/revenue')
@admin_required
def admin_revenue():
    conn = db()
    account = conn.execute('SELECT balance,owner_username FROM admin_account WHERE id=1').fetchone()
    summary = conn.execute("""SELECT
        COALESCE(SUM(CASE WHEN collected=1 AND date(collected_at,'unixepoch','localtime')=date('now','localtime') THEN amount ELSE 0 END),0) AS collected_today,
        COALESCE(SUM(CASE WHEN collected=1 THEN amount ELSE 0 END),0) AS collected_total,
        COALESCE(SUM(CASE WHEN collected=0 AND payment_status='pending_cash' THEN amount ELSE 0 END),0) AS cash_pending,
        COALESCE(SUM(CASE WHEN collected=0 AND payment_status='demo_not_processed' THEN amount ELSE 0 END),0) AS card_unprocessed,
        SUM(CASE WHEN collected=1 AND date(collected_at,'unixepoch','localtime')=date('now','localtime') THEN 1 ELSE 0 END) AS payments_today,
        SUM(CASE WHEN collected=1 THEN 1 ELSE 0 END) AS payments_total
        FROM admin_revenue_transactions""").fetchone()
    transactions = conn.execute('''SELECT id,session_id,payer_username,module_id,module_name,package_name,
                                          gross_amount,discount_amount,amount,payment_method,payment_status,
                                          collected,created_at,collected_at,received_by
                                   FROM admin_revenue_transactions ORDER BY id DESC LIMIT 50''').fetchall()
    conn.close()
    return jsonify({'ok': True,
        'account': {'owner_username': account['owner_username'] if account else 'tragic', 'balance': round(float(account['balance'] or 0),2) if account else 0},
        'summary': {key: (round(float(summary[key] or 0),2) if key in ('collected_today','collected_total','cash_pending','card_unprocessed') else int(summary[key] or 0))
                    for key in ('collected_today','collected_total','cash_pending','card_unprocessed','payments_today','payments_total')},
        'transactions': [dict(row) for row in transactions]})


@app.post('/api/admin/revenue/<int:revenue_id>/collect')
@admin_required
def admin_collect_cash(revenue_id):
    conn = db(); conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('SELECT * FROM admin_revenue_transactions WHERE id=?', (revenue_id,)).fetchone()
    if not row:
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': 'Movimiento de cobro no encontrado.'}), 404
    if row['collected']:
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': 'Este pago ya fue confirmado.'}), 409
    if row['payment_method'] != 'cash' or row['payment_status'] != 'pending_cash':
        conn.rollback(); conn.close()
        return jsonify({'ok': False, 'error': 'Solo se pueden confirmar pagos en efectivo pendientes. La tarjeta necesita una pasarela real.'}), 400
    now = time.time(); amount = round(float(row['amount'] or 0), 2)
    conn.execute("""UPDATE admin_revenue_transactions SET collected=1,payment_status='paid_cash',collected_at=?,received_by=? WHERE id=?""",
                 (now, session['username'], revenue_id))
    conn.execute('UPDATE admin_account SET balance=ROUND(balance+?,2),updated_at=? WHERE id=1', (amount, now))
    if row['session_id']:
        conn.execute("UPDATE module_sessions SET payment_status='paid_cash',amount_paid=?,amount_due=0 WHERE id=?", (amount, row['session_id']))
    conn.commit()
    account = conn.execute('SELECT balance FROM admin_account WHERE id=1').fetchone()
    conn.close()
    return jsonify({'ok': True, 'collected': amount, 'admin_balance': round(float(account['balance'] or 0),2)})


@app.get('/api/admin/users')
@admin_required
def admin_users():
    conn = db()
    rows = conn.execute('''SELECT username,role,usage,balance,discount_percent,blocked,display_name,created_at
                           FROM users ORDER BY created_at DESC''').fetchall()
    conn.close()
    return jsonify({'users': [user_public(row) for row in rows]})


@app.post('/api/admin/users/<username>/balance')
@admin_required
def admin_add_balance(username):
    data = request.get_json(silent=True) or {}
    try: amount = round(float(data.get('amount', 0)), 2)
    except (TypeError, ValueError): amount = 0
    if not 0 < amount <= 100000: return jsonify({'ok': False, 'error': 'El saldo debe ser mayor que $0 y no superar $100,000.'}), 400
    conn = db(); conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('SELECT balance FROM users WHERE username=?', (username,)).fetchone()
    if not row: conn.rollback(); conn.close(); return jsonify({'ok': False, 'error': 'Usuario no encontrado.'}), 404
    new_balance = round(float(row['balance'] or 0) + amount, 2)
    conn.execute('UPDATE users SET balance=? WHERE username=?', (new_balance, username))
    conn.execute('INSERT INTO balance_transactions(username,kind,amount,balance_after,description) VALUES(?,?,?,?,?)',
                 (username, 'admin_topup', amount, new_balance, f"Saldo agregado por {session['username']}"))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'username': username, 'balance': new_balance})


@app.put('/api/admin/users/<username>/discount')
@admin_required
def admin_set_discount(username):
    data = request.get_json(silent=True) or {}
    try: percent = float(data.get('discount_percent', 0))
    except (TypeError, ValueError): percent = -1
    if not 0 <= percent <= 90: return jsonify({'ok': False, 'error': 'El descuento debe estar entre 0% y 90%.'}), 400
    conn = db(); row = conn.execute('SELECT username FROM users WHERE username=?', (username,)).fetchone()
    if not row: conn.close(); return jsonify({'ok': False, 'error': 'Usuario no encontrado.'}), 404
    conn.execute('UPDATE users SET discount_percent=? WHERE username=?', (round(percent, 2), username)); conn.commit(); conn.close()
    return jsonify({'ok': True, 'username': username, 'discount_percent': round(percent, 2)})


@app.put('/api/admin/users/<username>/blocked')
@admin_required
def admin_set_blocked(username):
    data = request.get_json(silent=True) or {}; blocked = bool(data.get('blocked', False))
    if username == 'tragic': return jsonify({'ok': False, 'error': 'La cuenta admin principal no se puede bloquear.'}), 400
    conn = db(); row = conn.execute('SELECT role FROM users WHERE username=?', (username,)).fetchone()
    if not row: conn.close(); return jsonify({'ok': False, 'error': 'Usuario no encontrado.'}), 404
    if row['role'] == 'admin': conn.close(); return jsonify({'ok': False, 'error': 'No se puede bloquear otra cuenta administradora.'}), 400
    conn.execute('UPDATE users SET blocked=? WHERE username=?', (1 if blocked else 0, username))
    if blocked:
        now = time.time()
        active = conn.execute('SELECT module_id FROM module_sessions WHERE username=? AND active=1', (username,)).fetchall()
        for item in active: conn.execute("UPDATE modules SET status='available',occupied_by=NULL,occupied_until=NULL WHERE id=?", (item['module_id'],))
        conn.execute('UPDATE module_sessions SET active=0,ended_at=? WHERE username=? AND active=1', (now, username))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'username': username, 'blocked': blocked})


@app.get('/api/network/ping')
def network_ping(): return jsonify({'ok': True, 'server_time': time.time()})

@app.get('/manifest.webmanifest')
def manifest(): return app.send_static_file('manifest.webmanifest')

@app.get('/sw.js')
def service_worker(): return app.send_static_file('sw.js')

@app.errorhandler(413)
def file_too_large(_): return jsonify({'ok': False, 'error': 'El archivo es demasiado grande.'}), 413

init_db()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
