import os
import io
import csv
import uuid
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, session, jsonify, flash, url_for, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from config import Config
from database.db import get_db_connection, init_db, get_setting, set_setting
from utils.face_utils import register_face, recognize_face_with_liveness
from utils.validators import (
    validate_registration, validate_profile_update, validate_name,
    validate_employee_id, validate_email, validate_phone, validate_password,
    validate_department, validate_status, validate_date, validate_time,
    validate_profile_photo, generate_secure_password, ALLOWED_DEPARTMENTS
)

app = Flask(__name__)
app.config.from_object(Config)

# Ensure upload directory exists
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

# Initialize Database
if not Config.USE_POSTGRES:
    if not os.path.exists(Config.DATABASE_URI):
        os.makedirs(os.path.dirname(Config.DATABASE_URI), exist_ok=True)
        init_db()
    else:
        # Ensure new columns exist (migration for existing DBs)
        try:
            conn = get_db_connection()
            conn.execute("SELECT profile_photo FROM users LIMIT 1")
            conn.close()
        except Exception:
            conn = get_db_connection()
            try:
                conn.execute("ALTER TABLE users ADD COLUMN profile_photo TEXT")
                conn.commit()
            except Exception:
                pass
            conn.close()
else:
    init_db()

# --- APScheduler for auto email notifications ---
scheduler = None
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from email_service import send_absentee_emails

    email_enabled = get_setting('email_enabled', '0')
    trigger_hour = int(get_setting('email_trigger_hour', '18'))
    trigger_minute = int(get_setting('email_trigger_minute', '0'))

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        send_absentee_emails,
        'cron',
        hour=trigger_hour,
        minute=trigger_minute,
        id='absentee_emails',
        replace_existing=True,
        misfire_grace_time=3600
    )
    if email_enabled == '1':
        scheduler.start()
        print(f"[Scheduler] Email notifications enabled — triggers at {trigger_hour}:{trigger_minute:02d}")
    else:
        print("[Scheduler] Email notifications disabled (enable in Settings)")
except Exception as e:
    print(f"[Scheduler] Could not initialize: {e}")


def get_late_cutoff():
    hour = int(get_setting('late_cutoff_hour', '9'))
    minute = int(get_setting('late_cutoff_minute', '0'))
    return hour, minute


def is_late(time_str):
    try:
        t = datetime.strptime(time_str, "%H:%M:%S")
        hour, minute = get_late_cutoff()
        return t.hour > hour or (t.hour == hour and t.minute > minute)
    except Exception:
        return False


# ============================================================
#  PUBLIC ROUTES
# ============================================================

