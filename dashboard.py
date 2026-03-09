import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import gspread
import toml
import os
import sys
import json
import plotly.express as px
from io import BytesIO
from fpdf import FPDF
from datetime import datetime

# ---------------------------------------------------------
# 0. ИНИЦИАЛИЗАЦИЯ ПУТЕЙ (Критично для импорта miner_scanner)
# ---------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    import miner_scanner
except ImportError as e:
    print(f"CRITICAL ERROR: Не могу найти miner_scanner! {e}")
    miner_scanner = None

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="MinerHotel Monitor", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
    .block-container {padding-top: 1rem; padding-bottom: 3rem;}
    [data-testid="stMetricValue"] {font-size: 24px; color: #00E676;}
    iframe {border-radius: 10px; border: 1px solid #ddd;}
    div[data-testid="stDownloadButton"] > button {width: 100%;}
    div[data-testid="stButton"] > button {width: 100%;}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 1. УПРАВЛЕНИЕ ПУТЯМИ КОНФИГОВ
# ---------------------------------------------------------

def get_config_path(filename):
    return os.path.join(BASE_DIR, filename)

def get_internal_path(filename):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(BASE_DIR, filename)

RANGES_FILE = get_config_path("ip_ranges.json")
SECRETS_FILE = get_config_path("secrets.toml")
CREDS_FILE = get_config_path("credentials.json")
if not os.path.exists(CREDS_FILE):
    CREDS_FILE = get_internal_path("credentials.json")

# --- УПРАВЛЕНИЕ ДИАПАЗОНАМИ ---
def load_ranges_df():
    default_data = [{"name": "Локальная сеть", "range": "192.168.1.1-255"}]
    if os.path.exists(RANGES_FILE):
        try:
            with open(RANGES_FILE, "r", encoding="utf-8") as f: 
                data = json.load(f)
                return pd.DataFrame(data)
        except: pass
    return pd.DataFrame(default_data)

def save_ranges_df(df):
    try:
        data = df.to_dict(orient="records")
        with open(RANGES_FILE, "w", encoding="utf-8") as f: 
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"Ошибка сохранения настроек: {e}")

# --- ЗАГРУЗКА SECRETS ---
try:
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "r", encoding="utf-8") as f: MY_SECRETS = toml.load(f)
    else:
        MY_SECRETS = {}
except: MY_SECRETS = {}

# ---------------------------------------------------------
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------
def clean_currency_hardcore(value):
    s = str(value).strip()
    s = ''.join(c for c in s if c.isdigit() or c in '.,-')
    if not s: return 0.0
    if ',' in s: s = s.replace('.', '').replace(',', '.')
    elif s.count('.') > 1: s = s.replace('.', '', s.count('.') - 1)
    try: return float(s)
    except: return 0.0

@st.cache_data(persist="disk", show_spinner="Загрузка базы данных...")
def get_database():
    try:
        url = MY_SECRETS.get("google", {}).get("sheet_url")
        gc = gspread.service_account(filename=CREDS_FILE)
        sh = gc.open_by_url(url)
        raw = sh.worksheet("Реестр оборудования").get_all_values()
        df = pd.DataFrame(raw[1:], columns=raw[0])
        cols = ['План', 'Значение тарифа', 'Часы работы', 'Потребление (кВт/ч)', 'Хешрейт (Th/s)', 'Хешрейт (Mh/s)']
        for col in cols:
            if col in df.columns: df[col] = df[col].apply(clean_currency_hardcore)
        return df
    except Exception as e:
        print(f"DB Error: {e}") 
        return pd.DataFrame()

