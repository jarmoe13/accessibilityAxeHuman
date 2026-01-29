import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime
import time
import shutil
import anthropic

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="WCAG Audit Agent - Pro Edition", layout="wide")

# --- ŁADOWANIE SEKRETÓW ---
try:
    ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")
except Exception as e:
    st.warning(f"⚠️ Brak klucza API w secrets.toml: {e}")

# --- BAZA URLI ---
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

# --- 1. LOGIKA PUNKTACJI (SCORE) ---
def calculate_score(violations):
    """
    Oblicza wynik punktowy 0-100.
    Start = 100.
    Critical = -5, Serious = -3, Moderate = -1, Minor = -0.5
    """
    score = 100.0
    weights = {
        'critical': 5.0,
        'serious': 3.0,
        'moderate': 1.0,
        'minor': 0.5
    }
    
    for v in violations:
        impact = v.get('impact', 'minor')
        # Jeśli impact jest null/nieznany, traktujemy jako minor
        if not impact: impact = 'minor'
        
        count = len(v.get('nodes', []))
        penalty = weights.get(impact, 0.5) * count
        score -= penalty
        
    return max(0.0, round(score, 1))

# --- 2. GENERATOR REKOMENDACJI (AI) ---
def generate_human_recommendation(violation_data):
    if not ANTHROPIC_API_KEY:
        return "Brak klucza API."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    rule_id = violation_data.get('id', 'nieznany')
    help_text = violation_data.get('help', '')
    impact = violation_data.get('impact', 'minor')
    
    system_prompt = """
    Jesteś audytorem Human Thing. Tłumaczysz błędy WCAG na ludzki język.
    ZASADY:
    1. Nagłówek: Nazwa problemu po polsku + Priorytet.
    2. Kontekst: Dlaczego to przeszkadza użytkownikowi (np. niewidomemu)?
    3. Rozwiązanie: Konkretna instrukcja (semantyczny HTML).
    4. Krótko i zwięźle.
    """

    user_message = f"Błąd Axe: {rule_id}. Opis: {help_text}. Waga: {impact}. Przełóż na styl Human Thing."

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text
    except:
        return "Błąd generowania opisu AI."

# --- 3. TEST KLAWIATURY (KEYBOARD BOT) ---
def run_keyboard_test(driver):
    """
    Symuluje tabowanie i zwraca ścieżkę fokusa.
    """
    keyboard_log = []
    try:
        # Znajdź wszystkie elementy, które teoretycznie powinny być interaktywne
        interactive_selector = "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])"
        elements = driver.find_elements(By.CSS_SELECTOR, interactive_selector)
        visible_elements = [e for e in elements if e.is_displayed()]
        
        total_steps = min(len(visible_elements), 20) # Limitujemy do 20 kroków żeby nie trwało wieków
        
        keyboard_log.append(f"ℹ️ Wykryto {len(visible_elements)} elementów interaktywnych. Testuję pierwsze {total_steps} kroków.")
        
        # Reset fokusa do body
        driver.find_element(By.TAG_NAME, "body").click()
        
        actions = ActionChains(driver)
        
        for i in range(total_steps):
            actions.send_keys(Keys.TAB).perform()
            time.sleep(0.1) # Małe opóźnienie dla stabilności
            
            # Sprawdź co ma fokus
            active_elem = driver.execute_script("return document.activeElement")
            
            tag = active_elem.get_attribute("tagName")
            text = active_elem.text[:30].replace("\n", " ") if active_elem.text else "[Brak tekstu]"
            elem_id = active_elem.get_attribute("id") or "[Brak ID]"
            
            step_info = f"Krok {i+1}: <{tag}> ID: {elem_id} | Tekst: '{text}'"
            keyboard_log.append(step_info)
            
    except Exception as e:
        keyboard_log.append(f"❌ Błąd testu klawiatury: {str(e)}")
        
    return keyboard_log

