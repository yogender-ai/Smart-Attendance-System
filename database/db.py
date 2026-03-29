import sqlite3
import os
from config import Config

def get_db_connection():
    conn = sqlite3.connect(Config.DATABASE_URI)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path, 'r') as f:
        schema = f.read()
    
    conn = get_db_connection()
    conn.executescript(schema)

    # Insert default admin if not exists
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE role = 'admin'")
    if not cur.fetchone():
        from werkzeug.security import generate_password_hash
        default_admin_pw = generate_password_hash("admin123")
        cur.execute('''
            INSERT INTO users (employee_id, name, email, phone, department, password_hash, role, face_registered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', ('ADMIN001', 'Super Admin', 'admin@sofzenix.com', '0000000000', 'Management', default_admin_pw, 'admin', 0))

    # Insert default settings if not exists
    defaults = {
        'late_cutoff_hour': '9',
        'late_cutoff_minute': '0',
        'company_name': 'Sofzenix Technologies',
        'face_tolerance': '0.45',
        'min_face_samples': '5',
    }
    for key, value in defaults.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    """Get a setting value from the settings table."""
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key, value):
    """Set a setting value in the settings table."""
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