# PDF REPORT CLASS
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'MinerHotel Daily Report', 0, 1, 'L')
        self.line(10, 20, 287, 20); self.ln(5)
    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def create_pdf(df):
    pdf = PDFReport(orientation='L', unit='mm', format='A4')
    pdf.add_page()
    pdf.set_font("Arial", 'B', 12); pdf.cell(0, 8, "GENERAL SUMMARY", 0, 1, 'L')
    pdf.set_font("Arial", size=10); pdf.cell(0, 5, f"Total Devices: {len(df)}", 0, 1, 'L')
    
    if 'Model' in df.columns:
        makers = df['Model'].apply(lambda x: x.split()[0] if ' ' in x else x).value_counts()
        m_str = ", ".join([f"{k}: {v}" for k,v in makers.items()])
        pdf.cell(0, 5, f"Models: {m_str}", 0, 1, 'L')
    pdf.ln(2)
    
    if 'RawHash' in df.columns and 'Algo' in df.columns:
        # Сортируем по алгоритмам
        sha_rows = df[df['Algo'] == "SHA-256"]
        scrypt_rows = df[df['Algo'] == "Scrypt"]
        kheavy_rows = df[df['Algo'] == "kHeavyHash"]
        x11_rows = df[df['Algo'] == "X11"]
        equihash_rows = df[df['Algo'] == "Equihash"]
        etchash_rows = df[df['Algo'] == "Etchash"]
        
        # [NEW] iPollo Rows
        mwc_rows = df[df['Algo'] == "Cuckatoo31 (MWC)"]
        grin_rows = df[df['Algo'] == "Cuckatoo32 (GRIN)"]
        
        if not sha_rows.empty:
            total = sha_rows['RawHash'].sum()
            pdf.cell(0, 5, f"Total SHA-256: {total:,.2f} TH/s", 0, 1, 'L')
        if not scrypt_rows.empty:
            total = scrypt_rows['RawHash'].sum()
            pdf.cell(0, 5, f"Total Scrypt: {total:,.2f} GH/s", 0, 1, 'L')
        if not kheavy_rows.empty:
            total = kheavy_rows['RawHash'].sum()
            pdf.cell(0, 5, f"Total kHeavyHash: {total:,.2f} TH/s", 0, 1, 'L')
        if not x11_rows.empty:
            total = x11_rows['RawHash'].sum()
            pdf.cell(0, 5, f"Total X11: {total:,.2f} GH/s", 0, 1, 'L')
        if not equihash_rows.empty:
            total = equihash_rows['RawHash'].sum()
            pdf.cell(0, 5, f"Total Equihash: {total:,.2f} kSol/s", 0, 1, 'L')
        if not etchash_rows.empty:
            total = etchash_rows['RawHash'].sum()
            pdf.cell(0, 5, f"Total Etchash: {total:,.2f} MH/s", 0, 1, 'L')
            
        # [NEW] ИТОГИ ДЛЯ IPOLLO
        # Умножаем на 1000, т.к. в RawHash лежит значение / 1000
        if not mwc_rows.empty:
            total = mwc_rows['RawHash'].sum() * 1000
            pdf.cell(0, 5, f"Total Cuckatoo31 (MWC): {total:,.2f} G/s", 0, 1, 'L')
        if not grin_rows.empty:
            total = grin_rows['RawHash'].sum() * 1000
            pdf.cell(0, 5, f"Total Cuckatoo32 (GRIN): {total:,.2f} G/s", 0, 1, 'L')
            
    pdf.ln(5)
    cols = ["IP", "Model", "Uptime", "Real", "Avg", "Temp", "Fan", "Pool", "Worker", "Algo"]
    widths = [28, 40, 22, 20, 20, 20, 25, 45, 35, 20] # Подогнал ширину
    pdf.set_font("Arial", 'B', 8)
    pdf.set_fill_color(0, 51, 153); pdf.set_text_color(255, 255, 255)
    for i, c in enumerate(cols): pdf.cell(widths[i], 8, c, 1, 0, 'C', fill=True)
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", size=7)
    for _, row in df.iterrows():
        data = [str(row.get(c, '')) for c in cols]
        for i, d in enumerate(data):
            pdf.cell(widths[i], 6, str(d)[:38], 1, 0, 'C')
        pdf.ln()
    return pdf.output(dest='S').encode('latin-1', 'ignore')

