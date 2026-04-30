from flask import Flask, request, jsonify, render_template, Response, session, redirect, url_for, send_from_directory
import os
import sqlite3
import csv
import io
import secrets
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.utils import secure_filename
from datetime import datetime as dt, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "desktime_premium_secret" 

# --- CONFIGURATION ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
AGENT_FOLDER = os.path.join(BASE_DIR, 'static', 'agent')

if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(AGENT_FOLDER): os.makedirs(AGENT_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

app_config = { 'screenshot_interval': 10, 'idle_timeout': 30, 'app_version': '5.0 INVITE-ONLY SAAS' }

# =========================================================================
# 🔴 YAHAN APNA GMAIL AUR APP PASSWORD DAALEIN 🔴
# =========================================================================
GMAIL_ID = "princekumarsapariya23@gnu.ac.in"
GMAIL_APP_PASSWORD = "lppl lhlv wcgn lieg"


# --- DATABASE LOGIC ---
def get_db_connection():
    db_path = os.path.join(BASE_DIR, 'desktime.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS activity_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, date TEXT, time TEXT, status TEXT, app_name TEXT)''')
    try: conn.execute("ALTER TABLE activity_logs ADD COLUMN project_name TEXT DEFAULT 'General Work'")
    except: pass
    try: conn.execute("ALTER TABLE activity_logs ADD COLUMN productivity_status TEXT DEFAULT 'neutral'")
    except: pass
    try: conn.execute("ALTER TABLE activity_logs ADD COLUMN activity_pct INTEGER DEFAULT 0")
    except: pass

    conn.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'admin')''')
    try: conn.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'approved'")
    except: pass
    try: conn.execute("ALTER TABLE users ADD COLUMN reports_to TEXT")
    except: pass

    conn.execute('''CREATE TABLE IF NOT EXISTS user_settings (username TEXT PRIMARY KEY, screenshots_enabled INTEGER DEFAULT 1)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS app_settings (id INTEGER PRIMARY KEY AUTOINCREMENT, app_pattern TEXT UNIQUE, category TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, status TEXT DEFAULT 'active')''')
    conn.execute('''CREATE TABLE IF NOT EXISTS manual_time_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, date TEXT, duration_minutes INTEGER, project_name TEXT, reason TEXT, added_by TEXT, added_on TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS invitations (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, role TEXT, reports_to TEXT, token TEXT UNIQUE, status TEXT DEFAULT 'pending', created_at TEXT)''')

    try:
        conn.execute("INSERT INTO users (username, password, role, status) VALUES ('admin', 'admin123', 'admin', 'approved')")
        default_apps = [('code', 'productive'), ('studio', 'productive'), ('slack', 'productive'), ('youtube', 'unproductive')]
        conn.executemany("INSERT OR IGNORE INTO app_settings (app_pattern, category) VALUES (?, ?)", default_apps)
        conn.execute("INSERT OR IGNORE INTO projects (name, status) VALUES ('General Work', 'active')")
    except: pass
    conn.execute('CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('theme', 'light')")
    conn.commit(); conn.close()

init_db()

def get_my_team(username, role):
    conn = get_db_connection(); team = []
    if role == 'admin':
        rows = conn.execute("SELECT username FROM users WHERE username != ?", (username,)).fetchall()
        team = [r['username'] for r in rows]
    elif role == 'hr':
        managers = conn.execute("SELECT username FROM users WHERE role = 'manager' AND reports_to = ?", (username,)).fetchall()
        manager_usernames = [m['username'] for m in managers]
        team.extend(manager_usernames)
        if manager_usernames:
            placeholders = ','.join(['?'] * len(manager_usernames))
            emps = conn.execute(f"SELECT username FROM users WHERE role = 'employee' AND reports_to IN ({placeholders})", manager_usernames).fetchall()
            team.extend([e['username'] for e in emps])
    elif role == 'manager':
        emps = conn.execute("SELECT username FROM users WHERE role = 'employee' AND reports_to = ?", (username,)).fetchall()
        team = [e['username'] for e in emps]
    conn.close()
    return team

def get_productivity_status(app_name):
    conn = get_db_connection()
    row = conn.execute("SELECT category FROM app_settings WHERE ? LIKE '%' || app_pattern || '%'", (app_name.lower(),)).fetchone()
    conn.close()
    return row['category'] if row else 'neutral'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session: return redirect(url_for('login'))
            user_role = session.get('role', 'employee')
            if user_role not in allowed_roles:
                if user_role == 'employee': return redirect(url_for('my_dashboard', user_id=session['user_id']))
                else: return "ACCESS DENIED.", 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_stats_data(user_id=None, query_date=None, team_users=None):
    global app_config
    conn = get_db_connection()
    if not query_date: query_date = dt.now().strftime("%Y-%m-%d")
    interval = int(app_config.get('screenshot_interval', 10))
    
    query = "SELECT status, productivity_status, COUNT(*) as cnt FROM activity_logs WHERE date = ?"
    params = [query_date]
    if user_id: query += " AND user_id = ?"; params.append(user_id)
    elif team_users is not None:
        if not team_users: return {"total_time": "0h 0m", "idle_time": "0h 0m", "active_pct": 0, "active_users": 0, "prod_count": 0, "unprod_count": 0, "neutral_count": 0, "query_date": query_date, "chart_labels": ['No Data'], "chart_prod": [0], "chart_unprod": [0], "chart_neutral": [0]}
        placeholders = ','.join(['?'] * len(team_users)); query += f" AND user_id IN ({placeholders})"; params.extend(team_users)
        
    query += " GROUP BY status, productivity_status"
    rows = conn.execute(query, params).fetchall()
    active = 0; idle = 0; prod = 0; unprod = 0; neutral = 0
    for r in rows:
        status_val = str(r['status']).strip().lower()
        if status_val == 'active': active += r['cnt']
        elif status_val == 'idle': idle += r['cnt']
        prod_val = str(r['productivity_status']).strip().lower()
        if prod_val == 'productive': prod += r['cnt']
        elif prod_val == 'unproductive': unprod += r['cnt']
        else: neutral += r['cnt']

    total = active + idle; active_pct = round((active / total) * 100) if total > 0 else 0
    sec = total * interval; idle_sec = idle * interval

    hourly_q = "SELECT substr(time, 1, 2) as hour, productivity_status, COUNT(*) as cnt FROM activity_logs WHERE date = ?"
    h_params = [query_date]
    if user_id: hourly_q += " AND user_id = ?"; h_params.append(user_id)
    elif team_users is not None and team_users:
        placeholders = ','.join(['?'] * len(team_users)); hourly_q += f" AND user_id IN ({placeholders})"; h_params.extend(team_users)
        
    h_rows = conn.execute(hourly_q + " GROUP BY hour, productivity_status", h_params).fetchall()
    hourly_data = {f"{i:02d}": {'productive': 0, 'unproductive': 0, 'neutral': 0} for i in range(24)}
    for r in h_rows:
        h = r['hour']; p_stat = str(r['productivity_status']).strip().lower()
        if h in hourly_data and p_stat in hourly_data[h]: hourly_data[h][p_stat] += (r['cnt'] * interval) / 60.0 

    chart_labels = []; chart_prod = []; chart_unprod = []; chart_neutral = []
    for i in range(24):
        h = f"{i:02d}"
        p, u, n = hourly_data[h]['productive'], hourly_data[h]['unproductive'], hourly_data[h]['neutral']
        if p > 0 or u > 0 or n > 0: 
            chart_labels.append(f"{h}:00"); chart_prod.append(round(p, 1)); chart_unprod.append(round(u, 1)); chart_neutral.append(round(n, 1))

    if not chart_labels: chart_labels = ['No Data']; chart_prod = [0]; chart_unprod = [0]; chart_neutral = [0]
    act_q = "SELECT COUNT(DISTINCT user_id) FROM activity_logs WHERE date = ? AND time >= ? AND lower(status) != 'offline'"
    a_params = [query_date, (dt.now() - timedelta(seconds=30)).strftime("%H:%M:%S")]
    if team_users is not None and team_users:
        placeholders = ','.join(['?'] * len(team_users)); act_q += f" AND user_id IN ({placeholders})"; a_params.extend(team_users)
        
    active_users = conn.execute(act_q, a_params).fetchone()[0]; conn.close()
    return {"total_time": f"{sec // 3600}h {(sec % 3600) // 60}m", "idle_time": f"{idle_sec // 3600}h {(idle_sec % 3600) // 60}m", "active_pct": active_pct, "active_users": active_users, "prod_count": prod, "unprod_count": unprod, "neutral_count": neutral, "query_date": query_date, "chart_labels": chart_labels, "chart_prod": chart_prod, "chart_unprod": chart_unprod, "chart_neutral": chart_neutral}

# --- GMAIL SMTP LOGIC (Naya aur Aasaan) ---
def send_invite_email(to_email, invite_link, role):
    print(f"\n==============================================")
    print(f"📧 EMAIL INITIATED FOR: {to_email}")
    print(f"==============================================\n")

    if "yahan_apna_email_dalo" in GMAIL_ID:
        print("⚠️ ERROR: Bhai pehle GMAIL_ID aur GMAIL_APP_PASSWORD dalo server.py me!")
        return
        
    try:
        msg = MIMEMultipart()
        msg['From'] = f"DeskTime Admin <{GMAIL_ID}>"
        msg['To'] = to_email
        msg['Subject'] = f"Invitation to join DeskTime Workspace as {role.upper()}"

        html_content = f"""
        <div style='font-family:Arial,sans-serif; padding:20px; background-color:#f4f7f6; border-radius:10px;'>
            <h2 style='color:#00d4aa;'>Welcome to DeskTime!</h2>
            <p>You have been invited by the Admin to join the secure workspace.</p><br>
            <a href='{invite_link}' style='background:#00d4aa;color:#1a1d29;padding:12px 25px;text-decoration:none;border-radius:8px;font-weight:bold;display:inline-block;'>Set Up My Account</a><br><br>
            <p style='font-size:12px;color:#888;margin-top:20px;'>This link is secure and will expire in 24 hours.</p>
        </div>
        """
        msg.attach(MIMEText(html_content, 'html'))

        # Gmail ka server connect kar rahe hain
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        
        # Space hata kar login kar rahe hain (agar galti se space copy ho gaya ho)
        clean_password = GMAIL_APP_PASSWORD.replace(" ", "")
        server.login(GMAIL_ID, clean_password)
        
        text = msg.as_string()
        server.sendmail(GMAIL_ID, to_email, text)
        server.quit()
        
        print("✅ BINGO! Email sent successfully via GMAIL!")
    except smtplib.SMTPAuthenticationError:
        print("❌ GMAIL LOGIN ERROR: Aapka App Password galat hai ya 2-Step Verification OFF hai.")
    except Exception as e: 
        print(f"❌ Error sending email: {e}")

# --- AUTH & INVITE ROUTES ---
@app.route('/signup')
def signup():
    return render_template('login.html', error="Registration is strictly Invite-Only. Contact your HR/Admin.")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u, p = request.form['username'], request.form['password']
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', (u, p)).fetchone()
        conn.close()
        if user:
            user_status = user['status'] if 'status' in user.keys() else 'approved'
            if user_status != 'approved': return render_template('login.html', error="Your account is not approved.")
            session['user_id'] = user['username']; session['role'] = user['role']
            if user['role'] == 'employee': return redirect(url_for('my_dashboard', user_id=user['username']))
            return redirect(url_for('home'))
        return render_template('login.html', error="Invalid Credentials!")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/api/send_invite', methods=['POST'])
@role_required('admin', 'hr', 'manager')
def send_invite():
    data = request.json
    email = data.get('email', '').strip().lower()
    role = data.get('role', 'employee').lower()
    reports_to = session.get('user_id') if session.get('role') != 'admin' else 'admin'
    
    if not email: return jsonify({"status": "error", "msg": "Email is required"})
    
    token = secrets.token_urlsafe(32)
    invite_link = url_for('setup_account', token=token, _external=True)
    
    conn = get_db_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO invitations (email, role, reports_to, token, created_at) VALUES (?, ?, ?, ?, ?)", (email, role, reports_to, token, dt.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        send_invite_email(email, invite_link, role)
        res = {"status": "success"}
    except Exception as e:
        res = {"status": "error", "msg": str(e)}
    conn.close()
    return jsonify(res)

@app.route('/setup-account/<token>', methods=['GET', 'POST'])
def setup_account(token):
    conn = get_db_connection()
    invite = conn.execute("SELECT * FROM invitations WHERE token = ? AND status = 'pending'", (token,)).fetchone()
    
    if not invite:
        conn.close(); return "<h1>Invalid or Expired Invite Link.</h1>"
        
    if request.method == 'POST':
        username = request.form['username'].strip().replace(" ", "_")
        password = request.form['password']
        
        try:
            conn.execute("INSERT INTO users (username, password, role, status, reports_to) VALUES (?, ?, ?, 'approved', ?)", (username, password, invite['role'], invite['reports_to']))
            conn.execute("UPDATE invitations SET status = 'used' WHERE id = ?", (invite['id'],))
            conn.commit()
            
            session['user_id'] = username; session['role'] = invite['role']; conn.close()
            if invite['role'] == 'employee': return redirect(url_for('my_dashboard', user_id=username))
            return redirect(url_for('home'))
            
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('setup_account.html', invite=invite, error="Username is already taken. Please choose another.")
            
    conn.close()
    return render_template('setup_account.html', invite=invite, error=None)

# --- UI ROUTES ---
@app.route('/')
@role_required('admin', 'hr', 'manager')
def home():
    current_role = session.get('role', 'employee'); current_user = session.get('user_id')
    team_users = get_my_team(current_user, current_role); stats = get_stats_data(team_users=team_users)
    conn = get_db_connection(); today = dt.now().strftime("%Y-%m-%d"); recent = []
    if team_users:
        placeholders = ','.join(['?'] * len(team_users))
        query = f"SELECT id, time, status, app_name, project_name, activity_pct, user_id FROM activity_logs WHERE date = ? AND user_id IN ({placeholders}) ORDER BY id DESC LIMIT 5"
        recent = conn.execute(query, [today] + team_users).fetchall()
    conn.close()
    return render_template('dashboard.html', **stats, recent_logs=recent)

@app.route('/employees')
@role_required('admin', 'hr', 'manager')
def employees():
    global app_config; interval = int(app_config.get('screenshot_interval', 10))
    conn = get_db_connection(); today = dt.now().strftime("%Y-%m-%d")
    current_role = session.get('role', 'employee'); current_user = session.get('user_id')
    team_users = get_my_team(current_user, current_role)

    if not team_users: conn.close(); return render_template('employees.html', users=[])
    placeholders = ','.join(['?'] * len(team_users))
    
    query = f"""
    SELECT u.username as name, u.role, u.status as user_status, u.reports_to, IFNULL(SUM(CASE WHEN lower(a.status)='active' THEN 1 ELSE 0 END), 0) as active_beats, IFNULL(COUNT(a.id), 0) as total_beats, IFNULL(AVG(a.activity_pct), 0) as avg_act, MAX(a.time) as last_seen, IFNULL(s.screenshots_enabled, 1) as ss_enabled
    FROM users u LEFT JOIN activity_logs a ON u.username = a.user_id AND a.date = ? LEFT JOIN user_settings s ON u.username = s.username
    WHERE u.status = 'approved' AND u.username IN ({placeholders}) GROUP BY u.username
    """
    cursor = conn.execute(query, [today] + team_users); users_data = []; now = dt.now()
    for row in cursor.fetchall():
        sec = row['total_beats'] * interval; avg_activity = int(row['avg_act'])
        last_seen_str = row['last_seen']; is_online = False
        if last_seen_str:
            try:
                if (now - dt.strptime(f"{today} {last_seen_str}", "%Y-%m-%d %H:%M:%S")).total_seconds() <= 40: is_online = True
            except: pass
        users_data.append({'name': row['name'], 'role': row['role'], 'reports_to': row['reports_to'], 'time': f"{sec // 3600}h {(sec % 3600) // 60}m", 'productivity': round((row['active_beats']/row['total_beats'])*100) if row['total_beats'] > 0 else 0, 'avg_activity': avg_activity, 'ss_enabled': bool(row['ss_enabled']), 'is_online': is_online, 'last_seen': last_seen_str})
    conn.close()
    return render_template('employees.html', users=users_data)

@app.route('/employee/<user_id>')
@role_required('admin', 'hr', 'manager')
def employee_detail(user_id):
    stats = get_stats_data(user_id); conn = get_db_connection(); today = dt.now().strftime("%Y-%m-%d")
    row = conn.execute("SELECT screenshots_enabled FROM user_settings WHERE username = ?", (user_id,)).fetchone()
    ss_enabled = True if (row is None or row['screenshots_enabled'] == 1) else False
    logs = conn.execute("SELECT time, status, app_name, project_name, activity_pct, productivity_status FROM activity_logs WHERE date=? AND user_id=? ORDER BY id DESC LIMIT 50", (today, user_id)).fetchall(); conn.close()
    screenshots = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        screenshots = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if f.startswith(user_id)]; screenshots.sort(reverse=True)
    return render_template('employee_detail.html', user_id=user_id, **stats, user_logs=logs, screenshots=screenshots, ss_enabled=ss_enabled)

@app.route('/download_agent')
@login_required
def download_agent():
    try: return send_from_directory(AGENT_FOLDER, 'DeskTime_Agent.exe', as_attachment=True)
    except FileNotFoundError: return "Agent executable not found on server!", 404

@app.route('/timesheets', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'manager')
def timesheets():
    conn = get_db_connection(); global app_config; interval = int(app_config.get('screenshot_interval', 10))
    filter_date = request.form.get('filter_date') if request.method == 'POST' else dt.now().strftime("%Y-%m-%d")
    current_role = session.get('role', 'employee'); current_user = session.get('user_id')
    team_users = get_my_team(current_user, current_role)
    if not team_users: conn.close(); return render_template('timesheets.html', timesheet_data=[], filter_date=filter_date, users=[], projects=[], manual_entries=[])

    placeholders = ','.join(['?'] * len(team_users))
    all_users = [r['username'] for r in conn.execute(f"SELECT username FROM users WHERE status='approved' AND username IN ({placeholders})", team_users).fetchall()]
    active_projects = [r['name'] for r in conn.execute("SELECT name FROM projects WHERE status='active'").fetchall()]

    query = f"SELECT a.user_id, u.role, SUM(CASE WHEN lower(a.status)='active' THEN 1 ELSE 0 END) as active_beats, COUNT(a.id) as total_beats FROM activity_logs a JOIN users u ON a.user_id = u.username WHERE a.date = ? AND u.username IN ({placeholders}) GROUP BY a.user_id"
    cursor = conn.execute(query, [filter_date] + team_users); timesheet_data = []
    for row in cursor.fetchall():
        sec = row['total_beats'] * interval; act_sec = row['active_beats'] * interval
        timesheet_data.append({'name': row['user_id'], 'role': row['role'], 'total_time': f"{sec // 3600}h {(sec % 3600) // 60}m", 'active_time': f"{act_sec // 3600}h {(act_sec % 3600) // 60}m", 'productivity': round((row['active_beats']/row['total_beats'])*100) if row['total_beats'] > 0 else 0})
        
    manual_entries = conn.execute("SELECT * FROM manual_time_entries WHERE date = ? ORDER BY id DESC", (filter_date,)).fetchall()
    conn.close()
    return render_template('timesheets.html', timesheet_data=timesheet_data, filter_date=filter_date, users=all_users, projects=active_projects, manual_entries=manual_entries)

@app.route('/add_manual_time', methods=['POST'])
@role_required('admin', 'hr', 'manager')
def add_manual_time():
    global app_config; interval = int(app_config.get('screenshot_interval', 10))
    user_id = request.form['user_id']; date = request.form['date']; minutes = int(request.form['minutes'])
    project = request.form['project']; reason = request.form['reason']; total_seconds = minutes * 60; beats_to_add = total_seconds // interval
    conn = get_db_connection()
    conn.execute("INSERT INTO manual_time_entries (user_id, date, duration_minutes, project_name, reason, added_by, added_on) VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, date, minutes, project, reason, session['user_id'], dt.now().strftime("%Y-%m-%d %H:%M:%S")))
    prod_status = get_productivity_status("Manual Time Addition") 
    payloads = [(user_id, date, "Manual Entry", 'active', "Manual Time Addition", project, prod_status, 100)] * beats_to_add
    conn.executemany('INSERT INTO activity_logs (user_id, date, time, status, app_name, project_name, productivity_status, activity_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', payloads)
    conn.commit(); conn.close()
    return redirect(url_for('timesheets'))

@app.route('/reports', methods=['GET', 'POST'])
@role_required('admin', 'hr', 'manager')
def reports():
    conn = get_db_connection(); filter_date = request.form.get('filter_date') if request.method == 'POST' else dt.now().strftime("%Y-%m-%d")
    selected_user = request.form.get('selected_user', 'all'); current_role = session.get('role', 'employee'); current_user = session.get('user_id')
    team_users = get_my_team(current_user, current_role)
    if not team_users: conn.close(); return render_template('reports.html', logs=[], date=filter_date, team_details=[], selected_user='all')
    target_users = [selected_user] if selected_user != 'all' and selected_user in team_users else team_users
    placeholders = ','.join(['?'] * len(target_users))
    logs = conn.execute(f"SELECT a.time, a.user_id, a.app_name, a.project_name, a.status, a.productivity_status, a.activity_pct FROM activity_logs a JOIN users u ON a.user_id = u.username WHERE a.date = ? AND u.username IN ({placeholders}) ORDER BY a.id DESC", [filter_date] + target_users).fetchall()
    team_details = conn.execute(f"SELECT username, role, reports_to FROM users WHERE username IN ({','.join(['?'] * len(team_users))}) ORDER BY role, username", team_users).fetchall()
    conn.close()
    return render_template('reports.html', logs=logs, date=filter_date, team_details=team_details, selected_user=selected_user)

@app.route('/settings', methods=['GET', 'POST'])
@role_required('admin')
def settings():
    global app_config
    if request.method == 'POST':
        app_config['screenshot_interval'] = int(request.json.get('screenshot_interval', 10))
        app_config['idle_timeout'] = int(request.json.get('idle_timeout', 30))
        return jsonify({"status": "success"})
    return render_template('settings.html', config=app_config)

@app.route('/categories', methods=['GET', 'POST'])
@role_required('admin')
def categories():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute("INSERT OR REPLACE INTO app_settings (app_pattern, category) VALUES (?, ?)", (request.form['pattern'].lower(), request.form['category'].lower())); conn.commit()
    apps = conn.execute("SELECT * FROM app_settings").fetchall(); conn.close()
    return render_template('categories.html', apps=apps)

@app.route('/delete_category/<int:category_id>', methods=['POST'])
@role_required('admin')
def delete_category(category_id):
    conn = get_db_connection(); conn.execute("DELETE FROM app_settings WHERE id = ?", (category_id,)); conn.commit(); conn.close(); return redirect(url_for('categories'))

@app.route('/projects', methods=['GET', 'POST'])
@role_required('admin', 'manager')
def projects():
    conn = get_db_connection()
    if request.method == 'POST': conn.execute("INSERT OR IGNORE INTO projects (name, status) VALUES (?, 'active')", (request.form['name'],)); conn.commit()
    active = conn.execute("SELECT * FROM projects WHERE status = 'active'").fetchall()
    completed = conn.execute("SELECT * FROM projects WHERE status = 'completed'").fetchall()
    conn.close()
    return render_template('projects.html', active_projects=active, completed_projects=completed)

@app.route('/complete_project/<int:project_id>', methods=['POST'])
@role_required('admin', 'manager')
def complete_project(project_id): conn = get_db_connection(); conn.execute("UPDATE projects SET status = 'completed' WHERE id = ?", (project_id,)); conn.commit(); conn.close(); return redirect(url_for('projects'))

@app.route('/delete_project/<int:project_id>', methods=['POST'])
@role_required('admin', 'manager')
def delete_project(project_id): conn = get_db_connection(); conn.execute("DELETE FROM projects WHERE id = ?", (project_id,)); conn.commit(); conn.close(); return redirect(url_for('projects'))

@app.route('/export/csv')
@role_required('admin', 'hr', 'manager')
def export_csv():
    conn = get_db_connection(); today = dt.now().strftime("%Y-%m-%d"); team_users = get_my_team(session.get('user_id'), session.get('role', 'employee'))
    if not team_users: return Response("", mimetype="text/csv")
    cursor = conn.execute(f"SELECT a.date, a.time, a.user_id, a.project_name, a.app_name, a.status, a.productivity_status, a.activity_pct FROM activity_logs a JOIN users u ON a.user_id = u.username WHERE a.date=? AND u.username IN ({','.join(['?'] * len(team_users))})", [today] + team_users)
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(['Date', 'Time', 'Employee ID', 'Project', 'Application', 'Status', 'Productivity Category', 'Activity %'])
    for r in cursor.fetchall(): writer.writerow([r['date'], r['time'], r['user_id'], r['project_name'], r['app_name'], r['status'], r['productivity_status'], f"{r['activity_pct']}%"])
    conn.close(); output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-disposition": f"attachment; filename=DeskTime_Report_{today}.csv"})

@app.route('/delete_employee/<user_id>', methods=['POST'])
@role_required('admin')
def delete_employee(user_id): conn = get_db_connection(); conn.execute("DELETE FROM activity_logs WHERE user_id = ?", (user_id,)); conn.execute("DELETE FROM users WHERE username = ?", (user_id,)); conn.commit(); conn.close(); return redirect(url_for('employees'))

@app.route('/clear_all_logs', methods=['POST'])
@role_required('admin')
def clear_all_logs(): conn = get_db_connection(); conn.execute("DELETE FROM activity_logs"); conn.commit(); conn.close(); return redirect(url_for('home'))

@app.route('/delete_log/<int:log_id>', methods=['POST'])
@role_required('admin', 'manager')
def delete_log(log_id): conn = get_db_connection(); conn.execute("DELETE FROM activity_logs WHERE id = ?", (log_id,)); conn.commit(); conn.close(); return redirect(request.referrer or url_for('home'))

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    data = request.json; conn = get_db_connection()
    conn.execute('INSERT INTO activity_logs (user_id, date, time, status, app_name, project_name, productivity_status, activity_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (data.get('user_id', 'admin_user'), dt.now().strftime("%Y-%m-%d"), dt.now().strftime("%H:%M:%S"), data.get('status', 'active').strip().lower(), data.get('app_name', 'Unknown'), data.get('project_name', 'General Work'), get_productivity_status(data.get('app_name', 'Unknown')), data.get('activity_pct', 0)))
    conn.commit(); conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/upload', methods=['POST'])
def upload_data():
    file = request.files.get('screenshot')
    if file: file.save(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))); return jsonify({"status": "ok"})
    return jsonify({"status": "no file"}), 400

@app.route('/api/dashboard_stats')
@role_required('admin', 'hr', 'manager')
def dashboard_stats_api():
    team_users = get_my_team(session.get('user_id'), session.get('role', 'employee'))
    stats = get_stats_data(team_users=team_users); conn = get_db_connection(); today = dt.now().strftime("%Y-%m-%d"); recent = []
    if team_users: recent = conn.execute(f"SELECT id, time, status, app_name, project_name, activity_pct, user_id FROM activity_logs WHERE date = ? AND user_id IN ({','.join(['?'] * len(team_users))}) ORDER BY id DESC LIMIT 5", [today] + team_users).fetchall()
    conn.close(); stats["recent_logs"] = [{"id": r["id"], "time": r["time"], "status": r["status"], "app_name": r["app_name"], "project_name": r["project_name"], "activity_pct": r["activity_pct"], "user_id": r["user_id"]} for r in recent]
    return jsonify(stats)

@app.route('/my-dashboard/<user_id>')
@login_required
def my_dashboard(user_id):
    if session.get('role') == 'employee' and session.get('user_id') != user_id: return redirect(url_for('my_dashboard', user_id=session['user_id']))
    stats = get_stats_data(user_id); conn = get_db_connection(); recent = conn.execute("SELECT time, status, app_name, project_name, activity_pct FROM activity_logs WHERE date = ? AND user_id = ? ORDER BY id DESC LIMIT 5", (dt.now().strftime("%Y-%m-%d"), user_id)).fetchall(); conn.close()
    return render_template('my_dashboard.html', user_id=user_id, **stats, recent_logs=recent)

@app.route('/api/agent_login', methods=['POST'])
def agent_login():
    data = request.json; conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ? AND status="approved"', (data.get('username'), data.get('password'))).fetchone(); conn.close()
    if user: return jsonify({"status": "success", "user_id": user['username'], "role": user['role'], "msg": "Login successful"})
    return jsonify({"status": "error", "msg": "Invalid credentials or account not approved!"}), 401

@app.route('/api/update_theme', methods=['POST'])
@role_required('admin')
def update_theme(): conn = get_db_connection(); conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('theme', ?)", (request.json.get('theme', 'light'),)); conn.commit(); conn.close(); return jsonify({"status": "success"})

@app.route('/api/get_config', methods=['GET'])
def get_config():
    conn = get_db_connection(); row = conn.execute("SELECT screenshots_enabled FROM user_settings WHERE username = ?", (request.args.get('user_id', 'unknown'),)).fetchone()
    projects = [r['name'] for r in conn.execute("SELECT name FROM projects WHERE status = 'active'").fetchall()]; theme_row = conn.execute("SELECT value FROM system_settings WHERE key='theme'").fetchone()
    conn.close(); response_data = app_config.copy()
    response_data.update({'projects': projects, 'screenshots_enabled': True if (row is None or row['screenshots_enabled'] == 1) else False, 'theme': theme_row[0] if theme_row else 'light'})
    return jsonify(response_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5000)