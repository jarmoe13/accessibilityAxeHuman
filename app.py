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
st.set_page_config(page_title="WCAG Audit Agent", layout="wide")

# --- ŁADOWANIE SEKRETÓW ---
try:
    ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")
except:
    st.warning("⚠️ Brak klucza API.")

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

# --- FUNKCJE POMOCNICZE (SCORE & AI) ---
def calculate_score(violations):
    score = 100.0
    weights = {'critical': 5.0, 'serious': 3.0, 'moderate': 1.0, 'minor': 0.5}
    for v in violations:
        impact = v.get('impact', 'minor') or 'minor'
        count = len(v.get('nodes', []))
        score -= (weights.get(impact, 0.5) * count)
    return max(0.0, round(score, 1))

def generate_human_recommendation(violation_data):
    if not ANTHROPIC_API_KEY: return "Brak API Key."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    # Skracamy input, żeby nie marnować tokenów
    rule_id = violation_data.get('id', '')
    help_text = violation_data.get('help', '')
    
    system_prompt = "Jesteś ekspertem WCAG (Human Thing). Opisz błąd krótko: 1. Co to znaczy dla użytkownika? 2. Jak to naprawić (technicznie)? Bez lania wody."
    
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Błąd: {rule_id}, Opis: {help_text}"}]
        )
        return response.content[0].text
    except:
        return "Błąd generowania AI."

# --- TESTY (AXE + KLAWIATURA) ---
def run_keyboard_test(driver):
    log = []
    try:
        # Szukamy wszystkiego co klikalne
        elements = driver.find_elements(By.CSS_SELECTOR, "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])")
        visible = [e for e in elements if e.is_displayed()]
        
        log.append(f"ℹ️ Znaleziono {len(visible)} elementów interaktywnych. Testuję tabowanie (max 15 kroków).")
        
        # Reset fokusa
        driver.find_element(By.TAG_NAME, "body").click()
        actions = ActionChains(driver)
        
        for i in range(min(len(visible), 15)):
            actions.send_keys(Keys.TAB).perform()
            time.sleep(0.2)
            
            elem = driver.execute_script("return document.activeElement")
            tag = elem.get_attribute("tagName")
            text = elem.text[:20].replace("\n", "") if elem.text else "BRAK TEKSTU"
            e_id = elem.get_attribute("id") or "BRAK ID"
            
            log.append(f"Krok {i+1}: <{tag}> ID: {e_id} | Tekst: '{text}'")
            
    except Exception as e:
        log.append(f"❌ Błąd klawiatury: {str(e)}")
    return log

def run_audit(url, page_type, country):
    # Setup Chrome
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920x1080")
    
    # Stealth
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    service = Service(executable_path=shutil.which("chromedriver") or "/usr/bin/chromedriver")
    opts.binary_location = shutil.which("chromium") or "/usr/bin/chromium"

    data = {
        "page_type": page_type,
        "score": 100,
        "violations_count": 0,
        "violations": [],
        "keyboard_log": [],
        "screenshot": None,
        "url": url,
        "error": None
    }

    driver = None
    try:
        driver = webdriver.Chrome(service=service, options=opts)
        driver.get(url)
        time.sleep(5)
        
        # Screenshot debug
        data["screenshot"] = driver.get_screenshot_as_png()
        
        # 1. AXE
        driver.execute_script(requests.get("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js").text)
        res = driver.execute_async_script("var cb=arguments[arguments.length-1];axe.run().then(r=>cb(r)).catch(e=>cb({error:e.toString()}));")
        
        if res and 'violations' in res:
            data["violations_count"] = len(res['violations'])
            data["score"] = calculate_score(res['violations'])
            for v in res['violations']:
                data["violations"].append({
                    "id": v['id'],
                    "impact": v['impact'],
                    "count": len(v['nodes']),
                    "human_desc": generate_human_recommendation(v)
                })
        
        # 2. KEYBOARD
        data["keyboard_log"] = run_keyboard_test(driver)

    except Exception as e:
        data["error"] = str(e)
    finally:
        if driver: driver.quit()
        
    return data

# --- UI START ---
st.title("🤖 Lyreco Audit: Axe + AI + Keyboard")

country = st.sidebar.selectbox("Wybierz kraj", list(COUNTRIES.keys()))

if st.button("🚀 URUCHOM AUDYT"):
    st.session_state['audit_results'] = [] # Reset
    results = []
    
    progress = st.progress(0)
    status = st.empty()
    
    items = list(COUNTRIES[country].items())
    for i, (ptype, url) in enumerate(items):
        status.markdown(f"### 🔍 Skanuję: **{ptype}**...")
        res = run_audit(url, ptype, country)
        results.append(res)
        progress.progress((i+1)/len(items))
    
    status.success("Gotowe!")
    st.session_state['audit_results'] = results # Zapisz do sesji

# --- UI WYNIKI (TO CO BYŁO NIEWIDOCZNE) ---
if 'audit_results' in st.session_state and st.session_state['audit_results']:
    results = st.session_state['audit_results']
    
    # 1. TABELA SCOREBOARD (Przygotowana specjalnie dla st.dataframe - tylko proste dane)
    st.divider()
    st.subheader("📊 Podsumowanie (Scoreboard)")
    
    simple_data = []
    for r in results:
        simple_data.append({
            "Typ Strony": r['page_type'],
            "Wynik (0-100)": r['score'],
            "Liczba Błędów": r['violations_count'],
            "Status": "✅ OK" if not r['error'] else "❌ BŁĄD"
        })
    
    df_simple = pd.DataFrame(simple_data)
    
    # Kolorowanie tabeli
    st.dataframe(
        df_simple.style.background_gradient(subset=['Wynik (0-100)'], cmap='RdYlGn', vmin=0, vmax=100),
        use_container_width=True
    )

    # 2. SZCZEGÓŁY (TABS)
    st.divider()
    st.subheader("📝 Szczegóły Audytu")
    
    tabs = st.tabs([r['page_type'] for r in results])
    
    for i, tab in enumerate(tabs):
        row = results[i]
        with tab:
            if row['error']:
                st.error(f"Błąd krytyczny: {row['error']}")
                if row['screenshot']: st.image(row['screenshot'])
            else:
                col1, col2 = st.columns([1, 1])
                
                # KOLUMNA LEWA: AXE + AI
                with col1:
                    st.markdown("### 🚫 Błędy WCAG")
                    if not row['violations']:
                        st.info("Brak błędów automatycznych.")
                    
                    for v in row['violations']:
                        with st.expander(f"{v['id']} ({v['impact']})"):
                            st.markdown(v['human_desc'])
                            st.caption(f"Liczba wystąpień: {v['count']}")

                # KOLUMNA PRAWA: KLAWIATURA + SCREENSHOT
                with col2:
                    st.markdown("### ⌨️ Test Klawiatury")
                    if row['keyboard_log']:
                        # Wyświetlamy log jako kod lub listę
                        log_str = "\n".join(row['keyboard_log'])
                        st.text_area("Log Tabowania:", log_str, height=300)
                    else:
                        st.warning("Brak danych z klawiatury.")
                        
                    if row['screenshot']:
                        st.markdown("### 📸 Podgląd bota")
                        st.image(row['screenshot'], use_container_width=True)

else:
    st.info("Kliknij przycisk powyżej, aby rozpocząć audyt.")