def to_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as w: 
        df.drop(columns=['SortHash', 'Type', 'RawHash', 'SortIP', 'TempMax'], errors='ignore').to_excel(w, index=False)
    return output.getvalue()

# ---------------------------------------------------------
# 3. АВТОЗАГРУЗКА
# ---------------------------------------------------------
def handle_startup():
    try:
        from win32com.client import Dispatch
    except ImportError: return 

    if getattr(sys, 'frozen', False): target_path = sys.executable
    else: target_path = os.path.abspath(__file__)

    startup_folder = os.path.join(os.getenv('APPDATA'), r"Microsoft\Windows\Start Menu\Programs\Startup")
    shortcut_path = os.path.join(startup_folder, "MinerMonitor.lnk")
    is_in_startup = os.path.exists(shortcut_path)

    st.sidebar.markdown("---")
    auto_run = st.sidebar.checkbox("🚀 Автозапуск", value=is_in_startup)

    if auto_run and not is_in_startup:
        try:
            shell = Dispatch('WScript.Shell')
            shortcut = shell.CreateShortCut(shortcut_path)
            shortcut.Targetpath = target_path
            shortcut.WorkingDirectory = os.path.dirname(target_path)
            shortcut.IconLocation = target_path
            shortcut.save()
            st.toast("✅ Добавлено в автозагрузку")
        except Exception as e:
             st.error(f"Ошибка автозагрузки: {e}")
    elif not auto_run and is_in_startup:
        try: os.remove(shortcut_path); st.toast("❌ Удалено")
        except: pass

# ---------------------------------------------------------
# 4. UI
# ---------------------------------------------------------
if 'ip_ranges_df' not in st.session_state: st.session_state.ip_ranges_df = load_ranges_df()
if 'scan_results' not in st.session_state: st.session_state.scan_results = pd.DataFrame()

# Авторизация
if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
if not st.session_state["authenticated"]:
    st.markdown("<style>.block-container {padding-top: 5rem;}</style>", unsafe_allow_html=True)
    with st.form("login"):
        st.title("🔒 Вход")
        u = st.text_input("Логин"); p = st.text_input("Пароль", type="password")
        if st.form_submit_button("Войти"):
            if "users" in MY_SECRETS and MY_SECRETS["users"].get(u) == p:
                st.session_state["authenticated"] = True; st.rerun()
            else: st.error("Ошибка")
    st.stop()

with st.sidebar:
    st.title("🎛️ Меню")
    page = st.radio("Раздел:", ["📡 Мониторинг", "💳 Обновить биллинг", "📊 База Данных", "📄 Создать акт", "🗺️ Карта 2D", "🧊 Карта 3D"])
    handle_startup()
    st.markdown("---")
    if st.button("Выйти"): st.session_state["authenticated"] = False; st.rerun()