@app.route("/")
def home():
    return render_template("scanner.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session and session.get("role") == "employee":
        return redirect(url_for("employee_dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("login.html")

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ? AND role = 'employee'", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            session["employee_id"] = user["employee_id"]
            session["profile_photo"] = user["profile_photo"] or ""
            return redirect(url_for("employee_dashboard"))
        else:
            flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if "user_id" in session and session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("admin_login.html")

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ? AND role = 'admin'", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            session["employee_id"] = user["employee_id"]
            session["profile_photo"] = user["profile_photo"] or ""
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid admin email or password.", "danger")

    return render_template("admin_login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        cleaned, errors = validate_registration(request.form)

        if errors:
            # Return with first error
            first_error = list(errors.values())[0]
            flash(first_error, "danger")
            return render_template("register.html", departments=ALLOWED_DEPARTMENTS)

        conn = get_db_connection()
        try:
            hashed_pw = generate_password_hash(cleaned['password'])
            conn.execute('''
                INSERT INTO users (employee_id, name, email, phone, department, password_hash, role)
                VALUES (?, ?, ?, ?, ?, ?, 'employee')
            ''', (cleaned['employee_id'], cleaned['name'], cleaned['email'],
                  cleaned['phone'], cleaned['department'], hashed_pw))
            conn.commit()
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Employee ID or Email already exists.", "danger")
        except Exception as e:
            flash("Registration failed. Please try again.", "danger")
        finally:
            conn.close()

    return render_template("register.html", departments=ALLOWED_DEPARTMENTS)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ============================================================
#  PROFILE MANAGEMENT
# ============================================================

@app.route("/profile", methods=["GET", "POST"])
def profile():
    """Employee profile management page."""
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_info":
            cleaned, errors = validate_profile_update(request.form)

            if errors:
                first_error = list(errors.values())[0]
                flash(first_error, "danger")
                conn.close()
                return redirect(url_for("profile"))

            conn.execute('''
                UPDATE users SET name = ?, phone = ?, department = ? WHERE id = ?
            ''', (cleaned['name'], cleaned['phone'], cleaned['department'], session["user_id"]))
            conn.commit()
            session["name"] = cleaned['name']
            flash("Profile updated successfully.", "success")

        elif action == "change_password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")

            if not check_password_hash(user["password_hash"], current_pw):
                flash("Current password is incorrect.", "danger")
                conn.close()
                return redirect(url_for("profile"))

            if new_pw != confirm_pw:
                flash("New passwords do not match.", "danger")
                conn.close()
                return redirect(url_for("profile"))

            new_pw_clean, pw_err = validate_password(new_pw)
            if pw_err:
                flash(pw_err, "danger")
                conn.close()
                return redirect(url_for("profile"))

            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(new_pw_clean), session["user_id"]))
            conn.commit()
            flash("Password changed successfully.", "success")

        elif action == "upload_photo":
            if 'photo' not in request.files:
                flash("No file selected.", "danger")
                conn.close()
                return redirect(url_for("profile"))

            file = request.files['photo']
            is_valid, photo_err = validate_profile_photo(file)
            if not is_valid:
                flash(photo_err, "danger")
                conn.close()
                return redirect(url_for("profile"))

            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"profile_{session['user_id']}_{uuid.uuid4().hex[:8]}.{ext}"
            filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
            file.save(filepath)

            # Delete old photo if exists
            if user["profile_photo"]:
                old_path = os.path.join(Config.UPLOAD_FOLDER, user["profile_photo"])
                if os.path.exists(old_path):
                    os.remove(old_path)

            conn.execute("UPDATE users SET profile_photo = ? WHERE id = ?", (filename, session["user_id"]))
            conn.commit()
            session["profile_photo"] = filename
            flash("Profile photo updated.", "success")

        conn.close()
        return redirect(url_for("profile"))

    conn.close()
    return render_template("profile.html", user=user, departments=ALLOWED_DEPARTMENTS)


# ============================================================
#  ADMIN ROUTES
# ============================================================

@app.route("/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    total_emp = conn.execute("SELECT COUNT(*) FROM users WHERE role='employee'").fetchone()[0]

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    present_today = conn.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE date = ?", (today,)).fetchone()[0]
    present_yesterday = conn.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE date = ?", (yesterday,)).fetchone()[0]

    today_records = conn.execute("SELECT time FROM attendance WHERE date = ?", (today,)).fetchall()
    late_count = sum(1 for r in today_records if is_late(r['time']))

    yesterday_records = conn.execute("SELECT time FROM attendance WHERE date = ?", (yesterday,)).fetchall()
    late_yesterday = sum(1 for r in yesterday_records if is_late(r['time']))

    attendance_rate = round((present_today / total_emp * 100), 1) if total_emp > 0 else 0

    present_trend = 0
    if present_yesterday > 0:
        present_trend = round(((present_today - present_yesterday) / present_yesterday) * 100, 1)
    elif present_today > 0:
        present_trend = 100

    late_trend = 0
    if late_yesterday > 0:
        late_trend = round(((late_count - late_yesterday) / late_yesterday) * 100, 1)
    elif late_count > 0:
        late_trend = 100

    recent_attendance = conn.execute('''
        SELECT attendance.id, attendance.time, attendance.status, attendance.date,
               users.name, users.employee_id, users.department, users.profile_photo
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.date = ?
        ORDER BY attendance.time DESC LIMIT 20
    ''', (today,)).fetchall()

    weekly_data = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        count = conn.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE date = ?", (d,)).fetchone()[0]
        weekly_data.append(count)

    admin_user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

    employees_list = conn.execute("SELECT id, employee_id, name, department FROM users WHERE role = 'employee'").fetchall()

    conn.close()

    absent_today = total_emp - present_today

    return render_template("admin_dashboard.html",
                           total_emp=total_emp,
                           present_today=present_today,
                           absent_today=absent_today,
                           late_count=late_count,
                           attendance_rate=attendance_rate,
                           present_trend=present_trend,
                           late_trend=late_trend,
                           recent_attendance=recent_attendance,
                           weekly_data=weekly_data,
                           admin=admin_user,
                           employees_list=employees_list)


@app.route("/manage_employees", methods=["GET", "POST"])
def manage_employees():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))

    conn = get_db_connection()

    if request.method == "POST":
        action = request.form.get("action")
        user_id = request.form.get("user_id")
        if action == "delete" and user_id:
            conn.execute("DELETE FROM attendance WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            flash("Employee removed successfully.", "success")
            return redirect(url_for("manage_employees"))

    employees = conn.execute(
        "SELECT id, employee_id, name, email, phone, department, face_registered, profile_photo, created_at FROM users WHERE role='employee' ORDER BY name"
    ).fetchall()

    dept_stats = conn.execute(
        "SELECT department, COUNT(*) as count FROM users WHERE role='employee' GROUP BY department ORDER BY count DESC"
    ).fetchall()

    conn.close()

    return render_template("employees.html", employees=employees, dept_stats=dept_stats,
                           departments=ALLOWED_DEPARTMENTS)


@app.route("/attendance_history")
def attendance_history():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    dept_filter = request.args.get('department', '')
    status_filter = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    per_page = 25

    conn = get_db_connection()

    query = '''
        SELECT attendance.id, attendance.time, attendance.date, attendance.status, attendance.method,
               users.name, users.employee_id, users.department, users.profile_photo
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE 1=1
    '''
    params = []

    if date_from:
        query += " AND attendance.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND attendance.date <= ?"
        params.append(date_to)
    if dept_filter:
        query += " AND users.department = ?"
        params.append(dept_filter)
    if status_filter:
        query += " AND attendance.status = ?"
        params.append(status_filter)

    count_query = query.replace(
        "SELECT attendance.id, attendance.time, attendance.date, attendance.status, attendance.method,\n               users.name, users.employee_id, users.department, users.profile_photo",
        "SELECT COUNT(*)"
    )
    total_records = conn.execute(count_query, params).fetchone()[0]
    total_pages = max(1, (total_records + per_page - 1) // per_page)

    query += " ORDER BY attendance.date DESC, attendance.time DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])

    records = conn.execute(query, params).fetchall()

    departments = conn.execute("SELECT DISTINCT department FROM users WHERE department IS NOT NULL ORDER BY department").fetchall()

    conn.close()

    return render_template("attendance_history.html",
                           records=records,
                           departments=departments,
                           date_from=date_from,
                           date_to=date_to,
                           dept_filter=dept_filter,
                           status_filter=status_filter,
                           page=page,
                           total_pages=total_pages,
                           total_records=total_records)


@app.route("/analytics")
def analytics():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))

    conn = get_db_connection()

    weekly_labels = []
    weekly_present = []
    weekly_late = []
    weekly_absent = []
    total_emp = conn.execute("SELECT COUNT(*) FROM users WHERE role='employee'").fetchone()[0]

    for i in range(13, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        label = (datetime.now() - timedelta(days=i)).strftime("%d %b")
        weekly_labels.append(label)

        present = conn.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE date = ?", (d,)).fetchone()[0]
        records = conn.execute("SELECT time FROM attendance WHERE date = ?", (d,)).fetchall()
        late = sum(1 for r in records if is_late(r['time']))

        weekly_present.append(present)
        weekly_late.append(late)
        weekly_absent.append(max(0, total_emp - present))

    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    dept_data = conn.execute('''
        SELECT users.department, COUNT(DISTINCT attendance.user_id) as unique_present, COUNT(*) as total_records
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.date >= ?
        GROUP BY users.department
        ORDER BY total_records DESC
    ''', (month_start,)).fetchall()

    dept_labels = [d['department'] or 'Unknown' for d in dept_data]
    dept_values = [d['total_records'] for d in dept_data]

    this_month_present = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date >= ? AND status IN ('Present', 'Late')",
        (month_start,)
    ).fetchone()[0]

    this_month_late = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date >= ? AND status = 'Late'",
        (month_start,)
    ).fetchone()[0]

    days_elapsed = (datetime.now() - datetime.strptime(month_start, "%Y-%m-%d")).days + 1
    avg_daily = round(this_month_present / max(1, days_elapsed), 1)
    avg_rate = round((avg_daily / max(1, total_emp)) * 100, 1) if total_emp > 0 else 0

    top_late = conn.execute('''
        SELECT users.name, users.employee_id, users.department, COUNT(*) as late_count
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.status = 'Late' AND attendance.date >= ?
        GROUP BY attendance.user_id
        ORDER BY late_count DESC
        LIMIT 5
    ''', (month_start,)).fetchall()

    top_punctual = conn.execute('''
        SELECT users.name, users.employee_id, users.department,
               COUNT(*) as total_days,
               SUM(CASE WHEN attendance.status = 'Present' THEN 1 ELSE 0 END) as present_days
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.date >= ?
        GROUP BY attendance.user_id
        ORDER BY present_days DESC
        LIMIT 5
    ''', (month_start,)).fetchall()

    conn.close()

    return render_template("analytics.html",
                           weekly_labels=weekly_labels,
                           weekly_present=weekly_present,
                           weekly_late=weekly_late,
                           weekly_absent=weekly_absent,
                           dept_labels=dept_labels,
                           dept_values=dept_values,
                           total_emp=total_emp,
                           avg_rate=avg_rate,
                           avg_daily=avg_daily,
                           this_month_present=this_month_present,
                           this_month_late=this_month_late,
                           top_late=top_late,
                           top_punctual=top_punctual)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        late_hour = request.form.get("late_cutoff_hour", "9")
        late_minute = request.form.get("late_cutoff_minute", "0")
        company_name = request.form.get("company_name", "Sofzenix Technologies")
        face_tolerance = request.form.get("face_tolerance", "0.45")

        # Email settings
        email_enabled = request.form.get("email_enabled", "0")
        email_hour = request.form.get("email_trigger_hour", "18")
        email_minute = request.form.get("email_trigger_minute", "0")
        hr_email_val = (request.form.get("hr_email", "") or "").strip().lower()

        set_setting('late_cutoff_hour', late_hour)
        set_setting('late_cutoff_minute', late_minute)
        set_setting('company_name', company_name)
        set_setting('face_tolerance', face_tolerance)
        set_setting('email_enabled', email_enabled)
        set_setting('email_trigger_hour', email_hour)
        set_setting('email_trigger_minute', email_minute)
        set_setting('hr_email', hr_email_val)

        # Update scheduler
        global scheduler
        if scheduler:
            try:
                scheduler.remove_job('absentee_emails')
            except Exception:
                pass

            if email_enabled == '1':
                from email_service import send_absentee_emails
                scheduler.add_job(
                    send_absentee_emails, 'cron',
                    hour=int(email_hour), minute=int(email_minute),
                    id='absentee_emails', replace_existing=True,
                    misfire_grace_time=3600
                )
                if not scheduler.running:
                    scheduler.start()

        flash("Settings updated successfully.", "success")
        return redirect(url_for("settings"))

    current_settings = {
        'late_cutoff_hour': get_setting('late_cutoff_hour', '9'),
        'late_cutoff_minute': get_setting('late_cutoff_minute', '0'),
        'company_name': get_setting('company_name', 'Sofzenix Technologies'),
        'face_tolerance': get_setting('face_tolerance', '0.45'),
        'email_enabled': get_setting('email_enabled', '0'),
        'email_trigger_hour': get_setting('email_trigger_hour', '18'),
        'email_trigger_minute': get_setting('email_trigger_minute', '0'),
        'hr_email': get_setting('hr_email', ''),
    }

    return render_template("settings.html", settings=current_settings)


