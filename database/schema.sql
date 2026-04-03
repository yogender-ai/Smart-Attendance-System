CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    employee_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    department TEXT,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'employee' CHECK(role IN ('admin', 'employee')),
    profile_photo TEXT,
    face_encoding BYTEA,
    face_encodings TEXT,
    face_registered INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
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
