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

# === КОНФИГУРАЦИЯ ===
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "your_email@gmail.com"
SMTP_PASSWORD = "your_app_password"
ADMIN_EMAIL = "admin@xarid.uz"

TELEGRAM_TOKEN = "8925100564:AAH6GTe281eDfuzoyqBUBm_AhQ31edAscS8" 
TELEGRAM_CHAT_ID = "40209048" 

# === БАЗА ДАННЫХ ===
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL is None:
    DATABASE_URL = ""
USE_POSTGRES = DATABASE_URL.strip() != ""

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    logging.info("✅ Используется PostgreSQL")
    logging.info(f"DATABASE_URL: {DATABASE_URL[:30]}...")
else:
    import sqlite3
    logging.info("✅ Используется SQLite (локально)")

def get_db_connection():
    if USE_POSTGRES:
        if not DATABASE_URL:
            raise Exception("DATABASE_URL is empty")
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
        conn = sqlite3.connect("tender.db")
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(conn, query, params=None):
    cur = conn.cursor()
    if params is None:
        params = ()
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        cur.execute(query, params)
    else:
        cur.execute(query, params)
    return cur

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"pbkdf2_sha256$120000${salt}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return secrets.compare_digest(password, stored)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return secrets.compare_digest(check.hex(), digest)
    except Exception:
        return False

def send_email(to_email: str, subject: str, body: str):
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        if SMTP_USER == "your_email@gmail.com" and SMTP_PASSWORD == "your_app_password":
            print(f"\n[DEBUG EMAIL] To: {to_email}\nSubject: {subject}\nBody:\n{body}\n")
            return True
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Ошибка отправки почты: {e}")
        return False

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "ваш_токен":
        print("⚠️ Telegram не настроен")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload)
        print(f"📨 Telegram ответ: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка Telegram: {e}")
        return False

def send_admin_notification(subject: str, body: str):
    send_telegram(f"🔔 <b>{subject}</b>\n\n{body}")
    send_email(ADMIN_EMAIL, subject, body)

def log_action(username: str, action: str, request: Request):
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