@app.route("/export_csv")
def export_csv():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    conn = get_db_connection()
    query = '''
        SELECT users.employee_id, users.name, users.department,
               attendance.date, attendance.time, attendance.status, attendance.method
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE 1=1
    '''
    params = []

    if date_from:
        query += " AND attendance.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND attendance.date <= ?"
        params.append(date_to)

    query += " ORDER BY attendance.date DESC, attendance.time DESC"

    records = conn.execute(query, params).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Employee ID', 'Name', 'Department', 'Date', 'Time', 'Status', 'Method'])

    for r in records:
        writer.writerow([r['employee_id'], r['name'], r['department'],
                         r['date'], r['time'], r['status'], r['method'] or 'Face Recognition'])

    csv_content = output.getvalue()
    output.close()

    filename = f"attendance_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ============================================================
#  EMPLOYEE ROUTES
# ============================================================

@app.route("/employee")
def employee_dashboard():
    if session.get("role") != "employee":
        return redirect(url_for("login"))

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    history = conn.execute(
        "SELECT id, date, time, status FROM attendance WHERE user_id = ? ORDER BY date DESC, time DESC LIMIT 30",
        (session["user_id"],)
    ).fetchall()

    present_count = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND status = 'Present'",
        (session["user_id"],)
    ).fetchone()[0]

    late_count = sum(1 for r in history if is_late(r['time']))

    absent_count = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND status = 'Absent'",
        (session["user_id"],)
    ).fetchone()[0]

    leave_count = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND status = 'Leave'",
        (session["user_id"],)
    ).fetchone()[0]

    chart_data = {
        "Present": present_count,
        "Late": late_count,
        "Absent": absent_count,
        "Leave": leave_count
    }

    today = datetime.now().strftime("%Y-%m-%d")
    today_record = conn.execute(
        "SELECT time, status FROM attendance WHERE user_id = ? AND date = ?",
        (session["user_id"], today)
    ).fetchone()

    today_status = None
    if today_record:
        today_status = {
            'time': today_record['time'],
            'status': today_record['status']
        }

    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    month_records = conn.execute(
        "SELECT date, time, status FROM attendance WHERE user_id = ? AND date >= ? ORDER BY date",
        (session["user_id"], month_start)
    ).fetchall()

    calendar_data = {}
    for r in month_records:
        calendar_data[r['date']] = r['status']

    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good Morning"
    elif hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"

    conn.close()

    return render_template("employee_dashboard.html",
                           user=user,
                           history=history,
                           chart_data=chart_data,
                           today_status=today_status,
                           calendar_data=calendar_data,
                           greeting=greeting,
                           late_days=late_count)


