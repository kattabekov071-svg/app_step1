import os
import io
import random
import string
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import hashlib
import secrets
import uvicorn
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

logging.basicConfig(level=logging.DEBUG)
app = FastAPI()

# ==================== НАСТРОЙКИ ====================
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "your_email@gmail.com"
SMTP_PASSWORD = "your_app_password"
ADMIN_EMAIL = "admin@xarid.uz"

TELEGRAM_TOKEN = "ВАШ_ТОКЕН"           # ← замените
TELEGRAM_CHAT_ID = "ВАШ_CHAT_ID"       # ← замените

# ==================== БАЗА ДАННЫХ ====================
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.strip() != ""

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    logging.info("✅ Используется PostgreSQL")
else:
    import sqlite3
    logging.info("✅ Используется SQLite (локально)")

def get_db_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect("tender.db")
    conn.row_factory = sqlite3.Row
    return conn

def execute_query(conn, query, params=None):
    cur = conn.cursor()
    params = params or ()
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        cur.execute(query, params)
    else:
        cur.execute(query, params)
    return cur

def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"pbkdf2_sha256$120000${salt}${digest.hex()}"

def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return secrets.compare_digest(password, stored)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return secrets.compare_digest(check.hex(), digest)
    except Exception:
        return False

def send_email(to, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        if SMTP_USER == "your_email@gmail.com" and SMTP_PASSWORD == "your_app_password":
            print(f"\n[DEBUG EMAIL] To: {to}\nSubject: {subject}\nBody:\n{body}\n")
            return True
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Ошибка почты: {e}")
        return False

def send_telegram(message):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "ваш_токен":
        print("⚠️ Telegram не настроен")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        )
        print(f"📨 Telegram: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram: {e}")
        return False

def send_admin_notification(subject, body):
    send_telegram(f"🔔 <b>{subject}</b>\n\n{body}")
    send_email(ADMIN_EMAIL, subject, body)

