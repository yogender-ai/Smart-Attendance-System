import os
import io
import csv
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, session, jsonify, flash, url_for, Response
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from database.db import get_db_connection, init_db, get_setting, set_setting
from utils.face_utils import register_face, recognize_face_with_liveness

app = Flask(__name__)
app.config.from_object(Config)

# Initialize Database
if not os.path.exists(Config.DATABASE_URI):
    os.makedirs(os.path.dirname(Config.DATABASE_URI), exist_ok=True)
    init_db()


def get_late_cutoff():
    """Get late cutoff time from settings."""
    hour = int(get_setting('late_cutoff_hour', '9'))
    minute = int(get_setting('late_cutoff_minute', '0'))
    return hour, minute


def is_late(time_str):
    """Check if a check-in time is considered late."""
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
        email = request.form.get("email")
        password = request.form.get("password")
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ? AND role = 'employee'", (email,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            session["employee_id"] = user["employee_id"]
            return redirect(url_for("employee_dashboard"))
        else:
            flash("Invalid employee email or password", "danger")
            
    return render_template("login.html")


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if "user_id" in session and session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
        
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ? AND role = 'admin'", (email,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            session["employee_id"] = user["employee_id"]
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid admin email or password", "danger")
            
    return render_template("admin_login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        emp_id = request.form.get("employee_id")
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        dept = request.form.get("department")
        password = request.form.get("password")
        
        hashed_pw = generate_password_hash(password)
        conn = get_db_connection()
        
        try:
            conn.execute('''
                INSERT INTO users (employee_id, name, email, phone, department, password_hash, role)
                VALUES (?, ?, ?, ?, ?, ?, 'employee')
            ''', (emp_id, name, email, phone, dept, hashed_pw))
            conn.commit()
            flash("Registration successful. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Employee ID or Email already exists.", "danger")
        finally:
            conn.close()
            
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


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
    
    # Late arrivals count
    today_records = conn.execute("SELECT time FROM attendance WHERE date = ?", (today,)).fetchall()
    late_count = sum(1 for r in today_records if is_late(r['time']))
    
    yesterday_records = conn.execute("SELECT time FROM attendance WHERE date = ?", (yesterday,)).fetchall()
    late_yesterday = sum(1 for r in yesterday_records if is_late(r['time']))
    
    # Attendance rate
    attendance_rate = round((present_today / total_emp * 100), 1) if total_emp > 0 else 0
    
    # Trend calculations (real!)
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
    
    # Recent attendance with full details
    recent_attendance = conn.execute('''
        SELECT attendance.id, attendance.time, attendance.status, attendance.date, 
               users.name, users.employee_id, users.department
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.date = ?
        ORDER BY attendance.time DESC LIMIT 20
    ''', (today,)).fetchall()
    
    # Weekly data for sparkline (last 7 days)
    weekly_data = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        count = conn.execute("SELECT COUNT(DISTINCT user_id) FROM attendance WHERE date = ?", (d,)).fetchone()[0]
        weekly_data.append(count)
    
    admin_user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    
    # Employees list for manual entry
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
            flash("Employee deleted successfully.", "success")
            return redirect(url_for("manage_employees"))
            
    employees = conn.execute(
        "SELECT id, employee_id, name, email, phone, department, face_registered, created_at FROM users WHERE role='employee' ORDER BY name"
    ).fetchall()
    
    # Department stats
    dept_stats = conn.execute(
        "SELECT department, COUNT(*) as count FROM users WHERE role='employee' GROUP BY department ORDER BY count DESC"
    ).fetchall()
    
    conn.close()
    
    return render_template("employees.html", employees=employees, dept_stats=dept_stats)


@app.route("/attendance_history")
def attendance_history():
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))
    
    # Get filter params
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    dept_filter = request.args.get('department', '')
    status_filter = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    per_page = 25
    
    conn = get_db_connection()
    
    # Build query with filters
    query = '''
        SELECT attendance.id, attendance.time, attendance.date, attendance.status, attendance.method,
               users.name, users.employee_id, users.department
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
    
    # Count total for pagination
    count_query = query.replace(
        "SELECT attendance.id, attendance.time, attendance.date, attendance.status, attendance.method,\n               users.name, users.employee_id, users.department",
        "SELECT COUNT(*)"
    )
    total_records = conn.execute(count_query, params).fetchone()[0]
    total_pages = max(1, (total_records + per_page - 1) // per_page)
    
    query += " ORDER BY attendance.date DESC, attendance.time DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    
    records = conn.execute(query, params).fetchall()
    
    # Get unique departments for filter
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
    """Analytics dashboard with charts and trends."""
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))
    
    conn = get_db_connection()
    
    # Weekly attendance trend (last 14 days)
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
    
    # Department-wise attendance (this month)
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
    
    # Monthly stats
    this_month_present = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date >= ? AND status IN ('Present', 'Late')", 
        (month_start,)
    ).fetchone()[0]
    
    this_month_late = conn.execute(
        "SELECT COUNT(*) FROM attendance WHERE date >= ? AND status = 'Late'", 
        (month_start,)
    ).fetchone()[0]
    
    # Average attendance rate
    days_elapsed = (datetime.now() - datetime.strptime(month_start, "%Y-%m-%d")).days + 1
    avg_daily = round(this_month_present / max(1, days_elapsed), 1)
    avg_rate = round((avg_daily / max(1, total_emp)) * 100, 1) if total_emp > 0 else 0
    
    # Top late arrivals
    top_late = conn.execute('''
        SELECT users.name, users.employee_id, users.department, COUNT(*) as late_count
        FROM attendance
        JOIN users ON attendance.user_id = users.id
        WHERE attendance.status = 'Late' AND attendance.date >= ?
        GROUP BY attendance.user_id
        ORDER BY late_count DESC
        LIMIT 5
    ''', (month_start,)).fetchall()
    
    # Punctuality leaderboard (most present, least late)
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
    """Admin settings page."""
    if session.get("role") != "admin":
        return redirect(url_for("admin_login"))
    
    if request.method == "POST":
        # Update settings
        late_hour = request.form.get("late_cutoff_hour", "9")
        late_minute = request.form.get("late_cutoff_minute", "0")
        company_name = request.form.get("company_name", "Sofzenix Technologies")
        face_tolerance = request.form.get("face_tolerance", "0.45")
        
        set_setting('late_cutoff_hour', late_hour)
        set_setting('late_cutoff_minute', late_minute)
        set_setting('company_name', company_name)
        set_setting('face_tolerance', face_tolerance)
        
        flash("Settings updated successfully.", "success")
        return redirect(url_for("settings"))
    
    current_settings = {
        'late_cutoff_hour': get_setting('late_cutoff_hour', '9'),
        'late_cutoff_minute': get_setting('late_cutoff_minute', '0'),
        'company_name': get_setting('company_name', 'Sofzenix Technologies'),
        'face_tolerance': get_setting('face_tolerance', '0.45'),
    }
    
    return render_template("settings.html", settings=current_settings)


@app.route("/export_csv")
def export_csv():
    """Export attendance data as CSV."""
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
    
    # Generate CSV
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
    
    # Stats
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
    
    # Today's status
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
    
    # Monthly calendar data (for heatmap)
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    month_records = conn.execute(
        "SELECT date, time, status FROM attendance WHERE user_id = ? AND date >= ? ORDER BY date",
        (session["user_id"], month_start)
    ).fetchall()
    
    calendar_data = {}
    for r in month_records:
        calendar_data[r['date']] = r['status']
    
    # Time of day greeting
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
    
    return jsonify({"success": True, "msg": "Face registered successfully! You can now mark attendance."})


@app.route("/api/recognize_face", methods=["POST"])
def api_recognize_face():
    data = request.json
    base64_img = data.get("image")
    liveness_verified = data.get("liveness_verified", False)
    
    user_id, has_eyes, confidence, anti_spoof_score, spoof_checks = recognize_face_with_liveness(base64_img)
    
    # Multi-face rejection
    if spoof_checks.get("multi_face"):
        return jsonify({
            "success": False,
            "recognized": False,
            "msg": "Multiple faces detected. Only one person at a time.",
            "multi_face": True
        })
    
    if user_id:
        conn = get_db_connection()
        user_info = conn.execute("SELECT name, employee_id, department FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        
        if not user_info:
            return jsonify({"success": False, "recognized": False, "msg": "User not found in database."})
        
        # Anti-spoof rejection
        if anti_spoof_score < 25:
            return jsonify({
                "success": False,
                "recognized": True,
                "spoofing_detected": True,
                "anti_spoof_score": anti_spoof_score,
                "spoof_checks": spoof_checks,
                "msg": "⚠️ Spoofing attempt detected! Live presence required."
            })
        
        if not liveness_verified:
            return jsonify({
                "success": False, 
                "recognized": True, 
                "has_eyes": has_eyes,
                "anti_spoof_score": anti_spoof_score,
                "spoof_checks": spoof_checks,
                "confidence": confidence,
                "msg": f"Target locked: {user_info['name']}. Please blink to verify liveness.",
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
                "success": True, 
                "recognized": True,
                "msg": f"Access Granted. Attendance verified for {user_info['name']}", 
                "user": user_info['name'],
                "emp_id": user_info['employee_id'],
                "department": user_info['department'],
                "status": status,
                "time": time_str,
                "anti_spoof_score": anti_spoof_score,
                "confidence": confidence
            })
        else:
            conn.close()
            return jsonify({
                "success": True, 
                "recognized": True, 
                "msg": f"Welcome back, {user_info['name']}. Already checked in today.",
                "user": user_info['name'],
                "emp_id": user_info['employee_id'],
                "department": user_info['department'],
                "anti_spoof_score": anti_spoof_score,
                "confidence": confidence
            })
    
    if confidence > 0:
        return jsonify({
            "success": False, 
            "recognized": False, 
            "face_found": True,
            "has_eyes": has_eyes, 
            "anti_spoof_score": anti_spoof_score,
            "spoof_checks": spoof_checks,
            "msg": "Identity Unknown"
        })

    return jsonify({
        "success": False, 
        "recognized": False, 
        "face_found": False,
        "has_eyes": has_eyes, 
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
    new_time = data.get("time")
    new_date = data.get("date")
    new_status = data.get("status")
    
    if not all([record_id, new_time, new_date, new_status]):
        return jsonify({"success": False, "msg": "Missing fields"}), 400
        
    conn = get_db_connection()
    conn.execute('''
        UPDATE attendance 
        SET time = ?, date = ?, status = ?
        WHERE id = ?
    ''', (new_time, new_date, new_status, record_id))
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "msg": "Attendance updated successfully."})


@app.route("/api/manual_attendance", methods=["POST"])
def api_manual_attendance():
    """Admin can manually mark attendance for an employee."""
    if session.get("role") != "admin":
        return jsonify({"success": False, "msg": "Unauthorized"}), 401
    
    data = request.json
    user_id = data.get("user_id")
    status = data.get("status", "Present")
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    time_str = data.get("time", datetime.now().strftime("%H:%M:%S"))
    
    if not user_id:
        return jsonify({"success": False, "msg": "Employee not specified"}), 400
    
    conn = get_db_connection()
    
    # Check if already marked
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


@app.route("/api/search_employees")
def api_search_employees():
    """Search employees by name or ID."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"results": []})
    
    conn = get_db_connection()
    results = conn.execute('''
        SELECT id, employee_id, name, department, face_registered 
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
    """Live attendance feed for AJAX polling."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db_connection()
    
    records = conn.execute('''
        SELECT attendance.time, attendance.status, attendance.method,
               users.name, users.employee_id, users.department
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
            "method": r["method"] or "Face Recognition"
        })
    
    conn.close()
    return jsonify({"feed": feed})


@app.route("/api/system_status")
def api_system_status():
    """System health status for dashboard."""
    db_exists = os.path.exists(Config.DATABASE_URI)
    
    conn = get_db_connection()
    total_faces = conn.execute("SELECT COUNT(*) FROM users WHERE face_registered = 1").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'employee'").fetchone()[0]
    conn.close()
    
    return jsonify({
        "camera": {"status": "Online", "detail": "All cameras operational"},
        "ai": {"status": "Active", "detail": f"Deep Learning Engine • {total_faces}/{total_users} faces enrolled"},
        "database": {"status": "Healthy" if db_exists else "Error", "detail": f"Last sync: {datetime.now().strftime('%I:%M %p')}"}
    })


if __name__ == "__main__":
    app.run(debug=True)