@app.route("/scanner")
def scanner():
    return redirect(url_for('home'))


# ============================================================
#  API ENDPOINTS
# ============================================================

@app.route("/api/register_face", methods=["POST"])
def api_register_face():
    if "user_id" not in session:
        return jsonify({"success": False, "msg": "Unauthorized"}), 401

    data = request.json
    base64_img = data.get("image")

    success = register_face(session["user_id"], base64_img)
    if not success:
        return jsonify({"success": False, "msg": "No face detected. Please try again."}), 400

    return jsonify({"success": True, "msg": "Face registered successfully!"})


@app.route("/api/recognize_face", methods=["POST"])
def api_recognize_face():
    data = request.json
    base64_img = data.get("image")
    liveness_verified = data.get("liveness_verified", False)

    user_id, liveness_metrics, confidence, anti_spoof_score, spoof_checks = recognize_face_with_liveness(base64_img)

    if spoof_checks.get("multi_face"):
        return jsonify({
            "success": False, "recognized": False,
            "msg": "Multiple faces detected. Only one person at a time.",
            "multi_face": True
        })

    if user_id:
        conn = get_db_connection()
        user_info = conn.execute("SELECT name, employee_id, department FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()

        if not user_info:
            return jsonify({"success": False, "recognized": False, "msg": "User not found."})

        # Stricter anti-spoof threshold (raised from 25 to 45)
        if anti_spoof_score < 45:
            # Gather failed checks for error info
            failed = [k.replace('_', ' ').title() for k, v in spoof_checks.items() if not v]
            fail_reason = f"Failed checks: {', '.join(failed)}" if failed else "3D Liveness not verified"
            return jsonify({
                "success": False, "recognized": True,
                "spoofing_detected": True,
                "anti_spoof_score": anti_spoof_score,
                "spoof_checks": spoof_checks,
                "msg": f"⚠️ Spoofing detected! ({fail_reason})"
            })

        if not liveness_verified:
            return jsonify({
                "success": False, "recognized": True,
                "liveness_metrics": liveness_metrics,
                "anti_spoof_score": anti_spoof_score,
                "spoof_checks": spoof_checks,
                "confidence": confidence,
                "msg": f"Target locked: {user_info['name']}. Processing liveness challenge...",
                "user": user_info['name'],
                "emp_id": user_info['employee_id'],
                "department": user_info['department']
            })

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        status = "Late" if is_late(time_str) else "Present"

        conn = get_db_connection()
        existing = conn.execute("SELECT id FROM attendance WHERE user_id=? AND date=?", (user_id, date_str)).fetchone()

        if not existing:
            conn.execute("INSERT INTO attendance (user_id, date, time, status, method) VALUES (?, ?, ?, ?, ?)",
                         (user_id, date_str, time_str, status, 'Face Recognition'))
            conn.commit()
            conn.close()
            return jsonify({
                "success": True, "recognized": True,
                "msg": f"Access Granted. Attendance verified for {user_info['name']}",
                "user": user_info['name'],
                "emp_id": user_info['employee_id'],
                "department": user_info['department'],
                "status": status, "time": time_str,
                "anti_spoof_score": anti_spoof_score,
                "confidence": confidence
            })
        else:
            conn.close()
            return jsonify({
                "success": True, "recognized": True,
                "msg": f"Welcome back, {user_info['name']}. Already checked in today.",
                "user": user_info['name'],
                "emp_id": user_info['employee_id'],
                "department": user_info['department'],
                "anti_spoof_score": anti_spoof_score,
                "confidence": confidence
            })

    # Face was detected and analyzed (anti_spoof ran) but not recognized
    if confidence > 0 or anti_spoof_score > 0:
        return jsonify({
            "success": False, "recognized": False,
            "face_found": True, "liveness_metrics": liveness_metrics,
            "anti_spoof_score": anti_spoof_score,
            "spoof_checks": spoof_checks,
            "msg": "Identity Unknown — Please register first"
        })

    return jsonify({
        "success": False, "recognized": False,
        "face_found": False, "has_eyes": has_eyes,
        "anti_spoof_score": anti_spoof_score,
        "spoof_checks": spoof_checks,
        "msg": "Waiting for valid subject..."
    })


@app.route("/api/edit_attendance", methods=["POST"])
def api_edit_attendance():
    if session.get("role") != "admin":
        return jsonify({"success": False, "msg": "Unauthorized"}), 401

    data = request.json
    record_id = data.get("id")

    new_time, t_err = validate_time(data.get("time", ""))
    new_date, d_err = validate_date(data.get("date", ""))
    new_status, s_err = validate_status(data.get("status", ""))

    if t_err:
        return jsonify({"success": False, "msg": t_err}), 400
    if d_err:
        return jsonify({"success": False, "msg": d_err}), 400
    if s_err:
        return jsonify({"success": False, "msg": s_err}), 400

    conn = get_db_connection()
    conn.execute('UPDATE attendance SET time = ?, date = ?, status = ? WHERE id = ?',
                 (new_time, new_date, new_status, record_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True, "msg": "Attendance updated."})


@app.route("/api/system_status")
def api_system_status():
    """System health check for admin dashboard."""
    status = {}

    # Camera (always 'Available' on server — actual check is client-side)
    status['camera'] = {'status': 'Available', 'detail': 'Browser webcam access'}

    # AI Engine
    try:
        from utils.face_utils import get_face_detector, TRAINER_PATH
        net = get_face_detector()
        face_files = [f for f in os.listdir('face_data') if f.endswith('.enc') or (f.startswith('face_') and f.endswith('.jpg'))]
        trainer_exists = os.path.exists(TRAINER_PATH)
        ai_detail = f"DNN {'active' if net else 'fallback'}, {len(face_files)} faces enrolled"
        if not trainer_exists:
            status['ai'] = {'status': 'Ready', 'detail': ai_detail + ' — no model trained yet'}
        else:
            status['ai'] = {'status': 'Active', 'detail': ai_detail}
    except Exception as e:
        status['ai'] = {'status': 'Error', 'detail': str(e)}

    # Database
    try:
        conn = get_db_connection()
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        status['database'] = {'status': 'Healthy', 'detail': f'{user_count} users'}
    except Exception as e:
        status['database'] = {'status': 'Error', 'detail': str(e)}

    # Email
    email_enabled = get_setting('email_enabled', '0')
    hr = get_setting('email_trigger_hour', '18')
    mn = get_setting('email_trigger_minute', '0')
    if email_enabled == '1':
        status['email'] = {'status': 'Enabled', 'detail': f'Auto-trigger: {hr}:{int(mn):02d}'}
    else:
        status['email'] = {'status': 'Disabled', 'detail': f'Auto-trigger: {hr}:{int(mn):02d}'}

    return jsonify(status)


@app.route("/api/manual_attendance", methods=["POST"])
def api_manual_attendance():
    if session.get("role") != "admin":
        return jsonify({"success": False, "msg": "Unauthorized"}), 401

    data = request.json
    user_id = data.get("user_id")

    status, s_err = validate_status(data.get("status", "Present"))
    date_str, d_err = validate_date(data.get("date", datetime.now().strftime("%Y-%m-%d")))
    time_str, t_err = validate_time(data.get("time", datetime.now().strftime("%H:%M:%S")))

    if s_err:
        return jsonify({"success": False, "msg": s_err}), 400
    if d_err:
        return jsonify({"success": False, "msg": d_err}), 400
    if t_err:
        return jsonify({"success": False, "msg": t_err}), 400

    if not user_id:
        return jsonify({"success": False, "msg": "Employee not specified."}), 400

    conn = get_db_connection()
    existing = conn.execute("SELECT id FROM attendance WHERE user_id=? AND date=?", (user_id, date_str)).fetchone()
    if existing:
        conn.close()
        return jsonify({"success": False, "msg": "Attendance already marked for this date."})

    conn.execute(
        "INSERT INTO attendance (user_id, date, time, status, method) VALUES (?, ?, ?, ?, ?)",
        (user_id, date_str, time_str, status, 'Manual Entry')
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True, "msg": "Attendance marked successfully."})