def log_action(username, action, request):
    try:
        conn = get_db_connection()
        ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")
        execute_query(conn,
            "INSERT INTO user_logs (username, action, ip, user_agent) VALUES (?, ?, ?, ?)",
            (username, action, ip, user_agent)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка логирования: {e}")

def get_current_user(request):
    session_user = request.cookies.get("session_user")
    if not session_user:
        return None
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM users WHERE username = ?", (session_user,))
    user = cur.fetchone()
    conn.close()
    return user

def init_db():
    conn = get_db_connection()
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS deals (
            id SERIAL PRIMARY KEY, category TEXT, date TEXT, model TEXT,
            quantity INTEGER DEFAULT 1, start_price REAL, agreed_price REAL,
            upload_id INTEGER, UNIQUE(date, model, agreed_price)
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS uploads (
            id SERIAL PRIMARY KEY, filename TEXT, category TEXT,
            rows_count INTEGER, upload_date TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE, email TEXT UNIQUE,
            password TEXT, role TEXT, expires_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS user_logs (
            id SERIAL PRIMARY KEY, username TEXT, action TEXT,
            ip TEXT, user_agent TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY, email TEXT, token TEXT, expires_at TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS payment_requests (
            id SERIAL PRIMARY KEY, username TEXT, months INTEGER,
            receipt TEXT, status TEXT, created_at TEXT
        )''')
        cur.execute("SELECT COUNT(*) AS count FROM users")
        if cur.fetchone()["count"] == 0:
            cur.execute(
                "INSERT INTO users (username, email, password, role, expires_at) VALUES (%s, %s, %s, %s, %s)",
                ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31")
            )
        conn.commit()
    else:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, date TEXT, model TEXT,
            quantity INTEGER, start_price REAL, agreed_price REAL,
            upload_id INTEGER, UNIQUE(date, model, agreed_price)
        )''')
        cur.execute("PRAGMA table_info(deals)")
        if 'quantity' not in [col[1] for col in cur.fetchall()]:
            cur.execute("ALTER TABLE deals ADD COLUMN quantity INTEGER DEFAULT 1")
        cur.execute('''CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, category TEXT,
            rows_count INTEGER, upload_date TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, email TEXT UNIQUE,
            password TEXT, role TEXT, expires_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS user_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT,
            ip TEXT, user_agent TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, token TEXT, expires_at TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, months INTEGER,
            receipt TEXT, status TEXT, created_at TEXT
        )''')
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, ?, ?)",
                       ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31"))
        conn.commit()
    conn.close()

init_db()

# ==================== ШАБЛОНЫ ====================
LANDING_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>XARID ANALYTICS</title><style>body{background:#0d1117;color:#c9d1d9;font-family:sans-serif;padding:16px}</style></head><body><h1>Добро пожаловать в XARID ANALYTICS</h1><p>Мониторинг тендеров xarid.uzex.uz</p><button onclick="openLoginModal()">Войти</button><button onclick="openRegisterModal()">Регистрация</button><script>function openLoginModal(){alert('Вход (в реальном сайте будет форма)')}function openRegisterModal(){alert('Регистрация (в реальном сайте будет форма)')}</script></body></html>"""
UI_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Терминал</title><style>body{background:#131722;color:#d1d4dc;font-size:15px}</style></head><body><h1>XARID ANALYTICS</h1><p>Терминал (здесь будут все данные)</p><a href="/profile">Профиль</a> | <a href="/logout">Выйти</a></body></html>"""

# ==================== ЭНДПОИНТЫ ====================
@app.get("/", response_class=HTMLResponse)
def landing_page(request: Request):
    user = get_current_user(request)
    if user: return RedirectResponse("/app", 303)
    return HTMLResponse(LANDING_TEMPLATE)

@app.get("/app", response_class=HTMLResponse)
def terminal(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/", 303)
    log_action(user["username"], "Просмотр страницы", request)
    return HTMLResponse(UI_TEMPLATE)

@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = get_current_user(request)
    if not user: return RedirectResponse("/", 303)
    days_left = "∞" if user["role"] == "admin" else (datetime.strptime(user["expires_at"], "%Y-%m-%d") - datetime.now()).days
    status = "Безлимит" if user["role"] == "admin" else ("Активна" if days_left >= 0 else "Истекла")
    return HTMLResponse(f"""
    <html><body style="background:#131722;color:#d1d4dc;font-family:sans-serif;padding:20px">
    <h1>Профиль</h1><p>Логин: {user['username']}</p><p>Email: {user['email']}</p><p>Роль: {user['role']}</p><p>Подписка: {status}</p><p>Осталось дней: {days_left}</p>
    <a href="/app">Назад</a> | <a href="/logout">Выйти</a>
    </body></html>
    """)

@app.get("/api/check_auth")
def check_auth(request: Request):
    user = get_current_user(request)
    if not user: return {"authenticated": False, "role": "guest"}
    sub_active = user["role"] == "admin" or (datetime.strptime(user["expires_at"], "%Y-%m-%d") >= datetime.now())
    return {"authenticated": True, "role": user["role"], "username": user["username"], "sub_active": sub_active, "expires_at": user["expires_at"], "days_left": max(0, (datetime.strptime(user["expires_at"], "%Y-%m-%d") - datetime.now()).days)}

@app.post("/register")
async def register_post(username: str = Form(...), email: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    if execute_query(conn, "SELECT * FROM users WHERE username = ? OR email = ?", (username, email)).fetchone():
        conn.close(); return HTMLResponse("<script>alert('Уже существует'); window.location.href='/'</script>")
    execute_query(conn, "INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, 'client', '2026-01-01')",
                 (username, email, hash_password(password)))
    conn.commit(); conn.close()
    send_admin_notification("Новая регистрация", f"Логин: {username}\nEmail: {email}")
    response = RedirectResponse("/app", 303); response.set_cookie("session_user", username); return response

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    user = execute_query(conn, "SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not user or not verify_password(password, user["password"]):
        return HTMLResponse("<script>alert('Неверно'); window.location.href='/'</script>")
    log_action(username, "Вход", request)
    response = RedirectResponse("/app", 303); response.set_cookie("session_user", username); return response

@app.post("/api/forgot_password")
async def forgot_password(request: Request, email: str = Form(...)):
    conn = get_db_connection()
    user = execute_query(conn, "SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        execute_query(conn, "INSERT INTO password_resets (email, token, expires_at) VALUES (?, ?, ?)",
                     (email, token, (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        send_email(email, "Восстановление пароля", f"Ссылка: {request.base_url}reset_password?token={token}")
    conn.close()
    return HTMLResponse("Письмо отправлено, если email зарегистрирован")

@app.get("/reset_password", response_class=HTMLResponse)
def reset_password_page(token: str):
    return HTMLResponse(f"""
    <html><body>
    <form action="/api/do_reset_password" method="post">
    <input type="hidden" name="token" value="{token}">
    <input type="password" name="new_password" placeholder="Новый пароль" required>
    <button type="submit">Сменить</button>
    </form></body></html>
    """)

@app.post("/api/do_reset_password")
async def do_reset_password(token: str = Form(...), new_password: str = Form(...)):
    conn = get_db_connection()
    reset = execute_query(conn, "SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
    if not reset: conn.close(); return HTMLResponse("Неверный токен")
    execute_query(conn, "UPDATE users SET password = ? WHERE email = ?", (hash_password(new_password), reset["email"]))
    execute_query(conn, "DELETE FROM password_resets WHERE token = ?", (token,))
    conn.commit(); conn.close()
    return HTMLResponse("Пароль изменён")

@app.post("/api/submit_payment")
async def submit_payment(request: Request, months: int = Form(...), receipt: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse("/", 303)
    conn = get_db_connection()
    execute_query(conn, "INSERT INTO payment_requests (username, months, receipt, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                 (user["username"], months, receipt, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit(); conn.close()
    send_admin_notification("Заявка на подписку", f"Пользователь: {user['username']}\nСрок: {months} мес.\nЧек: {receipt}")
    return HTMLResponse("Чек отправлен. Ожидайте подтверждения.")

@app.post("/api/support")
async def support(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user: return RedirectResponse("/", 303)
    send_telegram(f"💬 Сообщение от {user['username']} ({user['email']}):\n\n{message}")
    return HTMLResponse("Сообщение отправлено")

@app.get("/logout")
def logout():
    response = RedirectResponse("/", 303)
    response.delete_cookie("session_user")
    return response

@app.get("/api/deals")
def get_deals(request: Request):
    user = get_current_user(request)
    if not user: return []
    conn = get_db_connection()
    deals = [dict(row) for row in execute_query(conn, "SELECT * FROM deals").fetchall()]
    conn.close()
    return deals

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": return RedirectResponse("/", 303)
    conn = get_db_connection()
    # Статистика – используем обычный курсор для агрегатов, чтобы не было ошибок
    if USE_POSTGRES:
        plain_conn = psycopg2.connect(DATABASE_URL)
        plain_cur = plain_conn.cursor()
    else:
        plain_conn = conn
        plain_cur = conn.cursor()
    plain_cur.execute("SELECT COUNT(*) FROM user_logs"); total_logs = plain_cur.fetchone()[0]
    plain_cur.execute("SELECT COUNT(DISTINCT username) FROM user_logs WHERE date(timestamp)=date('now')"); unique_today = plain_cur.fetchone()[0]
    plain_cur.execute("SELECT COUNT(DISTINCT username) FROM user_logs WHERE timestamp > datetime('now','-7 days')"); unique_week = plain_cur.fetchone()[0]
    plain_cur.execute("SELECT COUNT(*) FROM users WHERE role != 'admin' AND created_at > datetime('now','-7 days')"); new_reg = plain_cur.fetchone()[0]
    plain_cur.execute("SELECT COUNT(*) FROM payment_requests WHERE status='pending'"); pending = plain_cur.fetchone()[0]
    if USE_POSTGRES: plain_conn.close()
    # Получаем логи
    logs = execute_query(conn, "SELECT * FROM user_logs ORDER BY timestamp DESC LIMIT 20").fetchall()
    uploads = execute_query(conn, "SELECT * FROM uploads ORDER BY id DESC").fetchall()
    users_all = execute_query(conn, "SELECT * FROM users ORDER BY id DESC").fetchall()
    pay_reqs = execute_query(conn, "SELECT * FROM payment_requests ORDER BY id DESC").fetchall()
    recent_users = execute_query(conn, "SELECT * FROM users WHERE role != 'admin' ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()
    # Генерация таблиц
    def table(rows, cols):
        return "".join(f"<tr>{''.join(f'<td class=\"p-2 border border-[#2a2e39]\">{row[col]}</td>' for col in cols)}</tr>" for row in rows)
    return HTMLResponse(f"""
    <html><head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-[#131722] text-white p-6">
    <h1 class="text-2xl font-bold">Панель администратора</h1>
    <div class="grid grid-cols-3 gap-4 my-4">
        <div class="bg-[#1e222d] p-4 rounded"><h3>Новые регистрации (7д)</h3><p class="text-2xl">{new_reg}</p></div>
        <div class="bg-[#1e222d] p-4 rounded"><h3>Ожидают оплаты</h3><p class="text-2xl">{pending}</p></div>
        <div class="bg-[#1e222d] p-4 rounded"><h3>Всего пользователей</h3><p class="text-2xl">{len(users_all)}</p></div>
        <div class="bg-[#1e222d] p-4 rounded"><h3>Всего посещений</h3><p class="text-2xl">{total_logs}</p></div>
        <div class="bg-[#1e222d] p-4 rounded"><h3>Уникальных сегодня</h3><p class="text-2xl">{unique_today}</p></div>
        <div class="bg-[#1e222d] p-4 rounded"><h3>Уникальных за неделю</h3><p class="text-2xl">{unique_week}</p></div>
    </div>
    <div class="bg-[#1e222d] p-4 rounded my-4"><h2>Последние 20 действий</h2>
        <table class="w-full text-sm">{''.join(f"<tr><td>{log['timestamp']}</td><td>{log['username']}</td><td>{log['action']}</td><td>{log['ip']}</td><td>{log['user_agent'][:20]}</td></tr>" for log in logs)}</table>
    </div>
    <div><a href="/app" class="text-cyan-400">← На главную</a></div>
    <form action="/upload" method="post" enctype="multipart/form-data" class="my-4"><input type="text" name="category" placeholder="Категория" required><input type="file" name="files" accept=".xlsx,.xls" multiple required><button type="submit">Загрузить</button></form>
    <div class="bg-[#1e222d] p-4 rounded my-4"><h2>Заявки на оплату</h2>{''.join(f"<div>{p['username']} – {p['months']} мес., чек: {p['receipt']} <form style='display:inline' action='/admin/approve_payment/{p['id']}' method='post'><button>Одобрить</button></form> <form style='display:inline' action='/admin/reject_payment/{p['id']}' method='post'><button>Отклонить</button></form></div>" for p in pay_reqs)}</div>
    <div class="bg-[#1e222d] p-4 rounded my-4"><h2>Пользователи</h2>{''.join(f"<div>{u['username']} – {u['role']} до {u['expires_at']} <form style='display:inline' action='/admin/delete_user/{u['id']}' method='post'><button>Удалить</button></form></div>" for u in users_all)}</div>
    </body></html>
    """)

@app.post("/admin/approve_payment/{req_id}")
def approve_payment(req_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": raise HTTPException(403)
    conn = get_db_connection()
    req = execute_query(conn, "SELECT * FROM payment_requests WHERE id = ?", (req_id,)).fetchone()
    if req:
        days = int(req["months"]) * 30
        old = execute_query(conn, "SELECT expires_at FROM users WHERE username = ?", (req["username"],)).fetchone()
        base = datetime.now()
        if old and old["expires_at"]:
            try: base = max(base, datetime.strptime(old["expires_at"], "%Y-%m-%d"))
            except: pass
        expires = (base + timedelta(days=days)).strftime("%Y-%m-%d")
        execute_query(conn, "UPDATE users SET expires_at = ? WHERE username = ?", (expires, req["username"]))
        execute_query(conn, "DELETE FROM payment_requests WHERE id = ?", (req_id,))
        conn.commit()
        send_telegram(f"✅ Подписка одобрена для {req['username']} до {expires}")
    conn.close()
    return HTMLResponse("<script>alert('Одобрено'); window.location.href='/admin'</script>")

@app.post("/admin/reject_payment/{req_id}")
def reject_payment(req_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": raise HTTPException(403)
    conn = get_db_connection()
    req = execute_query(conn, "SELECT * FROM payment_requests WHERE id = ?", (req_id,)).fetchone()
    if req:
        send_telegram(f"❌ Отклонена заявка {req['username']}")
    execute_query(conn, "DELETE FROM payment_requests WHERE id = ?", (req_id,))
    conn.commit(); conn.close()
    return HTMLResponse("<script>alert('Удалено'); window.location.href='/admin'</script>")

@app.post("/admin/delete_user/{user_id}")
def delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": raise HTTPException(403)
    conn = get_db_connection()
    execute_query(conn, "DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
    conn.commit(); conn.close()
    return HTMLResponse("<script>alert('Удалён'); window.location.href='/admin'</script>")

@app.post("/upload")
async def upload_files(category: str = Form(...), files: list[UploadFile] = File(...), request: Request = None):
    user = get_current_user(request)
    if not user or user["role"] != "admin": raise HTTPException(403)
    conn = get_db_connection()
    for file in files:
        try:
            df = pd.read_excel(io.BytesIO(await file.read()))
            cur = execute_query(conn, "INSERT INTO uploads (filename, category, rows_count, upload_date) VALUES (?, ?, ?, ?)",
                               (file.filename, category, len(df), datetime.now().strftime("%Y-%m-%d")))
            if USE_POSTGRES:
                upload_id = execute_query(conn, "SELECT lastval()").fetchone()[0]
            else:
                upload_id = cur.lastrowid
            for _, row in df.iterrows():
                try:
                    date_val = str(row.iloc[0]).split('T')[0] if pd.notna(row.iloc[0]) else ""
                    model_val = str(row.iloc[1]) if len(row) > 1 and pd.notna(row.iloc[1]) else ""
                    qty_val = int(row.iloc[2]) if len(row) > 2 and pd.notna(row.iloc[2]) else 1
                    start_p = float(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else 0.0
                    agreed_p = float(row.iloc[4]) if len(row) > 4 and pd.notna(row.iloc[4]) else 0.0
                    if model_val and model_val.lower() != 'nan':
                        if USE_POSTGRES:
                            execute_query(conn,
                                "INSERT INTO deals (category, date, model, quantity, start_price, agreed_price, upload_id) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (date, model, agreed_price) DO NOTHING",
                                (category, date_val, model_val, qty_val, start_p, agreed_p, upload_id)
                            )
                        else:
                            execute_query(conn,
                                "INSERT OR IGNORE INTO deals (category, date, model, quantity, start_price, agreed_price, upload_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (category, date_val, model_val, qty_val, start_p, agreed_p, upload_id)
                            )
                except Exception as e:
                    print(f"Ошибка строки: {e}")
                    continue
        except Exception as e:
            print(f"Ошибка файла: {e}")
    conn.commit(); conn.close()
    return HTMLResponse("<script>alert('Файлы загружены'); window.location.href='/admin'</script>")

@app.post("/admin/delete/{upload_id}")
def delete_upload(upload_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin": raise HTTPException(403)
    conn = get_db_connection()
    execute_query(conn, "DELETE FROM deals WHERE upload_id = ?", (upload_id,))
    execute_query(conn, "DELETE FROM uploads WHERE id = ?", (upload_id,))
    conn.commit(); conn.close()
    return HTMLResponse("<script>alert('Удалено'); window.location.href='/admin'</script>")

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
