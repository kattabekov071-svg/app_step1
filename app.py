import io
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import sqlite3
import hashlib
import secrets
import uvicorn
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"pbkdf2_sha256$120000${salt}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return secrets.compare_digest(password, stored)  # legacy compatibility
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return secrets.compare_digest(check.hex(), digest)
    except Exception:
        return False


# НАСТРОЙКИ ПОЧТЫ (Замените на свои реальные данные при запуске)
SMTP_SERVER = "smtp.gmail.com"  # Например, для Gmail
SMTP_PORT = 587
SMTP_USER = "your_email@gmail.com"  # Ваша почта
SMTP_PASSWORD = "your_app_password"  # Пароль приложения (не основной пароль от почты!)

def init_db():
    conn = sqlite3.connect("tender.db")
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, date TEXT, model TEXT,
            start_price REAL, agreed_price REAL,
            upload_id INTEGER,
            UNIQUE(date, model, agreed_price)
        )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT, category TEXT, rows_count INTEGER, upload_date TEXT
        )''')
    
    # Добавили поле email в таблицу пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT,
            expires_at TEXT
        )''')
    
    # Таблица для одноразовых токенов сброса пароля
    cursor.execute('''CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            token TEXT,
            expires_at TEXT
        )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS payment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            months INTEGER,
            receipt TEXT,
            status TEXT,
            created_at TEXT
        )''')
    
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, ?, ?)",
                       ("admin", "admin@xarid.uz", hash_password("tender2026"), "admin", "2099-12-31"))
        
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect("tender.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_current_user(request: Request):
    session_user = request.cookies.get("session_user")
    if not session_user:
        return None
    
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (session_user,)).fetchone()
    conn.close()
    return user

def send_reset_email(to_email: str, reset_link: str):
    """Функция отправки письма со ссылкой сброса"""
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = "Восстановление пароля — XARID ANALYTICS"
        
        body = f"""
        Здравствуйте!
        Кто-то запросил сброс пароля для вашего аккаунта в XARID ANALYTICS.
        Если это были не вы, просто проигнорируйте это письмо.
        
        Для сброса пароля перейдите по ссылке (действительна 15 минут):
        {reset_link}
        """
        msg.attach(MIMEText(body, 'plain'))
        
        # Если вы еще не настроили SMTP, письмо упадет в консоль для отладки
        if SMTP_USER == "your_email@gmail.com":
            print(f"\n[DEBUG EMAIL] Ссылка для сброса пароля на {to_email}:\n{reset_link}\n")
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

UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.tailwindcss.com"></script>
    <title>XARID ANALYTICS - Мониторинг госзакупок Узбекистана</title>
    <style>
        body { background-color: #131722; color: #d1d4dc; font-family: 'Segoe UI', sans-serif; }
        .tv-panel { background-color: #1e222d; border: 1px solid #2a2e39; border-radius: 6px; }
        .tv-subpanel { background-color: #181c25; border: 1px solid #2a2e39; }
        .row-hover:hover { background-color: rgba(6, 182, 212, 0.08); }
        input, select { background-color: #131722 !important; border-color: #2a2e39 !important; color: white !important; }
        .cat-badge { background: #2a2e39; border: 1px solid #363c4e; color: #d1d4dc; font-size: 11px; padding: 4px 10px; border-radius: 4px; cursor: pointer; transition: all 0.2s; white-space: nowrap; font-weight: 600; }
        .cat-badge:hover, .cat-badge.active { background: #06b6d4; border-color: #22d3ee; color: white; }
        .lang-btn { background: #2a2e39; border: 1px solid #363c4e; color: #d1d4dc; font-size: 11px; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; gap: 5px; font-weight: 600; }
        .lang-btn:hover, .lang-btn.active { background: #06b6d4; border-color: #22d3ee; color: white; }
        .logo-emblem { background: linear-gradient(135deg, #06b6d4 0%, #0369a1 100%); box-shadow: 0 0 15px rgba(6, 182, 212, 0.4); border: 2px solid #22d3ee; }
        @keyframes marquee { 0% { transform: translateX(0%); } 100% { transform: translateX(-50%); } }
        .marquee-container { overflow: hidden; white-space: nowrap; display: flex; width: 100%; }
        .marquee-track { display: inline-flex; gap: 50px; animation: marquee 10s linear infinite; }
        .marquee-track:hover { animation-play-state: paused; }
        .blur-price { filter: blur(6px); user-select: none; }
    </style>
</head>
<body class="min-h-screen flex flex-col p-4 max-w-[1600px] mx-auto gap-4 justify-between">
    
    <div class="flex flex-col gap-4 flex-1">
        <header class="tv-panel px-5 py-3.5 flex flex-col gap-3 shadow-xl bg-gradient-to-r from-[#1e222d] via-[#1a1f2c] to-[#1e222d]">
            <div class="flex justify-between items-center">
                <div class="flex items-center gap-3.5 cursor-pointer select-none group" onclick="resetToHome()">
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

        <!-- Блок ожидания оплаты -->
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

        <!-- Гостевой баннер -->
        <div id="guestBanner" class="bg-gradient-to-r from-cyan-900/40 via-blue-900/30 to-[#1e222d] border border-cyan-500/30 p-4 rounded-lg flex justify-between items-center hidden">
            <div class="flex items-center gap-3">
                <span class="text-xl">💡</span>
                <div>
                    <h4 class="text-xs font-bold text-white uppercase tracking-wider">Добро пожаловать в терминал</h4>
                    <p class="text-[11px] text-gray-300">Создайте бесплатный аккаунт, чтобы получить доступ к системе и оформить подписку.</p>
                </div>
            </div>
            <div class="flex gap-2">
                <button onclick="openLoginModal()" class="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded text-xs font-bold transition">Войти</button>
                <button onclick="openRegisterModal()" class="bg-cyan-600 hover:bg-cyan-700 text-white px-4 py-2 rounded text-xs font-bold transition">Создать аккаунт 🚀</button>
            </div>
        </div>

        <div class="tv-panel p-3 flex items-center gap-2 overflow-x-auto">
            <span class="text-[11px] font-bold uppercase text-gray-400 mr-2 shrink-0" id="catLabelText">Категории:</span>
            <div class="flex items-center gap-1.5 overflow-x-auto" id="headerCategoriesContainer"></div>
        </div>

        <div class="tv-panel p-3">
            <input type="text" id="searchInput" oninput="applyFilters()" placeholder="🔎 Быстрый поиск по модели оборудования..." class="w-full px-3.5 py-2 rounded text-xs outline-none focus:border-cyan-500 transition border">
        </div>

        <div class="tv-panel overflow-hidden flex-1 flex flex-col">
            <div class="grid grid-cols-4 px-4 py-3 bg-[#151922] border-b border-[#2a2e39] text-[11px] font-bold uppercase text-gray-400 tracking-wider">
                <div id="thCat">Категория</div>
                <div id="thModel">Модель оборудования</div>
                <div id="thStartPrice">Стартовая цена (Мин)</div>
                <div id="thAgreedPrice">Цена договора (Мин)</div>
            </div>
            <div class="divide-y divide-[#2a2e39] overflow-y-auto max-h-[550px]" id="dealsContainer"></div>
        </div>
    </div>

    <!-- Модальное окно входа -->
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

    <!-- Модальное окно восстановления пароля -->
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

    <!-- Модальное окно регистрации -->
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

    <!-- Модальное окно отправки чека -->
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
        </div>
    </footer>

    <script>
        let allDeals = []; 
        let usdRate = 11820; 
        let isUsd = false;
        let isAuthorized = false;
        let isSubActive = false;
        let expandedModelKey = null;
        let selectedCategory = '';
        let currentLang = 'ru';

        const i18n = {
            ru: { searchPlaceholder: "🔎 Быстрый поиск по модели оборудования...", catAll: "Все", categoriesTitle: "Категории:", thCat: "Категория", thModel: "Модель оборудования", thStartPrice: "Стартовая цена (Мин)", thAgreedPrice: "Цена договора (Мин)", contractsCount: "Контрактов:", detailsTitle: "Деталика по модели:", btnUsd: "💱 Цена в USD", btnUzs: "💱 Цена в UZS", targetWin: "🏆 Рекомендуемая цена для победы (-1% от минимума):", givePrice: "⚡ Предложить цену", currencyLabel: "Валюта", thDate: "Дата", noData: "Ничего не найдено или база пуста.", hiddenPriceText: "🔒 Нужна активная подписка" },
            en: { searchPlaceholder: "🔎 Quick search by equipment model...", catAll: "All", categoriesTitle: "Categories:", thCat: "Category", thModel: "Equipment Model", thStartPrice: "Starting Price (Min)", thAgreedPrice: "Contract Price (Min)", contractsCount: "Contracts:", detailsTitle: "Details for model:", btnUsd: "💱 Price in USD", btnUzs: "💱 Price in UZS", targetWin: "🏆 Recommended winning price (-1% from minimum):", givePrice: "⚡ Place bid", currencyLabel: "Currency", thDate: "Date", noData: "Nothing found or database is empty.", hiddenPriceText: "🔒 Active subscription required" },
            uz: { searchPlaceholder: "🔎 Jihoz modeli bo'yicha tezkor qidiruv...", catAll: "Barchasi", categoriesTitle: "Kategoriyalar:", thCat: "Kategoriya", thModel: "Jihoz modeli", thStartPrice: "Boshlang'ich narx (Min)", thAgreedPrice: "Shartnoma narxi (Min)", contractsCount: "Shartnomalar:", detailsTitle: "Model tafsilotlari:", btnUsd: "💱 USD narxi", btnUzs: "💱 UZS narxi", targetWin: "🏆 G'alaba uchun tavsiya etilgan narx (minimumdan -1%):", givePrice: "⚡ Narx berish", currencyLabel: "Valyuta", thDate: "Sana", noData: "Hech narsa topilmadi yoki baza bo'sh.", hiddenPriceText: "🔒 Faol obuna talab etiladi" }
        };

        function setLanguage(lang) {
            currentLang = lang;
            ['ru', 'en', 'uz'].forEach(l => {
                let btn = document.getElementById('lang-' + l);
                if(l === lang) btn.classList.add('active'); else btn.classList.remove('active');
            });
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
                    for(let i = 0; i < 4; i++) {
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
                document.getElementById('guestBanner').classList.remove('hidden');
                authHeaderHtml += `<button onclick="openLoginModal()" class="bg-gray-800 hover:bg-gray-700 text-white px-3 py-1.5 rounded text-xs font-bold transition">Войти</button>`;
                authHeaderHtml += `<button onclick="openRegisterModal()" class="bg-cyan-600 hover:bg-cyan-700 text-white px-3 py-1.5 rounded text-xs font-bold transition">Регистрация</button>`;
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

        function filterByCategory(cat) {
            selectedCategory = cat;
            expandedModelKey = null;
            renderHeaderCategories(allDeals);
            applyFilters();
        }

        function resetToHome() {
            selectedCategory = '';
            document.getElementById('searchInput').value = '';
            expandedModelKey = null;
            renderHeaderCategories(allDeals);
            applyFilters();
        }

        function toggleCurrency() { 
            isUsd = !isUsd; 
            document.getElementById('currencyBtnText').innerText = isUsd ? 'UZS' : 'USD';
            applyFilters(); 
        }

        function canSeeData() {
            return isAuthorized && (isSubActive || window.userRole === 'admin');
        }

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
                    <div onclick="toggleRow('${item.model.replace(/'/g, '\\\'')}')" class="p-3.5 grid grid-cols-4 items-center cursor-pointer row-hover transition ${isExp ? 'bg-cyan-600/10 border-l-4 border-cyan-500' : ''}">
                        <div class="text-cyan-400 font-semibold text-xs truncate pr-2">${item.category || '—'}</div>
                        <div class="font-bold text-xs text-white truncate pr-2">${item.model || '—'}</div>
                        <div class="text-gray-400 text-xs ${blurClass}">${startPriceDisplay}</div>
                        <div class="text-emerald-400 font-bold text-xs flex items-center justify-between">
                            <span class="${blurClass}">${agreedPriceDisplay}</span>
                            <span class="text-[10px] text-gray-500 font-normal">${t.contractsCount} ${item.dealsList.length} ▼</span>
                        </div>
                    </div>`;
                
                if(isExp) {
                    let modalId = 'sub_' + mKey.replace(/[^a-z0-9]/g, '_');
                    html += `<div class="p-4 tv-subpanel border-t border-[#2a2e39] bg-[#141720]">
                        <div class="tv-panel p-4 rounded space-y-3">
                            <div class="flex justify-between items-center">
                                <h4 class="font-bold text-cyan-400 text-xs uppercase tracking-wide">${t.detailsTitle} ${item.model}</h4>`;
                    if (canSeeData()) {
                        html += `<button onclick="event.stopPropagation(); toggleSubCurrency('${modalId}')" id="${modalId}_btn" class="bg-cyan-600/20 border border-cyan-500/40 text-cyan-300 px-2.5 py-1 rounded text-[11px] font-bold transition">${t.btnUsd}</button>`;
                    }
                    html += `</div>`;
                    
                    if (!canSeeData()) {
                        html += `<div class="p-6 text-center space-y-3 bg-[#131722] rounded border border-dashed border-gray-700">
                            <p class="text-xs text-gray-400">Для просмотра детальной истории и аналитики необходима активная подписка.</p>
                            <button onclick="openPaymentModal()" class="bg-amber-600 hover:bg-amber-700 text-white px-4 py-2 rounded text-xs font-bold transition">Отправить чек об оплате 💳</button>
                        </div>`;
                    } else {
                        html += `<table class="w-full text-left text-xs">
                            <thead><tr class="text-gray-500 border-b border-[#2a2e39]"><th class="pb-2">${t.thDate}</th><th class="pb-2">${t.thStartPrice}</th><th class="pb-2">${t.thAgreedPrice}</th></tr></thead>
                            <tbody class="divide-y divide-[#2a2e39]/50" id="${modalId}_tbody">
                                ${item.dealsList.map(d => `<tr><td class="py-2 text-gray-400">${d.date}</td><td class="py-2" data-val="${d.start_price}">${fmt(d.start_price)}</td><td class="py-2 text-emerald-400 font-bold" data-val="${d.agreed_price}">${fmt(d.agreed_price)}</td></tr>`).join('')}
                            </tbody>
                        </table>
                        <div class="p-3 rounded bg-cyan-950/40 border border-cyan-500/30 flex justify-between items-center text-xs mt-2">
                            <span class="text-cyan-300 font-bold" id="${modalId}_target" data-val="${targetPrice}">${t.targetWin} ${fmt(targetPrice)}</span>
                            <span class="bg-emerald-500/20 text-emerald-400 font-bold px-2.5 py-1 rounded">${t.givePrice}</span>
                        </div>`;
                    }
                    html += `</div></div>`;
                }
                html += `</div>`;
            });
            document.getElementById('dealsContainer').innerHTML = html || `<div class="p-8 text-center text-gray-500 text-xs">${i18n[currentLang].noData}</div>`;
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
                if (tds.length >= 3) {
                    let sVal = parseFloat(tds[1].getAttribute('data-val') || 0);
                    let aVal = parseFloat(tds[2].getAttribute('data-val') || 0);
                    tds[1].innerText = isShowingUsd ? fmtUsdOnly(sVal) : Number(sVal).toLocaleString() + ' UZS';
                    tds[2].innerText = isShowingUsd ? fmtUsdOnly(aVal) : Number(aVal).toLocaleString() + ' UZS';
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
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, email, password, role, expires_at) VALUES (?, ?, ?, 'client', '2026-01-01')",
                       (username, email, hash_password(password)))
        conn.commit()
    except Exception as e:
        conn.close()
        return HTMLResponse("<script>alert('Такой логин или email уже занят!'); window.location.href='/';</script>")
    conn.close()
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="session_user", value=username, httponly=True)
    return response

@app.post("/login")
async def login_post(username: str = Form(...), password: str = Form(...)):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password)).fetchone()
    conn.close()
    
    if not user:
        return HTMLResponse("<script>alert('Неверный логин или пароль!'); window.location.href='/';</script>")
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="session_user", value=username, httponly=True)
    return response

@app.post("/api/forgot_password")
async def forgot_password(request: Request, email: str = Form(...)):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    if user:
        # Генерируем уникальный случайный токен
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
        expires_at = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute("INSERT INTO password_resets (email, token, expires_at) VALUES (?, ?, ?)", (email, token, expires_at))
        conn.commit()
        
        # Формируем ссылку для сброса
        host_url = str(request.base_url).rstrip('/')
        reset_link = f"{host_url}/reset_password?token={token}"
        
        send_reset_email(email, reset_link)
        
    conn.close()
    
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Письмо отправлено</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans flex items-center justify-center min-h-screen p-4">
        <div class="tv-panel bg-[#1e222d] border border-[#2a2e39] p-8 rounded-lg max-w-md w-full text-center space-y-4 shadow-2xl">
            <div class="text-4xl">✉️</div>
            <h2 class="text-base font-bold text-white uppercase tracking-wider">Инструкция отправлена</h2>
            <p class="text-xs text-gray-300">Если указанный E-mail зарегистрирован в системе, на него отправлено письмо со ссылкой для сброса пароля (проверьте также папку Спам или консоль терминала).</p>
            <a href="/" class="inline-block w-full bg-cyan-600 hover:bg-cyan-700 text-white py-2.5 rounded text-xs font-bold uppercase tracking-wider transition">На главную</a>
        </div>
    </body>
    </html>
    """)

@app.get("/reset_password", response_class=HTMLResponse)
def reset_password_page(token: str):
    conn = get_db_connection()
    reset_entry = conn.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
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
    cursor = conn.cursor()
    reset_entry = cursor.execute("SELECT * FROM password_resets WHERE token = ?", (token,)).fetchone()
    
    if not reset_entry:
        conn.close()
        return HTMLResponse("<script>alert('Неверный или просроченный токен!'); window.location.href='/';</script>")
    
    email = reset_entry["email"]
    cursor.execute("UPDATE users SET password = ? WHERE email = ?", (hash_password(new_password), email))
    cursor.execute("DELETE FROM password_resets WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    
    return HTMLResponse("<script>alert('Пароль успешно изменен! Теперь вы можете войти.'); window.location.href='/';</script>")

@app.post("/api/submit_payment")
async def submit_payment(request: Request, months: int = Form(...), receipt: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO payment_requests (username, months, receipt, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                       (user["username"], months, receipt, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
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
            <p class="text-xs text-gray-300">Администратор проверяет поступление средств на карту Humo. Доступ к ценам откроется сразу после подтверждения платежа.</p>
            <a href="/" class="inline-block w-full bg-cyan-600 hover:bg-cyan-700 text-white py-2.5 rounded text-xs font-bold uppercase tracking-wider transition">Вернуться в терминал</a>
        </div>
    </body>
    </html>
    """)

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="session_user")
    return response

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return UI_TEMPLATE

@app.get("/api/deals")
def get_deals(request: Request):
    user = get_current_user(request)

    # Без авторизации цены и история сделок не выдаются.
    if not user:
        return []

    # Администратор всегда имеет полный доступ.
    if user["role"] != "admin":
        expires_at = user["expires_at"]
        try:
            active = datetime.strptime(expires_at, "%Y-%m-%d") >= datetime.now()
        except Exception:
            active = False

        if not active:
            return []

    conn = get_db_connection()
    deals = [dict(row) for row in conn.execute("SELECT * FROM deals").fetchall()]
    conn.close()
    return deals

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/", status_code=303)
    
    conn = get_db_connection()
    uploads = conn.execute("SELECT * FROM uploads ORDER BY id DESC").fetchall()
    users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    pay_requests = conn.execute("SELECT * FROM payment_requests ORDER BY id DESC").fetchall()
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

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><script src="https://cdn.tailwindcss.com"></script><title>Admin Panel</title></head>
    <body class="bg-[#131722] text-[#d1d4dc] font-sans p-8">
        <div class="max-w-4xl mx-auto space-y-6">
            <div class="flex justify-between items-center border-b border-[#2a2e39] pb-4">
                <h1 class="text-sm font-bold uppercase text-white tracking-wider">Панель управления администратора</h1>
                <a href="/" class="text-xs text-cyan-400 hover:underline">← На главную терминала</a>
            </div>
            
            <div class="bg-[#1e222d] border border-[#2a2e39] p-6 rounded shadow">
                <h2 class="text-xs font-semibold uppercase text-amber-400 mb-4">💳 Заявки на подтверждение оплаты (Humo)</h2>
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
    cursor = conn.cursor()
    req = cursor.execute("SELECT * FROM payment_requests WHERE id = ?", (req_id,)).fetchone()
    if req:
        days = int(req["months"]) * 30
        old_user = cursor.execute("SELECT expires_at FROM users WHERE username = ?", (req["username"],)).fetchone()
        base_date = datetime.now()
        if old_user and old_user["expires_at"]:
            try:
                parsed_old = datetime.strptime(old_user["expires_at"], "%Y-%m-%d")
                if parsed_old > datetime.now():
                    base_date = parsed_old
            except:
                pass
                
        expires_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE users SET expires_at = ? WHERE username = ?", (expires_date, req["username"]))
        cursor.execute("DELETE FROM payment_requests WHERE id = ?", (req_id,))
        conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Оплата одобрена, подписка успешно продлена!'); window.location.href='/admin';</script>")

@app.post("/admin/reject_payment/{req_id}")
def reject_payment(req_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
        
    conn = get_db_connection()
    conn.execute("DELETE FROM payment_requests WHERE id = ?", (req_id,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Заявка удалена.'); window.location.href='/admin';</script>")

@app.post("/admin/delete_user/{user_id}")
def delete_user(user_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
        
    conn = get_db_connection()
    conn.execute("DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Пользователь удален!'); window.location.href='/admin';</script>")

@app.post("/upload")
async def upload_files(category: str = Form(...), files: list[UploadFile] = File(...), request: Request = None):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
        
    conn = get_db_connection()
    cursor = conn.cursor()
    for file in files:
        try:
            contents = await file.read()
            df = pd.read_excel(io.BytesIO(contents))
            cursor.execute('INSERT INTO uploads (filename, category, rows_count, upload_date) VALUES (?, ?, ?, ?)', (file.filename, category, len(df), datetime.now().strftime("%Y-%m-%d")))
            upload_id = cursor.lastrowid
            for _, row in df.iterrows():
                try:
                    date_val = str(row.iloc[0]).split('T')[0] if pd.notna(row.iloc[0]) else ""
                    model_val = str(row.iloc[1]) if len(row) > 1 and pd.notna(row.iloc[1]) else ""
                    start_p = float(row.iloc[3]) if len(row) > 3 and pd.notna(row.iloc[3]) else 0.0
                    agreed_p = float(row.iloc[4]) if len(row) > 4 and pd.notna(row.iloc[4]) else 0.0
                    if model_val and model_val.lower() != 'nan':
                        cursor.execute('INSERT OR IGNORE INTO deals (category, date, model, start_price, agreed_price, upload_id) VALUES (?, ?, ?, ?, ?, ?)', 
                                       (category, date_val, model_val, start_p, agreed_p, upload_id))
                except: continue
        except Exception as e: print(e)
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Файлы успешно импортированы!'); window.location.href='/admin';</script>")

@app.post("/admin/delete/{upload_id}")
def delete_upload(upload_id: int, request: Request):
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403)
        
    conn = get_db_connection()
    conn.execute("DELETE FROM deals WHERE upload_id = ?", (upload_id,))
    conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    conn.commit()
    conn.close()
    return HTMLResponse("<script>alert('Успешно удалено!'); window.location.href='/admin';</script>")

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)