def get_current_user(request: Request):
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
        cur.execute('''
            CREATE TABLE IF NOT EXISTS deals (
                id SERIAL PRIMARY KEY,
                category TEXT, date TEXT, model TEXT,
                quantity INTEGER DEFAULT 1,
                start_price REAL, agreed_price REAL,
                upload_id INTEGER,
                UNIQUE(date, model, agreed_price)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS uploads (
                id SERIAL PRIMARY KEY,
                filename TEXT, category TEXT,
                rows_count INTEGER, upload_date TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE, email TEXT UNIQUE,
                password TEXT, role TEXT, expires_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_logs (
                id SERIAL PRIMARY KEY,
                username TEXT, action TEXT, ip TEXT, user_agent TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS password_resets (
                id SERIAL PRIMARY KEY,
                email TEXT, token TEXT, expires_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payment_requests (
                id SERIAL PRIMARY KEY,
                username TEXT, months INTEGER,
                receipt TEXT, status TEXT, created_at TEXT
            )
        ''')
        cur.execute("SELECT COUNT(*) AS count FROM users")
        row = cur.fetchone()
        if row and row["count"] == 0:
            cur.execute(
                "INSERT INTO users (username, email, password, role, expires_at) VALUES (%s, %s, %s, %s, %s)",
                ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31")
            )
        conn.commit()
    else:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, date TEXT, model TEXT,
            quantity INTEGER,
            start_price REAL, agreed_price REAL,
            upload_id INTEGER,
            UNIQUE(date, model, agreed_price)
        )''')
        cur.execute("PRAGMA table_info(deals)")
        columns = [col[1] for col in cur.fetchall()]
        if 'quantity' not in columns:
            cur.execute("ALTER TABLE deals ADD COLUMN quantity INTEGER DEFAULT 1")
        cur.execute('''CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT, category TEXT, rows_count INTEGER, upload_date TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE, email TEXT UNIQUE,
            password TEXT, role TEXT, expires_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS user_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, action TEXT, ip TEXT, user_agent TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT, token TEXT, expires_at TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, months INTEGER,
            receipt TEXT, status TEXT, created_at TEXT
        )''')
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, ?, ?)",
                       ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31"))
        conn.commit()
    conn.close()

init_db()

# ==================== ШАБЛОНЫ (LANDING_TEMPLATE и UI_TEMPLATE) ====================
# Они ОГРОМНЫЕ, я их сокращаю для экономии места, но в реальном файле они такие же, как в предыдущем ответе.
# Пожалуйста, вставьте сюда свои полноценные LANDING_TEMPLATE и UI_TEMPLATE из предыдущего рабочего файла.
# Если их нет, я пришлю их отдельно.
LANDING_TEMPLATE = "<!DOCTYPE html>...</html>"  # замените на полный шаблон
UI_TEMPLATE = "<!DOCTYPE html>...</html>"       # замените на полный шаблон

# ==================== ЭНДПОИНТЫ ====================

@app.get("/", response_class=HTMLResponse)
def landing_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/app", status_code=303)
    return HTMLResponse(LANDING_TEMPLATE)

@app.get("/app", response_class=HTMLResponse)
def terminal(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    log_action(user["username"], "Просмотр страницы", request)
    return HTMLResponse(UI_TEMPLATE)

@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    days_left = 0
    sub_status = "Не активна"
    if user["role"] == "admin":
        sub_status = "Безлимит (администратор)"
        days_left = "∞"
    else:
        try:
            exp_date = datetime.strptime(user["expires_at"], "%Y-%m-%d")
            delta = exp_date - datetime.now()
            days_left = delta.days
            if days_left >= 0:
                sub_status = f"Активна до {user['expires_at']}"
            else:
                sub_status = "Истекла"
        except:
            sub_status = "Ошибка даты"
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Профиль</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans p-8 flex justify-center">
        <div class="max-w-md w-full bg-[#1e222d] border border-[#2a2e39] rounded-lg p-6 shadow-xl">
            <h1 class="text-xl font-bold text-white mb-4">👤 Профиль пользователя</h1>
            <div class="space-y-3 text-sm">
                <div><span class="text-gray-400">Логин:</span> <span class="text-white font-semibold">{user['username']}</span></div>
                <div><span class="text-gray-400">E-mail:</span> <span class="text-white font-semibold">{user['email']}</span></div>
                <div><span class="text-gray-400">Роль:</span> <span class="text-cyan-400 font-semibold">{user['role']}</span></div>
                <div><span class="text-gray-400">Статус подписки:</span> 
                    <span class="{'text-emerald-400' if 'Активна' in sub_status or 'Безлимит' in sub_status else 'text-rose-400'} font-semibold">{sub_status}</span>
                </div>
                <div><span class="text-gray-400">Осталось дней:</span> 
                    <span class="{'text-emerald-400' if days_left != '∞' and days_left > 0 else 'text-rose-400'} font-semibold">{days_left if days_left != '∞' else '∞'}</span>
                </div>
            </div>
            <div class="mt-6 flex gap-3">
                <a href="/app" class="bg-cyan-600 hover:bg-cyan-700 text-white px-4 py-2 rounded text-xs font-bold transition">← Вернуться в терминал</a>
                <a href="/logout" class="bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded text-xs font-bold transition">Выйти</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.get("/api/check_auth")
def check_auth(request: Request):
    user = get_current_user(request)
    if not user:
        return {"authenticated": False, "role": "guest", "sub_active": False}
    sub_active = False
    days_left = 0
    expires_at = user["expires_at"] or "2026-01-01"
    if user["role"] == "admin":
        sub_active = True
    else:
        try:
            exp_date = datetime.strptime(expires_at, "%Y-%m-%d")
            days_left = (exp_date - datetime.now()).days
            if days_left >= 0:
                sub_active = True
        except:
            pass
    return {
        "authenticated": True,
        "role": user["role"],
        "username": user["username"],
        "sub_active": sub_active,
        "expires_at": expires_at,
        "days_left": max(0, days_left)
    }

@app.post("/register")
async def register_post(username: str = Form(...), email: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM users WHERE username = ? OR email = ?", (username, email))
    existing = cur.fetchone()
    if existing:
        conn.close()
        return HTMLResponse("<script>alert('Пользователь с таким логином или email уже существует!'); window.location.href='/';</script>")
    try:
        execute_query(conn,
            "INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, 'client', '2026-01-01')",
            (username, email, hash_password(password))
        )
        conn.commit()
        msg = f"Новая регистрация!\nЛогин: {username}\nE-mail: {email}\nВремя: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        send_admin_notification("Новая регистрация", msg)
    except Exception as e:
        conn.close()
        return HTMLResponse(f"<script>alert('Ошибка регистрации: {e}'); window.location.href='/';</script>")
    conn.close()
    response = RedirectResponse(url="/app", status_code=303)
    response.set_cookie(key="session_user", value=username, httponly=True)
    return response

@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM users WHERE username = ?", (username,))
    user = cur.fetchone()
    conn.close()
    if not user or not verify_password(password, user["password"]):
        return HTMLResponse("<script>alert('Неверный логин или пароль!'); window.location.href='/';</script>")
    log_action(username, "Вход", request)
    response = RedirectResponse(url="/app", status_code=303)
    response.set_cookie(key="session_user", value=username, httponly=True)
    return response

@app.post("/api/forgot_password")
async def forgot_password(request: Request, email: str = Form(...)):
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM users WHERE email = ?", (email,))
    user = cur.fetchone()
    if user:
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        expires_at = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        execute_query(conn,
            "INSERT INTO password_resets (email, token, expires_at) VALUES (?, ?, ?)",
            (email, token, expires_at)
        )
        conn.commit()
        host_url = str(request.base_url).rstrip('/')
        reset_link = f"{host_url}/reset_password?token={token}"
        send_email(email, "Восстановление пароля — XARID ANALYTICS",
                   f"Перейдите по ссылке для сброса пароля (действительна 15 минут):\n{reset_link}")
    conn.close()
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Письмо отправлено</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans flex items-center justify-center min-h-screen p-4">
        <div class="tv-panel bg-[#1e222d] border border-[#2a2e39] p-8 rounded-lg max-w-md w-full text-center space-y-4 shadow-2xl">
            <div class="text-4xl">✉️</div>
            <h2 class="text-base font-bold text-white uppercase tracking-wider">Инструкция отправлена</h2>
            <p class="text-xs text-gray-300">Если указанный E-mail зарегистрирован в системе, на него отправлено письмо со ссылкой для сброса пароля.</p>
            <a href="/" class="inline-block w-full bg-cyan-600 hover:bg-cyan-700 text-white py-2.5 rounded text-xs font-bold uppercase tracking-wider transition">На главную</a>
        </div>
    </body>
    </html>
    """)

@app.get("/reset_password", response_class=HTMLResponse)
def reset_password_page(token: str):
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM password_resets WHERE token = ?", (token,))
    reset_entry = cur.fetchone()
    conn.close()
    if not reset_entry:
        return HTMLResponse("<script>alert('Срок действия ссылки истек или она недействительна.'); window.location.href='/';</script>")
    try:
        if datetime.now() > datetime.strptime(reset_entry["expires_at"], "%Y-%m-%d %H:%M:%S"):
            return HTMLResponse("<script>alert('Срок действия ссылки истек (15 минут). Запросите заново.'); window.location.href='/';</script>")
    except:
        pass
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Новый пароль</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans flex items-center justify-center min-h-screen p-4">
        <div class="tv-panel bg-[#1e222d] border border-[#2a2e39] p-8 rounded-lg max-w-md w-full space-y-4 shadow-2xl">
            <h2 class="text-sm font-bold text-white uppercase tracking-wider">Создание нового пароля</h2>
            <form action="/api/do_reset_password" method="POST" class="space-y-3">
                <input type="hidden" name="token" value="{token}">
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Новый пароль</label>
                    <input type="password" name="new_password" required placeholder="••••••••" class="w-full bg-[#131722] border border-[#2a2e39] px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition text-white">
                </div>
                <button type="submit" class="w-full bg-cyan-600 hover:bg-cyan-700 text-white py-2.5 rounded text-xs font-bold uppercase tracking-wider transition shadow">Обновить пароль 🔒</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/api/do_reset_password")
async def do_reset_password(token: str = Form(...), new_password: str = Form(...)):
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM password_resets WHERE token = ?", (token,))
    reset_entry = cur.fetchone()
    if not reset_entry:
        conn.close()
        return HTMLResponse("<script>alert('Неверный или просроченный токен!'); window.location.href='/';</script>")
    email = reset_entry["email"]
    execute_query(conn, "UPDATE users SET password = ? WHERE email = ?", (hash_password(new_password), email))
    execute_query(conn, "DELETE FROM password_resets WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Пароль успешно изменен! Теперь вы можете войти.'); window.location.href='/';</script>")

@app.post("/api/submit_payment")
async def submit_payment(request: Request, months: int = Form(...), receipt: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    conn = get_db_connection()
    try:
        execute_query(conn,
            "INSERT INTO payment_requests (username, months, receipt, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (user["username"], months, receipt, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
        msg = f"Новая заявка на подписку!\nПользователь: {user['username']}\nСрок: {months} месяц(ев)\nЧек: {receipt}\nВремя: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        send_admin_notification("Заявка на подписку", msg)
    except Exception as e:
        print(e)
    conn.close()
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Чек отправлен</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans flex items-center justify-center min-h-screen p-4">
        <div class="tv-panel bg-[#1e222d] border border-[#2a2e39] p-8 rounded-lg max-w-md w-full text-center space-y-4 shadow-2xl">
            <div class="text-4xl">⏳</div>
            <h2 class="text-base font-bold text-white uppercase tracking-wider">Чек успешно отправлен!</h2>
            <p class="text-xs text-gray-300">Администратор проверяет поступление средств. Доступ к ценам откроется после подтверждения.</p>
            <a href="/app" class="inline-block w-full bg-cyan-600 hover:bg-cyan-700 text-white py-2.5 rounded text-xs font-bold uppercase tracking-wider transition">Вернуться в терминал</a>
        </div>
    </body>
    </html>
    """)

@app.post("/api/support")
async def support(request: Request, message: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    msg = f"💬 Сообщение от пользователя {user['username']} ({user['email']}):\n\n{message}\n\nВремя: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    send_telegram(msg)
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Сообщение отправлено</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans flex items-center justify-center min-h-screen p-4">
        <div class="tv-panel bg-[#1e222d] border border-[#2a2e39] p-8 rounded-lg max-w-md w-full text-center space-y-4 shadow-2xl">
            <div class="text-4xl">✅</div>
            <h2 class="text-base font-bold text-white uppercase tracking-wider">Сообщение отправлено!</h2>
            <p class="text-xs text-gray-300">Администратор получил ваше сообщение и ответит в ближайшее время.</p>
            <a href="/app" class="inline-block w-full bg-cyan-600 hover:bg-cyan-700 text-white py-2.5 rounded text-xs font-bold uppercase tracking-wider transition">Вернуться в терминал</a>
        </div>
    </body>
    </html>
    """)

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="session_user")
    return response

@app.get("/api/deals")
def get_deals(request: Request):
    user = get_current_user(request)
    if not user:
        return []
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM deals")
    deals = [dict(row) for row in cur.fetchall()]
    conn.close()
    return deals

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/", status_code=303)
    conn = get_db_connection()

    # СТАТИСТИКА
    if USE_POSTGRES:
        plain_conn = psycopg2.connect(DATABASE_URL)
        plain_cur = plain_conn.cursor()
    else:
        plain_conn = conn
        plain_cur = conn.cursor()

    plain_cur.execute("SELECT COUNT(*) FROM user_logs")
    total_logs = plain_cur.fetchone()[0]

    plain_cur.execute("SELECT COUNT(DISTINCT username) FROM user_logs WHERE date(timestamp) = date('now')")
    unique_today = plain_cur.fetchone()[0]

    plain_cur.execute("SELECT COUNT(DISTINCT username) FROM user_logs WHERE timestamp > datetime('now', '-7 days')")
    unique_week = plain_cur.fetchone()[0]

    plain_cur.execute("SELECT COUNT(*) FROM users WHERE role != 'admin' AND created_at > datetime('now', '-7 days')")
    new_registrations = plain_cur.fetchone()[0]

    plain_cur.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
    pending_payments = plain_cur.fetchone()[0]

    if USE_POSTGRES:
        plain_conn.close()

    cur = execute_query(conn, "SELECT * FROM user_logs ORDER BY timestamp DESC LIMIT 20")
    logs = cur.fetchall()

    cur = execute_query(conn, "SELECT * FROM uploads ORDER BY id DESC")
    uploads = cur.fetchall()

    cur = execute_query(conn, "SELECT * FROM users ORDER BY id DESC")
    users = cur.fetchall()

    cur = execute_query(conn, "SELECT * FROM payment_requests ORDER BY id DESC")
    pay_requests = cur.fetchall()

    cur = execute_query(conn, "SELECT * FROM users WHERE role != 'admin' ORDER BY id DESC LIMIT 10")
    recent_users = cur.fetchall()

    conn.close()

    rows = "".join([f"<tr class='border-t border-[#2a2e39]'><td class='p-3'>{u['filename']}</td><td class='p-3 text-cyan-400'>{u['category']}</td><td class='p-3 text-emerald-400 font-bold'>{u['rows_count']}</td><td class='p-3 text-gray-400'>{u['upload_date']}</td><td class='p-3 text-right'><form action='/admin/delete/{u['id']}' method='post'><button class='text-rose-400 hover:text-rose-300 font-bold'>Удалить</button></form></td></tr>" for u in uploads])
    users_rows = "".join([f"<tr class='border-t border-[#2a2e39]'><td class='p-3 text-white font-bold'>{usr['username']}</td><td class='p-3 text-gray-300'>{usr['email'] or '—'}</td><td class='p-3 text-cyan-400'>{usr['role']}</td><td class='p-3 text-gray-300'>{usr['expires_at']}</td><td class='p-3 text-right'><form action='/admin/delete_user/{usr['id']}' method='post'><button class='text-rose-400 hover:text-rose-300 font-bold'>Удалить</button></form></td></tr>" for usr in users])
    pay_rows = "".join([f"""<tr class='border-t border-[#2a2e39]'>
        <td class='p-3 text-white font-bold'>{pr['username']}</td>
        <td class='p-3 text-cyan-400'>{pr['months']} мес.</td>
        <td class='p-3 font-mono text-yellow-400'>{pr['receipt']}</td>
        <td class='p-3 text-gray-400'>{pr['created_at']}</td>
        <td class='p-3 text-right space-x-2'>
            <form action='/admin/approve_payment/{pr['id']}' method='post' class='inline'><button class='bg-emerald-600 hover:bg-emerald-700 text-white px-2.5 py-1 rounded font-bold'>Одобрить</button></form>
            <form action='/admin/reject_payment/{pr['id']}' method='post' class='inline'><button class='bg-rose-600 hover:bg-rose-700 text-white px-2.5 py-1 rounded font-bold'>Удалить</button></form>
        </td>
    </tr>""" for pr in pay_requests])
    recent_rows = "".join([f"<tr class='border-t border-[#2a2e39]'><td class='p-3 text-white'>{u['username']}</td><td class='p-3 text-gray-300'>{u['email']}</td><td class='p-3 text-cyan-400'>{u['role']}</td><td class='p-3 text-gray-300'>{u['created_at']}</td></tr>" for u in recent_users])

    log_rows = "".join([f"<tr class='border-t border-[#2a2e39]'><td class='py-1 px-2 text-gray-400 text-[10px]'>{log['timestamp'][:16]}</td><td class='py-1 px-2 text-white text-[10px]'>{log['username'][:12]}</td><td class='py-1 px-2 text-cyan-400 text-[10px]'>{log['action'][:10]}</td><td class='py-1 px-2 text-gray-300 text-[10px]'>{log['ip']}</td><td class='py-1 px-2 text-gray-300 text-[10px]'>{log['user_agent'][:25]}…</td></tr>" for log in logs]) or '<tr><td colspan="5" class="p-4 text-center text-gray-500">Нет записей</td></tr>'

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Admin Panel</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans p-8">
        <div class="max-w-5xl mx-auto space-y-6">
            <div class="flex justify-between items-center border-b border-[#2a2e39] pb-4">
                <h1 class="text-sm font-bold uppercase text-white tracking-wider">Панель управления администратора</h1>
                <a href="/app" class="text-xs text-cyan-400 hover:underline">← На главную терминала</a>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="bg-[#1e222d] border border-[#2a2e39] p-4 rounded shadow">
                    <div class="text-gray-400 text-xs uppercase">Новые регистрации (7 дней)</div>
                    <div class="text-2xl font-bold text-cyan-400">{new_registrations}</div>
                </div>
                <div class="bg-[#1e222d] border border-[#2a2e39] p-4 rounded shadow">
                    <div class="text-gray-400 text-xs uppercase">Ожидают оплаты</div>
                    <div class="text-2xl font-bold text-amber-400">{pending_payments}</div>
                </div>
                <div class="bg-[#1e222d] border border-[#2a2e39] p-4 rounded shadow">
                    <div class="text-gray-400 text-xs uppercase">Всего пользователей</div>
                    <div class="text-2xl font-bold text-emerald-400">{len(users)}</div>
                </div>
                <div class="bg-[#1e222d] border border-[#2a2e39] p-4 rounded shadow">
                    <div class="text-gray-400 text-xs uppercase">Всего посещений</div>
                    <div class="text-2xl font-bold text-cyan-400">{total_logs}</div>
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div class="bg-[#1e222d] border border-[#2a2e39] p-4 rounded shadow">
                    <div class="text-gray-400 text-xs uppercase">Уникальных сегодня</div>
                    <div class="text-2xl font-bold text-emerald-400">{unique_today}</div>
                </div>
                <div class="bg-[#1e222d] border border-[#2a2e39] p-4 rounded shadow">
                    <div class="text-gray-400 text-xs uppercase">Уникальных за неделю</div>
                    <div class="text-2xl font-bold text-amber-400">{unique_week}</div>
                </div>
            </div>

            <div class="bg-[#1e222d] border border-[#2a2e39] rounded shadow overflow-hidden">
                <div class="px-4 py-2 border-b border-[#2a2e39]">
                    <h2 class="text-xs font-semibold uppercase text-gray-400">📋 Последние 20 действий</h2>
                </div>
                <table class="w-full text-left text-[10px]">
                    <thead>
                        <tr class="text-gray-500 border-b border-[#2a2e39] bg-[#1a1f2c]">
                            <th class="py-1 px-2">Время</th>
                            <th class="py-1 px-2">Пользователь</th>
                            <th class="py-1 px-2">Действие</th>
                            <th class="py-1 px-2">IP</th>
                            <th class="py-1 px-2">Браузер</th>
                        </tr>
                    </thead>
                    <tbody>{log_rows}</tbody>
                </table>
            </div>

            <div class="bg-[#1e222d] border border-[#2a2e39] p-6 rounded shadow">
                <h2 class="text-xs font-semibold uppercase text-amber-400 mb-4">📝 Новые регистрации (последние 10)</h2>
                <table class="w-full text-left text-xs">
                    <thead><tr class="text-gray-500 border-b border-[#2a2e39]"><th class="pb-3">Логин</th><th class="pb-3">E-mail</th><th class="pb-3">Роль</th><th class="pb-3">Дата</th></tr></thead>
                    <tbody>{recent_rows or '<tr><td colspan="4" class="p-4 text-center text-gray-500">Нет новых регистраций</td></tr>'}</tbody>
                </table>
            </div>

            <div class="bg-[#1e222d] border border-[#2a2e39] p-6 rounded shadow">
                <h2 class="text-xs font-semibold uppercase text-amber-400 mb-4">💳 Заявки на подтверждение оплаты</h2>
                <table class="w-full text-left text-xs">
                    <thead><tr class="text-gray-500 border-b border-[#2a2e39]"><th class="pb-3">Логин</th><th class="pb-3">Срок</th><th class="pb-3">Чек / Квитанция</th><th class="pb-3">Дата</th><th class="pb-3 text-right">Действие</th></tr></thead>
                    <tbody>{pay_rows or '<tr><td colspan="5" class="p-4 text-center text-gray-500">Нет новых чеков на проверку</td></tr>'}</tbody>
                </table>
            </div>

            <div class="bg-[#1e222d] border border-[#2a2e39] p-6 rounded shadow">
                <h2 class="text-xs font-semibold uppercase text-gray-400 mb-4">Управление пользователями</h2>
                <table class="w-full text-left text-xs">
                    <thead><tr class="text-gray-500 border-b border-[#2a2e39]"><th class="pb-3">Логин</th><th class="pb-3">E-mail</th><th class="pb-3">Роль</th><th class="pb-3">Подписка до</th><th class="pb-3 text-right">Действие</th></tr></thead>
                    <tbody>{users_rows}</tbody>
                </table>
            </div>

            <div class="bg-[#1e222d] border border-[#2a2e39] p-6 rounded shadow">
                <h2 class="text-xs font-semibold uppercase text-gray-400 mb-4">Импорт Excel файлов (.xlsx)</h2>
                <form action="/upload" method="post" enctype="multipart/form-data" class="space-y-4">
                    <input type="text" name="category" placeholder="Категория (например: Видеонаблюдение / Ноутбуки)" class="w-full bg-[#131722] border border-[#2a2e39] p-3 rounded text-xs text-white outline-none focus:border-cyan-500" required>
                    <input type="file" name="files" accept=".xlsx, .xls" multiple class="w-full text-xs text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:bg-cyan-600 file:text-white cursor-pointer" required>
                    <button class="w-full bg-cyan-600 hover:bg-cyan-700 py-3 rounded text-xs font-bold uppercase text-white tracking-wider transition shadow">Загрузить в систему</button>
                </form>
            </div>

            <div class="bg-[#1e222d] border border-[#2a2e39] p-6 rounded shadow">
                <h2 class="text-xs font-semibold uppercase text-gray-400 mb-4">История загрузок</h2>
                <table class="w-full text-left text-xs">
                    <thead><tr class="text-gray-500 border-b border-[#2a2e39]"><th class="pb-3">Файл</th><th class="pb-3">Категория</th><th class="pb-3">Строк</th><th class="pb-3">Дата</th><th class="pb-3 text-right">Действие</th></tr></thead>
                    <tbody>{rows or '<tr><td colspan="5" class="p-4 text-center text-gray-500">Нет загруженных файлов</td></tr>'}</tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """

@app.post("/admin/approve_payment/{req_id}")
def approve_payment(req_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM payment_requests WHERE id = ?", (req_id,))
    req = cur.fetchone()
    if req:
        days = int(req["months"]) * 30
        cur = execute_query(conn, "SELECT expires_at FROM users WHERE username = ?", (req["username"],))
        old_user = cur.fetchone()
        base_date = datetime.now()
        if old_user and old_user["expires_at"]:
            try:
                parsed_old = datetime.strptime(old_user["expires_at"], "%Y-%m-%d")
                if parsed_old > datetime.now():
                    base_date = parsed_old
            except:
                pass
        expires_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%d")
        execute_query(conn, "UPDATE users SET expires_at = ? WHERE username = ?", (expires_date, req["username"]))
        execute_query(conn, "DELETE FROM payment_requests WHERE id = ?", (req_id,))
        conn.commit()
        send_telegram(f"✅ Подписка одобрена для {req['username']} до {expires_date}")
    conn.close()
    return HTMLResponse("<script>alert('Оплата одобрена, подписка успешно продлена!'); window.location.href='/admin';</script>")

@app.post("/admin/reject_payment/{req_id}")
def reject_payment(req_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db_connection()
    cur = execute_query(conn, "SELECT * FROM payment_requests WHERE id = ?", (req_id,))
    req = cur.fetchone()
    if req:
        send_telegram(f"❌ Заявка на подписку отклонена для {req['username']}")
    execute_query(conn, "DELETE FROM payment_requests WHERE id = ?", (req_id,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Заявка удалена.'); window.location.href='/admin';</script>")

@app.post("/admin/delete_user/{user_id}")
def delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db_connection()
    execute_query(conn, "DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Пользователь удален!'); window.location.href='/admin';</script>")

@app.post("/upload")
async def upload_files(category: str = Form(...), files: list[UploadFile] = File(...), request: Request = None):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db_connection()
    for file in files:
        try:
            contents = await file.read()
            df = pd.read_excel(io.BytesIO(contents))
            cur = execute_query(conn,
                "INSERT INTO uploads (filename, category, rows_count, upload_date) VALUES (?, ?, ?, ?)",
                (file.filename, category, len(df), datetime.now().strftime("%Y-%m-%d"))
            )
            if USE_POSTGRES:
                cur = execute_query(conn, "SELECT lastval()")
                upload_id = cur.fetchone()[0]
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
            print(f"Ошибка файла {file.filename}: {e}")
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Файлы успешно импортированы!'); window.location.href='/admin';</script>")

@app.post("/admin/delete/{upload_id}")
def delete_upload(upload_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
    conn = get_db_connection()
    execute_query(conn, "DELETE FROM deals WHERE upload_id = ?", (upload_id,))
    execute_query(conn, "DELETE FROM uploads WHERE id = ?", (upload_id,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Успешно удалено!'); window.location.href='/admin';</script>")

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