@app.route("/api/admin_add_employee", methods=["POST"])
def api_admin_add_employee():
    """Admin can add an employee directly with auto-generated password."""
    if session.get("role") != "admin":
        return jsonify({"success": False, "msg": "Unauthorized"}), 401

    data = request.json

    # Validate all fields
    emp_id, err = validate_employee_id(data.get("employee_id", ""))
    if err:
        return jsonify({"success": False, "msg": err}), 400

    name, err = validate_name(data.get("name", ""))
    if err:
        return jsonify({"success": False, "msg": err}), 400

    email, err = validate_email(data.get("email", ""))
    if err:
        return jsonify({"success": False, "msg": err}), 400

    phone, err = validate_phone(data.get("phone", ""))
    if err:
        return jsonify({"success": False, "msg": err}), 400

    dept, err = validate_department(data.get("department", ""))
    if err:
        return jsonify({"success": False, "msg": err}), 400

    # Generate secure password
    gen_password = generate_secure_password(12)
    hashed_pw = generate_password_hash(gen_password)

    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO users (employee_id, name, email, phone, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?, ?, 'employee')
        ''', (emp_id, name, email, phone, dept, hashed_pw))
        conn.commit()
        conn.close()
        return jsonify({
            "success": True,
            "msg": f"Employee {name} added successfully.",
            "generated_password": gen_password
        })
    except Exception:
        conn.close()
        return jsonify({"success": False, "msg": "Employee ID or Email already exists."}), 400


@app.route("/api/search_employees")
def api_search_employees():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"results": []})

    conn = get_db_connection()
    results = conn.execute('''
        SELECT id, employee_id, name, department, face_registered, profile_photo
        FROM users
        WHERE role = 'employee' AND (name LIKE ? OR employee_id LIKE ? OR email LIKE ?)
        ORDER BY name
        LIMIT 20
    ''', (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()
    conn.close()

    return jsonify({
        "results": [dict(r) for r in results]
    })


@app.route("/api/attendance_feed")
def api_attendance_feed():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db_connection()

    records = conn.execute('''
        SELECT attendance.time, attendance.status, attendance.method,
               users.name, users.employee_id, users.department, users.profile_photo
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.date = ?
        ORDER BY attendance.time DESC LIMIT 15
    ''', (today,)).fetchall()

    feed = []
    for r in records:
        feed.append({
            "name": r["name"],
            "emp_id": r["employee_id"],
            "time": r["time"],
            "status": r["status"],
            "department": r["department"],
            "method": r["method"] or "Face Recognition",
            "photo": r["profile_photo"] or ""
        })

    conn.close()
    return jsonify({"feed": feed})



@app.route("/api/upload_photo", methods=["POST"])
def api_upload_photo():
    """Upload profile photo via API."""
    if "user_id" not in session:
        return jsonify({"success": False, "msg": "Unauthorized"}), 401

    if 'photo' not in request.files:
        return jsonify({"success": False, "msg": "No file uploaded."}), 400

    file = request.files['photo']
    is_valid, err = validate_profile_photo(file)
    if not is_valid:
        return jsonify({"success": False, "msg": err}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"profile_{session['user_id']}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
    file.save(filepath)

    conn = get_db_connection()
    # Delete old photo
    old = conn.execute("SELECT profile_photo FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if old and old["profile_photo"]:
        old_path = os.path.join(Config.UPLOAD_FOLDER, old["profile_photo"])
        if os.path.exists(old_path):
            os.remove(old_path)

    conn.execute("UPDATE users SET profile_photo = ? WHERE id = ?", (filename, session["user_id"]))
    conn.commit()
    conn.close()

    session["profile_photo"] = filename

    return jsonify({"success": True, "msg": "Photo uploaded.", "filename": filename})


@app.route("/api/send_test_email", methods=["POST"])
def api_send_test_email():
    """Send a test email to verify SMTP configuration."""
    if session.get("role") != "admin":
        return jsonify({"success": False, "msg": "Unauthorized"}), 401

    if not Config.MAIL_USERNAME or not Config.MAIL_PASSWORD:
        return jsonify({"success": False, "msg": "Mail credentials not configured in environment variables."}), 400

    try:
        import smtplib
        from email.mime.text import MIMEText

        server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT)
        server.starttls()
        server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)

        msg = MIMEText("This is a test email from SmartFace AI. Email notifications are working correctly!")
        msg['From'] = Config.MAIL_DEFAULT_SENDER
        msg['To'] = Config.MAIL_USERNAME
        msg['Subject'] = "SmartFace: Test Email ✓"

        server.send_message(msg)
        server.quit()

        return jsonify({"success": True, "msg": "Test email sent successfully!"})
    except Exception as e:
        return jsonify({"success": False, "msg": f"Email failed: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True)
