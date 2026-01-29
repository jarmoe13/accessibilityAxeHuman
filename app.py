import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime
import json
import time
import shutil
import anthropic

# Importy Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="WCAG Audit Agent - Human Thing Style", layout="wide")

# --- ŁADOWANIE SEKRETÓW ---
try:
    # Klucz do AI (Claude)
    ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")
    # Klucze opcjonalne (jeśli ich używasz w innych miejscach)
    GOOGLE_KEY = st.secrets.get("GOOGLE_KEY", "")
    WAVE_KEY = st.secrets.get("WAVE_KEY", "")
except Exception as e:
    st.warning(f"⚠️ Sprawdź plik .streamlit/secrets.toml. Błąd: {e}")

# --- BAZA DANYCH URLI ---
COUNTRIES = {
    "France": {
        "home": "https://shop.lyreco.fr/fr",
        "category": "https://shop.lyreco.fr/fr/list/001001/papier-et-enveloppes/papier-blanc",
        "product": "https://shop.lyreco.fr/fr/product/157.796/papier-blanc-a4-lyreco-multi-purpose-80-g-ramette-500-feuilles"
    },
    "UK": {
        "home": "https://shop.lyreco.co.uk/",
        "category": "https://shop.lyreco.co.uk/list/001001/paper-envelopes/white-paper",
        "product": "https://shop.lyreco.co.uk/product/157.796/lyreco-budget-paper-a4-80g-white-ream-of-500-sheets"
    }
}

# --- FUNKCJA 1: GENERATOR REKOMENDACJI (AI - HUMAN THING) ---
def generate_human_recommendation(violation_data):
    """
    Tworzy rekomendację w stylu 'Human Thing' używając Claude.
    """
    if not ANTHROPIC_API_KEY:
        return "⚠️ Brak klucza ANTHROPIC_API_KEY. Opis AI niedostępny."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    # Dane techniczne z Axe
    rule_id = violation_data.get('id', 'nieznany')
    help_text = violation_data.get('help', 'brak opisu')
    impact = violation_data.get('impact', 'minor')
    tags = ", ".join(violation_data.get('tags', []))
    
    # SYSTEM PROMPT - TU JEST "MAGIA" STYLU HUMAN THING
    system_prompt = """
    Jesteś Audytorem Dostępności w stylu agencji 'Human Thing'.
    
    ZASADY:
    1. Najważniejsze jest doświadczenie użytkownika. Nie pisz "brak atrybutu", pisz "użytkownik nie wie...".
    2. Język prosty i empatyczny. Żadnego technicznego bełkotu w opisie problemu.
    3. Rekomendacja musi być techniczna, konkretna i używać semantycznego HTML.
    4. Jedna rekomendacja na problem. Nie dawaj wyboru.
    
    FORMAT ODPOWIEDZI (MARKDOWN):
    ### [Polska nazwa problemu] (Priorytet: [Wysoki/Średni/Niski])
    
    **Co to oznacza dla użytkownika?**
    [Opis skutku dla człowieka]
    
    **Jak to naprawić?**
    [Prosta instrukcja]
    
    **Zgodność z WCAG:**
    > Naruszenie: [Numer kryterium WCAG]
    
    **Przykład kodu:**
    ```html
    [Poprawny snippet]
    ```
    """

    user_message = f"Przeanalizuj błąd Axe: ID={rule_id}, Opis={help_text}, Impact={impact}, Tagi={tags}"

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text
    except Exception as e:
        return f"Błąd AI: {str(e)}"