# --- 4. GŁÓWNA FUNKCJA AUDYTU ---
def run_full_audit(url, page_type, country):
    # Konfiguracja Chrome (Headless Cloud)
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920x1080")
    
    chromium_path = shutil.which("chromium") or "/usr/bin/chromium"
    chromedriver_path = shutil.which("chromedriver") or "/usr/bin/chromedriver"
    chrome_options.binary_location = chromium_path
    service = Service(executable_path=chromedriver_path)
    
    data = {
        "url": url,
        "page_type": page_type,
        "country": country,
        "score": 0,
        "violations_count": 0,
        "violations": [],
        "keyboard_log": [],
        "error": None
    }

    driver = None
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        time.sleep(4)
        
        # --- A. AXE CORE (WCAG) ---
        axe_cdn = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js"
        driver.execute_script(requests.get(axe_cdn).text)
        
        js_command = """
            var callback = arguments[arguments.length - 1];
            axe.run().then(results => callback(results)).catch(err => callback({error: err.toString()}));
        """
        results = driver.execute_async_script(js_command)
        
        if results and 'violations' in results:
            raw_violations = results['violations']
            data["violations_count"] = len(raw_violations)
            data["score"] = calculate_score(raw_violations)
            
            for v in raw_violations:
                human_text = generate_human_recommendation(v)
                data["violations"].append({
                    "id": v['id'],
                    "impact": v['impact'],
                    "count": len(v['nodes']),
                    "human_desc": human_text
                })
        
        # --- B. TEST KLAWIATURY ---
        data["keyboard_log"] = run_keyboard_test(driver)
        
    except Exception as e:
        data["error"] = str(e)
    finally:
        if driver:
            driver.quit()
            
    return data

# --- UI ---
st.title("🤖 Accessibility Audit: Human Thing Edition")
st.markdown("Automatyczna analiza WCAG + Symulacja Klawiatury + Rekomendacje AI")

country = st.sidebar.selectbox("Wybierz rynek", list(COUNTRIES.keys()))

if st.button(f"🚀 Uruchom Pełny Audyt dla {country}"):
    results_list = []
    pages = COUNTRIES[country]
    
    progress = st.progress(0)
    status = st.empty()
    
    for i, (p_type, url) in enumerate(pages.items()):
        status.markdown(f"### 🔍 Analizuję: **{p_type.upper()}** ({url})...")
        res = run_full_audit(url, p_type, country)
        results_list.append(res)
        progress.progress((i + 1) / len(pages))
    
    progress.empty()
    status.success("Audyt zakończony!")
    
    # Zapis do sesji
    df = pd.DataFrame(results_list)
    st.session_state['audit_results'] = df

# --- WYŚWIETLANIE WYNIKÓW ---
if 'audit_results' in st.session_state:
    df = st.session_state['audit_results']
    
    # 1. TABELA ZBIORCZA (MIĘSO)
    st.divider()
    st.subheader("📊 Podsumowanie Wyników (Scoreboard)")
    
    # Formatowanie tabeli
    summary_df = df[['page_type', 'score', 'violations_count', 'url']].copy()
    summary_df.columns = ['Typ Strony', 'Wynik (0-100)', 'Liczba Błędów', 'URL']
    
    # Kolorowanie wyników (Highlight)
    st.dataframe(
        summary_df.style.background_gradient(subset=['Wynik (0-100)'], cmap="RdYlGn", vmin=0, vmax=100),
        use_container_width=True
    )
    
    # Wykres
    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(df, x='page_type', y='score', title="Jakość Dostępności (Score)", color='score', color_continuous_scale='RdYlGn')
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.bar(df, x='page_type', y='violations_count', title="Liczba Naruszeń WCAG", color='violations_count', color_continuous_scale='Reds')
        st.plotly_chart(fig2, use_container_width=True)

    # 2. SZCZEGÓŁY
    st.divider()
    st.subheader("📝 Szczegółowe Raporty")
    
    tabs = st.tabs([f"{row['page_type']} (Score: {row['score']})" for _, row in df.iterrows()])
    
    for i, tab in enumerate(tabs):
        row = df.iloc[i]
        with tab:
            col_a, col_b = st.columns([2, 1])
            
            with col_a:
                st.markdown("### 🚫 Błędy WCAG i Rekomendacje")
                if not row['violations']:
                    st.success("Czysto! Brak błędów automatycznych.")
                for v in row['violations']:
                    with st.expander(f"{v['id']} (Waga: {v['impact']}) - {v['count']} wystąpień"):
                        st.markdown(v['human_desc'])
            
            with col_b:
                st.markdown("### ⌨️ Symulacja Klawiatury")
                st.info("Poniżej ścieżka, którą pokonał robot, wciskając TAB:")
                
                log_text = "\n".join(row['keyboard_log'])
                st.text_area("Keyboard Log", log_text, height=400)
                
                if "body" in log_text.lower() and len(row['keyboard_log']) < 3:
                    st.error("⚠️ Uwaga: Fokus prawdopodobnie utknął na początku strony (Trap?)")
                else:
                    st.success("✅ Fokus przemieszcza się po elementach.")
