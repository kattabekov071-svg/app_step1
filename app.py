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

# === Настройка логирования ===
logging.basicConfig(level=logging.DEBUG)

app = FastAPI()

# === Конфигурация ===
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "your_email@gmail.com"
SMTP_PASSWORD = "your_app_password"
ADMIN_EMAIL = "admin@xarid.uz"

TELEGRAM_TOKEN = "8925100564:AAH6GTe281eDfuzoyqBUBm_AhQ31edAscS8"           # ← вставьте свой
TELEGRAM_CHAT_ID = "40209048"       # ← вставьте свой

# === Определяем, какую БД использовать ===
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

# === УНИВЕРСАЛЬНАЯ РАБОТА С БАЗОЙ ===
def get_db_connection():
    if USE_POSTGRES:
        if not DATABASE_URL:
            raise Exception("DATABASE_URL is empty, but USE_POSTGRES is True")
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect("tender.db")
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(conn, query, params=None):
    """Выполняет запрос с автоматической подстановкой параметров."""
    cur = conn.cursor()
    if params is None:
        params = ()
    if USE_POSTGRES:
        query = query.replace("?", "%s")
        cur.execute(query, params)
    else:
        cur.execute(query, params)
    return cur

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
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
        print("⚠️ Telegram не настроен: токен не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload)
        print(f"📨 Telegram ответ: {response.status_code} - {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")
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

# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ===
def init_db():
    conn = get_db_connection()
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS deals (
                id SERIAL PRIMARY KEY,
                category TEXT,
                date TEXT,
                model TEXT,
                quantity INTEGER DEFAULT 1,
                start_price REAL,
                agreed_price REAL,
                upload_id INTEGER,
                UNIQUE(date, model, agreed_price)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS uploads (
                id SERIAL PRIMARY KEY,
                filename TEXT,
                category TEXT,
                rows_count INTEGER,
                upload_date TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                email TEXT UNIQUE,
                password TEXT,
                role TEXT,
                expires_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_logs (
                id SERIAL PRIMARY KEY,
                username TEXT,
                action TEXT,
                ip TEXT,
                user_agent TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS password_resets (
                id SERIAL PRIMARY KEY,
                email TEXT,
                token TEXT,
                expires_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payment_requests (
                id SERIAL PRIMARY KEY,
                username TEXT,
                months INTEGER,
                receipt TEXT,
                status TEXT,
                created_at TEXT
            )
        ''')
        # Проверка на админа
        cur.execute("SELECT COUNT(*) AS count FROM users")
        row = cur.fetchone()
        if row and row["count"] == 0:
            cur.execute(
                "INSERT INTO users (username, email, password, role, expires_at) VALUES (%s, %s, %s, %s, %s)",
                ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31")
            )
        conn.commit()
    else:
        # SQLite
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
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT,
            expires_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS user_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            ip TEXT,
            user_agent TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            token TEXT,
            expires_at TEXT
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            months INTEGER,
            receipt TEXT,
            status TEXT,
            created_at TEXT
        )''')
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, ?, ?)",
                       ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31"))
        conn.commit()
    conn.close()

# Вызываем инициализацию
init_db()

# ==================== ЛЕНДИНГ ====================
LANDING_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>XARID ANALYTICS - Мониторинг тендеров xarid.uzex.uz</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background-color: #0d1117; color: #c9d1d9; min-height: 100vh; display: flex; flex-direction: column; padding: 16px; }
        .app-header { max-width: 1600px; width: 100%; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; padding: 12px 20px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; gap: 20px; flex-wrap: wrap; }
        .app-logo-area { display: flex; align-items: center; gap: 14px; }
        .logo-icon { width: 38px; height: 38px; background: linear-gradient(135deg, #00b4d8, #0077b6); border: 2px solid #00f5ff; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #ffffff; font-weight: 800; font-size: 1.1rem; box-shadow: 0 0 12px rgba(0, 245, 255, 0.4); }
        .app-title-group { display: flex; flex-direction: column; gap: 3px; }
        .app-logo { font-weight: 800; font-size: 1.05rem; color: #00c2ff; letter-spacing: 0.8px; text-shadow: 0 0 8px rgba(0, 194, 255, 0.3); }
        .app-about { font-size: 0.78rem; color: #8b949e; font-weight: 400; }
        .header-controls { display: flex; align-items: center; gap: 8px; }
        .header-btn-outline { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 6px 14px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer; text-decoration: none; transition: 0.2s; }
        .header-btn-outline:hover { background: #30363d; }
        .header-btn-primary { background: #0077b6; border: 1px solid #0096c7; color: #ffffff; padding: 6px 14px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; cursor: pointer; text-decoration: none; transition: 0.2s; }
        .header-btn-primary:hover { background: #0096c7; }
        .controls-bar { max-width: 1600px; width: 100%; margin: 12px auto 0 auto; display: flex; flex-direction: column; gap: 10px; background: #161b22; border: 1px solid #30363d; padding: 12px 16px; border-radius: 8px; }
        .categories-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .categories-label { font-size: 0.75rem; color: #8b949e; font-weight: 600; text-transform: uppercase; margin-right: 4px; }
        .cat-btn { background: #0d1117; border: 1px solid #30363d; color: #8b949e; padding: 6px 14px; border-radius: 6px; font-size: 0.78rem; font-weight: 500; cursor: pointer; transition: 0.2s; }
        .cat-btn:hover { background: #21262d; color: #ffffff; border-color: #484f58; }
        .cat-btn.active { border-color: #00c2ff; color: #00c2ff; background: rgba(0, 194, 255, 0.1); }
        .search-box { position: relative; width: 100%; }
        .search-input { width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 8px 12px 8px 34px; border-radius: 6px; font-size: 0.8rem; outline: none; transition: border-color 0.2s; }
        .search-input:focus { border-color: #00c2ff; }
        .search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: #484f58; font-size: 0.8rem; }
        .app-layout { max-width: 1600px; width: 100%; margin: 12px auto; display: flex; flex-direction: column; flex-grow: 1; }
        .table-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; }
        .data-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.8rem; }
        .data-table th, .data-table td { padding: 12px 16px; border-bottom: 1px solid #30363d; }
        .data-table th { background: #161b22; color: #8b949e; font-weight: 600; text-transform: uppercase; font-size: 0.68rem; letter-spacing: 0.5px; border-bottom: 2px solid #21262d; }
        .data-table tbody tr { cursor: pointer; transition: background 0.1s; }
        .data-table tbody tr:hover { background: #21262d; }
        @keyframes slideDown { 0% { opacity: 0; transform: translateY(-10px); background-color: rgba(0, 194, 255, 0.15); } 100% { opacity: 1; transform: translateY(0); background-color: transparent; } }
        .smooth-new-row { animation: slideDown 0.6s ease-out forwards; }
        .col-cat { color: #58a6ff; font-weight: 500; font-size: 0.78rem; }
        .col-model { color: #f0f6fc; font-weight: 600; }
        .price-old { color: #8b949e; font-family: monospace; }
        .price-new { color: #3fb950; font-weight: 600; font-family: monospace; }
        .contracts-count { color: #8b949e; font-size: 0.75rem; text-align: right; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(13, 17, 23, 0.85); backdrop-filter: blur(4px); z-index: 1000; justify-content: center; align-items: center; }
        .modal-card { background: #161b22; border: 1px solid #30363d; padding: 24px; border-radius: 8px; width: 100%; max-width: 380px; box-shadow: 0 15px 30px rgba(0,0,0,0.6); display: flex; flex-direction: column; gap: 14px; position: relative; }
        .modal-title { font-size: 1.05rem; font-weight: 700; color: #f0f6fc; }
        .modal-desc { font-size: 0.8rem; color: #8b949e; line-height: 1.4; }
        .modal-close { position: absolute; top: 12px; right: 12px; background: none; border: none; font-size: 1.2rem; cursor: pointer; color: #8b949e; }
        .modal-close:hover { color: #f0f6fc; }
        .modal-input { width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 8px 12px; border-radius: 6px; font-size: 0.8rem; outline: none; transition: border-color 0.2s; }
        .modal-input:focus { border-color: #00c2ff; }
        .modal-btn-primary { background: #0077b6; color: #ffffff; border: none; padding: 9px; border-radius: 6px; font-weight: 600; text-align: center; font-size: 0.78rem; cursor: pointer; transition: background 0.2s; width: 100%; }
        .modal-btn-primary:hover { background: #0096c7; }
        .modal-link { color: #00c2ff; font-size: 0.78rem; cursor: pointer; text-decoration: none; }
        .modal-link:hover { text-decoration: underline; }
        .modal-row { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
    </style>
</head>
<body>
    <div class="app-header">
        <div class="app-logo-area">
            <div class="logo-icon">X</div>
            <div class="app-title-group">
                <div class="app-logo">XARID ANALYTICS</div>
                <div class="app-about">Мониторинг тендеров xarid.uzex.uz</div>
            </div>
        </div>
        <div class="header-controls">
            <button class="header-btn-outline" onclick="openRegisterModal()">Регистрация</button>
            <button class="header-btn-primary" onclick="openLoginModal()">Войти</button>
        </div>
    </div>
    <div class="controls-bar">
        <div class="categories-row" id="categoryButtons">
            <span class="categories-label">Категории:</span>
            <button class="cat-btn active" data-cat="all">Все</button>
            <button class="cat-btn" data-cat="printer">Принтеры</button>
            <button class="cat-btn" data-cat="camera">Камера Видеонаблюдения</button>
            <button class="cat-btn" data-cat="nvr">Регистратор (NVR)</button>
            <button class="cat-btn" data-cat="laptop">Ноутбук</button>
        </div>
        <div class="search-box">
            <span class="search-icon">🔍</span>
            <input type="text" id="searchInput" class="search-input" placeholder="Быстрый поиск по модели оборудования..." onclick="openLoginModal()">
        </div>
    </div>
    <div class="app-layout">
        <div class="table-card">
            <table class="data-table">
                <thead><tr><th>Категория</th><th>Модель оборудования</th><th>Стартовая цена (мин)</th><th>Цена договора (мин)</th><th style="text-align: right;">Контракты</th></tr></thead>
                <tbody id="dealsTbody" onclick="openLoginModal()"></tbody>
            </table>
        </div>
    </div>
    <!-- Модалки -->
    <div class="modal-overlay" id="loginModal">
        <div class="modal-card">
            <button class="modal-close" onclick="closeLoginModal()">&times;</button>
            <div class="modal-title">Вход в систему</div>
            <div class="modal-desc">Введите ваш логин и пароль</div>
            <form action="/login" method="POST" style="display: flex; flex-direction: column; gap: 12px;">
                <input type="text" name="username" placeholder="Логин" class="modal-input" required>
                <input type="password" name="password" placeholder="Пароль" class="modal-input" required>
                <div class="modal-row">
                    <span class="modal-link" onclick="closeLoginModal(); openForgotModal();">Забыли пароль?</span>
                </div>
                <button type="submit" class="modal-btn-primary">Войти</button>
            </form>
        </div>
    </div>
    <div class="modal-overlay" id="registerModal">
        <div class="modal-card">
            <button class="modal-close" onclick="closeRegisterModal()">&times;</button>
            <div class="modal-title">Создание аккаунта</div>
            <div class="modal-desc">Укажите почту для восстановления пароля</div>
            <form action="/register" method="POST" style="display: flex; flex-direction: column; gap: 12px;">
                <input type="text" name="username" placeholder="Логин" class="modal-input" required>
                <input type="email" name="email" placeholder="E-mail" class="modal-input" required>
                <input type="password" name="password" placeholder="Пароль" class="modal-input" required>
                <button type="submit" class="modal-btn-primary">Зарегистрироваться 🚀</button>
            </form>
        </div>
    </div>
    <div class="modal-overlay" id="forgotModal">
        <div class="modal-card">
            <button class="modal-close" onclick="closeForgotModal()">&times;</button>
            <div class="modal-title">Восстановление пароля</div>
            <div class="modal-desc">Введите ваш E-mail, указанный при регистрации</div>
            <form action="/api/forgot_password" method="POST" style="display: flex; flex-direction: column; gap: 12px;">
                <input type="email" name="email" placeholder="E-mail" class="modal-input" required>
                <button type="submit" class="modal-btn-primary">Отправить ссылку ✉️</button>
            </form>
        </div>
    </div>
    <script>
        function openLoginModal() { document.getElementById('loginModal').style.display = 'flex'; }
        function closeLoginModal() { document.getElementById('loginModal').style.display = 'none'; }
        function openRegisterModal() { document.getElementById('registerModal').style.display = 'flex'; }
        function closeRegisterModal() { document.getElementById('registerModal').style.display = 'none'; }
        function openForgotModal() { document.getElementById('forgotModal').style.display = 'flex'; }
        function closeForgotModal() { document.getElementById('forgotModal').style.display = 'none'; }
        window.addEventListener('click', function(event) {
            if (event.target.classList.contains('modal-overlay')) {
                event.target.style.display = 'none';
            }
        });
        const demoDeals = [
            { cat: "Принтеры", catKey: "printer", model: "3200", start: "3 000 000 UZS", deal: "2 020 000 UZS", count: "Контрактов: 2 ▼" },
            { cat: "Принтеры", catKey: "printer", model: "Canon LBP-2900", start: "2 000 000 UZS", deal: "1 600 000,01 UZS", count: "Контрактов: 17 ▼" },
            { cat: "Принтеры", catKey: "printer", model: "Canon i-SENSYS LBP6030", start: "1 548 907,1 UZS", deal: "1 499 000 UZS", count: "Контрактов: 57 ▼" },
            { cat: "Принтеры", catKey: "printer", model: "Canon i-SENSYS MF3010", start: "1 500 000 UZS", deal: "1 500 000 UZS", count: "Контрактов: 264 ▼" },
            { cat: "Камера Видеонаблюдения", catKey: "camera", model: "IP-видеокамера Hikvision DS-2CD1023G0-I", start: "4 500 000 UZS", deal: "4 100 000 UZS", count: "Контрактов: 42 ▼" },
            { cat: "Регистратор (NVR)", catKey: "nvr", model: "Видеорегистратор Dahua NVR4104-4KS2/L", start: "6 800 000 UZS", deal: "6 200 000 UZS", count: "Контрактов: 19 ▼" },
            { cat: "Ноутбук", catKey: "laptop", model: "Ноутбук HP ProBook 450 G9 i5 / 16GB", start: "92 000 000 UZS", deal: "84 500 000 UZS", count: "Контрактов: 8 ▼" }
        ];
        const livePool = [
            { cat: "Принтеры", catKey: "printer", model: "Epson L3210", start: "1 000 000 UZS", deal: "999 999 UZS", count: "Контрактов: 87 ▼" },
            { cat: "Принтеры", catKey: "printer", model: "Epson L3200", start: "2 100 000 UZS", deal: "1 904 000 UZS", count: "Контрактов: 42 ▼" },
            { cat: "Камера Видеонаблюдения", catKey: "camera", model: "IP-камера Dahua IPC-HFW1230SP", start: "3 800 000 UZS", deal: "3 450 000 UZS", count: "Контрактов: 15 ▼" },
            { cat: "Регистратор (NVR)", catKey: "nvr", model: "Видеорегистратор HiWatch DS-N304", start: "5 200 000 UZS", deal: "4 800 000 UZS", count: "Контрактов: 23 ▼" },
            { cat: "Ноутбук", catKey: "laptop", model: "Ноутбук Acer Aspire 3 i3 / 8GB", start: "54 000 000 UZS", deal: "49 500 000 UZS", count: "Контрактов: 12 ▼" }
        ];
        let activeCategory = 'all';
        function renderTable(data) {
            const tbody = document.getElementById('dealsTbody');
            tbody.innerHTML = '';
            data.forEach(item => {
                const tr = document.createElement('tr');
                tr.setAttribute('data-category', item.catKey);
                if (activeCategory !== 'all' && item.catKey !== activeCategory) {
                    tr.style.display = 'none';
                }
                tr.innerHTML = `
                    <td><div class="col-cat">${item.cat}</div></td>
                    <td><div class="col-model">${item.model}</div></td>
                    <td><span class="price-old">${item.start}</span></td>
                    <td><span class="price-new">${item.deal}</span></td>
                    <td class="contracts-count">${item.count}</td>
                `;
                tbody.appendChild(tr);
            });
        }
        renderTable(demoDeals);
        document.querySelectorAll('.cat-btn').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                activeCategory = this.getAttribute('data-cat');
                const rows = document.querySelectorAll('#dealsTbody tr');
                rows.forEach(row => {
                    const rowCat = row.getAttribute('data-category');
                    if (activeCategory === 'all' || rowCat === activeCategory) {
                        row.style.display = '';
                    } else {
                        row.style.display = 'none';
                    }
                });
            });
        });
        function addNewLiveRow() {
            const tbody = document.getElementById('dealsTbody');
            const randomItem = livePool[Math.floor(Math.random() * livePool.length)];
            const newTr = document.createElement('tr');
            newTr.className = 'smooth-new-row';
            newTr.setAttribute('data-category', randomItem.catKey);
            if (activeCategory !== 'all' && randomItem.catKey !== activeCategory) {
                newTr.style.display = 'none';
            }
            newTr.innerHTML = `
                <td><div class="col-cat">${randomItem.cat}</div></td>
                <td><div class="col-model">${randomItem.model}</div></td>
                <td><span class="price-old">${randomItem.start}</span></td>
                <td><span class="price-new">${randomItem.deal}</span></td>
                <td class="contracts-count">${randomItem.count}</td>
            `;
            tbody.insertBefore(newTr, tbody.firstChild);
            if (tbody.rows.length > 10) {
                tbody.deleteRow(tbody.rows.length - 1);
            }
        }
        setInterval(addNewLiveRow, 4000);
    </script>
</body>
</html>
"""

# ==================== ТЕРМИНАЛ ====================
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.tailwindcss.com"></script>
    <title>XARID ANALYTICS - Мониторинг госзакупок Узбекистана</title>
    <style>
        body { background-color: #131722; color: #d1d4dc; font-family: 'Segoe UI', sans-serif; font-size: 15px; }
        .tv-panel { background-color: #1e222d; border: 1px solid #2a2e39; border-radius: 6px; }
        .tv-subpanel { background-color: #181c25; border: 1px solid #2a2e39; }
        .row-hover:hover { background-color: rgba(6, 182, 212, 0.08); }
        input, select { background-color: #131722 !important; border-color: #2a2e39 !important; color: white !important; }
        .cat-badge { background: #2a2e39; border: 1px solid #363c4e; color: #d1d4dc; font-size: 0.8rem; padding: 4px 12px; border-radius: 4px; cursor: pointer; transition: all 0.2s; white-space: nowrap; font-weight: 600; }
        .cat-badge:hover, .cat-badge.active { background: #06b6d4; border-color: #22d3ee; color: white; }
        .lang-btn { background: #2a2e39; border: 1px solid #363c4e; color: #d1d4dc; font-size: 0.75rem; padding: 4px 10px; border-radius: 4px; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; gap: 5px; font-weight: 600; }
        .lang-btn:hover, .lang-btn.active { background: #06b6d4; border-color: #22d3ee; color: white; }
        .logo-emblem { background: linear-gradient(135deg, #06b6d4 0%, #0369a1 100%); box-shadow: 0 0 15px rgba(6, 182, 212, 0.4); border: 2px solid #22d3ee; }
        @keyframes marquee { 0% { transform: translateX(0%); } 100% { transform: translateX(-50%); } }
        .marquee-container { overflow: hidden; white-space: nowrap; display: flex; width: 100%; }
        .marquee-track { display: inline-flex; gap: 50px; animation: marquee 10s linear infinite; }
        .marquee-track:hover { animation-play-state: paused; }
        .blur-price { filter: blur(6px); user-select: none; }
        .compact-row { padding: 6px 12px !important; font-size: 0.85rem; }
        .compact-detail { padding: 6px 12px !important; }
        .btn-show-all { background: #2a2e39; border: 1px solid #363c4e; color: #d1d4dc; padding: 2px 10px; border-radius: 3px; font-size: 0.7rem; cursor: pointer; transition: 0.2s; }
        .btn-show-all:hover { background: #06b6d4; border-color: #22d3ee; color: white; }
        .detail-table td, .detail-table th { padding: 4px 8px !important; font-size: 0.8rem; }
        .detail-table { border-collapse: collapse; }
        .detail-table tr { border-bottom: 1px solid #1a1f2c; }
        .detail-table tr:last-child { border-bottom: none; }
    </style>
</head>
<body class="min-h-screen flex flex-col p-4 max-w-[1600px] mx-auto gap-4 justify-between">
    <div class="flex flex-col gap-4 flex-1">
        <header class="tv-panel px-5 py-3.5 flex flex-col gap-3 shadow-xl bg-gradient-to-r from-[#1e222d] via-[#1a1f2c] to-[#1e222d]">
            <div class="flex justify-between items-center">
                <div class="flex items-center gap-3.5 cursor-pointer select-none group" onclick="window.location.href='/app'">
                    <div class="logo-emblem w-10 h-10 rounded-full flex items-center justify-center text-white shrink-0 font-black text-lg transition transform group-hover:scale-105 tracking-tighter">X</div>
                    <div class="flex flex-col">
                        <span class="font-black text-sm tracking-wider text-white group-hover:text-cyan-400 transition">XARID ANALYTICS</span>
                        <span class="text-[10px] text-cyan-400 font-medium">Мониторинг тендеров xarid.uzex.uz</span>
                    </div>
                </div>
                <div class="flex gap-3.5 items-center">
                    <div class="flex items-center gap-1.5" id="langContainer">
                        <button onclick="setLanguage('ru')" class="lang-btn active" id="lang-ru">RU</button>
                        <button onclick="setLanguage('en')" class="lang-btn" id="lang-en">EN</button>
                        <button onclick="setLanguage('uz')" class="lang-btn" id="lang-uz">UZ</button>
                    </div>
                    <button onclick="toggleCurrency()" class="bg-[#2a2e39] hover:bg-[#363c4e] px-3 py-1.5 rounded text-xs text-white font-semibold transition flex items-center gap-1.5 border border-[#363c4e]">
                        💱 <span id="currencyLabelText">Валюта</span>: <span id="currencyBtnText" class="text-cyan-400 font-bold">USD</span>
                    </button>
                    <div id="authHeaderBlock" class="flex items-center gap-2"></div>
                </div>
            </div>
            <div class="marquee-container text-xs text-gray-300 border-t border-[#2a2e39] pt-2">
                <div class="marquee-track" id="ratesMarqueeTrack"><span class="text-cyan-400">Загрузка курса USD...</span></div>
            </div>
        </header>
        <div id="paymentPendingBanner" class="bg-gradient-to-r from-amber-950/40 via-yellow-950/30 to-[#1e222d] border border-yellow-500/30 p-4 rounded-lg flex justify-between items-center hidden">
            <div class="flex items-center gap-3">
                <span class="text-xl">💳</span>
                <div>
                    <h4 class="text-xs font-bold text-yellow-400 uppercase tracking-wider">Подписка не активна: Цены скрыты</h4>
                    <p class="text-[11px] text-gray-300">Переведите <b>200,000 UZS</b> на карту Humo <b class="text-white font-mono">9860 1701 0525 9973</b> (B.K.) и отправьте чек.</p>
                </div>
            </div>
            <div><button onclick="openPaymentModal()" class="bg-amber-600 hover:bg-amber-700 text-white px-4 py-2 rounded text-xs font-bold transition shadow">Отправить чек об оплате 🧾</button></div>
        </div>
        <div class="tv-panel p-3 flex items-center gap-2 overflow-x-auto">
            <span class="text-[11px] font-bold uppercase text-gray-400 mr-2 shrink-0" id="catLabelText">Категории:</span>
            <div class="flex items-center gap-1.5 overflow-x-auto" id="headerCategoriesContainer"></div>
        </div>
        <div class="tv-panel p-3">
            <input type="text" id="searchInput" oninput="applyFilters()" placeholder="🔎 Быстрый поиск по модели оборудования..." class="w-full px-3.5 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
        </div>
        <div class="tv-panel overflow-hidden flex-1 flex flex-col">
            <div class="grid grid-cols-4 px-4 py-2 bg-[#151922] border-b border-[#2a2e39] text-[10px] font-bold uppercase text-gray-400 tracking-wider">
                <div id="thCat">Категория</div>
                <div id="thModel">Модель оборудования</div>
                <div id="thStartPrice">Стартовая цена (Мин)</div>
                <div id="thAgreedPrice">Цена договора (Мин)</div>
            </div>
            <div class="divide-y divide-[#2a2e39] overflow-y-auto max-h-[550px]" id="dealsContainer"></div>
        </div>
    </div>
    <!-- Модальные окна -->
    <div id="loginModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="tv-panel p-6 w-full max-w-md space-y-4 shadow-2xl relative">
            <button onclick="closeLoginModal()" class="absolute top-4 right-4 text-gray-400 hover:text-white font-bold">✕</button>
            <div>
                <h3 class="text-sm font-bold text-white uppercase">Вход в систему</h3>
                <p class="text-[11px] text-gray-400">Введите ваш логин и пароль</p>
            </div>
            <form action="/login" method="POST" class="space-y-3">
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Логин</label>
                    <input type="text" name="username" required class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Пароль</label>
                    <input type="password" name="password" required class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <div class="flex justify-between items-center text-[11px]">
                    <button type="button" onclick="openForgotModal(); closeLoginModal();" class="text-cyan-400 hover:underline">Забыли пароль?</button>
                </div>
                <button type="submit" class="w-full bg-cyan-600 hover:bg-cyan-700 text-white font-bold py-2.5 rounded text-xs uppercase tracking-wider transition">Войти</button>
            </form>
        </div>
    </div>
    <div id="forgotModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="tv-panel p-6 w-full max-w-md space-y-4 shadow-2xl relative">
            <button onclick="closeForgotModal()" class="absolute top-4 right-4 text-gray-400 hover:text-white font-bold">✕</button>
            <div>
                <h3 class="text-sm font-bold text-white uppercase">Восстановление пароля</h3>
                <p class="text-[11px] text-gray-400">Введите ваш E-mail, указанный при регистрации</p>
            </div>
            <form action="/api/forgot_password" method="POST" class="space-y-3">
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Ваш E-mail</label>
                    <input type="email" name="email" required placeholder="example@mail.com" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <button type="submit" class="w-full bg-cyan-600 hover:bg-cyan-700 text-white font-bold py-2.5 rounded text-xs uppercase tracking-wider transition">Отправить ссылку сброса ✉️</button>
            </form>
        </div>
    </div>
    <div id="registerModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="tv-panel p-6 w-full max-w-md space-y-4 shadow-2xl relative">
            <button onclick="closeRegisterModal()" class="absolute top-4 right-4 text-gray-400 hover:text-white font-bold">✕</button>
            <div>
                <h3 class="text-sm font-bold text-white uppercase">Создание аккаунта</h3>
                <p class="text-[11px] text-gray-400">Укажите почту для возможности восстановления пароля</p>
            </div>
            <form action="/register" method="POST" class="space-y-3">
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Логин</label>
                    <input type="text" name="username" required placeholder="например: my_supplier" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">E-mail (для восстановления)</label>
                    <input type="email" name="email" required placeholder="example@mail.com" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Пароль</label>
                    <input type="password" name="password" required placeholder="••••••••" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <button type="submit" class="w-full bg-cyan-600 hover:bg-cyan-700 text-white font-bold py-2.5 rounded text-xs uppercase tracking-wider transition">Зарегистрироваться 🚀</button>
            </form>
        </div>
    </div>
    <div id="paymentModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="tv-panel p-6 w-full max-w-md space-y-4 shadow-2xl relative bg-[#1e222d] border border-[#2a2e39] text-[#d1d4dc]">
            <button onclick="closePaymentModal()" class="absolute top-4 right-4 text-gray-400 hover:text-white font-bold">✕</button>
            <div>
                <h3 class="text-sm font-bold text-white uppercase">Подтверждение оплаты подписки</h3>
                <p class="text-[11px] text-gray-400">Укажите данные перевода для проверки администратором</p>
            </div>
            <form action="/api/submit_payment" method="POST" class="space-y-3">
                <div class="p-3 bg-[#131722] border border-amber-500/30 rounded space-y-2 text-xs">
                    <div class="flex justify-between items-center text-gray-300">
                        <span>Сумма к оплате:</span>
                        <span class="text-amber-400 font-bold text-sm">200,000 UZS / месяц</span>
                    </div>
                    <div class="border-t border-[#2a2e39] pt-2 space-y-1">
                        <div class="text-gray-400 text-[11px]">Карта Humo для перевода:</div>
                        <div class="font-mono text-white font-bold bg-[#1e222d] p-2 rounded text-center tracking-wider text-sm">9860 1701 0525 9973</div>
                        <div class="text-right text-[11px] text-gray-400">Получатель: <b class="text-white">B.K.</b></div>
                    </div>
                </div>
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Срок подписки</label>
                    <select name="months" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                        <option value="1">1 месяц — 200,000 UZS</option>
                        <option value="3">3 месяца — 550,000 UZS</option>
                        <option value="12">1 год — 2,000,000 UZS</option>
                    </select>
                </div>
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Номер квитанции или ID чека</label>
                    <input type="text" name="receipt" required placeholder="Например: №458921" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
                </div>
                <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 rounded text-xs uppercase tracking-wider transition shadow">Отправить чек администратору ✅</button>
            </form>
        </div>
    </div>
    <div id="supportModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="tv-panel p-6 w-full max-w-md space-y-4 shadow-2xl relative bg-[#1e222d] border border-[#2a2e39] text-[#d1d4dc]">
            <button onclick="closeSupportModal()" class="absolute top-4 right-4 text-gray-400 hover:text-white font-bold">✕</button>
            <div>
                <h3 class="text-sm font-bold text-white uppercase flex items-center gap-2">
                    <span>💬 Поддержка</span>
                    <span class="text-blue-400 text-lg">✈️</span>
                </h3>
                <p class="text-[11px] text-gray-400">Опишите вашу проблему или вопрос. Администратор получит уведомление в Telegram.</p>
            </div>
            <form action="/api/support" method="POST" class="space-y-3">
                <div>
                    <label class="block text-[10px] font-bold uppercase text-gray-400 mb-1">Сообщение</label>
                    <textarea name="message" required rows="4" class="w-full px-3 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border bg-[#131722] border-[#2a2e39] text-white" placeholder="Опишите вашу проблему..."></textarea>
                </div>
                <button type="submit" class="w-full bg-cyan-600 hover:bg-cyan-700 text-white font-bold py-2.5 rounded text-xs uppercase tracking-wider transition shadow flex items-center justify-center gap-2">
                    <span>Отправить в Telegram</span>
                    <span>✈️</span>
                </button>
            </form>
        </div>
    </div>
    <footer class="tv-panel px-6 py-4 mt-4 text-xs text-gray-400 flex flex-col md:flex-row justify-between items-center gap-4 border-t border-[#2a2e39]">
        <div class="flex flex-col gap-1 text-center md:text-left">
            <div class="text-white font-bold tracking-wider flex items-center gap-2 justify-center md:justify-start">
                <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                XARID ANALYTICS &copy; 2026
            </div>
            <p class="text-[11px] text-gray-500">Системный анализ и мониторинг электронных госзакупок (xarid.uzex.uz)</p>
        </div>
        <div class="flex flex-wrap justify-center gap-6 text-[11px]">
            <span>📍 г. Ташкент, Узбекистан</span>
            <a href="/profile" class="text-cyan-400 hover:underline">Мой профиль</a>
            <button onclick="openSupportModal()" class="text-cyan-400 hover:underline flex items-center gap-1">
                <span>✈️</span> Поддержка
            </button>
        </div>
    </footer>
    <script>
        let allDeals = []; let usdRate = 11820; let isUsd = false; let isAuthorized = false; let isSubActive = false; let expandedModelKey = null; let selectedCategory = ''; let currentLang = 'ru';
        const i18n = {
            ru: { searchPlaceholder: "🔎 Быстрый поиск по модели оборудования...", catAll: "Все", categoriesTitle: "Категории:", thCat: "Категория", thModel: "Модель оборудования", thStartPrice: "Стартовая цена (Мин)", thAgreedPrice: "Цена договора (Мин)", contractsCount: "Контрактов:", detailsTitle: "Деталика по модели:", btnUsd: "💱 Цена в USD", btnUzs: "💱 Цена в UZS", targetWin: "🏆 Рекомендуемая цена для победы (-1% от минимума):", givePrice: "⚡ Предложить цену", currencyLabel: "Валюта", thDate: "Дата", thQty: "Кол-во", noData: "Ничего не найдено или база пуста.", hiddenPriceText: "🔒 Нужна активная подписка", showAll: "Показать все", hideAll: "Скрыть" },
            en: { searchPlaceholder: "🔎 Quick search by equipment model...", catAll: "All", categoriesTitle: "Categories:", thCat: "Category", thModel: "Equipment Model", thStartPrice: "Starting Price (Min)", thAgreedPrice: "Contract Price (Min)", contractsCount: "Contracts:", detailsTitle: "Details for model:", btnUsd: "💱 Price in USD", btnUzs: "💱 Price in UZS", targetWin: "🏆 Recommended winning price (-1% from minimum):", givePrice: "⚡ Place bid", currencyLabel: "Currency", thDate: "Date", thQty: "Qty", noData: "Nothing found or database is empty.", hiddenPriceText: "🔒 Active subscription required", showAll: "Show all", hideAll: "Hide" },
            uz: { searchPlaceholder: "🔎 Jihoz modeli bo'yicha tezkor qidiruv...", catAll: "Barchasi", categoriesTitle: "Kategoriyalar:", thCat: "Kategoriya", thModel: "Jihoz modeli", thStartPrice: "Boshlang'ich narx (Min)", thAgreedPrice: "Shartnoma narxi (Min)", contractsCount: "Shartnomalar:", detailsTitle: "Model tafsilotlari:", btnUsd: "💱 USD narxi", btnUzs: "💱 UZS narxi", targetWin: "🏆 G'alaba uchun tavsiya etilgan narx (minimumdan -1%):", givePrice: "⚡ Narx berish", currencyLabel: "Valyuta", thDate: "Sana", thQty: "Soni", noData: "Hech narsa topilmadi yoki baza bo'sh.", hiddenPriceText: "🔒 Faol obuna talab etiladi", showAll: "Hammasini ko'rsat", hideAll: "Yopish" }
        };
        function setLanguage(lang) {
            currentLang = lang;
            ['ru','en','uz'].forEach(l => { let btn = document.getElementById('lang-'+l); if(l===lang) btn.classList.add('active'); else btn.classList.remove('active'); });
            document.getElementById('searchInput').placeholder = i18n[currentLang].searchPlaceholder;
            document.getElementById('catLabelText').innerText = i18n[currentLang].categoriesTitle;
            document.getElementById('thCat').innerText = i18n[currentLang].thCat;
            document.getElementById('thModel').innerText = i18n[currentLang].thModel;
            document.getElementById('thStartPrice').innerText = i18n[currentLang].thStartPrice;
            document.getElementById('thAgreedPrice').innerText = i18n[currentLang].thAgreedPrice;
            document.getElementById('currencyLabelText').innerText = i18n[currentLang].currencyLabel;
            renderHeaderCategories(allDeals);
            applyFilters();
        }
        function openLoginModal() { document.getElementById('loginModal').classList.remove('hidden'); }
        function closeLoginModal() { document.getElementById('loginModal').classList.add('hidden'); }
        function openForgotModal() { document.getElementById('forgotModal').classList.remove('hidden'); }
        function closeForgotModal() { document.getElementById('forgotModal').classList.add('hidden'); }
        function openRegisterModal() { document.getElementById('registerModal').classList.remove('hidden'); }
        function closeRegisterModal() { document.getElementById('registerModal').classList.add('hidden'); }
        function openPaymentModal() { document.getElementById('paymentModal').classList.remove('hidden'); }
        function closePaymentModal() { document.getElementById('paymentModal').classList.add('hidden'); }
        function openSupportModal() { document.getElementById('supportModal').classList.remove('hidden'); }
        function closeSupportModal() { document.getElementById('supportModal').classList.add('hidden'); }
        async function load() {
            try {
                let res = await fetch('https://cbu.uz/ru/arkhiv-kursov-valyut/json/');
                let data = await res.json();
                let usdObj = data.find(item => item.Code === '840');
                if (usdObj) usdRate = parseFloat(usdObj.Rate);
                let ratesHtml = '';
                if (usdObj) {
                    let diffColor = parseFloat(usdObj.Diff) >= 0 ? 'text-emerald-400' : 'text-rose-400';
                    let sign = parseFloat(usdObj.Diff) > 0 ? '+' : '';
                    for(let i=0; i<4; i++) {
                        ratesHtml += `<span class="inline-flex items-center gap-2 font-mono"><span class="font-bold text-white text-[13px]">USD</span>: <span class="text-cyan-300 font-semibold">${parseFloat(usdObj.Rate).toLocaleString()} UZS</span> <span class="${diffColor} text-[11px]">(${sign}${usdObj.Diff})</span><span class="text-gray-400 text-[11px] ml-1">от ${usdObj.Date}</span></span> <span class="text-gray-600 mx-3">|</span> `;
                    }
                }
                document.getElementById('ratesMarqueeTrack').innerHTML = ratesHtml;
            } catch(e) {
                document.getElementById('ratesMarqueeTrack').innerHTML = `<span class="text-gray-400">Курс ЦБ временно недоступен</span>`;
            }
            let authRes = await fetch('/api/check_auth');
            let authData = await authRes.json();
            isAuthorized = authData.authenticated;
            isSubActive = authData.sub_active;
            let role = authData.role; window.userRole = role;
            let authHeaderHtml = '';
            if (isAuthorized) {
                authHeaderHtml += `<a href="/profile" class="bg-gray-700/30 hover:bg-gray-700/50 text-gray-300 px-3 py-1.5 rounded text-xs font-bold transition flex items-center gap-1.5">👤 ${authData.username}</a>`;
                if (role === 'admin') {
                    authHeaderHtml += `<a href="/admin" class="bg-rose-500/10 border border-rose-500/30 hover:bg-rose-500/20 text-rose-400 px-3 py-1.5 rounded text-xs font-bold transition flex items-center gap-1"><span>🔒</span> Admin</a>`;
                }
                if (!isSubActive && role !== 'admin') {
                    document.getElementById('paymentPendingBanner').classList.remove('hidden');
                    authHeaderHtml += `<div class="bg-amber-500/10 border border-amber-500/30 text-amber-400 px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5"><span>⏳ Подписка не активна</span></div>`;
                } else if (role === 'admin') {
                    authHeaderHtml += `<div class="bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 px-3 py-1.5 rounded text-xs font-semibold">👤 ${authData.username} (Безлимит)</div>`;
                } else {
                    let daysLeft = authData.days_left;
                    let badgeColor = daysLeft <= 3 ? 'bg-rose-500/10 border-rose-500/30 text-rose-400' : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400';
                    authHeaderHtml += `<div class="${badgeColor} border px-3 py-1.5 rounded text-xs font-semibold flex items-center gap-1.5"><span>📅 До ${authData.expires_at} (${daysLeft} дн.)</span></div>`;
                }
                authHeaderHtml += `<a href="/logout" class="bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded text-xs font-bold transition">Выйти</a>`;
            } else {
                window.location.href = '/';
                return;
            }
            document.getElementById('authHeaderBlock').innerHTML = authHeaderHtml;
            let dealsRes = await fetch('/api/deals');
            allDeals = await dealsRes.json();
            renderHeaderCategories(allDeals);
            applyFilters();
        }
        function renderHeaderCategories(deals) {
            let cats = [...new Set(deals.map(d => d.category).filter(Boolean))];
            let allText = i18n[currentLang].catAll;
            let html = `<button onclick="filterByCategory('')" class="cat-badge ${selectedCategory === '' ? 'active' : ''}">${allText}</button>`;
            cats.forEach(c => {
                let isActive = (selectedCategory.toLowerCase() === c.toLowerCase());
                html += `<button onclick="filterByCategory('${c.replace(/'/g, '\\\'')}')" class="cat-badge ${isActive ? 'active' : ''}">${c}</button>`;
            });
            document.getElementById('headerCategoriesContainer').innerHTML = html;
        }
        function filterByCategory(cat) { selectedCategory = cat; expandedModelKey = null; renderHeaderCategories(allDeals); applyFilters(); }
        function resetToHome() { selectedCategory = ''; document.getElementById('searchInput').value = ''; expandedModelKey = null; renderHeaderCategories(allDeals); applyFilters(); }
        function toggleCurrency() { isUsd = !isUsd; document.getElementById('currencyBtnText').innerText = isUsd ? 'UZS' : 'USD'; applyFilters(); }
        function canSeeData() { return isAuthorized && (isSubActive || window.userRole === 'admin'); }
        function fmt(val) {
            if (!canSeeData()) return i18n[currentLang].hiddenPriceText;
            if (!val || val <= 0) return '—';
            return isUsd ? Number(val / usdRate).toLocaleString(undefined, {maximumFractionDigits: 2}) + ' $' : Number(val).toLocaleString() + ' UZS';
        }
        function fmtUsdOnly(val) {
            if (!canSeeData()) return i18n[currentLang].hiddenPriceText;
            if (!val || val <= 0) return '—';
            return Number(val / usdRate).toLocaleString(undefined, {maximumFractionDigits: 2}) + ' $';
        }
        let fullDealsMap = {};
        function renderTable(deals) {
            let groupedMap = {};
            deals.forEach(d => {
                let mKey = (d.model || "").trim().toLowerCase();
                if(!groupedMap[mKey]) groupedMap[mKey] = {model: d.model, category: d.category, dealsList: []};
                groupedMap[mKey].dealsList.push(d);
            });
            let html = '';
            Object.values(groupedMap).forEach(item => {
                let mKey = (item.model || "").trim().toLowerCase();
                let isExp = (expandedModelKey === mKey);
                let agreedPrices = item.dealsList.map(d => d.agreed_price || 0).filter(p => p > 0);
                let startPrices = item.dealsList.map(d => d.start_price || 0).filter(p => p > 0);
                let minAgreed = agreedPrices.length ? Math.min(...agreedPrices) : 0;
                let minStart = startPrices.length ? Math.min(...startPrices) : 0;
                let targetPrice = minAgreed * 0.99;
                let t = i18n[currentLang];
                let startPriceDisplay = fmt(minStart);
                let agreedPriceDisplay = fmt(minAgreed);
                let blurClass = !canSeeData() ? 'blur-price text-gray-500' : '';
                html += `<div>
                    <div onclick="toggleRow('${item.model.replace(/'/g, '\\\'')}')" class="grid grid-cols-4 items-center cursor-pointer row-hover transition compact-row ${isExp ? 'bg-cyan-600/10 border-l-4 border-cyan-500' : ''}">
                        <div class="text-cyan-400 font-semibold truncate pr-2">${item.category || '—'}</div>
                        <div class="font-bold text-white truncate pr-2">${item.model || '—'}</div>
                        <div class="text-gray-400 ${blurClass}">${startPriceDisplay}</div>
                        <div class="text-emerald-400 font-bold flex items-center justify-between">
                            <span class="${blurClass}">${agreedPriceDisplay}</span>
                            <span class="text-[10px] text-gray-500 font-normal">${t.contractsCount} ${item.dealsList.length} ▼</span>
                        </div>
                    </div>`;
                if(isExp) {
                    let modalId = 'sub_' + mKey.replace(/[^a-z0-9]/g, '_');
                    fullDealsMap[modalId] = item.dealsList;
                    let sortedDeals = item.dealsList.slice().sort((a,b) => new Date(b.date) - new Date(a.date));
                    let showCount = 5;
                    let limitedDeals = sortedDeals.slice(0, showCount);
                    let moreCount = sortedDeals.length - showCount;
                    html += `<div class="tv-subpanel border-t border-[#2a2e39] bg-[#141720] compact-detail">
                        <div class="tv-panel p-2 rounded space-y-1.5">
                            <div class="flex justify-between items-center">
                                <h4 class="font-bold text-cyan-400 text-[10px] uppercase tracking-wide">${t.detailsTitle} ${item.model}</h4>`;
                    if (canSeeData()) {
                        html += `<button onclick="event.stopPropagation(); toggleSubCurrency('${modalId}')" id="${modalId}_btn" class="bg-cyan-600/20 border border-cyan-500/40 text-cyan-300 px-2 py-0.5 rounded text-[9px] font-bold transition">${t.btnUsd}</button>`;
                    }
                    html += `</div>`;
                    if (!canSeeData()) {
                        html += `<div class="p-2 text-center space-y-1 bg-[#131722] rounded border border-dashed border-gray-700 text-xs">
                            <p class="text-gray-400 text-[10px]">Для просмотра детальной истории и аналитики необходима активная подписка.</p>
                            <button onclick="openPaymentModal()" class="bg-amber-600 hover:bg-amber-700 text-white px-3 py-1 rounded text-[9px] font-bold transition">Отправить чек об оплате 💳</button>
                        </div>`;
                    } else {
                        html += `<table class="w-full text-left detail-table">
                            <thead><tr class="text-gray-500 border-b border-[#2a2e39]"><th class="pb-0.5">${t.thDate}</th><th class="pb-0.5">${t.thQty}</th><th class="pb-0.5">${t.thStartPrice}</th><th class="pb-0.5">${t.thAgreedPrice}</th></tr></thead>
                            <tbody id="${modalId}_tbody">
                                ${limitedDeals.map(d => `<tr><td class="text-gray-400">${d.date}</td><td>${d.quantity || 1}</td><td data-val="${d.start_price}">${fmt(d.start_price)}</td><td class="text-emerald-400 font-bold" data-val="${d.agreed_price}">${fmt(d.agreed_price)}</td></tr>`).join('')}
                            </tbody>
                        </table>`;
                        if (moreCount > 0) {
                            html += `<div class="mt-1 text-right">
                                <button onclick="event.stopPropagation(); showAllDeals('${modalId}')" class="btn-show-all">${t.showAll} (${moreCount})</button>
                            </div>`;
                        }
                        html += `<div class="p-1.5 rounded bg-cyan-950/40 border border-cyan-500/30 flex justify-between items-center text-[9px] mt-1">
                            <span class="text-cyan-300 font-bold" id="${modalId}_target" data-val="${targetPrice}">${t.targetWin} ${fmt(targetPrice)}</span>
                            <span class="bg-emerald-500/20 text-emerald-400 font-bold px-2 py-0.5 rounded">${t.givePrice}</span>
                        </div>`;
                    }
                    html += `</div></div>`;
                }
                html += `</div>`;
            });
            document.getElementById('dealsContainer').innerHTML = html || `<div class="p-8 text-center text-gray-500 text-xs">${i18n[currentLang].noData}</div>`;
        }
        function showAllDeals(modalId) {
            let tbody = document.getElementById(modalId + '_tbody');
            if (!tbody) return;
            let fullList = fullDealsMap[modalId];
            if (!fullList) return;
            let sorted = fullList.slice().sort((a,b) => new Date(b.date) - new Date(a.date));
            let t = i18n[currentLang];
            tbody.innerHTML = sorted.map(d => 
                `<tr><td class="text-gray-400">${d.date}</td><td>${d.quantity || 1}</td><td data-val="${d.start_price}">${fmt(d.start_price)}</td><td class="text-emerald-400 font-bold" data-val="${d.agreed_price}">${fmt(d.agreed_price)}</td></tr>`
            ).join('');
            let parent = tbody.closest('.tv-panel');
            let btnContainer = parent.querySelector('.mt-1.text-right');
            if (btnContainer) {
                btnContainer.innerHTML = `<button onclick="event.stopPropagation(); hideAllDeals('${modalId}')" class="btn-show-all">${t.hideAll}</button>`;
            }
        }
        function hideAllDeals(modalId) {
            let tbody = document.getElementById(modalId + '_tbody');
            if (!tbody) return;
            let fullList = fullDealsMap[modalId];
            if (!fullList) return;
            let sorted = fullList.slice().sort((a,b) => new Date(b.date) - new Date(a.date));
            let limited = sorted.slice(0, 5);
            let moreCount = sorted.length - 5;
            let t = i18n[currentLang];
            tbody.innerHTML = limited.map(d => 
                `<tr><td class="text-gray-400">${d.date}</td><td>${d.quantity || 1}</td><td data-val="${d.start_price}">${fmt(d.start_price)}</td><td class="text-emerald-400 font-bold" data-val="${d.agreed_price}">${fmt(d.agreed_price)}</td></tr>`
            ).join('');
            let parent = tbody.closest('.tv-panel');
            let btnContainer = parent.querySelector('.mt-1.text-right');
            if (btnContainer) {
                if (moreCount > 0) {
                    btnContainer.innerHTML = `<button onclick="event.stopPropagation(); showAllDeals('${modalId}')" class="btn-show-all">${t.showAll} (${moreCount})</button>`;
                } else {
                    btnContainer.innerHTML = '';
                }
            }
        }
        function toggleSubCurrency(modalId) {
            let btn = document.getElementById(modalId + '_btn');
            let tbody = document.getElementById(modalId + '_tbody');
            let target = document.getElementById(modalId + '_target');
            let t = i18n[currentLang];
            let isShowingUsd = btn.getAttribute('data-usd') === 'true';
            isShowingUsd = !isShowingUsd;
            btn.setAttribute('data-usd', isShowingUsd);
            btn.innerText = isShowingUsd ? t.btnUzs : t.btnUsd;
            tbody.querySelectorAll('tr').forEach(r => {
                let tds = r.querySelectorAll('td');
                if (tds.length >= 4) {
                    let sVal = parseFloat(tds[2].getAttribute('data-val') || 0);
                    let aVal = parseFloat(tds[3].getAttribute('data-val') || 0);
                    tds[2].innerText = isShowingUsd ? fmtUsdOnly(sVal) : Number(sVal).toLocaleString() + ' UZS';
                    tds[3].innerText = isShowingUsd ? fmtUsdOnly(aVal) : Number(aVal).toLocaleString() + ' UZS';
                }
            });
            let tVal = parseFloat(target.getAttribute('data-val') || 0);
            target.innerText = `${t.targetWin} ` + (isShowingUsd ? fmtUsdOnly(tVal) : Number(tVal).toLocaleString() + ' UZS');
        }
        function toggleRow(m) {
            expandedModelKey = (expandedModelKey === m.trim().toLowerCase()) ? null : m.trim().toLowerCase();
            applyFilters();
        }
        function applyFilters() {
            let s = document.getElementById('searchInput').value.toLowerCase();
            let filtered = allDeals.filter(d => {
                let matchSearch = (d.model || "").toLowerCase().includes(s);
                let matchCat = selectedCategory === '' || (d.category || "").toLowerCase() === selectedCategory.toLowerCase();
                return matchSearch && matchCat;
            });
            renderTable(filtered);
        }
        load();
    </script>
</body>
</html>
"""

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
            delta = exp_date - datetime.now()
            days_left = delta.days
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
    # Статистика
    cur = execute_query(conn, "SELECT COUNT(*) FROM user_logs")
    total_logs = cur.fetchone()[0]
    cur = execute_query(conn, "SELECT COUNT(DISTINCT username) FROM user_logs WHERE date(timestamp) = date('now')")
    unique_today = cur.fetchone()[0]
    cur = execute_query(conn, "SELECT COUNT(DISTINCT username) FROM user_logs WHERE timestamp > datetime('now', '-7 days')")
    unique_week = cur.fetchone()[0]
    cur = execute_query(conn, "SELECT * FROM user_logs ORDER BY timestamp DESC LIMIT 20")
    logs = cur.fetchall()
    
    cur = execute_query(conn, "SELECT COUNT(*) FROM users WHERE role != 'admin' AND created_at > datetime('now', '-7 days')")
    new_registrations = cur.fetchone()[0]
    cur = execute_query(conn, "SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
    pending_payments = cur.fetchone()[0]
    
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