# --- FUNKCJA 2: AUDYT TECHNICZNY (SELENIUM + AXE) ---
def run_audit(url, page_type, country):
    """
    Uruchamia przeglądarkę w chmurze i skanuje Axe-core.
    """
    
    # 1. Konfiguracja Chrome pod Cloud (Linux)
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920x1080")
    
    # 2. Szukanie Chromium w systemie (Fix na biały ekran)
    chromium_path = shutil.which("chromium") or "/usr/bin/chromium"
    chromedriver_path = shutil.which("chromedriver") or "/usr/bin/chromedriver"
    
    chrome_options.binary_location = chromium_path
    service = Service(executable_path=chromedriver_path)
    
    # Wynik domyślny w razie awarii
    audit_data = {
        "url": url,
        "page_type": page_type,
        "country": country,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "violations": [],
        "error": None
    }

    driver = None
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        time.sleep(4) # Czekamy na załadowanie strony
        
        # 3. Wstrzykiwanie Axe
        axe_cdn = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js"
        driver.execute_script(requests.get(axe_cdn).text)
        
        # 4. Uruchomienie skanera (Async)
        js_command = """
            var callback = arguments[arguments.length - 1];
            axe.run().then(results => callback(results)).catch(err => callback({error: err.toString()}));
        """
        results = driver.execute_async_script(js_command)
        
        if results and 'violations' in results:
            for v in results['violations']:
                # Generujemy opis AI dla każdego błędu
                human_text = generate_human_recommendation(v)
                audit_data["violations"].append({
                    "id": v['id'],
                    "impact": v['impact'],
                    "count": len(v['nodes']),
                    "human_desc": human_text
                })
                
    except Exception as e:
        audit_data["error"] = str(e)
    finally:
        if driver:
            driver.quit()
            
    return audit_data

# --- UI: WYŚWIETLANIE WYNIKÓW ---
def display_dashboard(df):
    if df.empty:
        st.info("Brak danych.")
        return
        
    st.subheader("📊 Wyniki Audytu")
    
    # Wyświetlanie błędów
    for index, row in df.iterrows():
        status = "❌ Błąd Krytyczny" if row['error'] else f"✅ Znaleziono: {len(row['violations'])} typów błędów"
        with st.expander(f"{row['page_type']} ({row['country']}) - {status}"):
            st.write(f"URL: {row['url']}")
            
            if row['error']:
                st.error(f"Błąd systemu: {row['error']}")
            else:
                if not row['violations']:
                    st.success("Brak błędów automatycznych! 🎉")
                
                for v in row['violations']:
                    st.markdown("---")
                    # Tutaj wyświetlamy to, co wygenerowało AI
                    st.markdown(v['human_desc'])
                    st.caption(f"Techniczny ID: {v['id']} | Wystąpień: {v['count']}")

# --- GŁÓWNA APLIKACJA ---
st.title("🤖 Lyreco Accessibility Agent (Human Thing Style)")

# Sidebar
country = st.sidebar.selectbox("Wybierz kraj", list(COUNTRIES.keys()))

# Zakładki (Zgodne z Twoim oryginałem)
tab1, tab2, tab3 = st.tabs(["🚀 Uruchom Audyt", "⌨️ Testy Klawiatury", "📂 Upload CSV"])

with tab1:
    st.header(f"Audyt automatyczny: {country}")
    if st.button("Start Audit"):
        results_list = []
        pages = COUNTRIES[country]
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total = len(pages)
        for i, (p_type, url) in enumerate(pages.items()):
            status_text.text(f"🔍 Analizuję: {p_type}...")
            data = run_audit(url, p_type, country)
            results_list.append(data)
            progress_bar.progress((i + 1) / total)
            
        progress_bar.empty()
        status_text.success("Gotowe!")
        
        df_results = pd.DataFrame(results_list)
        st.session_state['last_audit'] = df_results
        display_dashboard(df_results)

    elif 'last_audit' in st.session_state:
        display_dashboard(st.session_state['last_audit'])

with tab2:
    st.info("Tutaj będą testy klawiatury (Placeholder)")

with tab3:
    st.subheader("Wgraj poprzednie wyniki")
    uploaded = st.file_uploader("Wybierz plik CSV", type="csv")
    if uploaded:
        st.write("Obsługa CSV do wdrożenia.")