# === МОНИТОРИНГ ===
if page == "📡 Мониторинг":
    st.title("📡 Мониторинг")
    
    with st.expander("🛠 Настройка диапазонов", expanded=True):
        edited_df = st.data_editor(
            st.session_state.ip_ranges_df,
            num_rows="dynamic",
            column_config={
                "name": st.column_config.TextColumn("Название"),
                "range": st.column_config.TextColumn("IP Диапазон")
            },
            width="stretch", 
            key="ranges_editor"
        )
        if not edited_df.equals(st.session_state.ip_ranges_df):
            st.session_state.ip_ranges_df = edited_df
            save_ranges_df(edited_df); st.rerun()

    st.markdown("---")
    range_opts = st.session_state.ip_ranges_df["name"].tolist() if not st.session_state.ip_ranges_df.empty else []
    selected_ranges = st.multiselect("Выберите диапазоны:", range_opts)
    
    if st.button("▶️ ЗАПУСТИТЬ СКАНЕР", type="primary"):
        if selected_ranges and miner_scanner:
            all_data = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            total = len(selected_ranges)
            
            for i, range_name in enumerate(selected_ranges):
                ip_val = st.session_state.ip_ranges_df.loc[
                    st.session_state.ip_ranges_df["name"] == range_name, "range"
                ].iloc[0]
                
                status_text.text(f"Сканирую: {range_name} ({ip_val})...")
                print(f"--- ЗАПУСК СКАНА: {range_name} [{ip_val}] ---")
                
                try:
                    res = miner_scanner.scan_network_range(ip_val)
                    print(f"Результат для {ip_val}: Найдено {len(res) if res else 0} устройств")
                    if res: all_data.extend(res)
                except Exception as e:
                    st.error(f"Ошибка при сканировании {range_name}: {e}")
                    print(f"ERROR Scanning {range_name}: {e}")

                progress_bar.progress((i + 1) / total)
            
            status_text.empty(); progress_bar.empty()
            if all_data:
                df = pd.DataFrame(all_data)
                df = df.drop_duplicates(subset=['IP'])
                st.session_state.scan_results = df
            else: 
                print("Скан завершен, данные пустые.")
                st.session_state.scan_results = pd.DataFrame()
        elif not miner_scanner:
            st.error("Ошибка: модуль miner_scanner не загружен.")
        else: st.warning("Выберите диапазон.")

    if not st.session_state.scan_results.empty:
        df = st.session_state.scan_results.copy()
        st.markdown(f"### 📊 Результаты ({len(df)} устройств)")
        
        c_sort, c_asc = st.columns([3, 1])
        sort_by = c_sort.selectbox("Сортировать:", ["IP адрес", "Хешрейт", "Модель", "Uptime", "Температура"])
        asc = c_asc.checkbox("По возрастанию", value=True)
        
        if sort_by == "IP адрес": df = df.sort_values("SortIP", ascending=asc)
        elif sort_by == "Хешрейт": df = df.sort_values("RawHash", ascending=asc)
        elif sort_by == "Модель": df = df.sort_values("Model", ascending=asc)
        elif sort_by == "Uptime": df = df.sort_values("Uptime", ascending=asc)
        elif sort_by == "Температура":
            df['TempMax'] = df['Temp'].apply(lambda x: max([int(n) for n in str(x).split() if n.isdigit()] or [0]))
            df = df.sort_values("TempMax", ascending=asc)

        algos = df['Algo'].unique()
        cols_m = st.columns(len(algos)+1)
        
        # --- МЕТРИКИ (ОБНОВЛЕНО) ---
        for i, algo in enumerate(algos):
            sub = df[df['Algo']==algo]
            total = sub['RawHash'].sum()
            
            unit = "TH/s"
            if algo == "Scrypt" or algo == "X11": unit = "GH/s"
            elif algo == "Equihash": unit = "kSol/s"
            elif algo == "Etchash": unit = "MH/s"
            
            cols_m[i].metric(f"{algo}", f"{total:,.1f} {unit}")
            
        def highlight_hot(val):
            try: 
                if any(int(x) > 80 for x in str(val).split() if x.isdigit()): return 'color: red; font-weight: bold'
            except: pass
            return ''

        show_df = df.drop(columns=['SortHash', 'Type', 'RawHash', 'SortIP', 'TempMax'], errors='ignore')
        
        st.dataframe(show_df.style.applymap(highlight_hot, subset=['Temp']), width="stretch")
        
        # Генерация имени файла для PDF
        date_str = datetime.now().strftime("%Y-%m-%d")
        safe_names = "-".join(selected_ranges).replace(" ", "_").replace("/", "-")
        pdf_name = f"{safe_names}_{date_str}.pdf"
        
        c1,c2,c3 = st.columns(3)
        c1.download_button(f"📄 PDF ({pdf_name})", create_pdf(df), pdf_name)
        c2.download_button("📗 Excel", to_excel(df), "Report.xlsx")
        c3.download_button("🗒 CSV", df.to_csv(index=False).encode('utf-8'), "Report.csv")
    else:
        if st.session_state.get('scan_results') is not None and st.session_state.scan_results.empty:
             st.info("Устройства не найдены.")

