"""
SmartFace Database Layer
========================
Supports both SQLite (local development) and PostgreSQL (Neon production).
Auto-detects based on DATABASE_URL environment variable.
"""

import os
from config import Config


def get_db_connection():
    """Get a database connection. Auto-detects PostgreSQL vs SQLite."""
    if Config.USE_POSTGRES:
        return _get_postgres_connection()
    else:
        return _get_sqlite_connection()


def _get_sqlite_connection():
    """SQLite connection for local development."""
    import sqlite3
    conn = sqlite3.connect(Config.DATABASE_URI)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_postgres_connection():
    """PostgreSQL connection for production (Neon).
    Returns a wrapper that behaves identically to sqlite3 connections,
    so ALL existing conn.execute() calls with ? placeholders work as-is.
    """
    import psycopg2
    import psycopg2.extras

    raw_conn = psycopg2.connect(Config.DATABASE_URL, sslmode='require')
    raw_conn.autocommit = False
    return PostgresConnectionWrapper(raw_conn)


class PostgresCursorWrapper:
    """Makes psycopg2 cursor results behave like sqlite3.Row objects."""
    def __init__(self, cursor):
        self._cursor = cursor
        self._description = cursor.description

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PostgresRowWrapper(self._cursor, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [PostgresRowWrapper(self._cursor, row) for row in rows]

    def __iter__(self):
        return self

    def __next__(self):
        row = self._cursor.fetchone()
        if row is None:
            raise StopIteration
        return PostgresRowWrapper(self._cursor, row)


class PostgresConnectionWrapper:
    """Wraps psycopg2 connection to behave exactly like sqlite3 connection.
    - Auto-converts ? placeholders to %s
    - Returns dict-like Row objects from .execute().fetchone()
    - Supports .executescript() via splitting on ;
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        # Convert sqlite-style ? to postgres-style %s
        query = query.replace('?', '%s')
        cur = self._conn.cursor()
        if params:
            cur.execute(query, params)
        else:
            cur.execute(query)
        return PostgresCursorWrapper(cur)

    def executescript(self, script):
        cur = self._conn.cursor()
        cur.execute(script)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        return self._conn.cursor()


class PostgresRowWrapper:
    """Make psycopg2 rows behave like sqlite3.Row (dict-like access)."""
    def __init__(self, cursor, row):
        self._data = {}
        if row and cursor.description:
            for i, col in enumerate(cursor.description):
                self._data[col[0]] = row[i]

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()


def init_db():
    """Initialize database tables and default data."""
    if Config.USE_POSTGRES:
        _init_postgres()
    else:
        _init_sqlite()


def _init_sqlite():
    """Initialize SQLite database."""
    import sqlite3

    # Use SQLite-specific schema
    sqlite_schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT,
        department TEXT,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'employee' CHECK(role IN ('admin', 'employee')),
        profile_photo TEXT,
        face_encoding BLOB,
        face_encodings TEXT,
        face_registered INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('Present', 'Absent', 'Late', 'Leave')),
        method TEXT DEFAULT 'Face Recognition',
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    os.makedirs(os.path.dirname(Config.DATABASE_URI), exist_ok=True)
    conn = _get_sqlite_connection()
    conn.executescript(sqlite_schema)
    _insert_defaults(conn)
    conn.commit()
    conn.close()


def _init_postgres():
    """Initialize PostgreSQL database (Neon)."""
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, 'r') as f:
        schema = f.read()

    conn = _get_postgres_connection()
    conn.executescript(schema)
    conn.commit()

    _insert_defaults_pg(conn)
    conn.commit()
    conn.close()


def _insert_defaults(conn):
    """Insert default admin and settings for SQLite."""
    from werkzeug.security import generate_password_hash

    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE role = 'admin'")
    if not cur.fetchone():
        default_admin_pw = generate_password_hash("Admin@123")
        cur.execute('''
            INSERT INTO users (employee_id, name, email, phone, department, password_hash, role, face_registered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('ADMIN001', 'Super Admin', 'admin@sofzenix.com', '0000000000', 'Management', default_admin_pw, 'admin', 0))

    defaults = {
        'late_cutoff_hour': '9',
        'late_cutoff_minute': '0',
        'company_name': 'Sofzenix Technologies',
        'face_tolerance': '0.45',
        'min_face_samples': '5',
        'email_enabled': '0',
        'email_trigger_hour': '18',
        'email_trigger_minute': '0',
        'hr_email': '',
    }
    for key, value in defaults.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))


def _insert_defaults_pg(conn):
    """Insert default admin and settings for PostgreSQL."""
    from werkzeug.security import generate_password_hash

    result = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()
    if not result:
        default_admin_pw = generate_password_hash("Admin@123")
        conn.execute('''
            INSERT INTO users (employee_id, name, email, phone, department, password_hash, role, face_registered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('ADMIN001', 'Super Admin', 'admin@sofzenix.com', '0000000000', 'Management', default_admin_pw, 'admin', 0))

    defaults = {
        'late_cutoff_hour': '9',
        'late_cutoff_minute': '0',
        'company_name': 'Sofzenix Technologies',
        'face_tolerance': '0.45',
        'min_face_samples': '5',
        'email_enabled': '0',
        'email_trigger_hour': '18',
        'email_trigger_minute': '0',
        'hr_email': '',
    }
    for key, value in defaults.items():
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (key, value))


def execute_query(conn, query, params=None):
    """Execute a query. The connection wrapper handles placeholder conversion."""
    return conn.execute(query, params) if params else conn.execute(query)


def fetchone(conn, query, params=None):
    """Fetch one row, returning dict-like object."""
    if params:
        return conn.execute(query, params).fetchone()
    return conn.execute(query).fetchone()


def fetchall(conn, query, params=None):
    """Fetch all rows, returning list of dict-like objects."""
    if params:
        return conn.execute(query, params).fetchall()
    return conn.execute(query).fetchall()


def get_setting(key, default=None):
    """Get a setting value from the settings table."""
    conn = get_db_connection()
    row = fetchone(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    """Set a setting value in the settings table."""
    conn = get_db_connection()
    if Config.USE_POSTGRES:
        execute_query(conn, "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = ?", (key, str(value), str(value)))
    else:
        execute_query(conn, "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