# === БИЛЛИНГ ===
elif page == "💳 Обновить биллинг":
    st.title("💳 Обновление биллинга")
    billing_url = "https://script.google.com/macros/s/AKfycbyn5zM4PQ4kiOTw-bCIfgo6G43oPPJEkz5rdfOniAFgydzZqJVo7Msuip5QigA1moiH/exec?token=Miner2025&view=billing"
    components.iframe(billing_url, height=900)

# === БАЗА ДАННЫХ ===
elif page == "📊 База Данных":
    df = get_database()
    if not df.empty:
        with st.sidebar:
            st.header("🔍 Фильтры БД")
            def uniq(c): return ["Все"] + sorted([str(x) for x in set(df[c]) if str(x)])
            sel_cl = st.selectbox("Клиент", uniq("ФИО/Орг. Клиента"))
            sel_ad = st.selectbox("Ответственный", uniq("Ответственный сотрудник"))
            sel_st = st.selectbox("Состояние", uniq("Состояние"))
            sel_pe = st.selectbox("Период", uniq("Период расчёта"))
            if st.button("Обновить БД"): get_database.clear(); st.rerun()

        mask = pd.Series(True, index=df.index)
        if sel_cl!="Все": mask &= (df["ФИО/Орг. Клиента"]==sel_cl)
        if sel_ad!="Все": mask &= (df["Ответственный сотрудник"]==sel_ad)
        if sel_st!="Все": mask &= (df["Состояние"]==sel_st)
        if sel_pe!="Все": mask &= (df["Период расчёта"]==sel_pe)
        df_view = df[mask]

        st.title("📦 Реестр оборудования")
        k1,k2,k3,k4,k5 = st.columns(5)
        k1.metric("Устройств", len(df_view))
        k2.metric("SHA-256", f"{df_view['Хешрейт (Th/s)'].sum():,.0f} TH/s")
        k3.metric("Scrypt", f"{df_view['Хешрейт (Mh/s)'].sum():,.0f} MH/s")
        k4.metric("Потребление", f"{df_view['Потребление (кВт/ч)'].sum():,.1f} кВт")
        k5.metric("План", f"{df_view['План'].sum():,.2f} ₽")
        
        if not df_view.empty:
            st.markdown("---")
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Модели")
                if 'Модель' in df_view.columns:
                    fig = px.pie(df_view['Модель'].value_counts().reset_index(), names='Модель', values='count', hole=0.4)
                    st.plotly_chart(fig, width="stretch")
            with c2:
                st.subheader("Топ Клиентов")
                if "ФИО/Орг. Клиента" in df_view.columns:
                    money = df_view.groupby("ФИО/Орг. Клиента")['План'].sum().reset_index().sort_values('План').tail(10)
                    fig2 = px.bar(money, x='План', y='ФИО/Орг. Клиента', orientation='h', text_auto='.2s')
                    st.plotly_chart(fig2, width="stretch")

        st.dataframe(df_view, width="stretch", height=600, hide_index=True)
    else: st.info("БД не загружена (проверьте интернет или credentials.json)")

# === СОЗДАТЬ АКТ ===
elif page == "📄 Создать акт":
    st.title("📄 Создание Акта приема-передачи")
    if "maps" in MY_SECRETS: 
        base_url = MY_SECRETS['maps']['script_url']
        token = MY_SECRETS['maps']['token']
        act_url = f"{base_url}?token={token}&view=acts"
        components.iframe(act_url, height=900)
    else: 
        st.error("Нет secrets")

# === КАРТЫ ===
elif page == "🗺️ Карта 2D":
    if "maps" in MY_SECRETS: components.iframe(f"{MY_SECRETS['maps']['script_url']}?token={MY_SECRETS['maps']['token']}", height=800)
    else: st.error("Нет secrets")
elif page == "🧊 Карта 3D":
    if "maps" in MY_SECRETS: components.iframe(f"{MY_SECRETS['maps']['script_url']}?token={MY_SECRETS['maps']['token']}&view=3d", height=800)
    else: st.error("Нет secrets")