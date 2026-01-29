import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime
import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import anthropic
import shutil

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="WCAG Audit Agent - Cloud Edition", layout="wide")

# ... (Tutaj wklej ładowanie kluczy API i funkcję generate_human_recommendation z poprzedniej odpowiedzi) ...

# --- NOWA FUNKCJA AUDYTU (DOSTOSOWANA DO CHMURY) ---
def run_audit(url, page_type, country, run_axe=True):
    """
    Wersja Cloud-Ready: Uruchamia Chromium w trybie headless.
    """
    
    # 1. Konfiguracja Chrome dla środowiska Cloud (Linux)
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Bez okna
    chrome_options.add_argument("--no-sandbox") # Wymagane na serwerach
    chrome_options.add_argument("--disable-dev-shm-usage") # Zapobiega błędom pamięci
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-features=NetworkService")
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    
    # Próbujemy znaleźć chromium automatycznie (dla Streamlit Cloud działa zazwyczaj bez wskazywania ścieżki,
    # ale jeśli pakiety są zainstalowane przez packages.txt, selenium powinno je znaleźć)
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        st.error(f"❌ Nie udało się uruchomić przeglądarki. Upewnij się, że masz plik packages.txt! Błąd: {e}")
        return {}
    
    audit_data = {
        "url": url,
        "page_type": page_type,
        "country": country,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "violations_count": 0,
        "violations": []
    }

    try:
        driver.get(url)
        time.sleep(4) # Dajmy chmurze chwilę więcej na renderowanie
        
        if run_axe:
            # POBIERANIE AXE Z CDN
            axe_cdn_url = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js"
            response = requests.get(axe_cdn_url)
            axe_script = response.text
            
            # WSTRZYKIWANIE
            driver.execute_script(axe_script)
            
            # URUCHAMIANIE
            js_command = """
                var callback = arguments[arguments.length - 1];
                axe.run().then(results => {
                    callback(results);
                }).catch(err => {
                    callback({error: err.toString()});
                });
            """
            axe_results = driver.execute_async_script(js_command)
            
            if not axe_results or 'error' in axe_results:
                st.error(f"Błąd Axe: {axe_results.get('error', 'Nieznany błąd')}")
            else:
                raw_violations = axe_results.get('violations', [])
                audit_data["violations_count"] = len(raw_violations)
                
                for v in raw_violations:
                    # Tutaj wywołujemy Twoją funkcję AI z poprzedniego kroku
                    human_recommendation = generate_human_recommendation(v)
                    
                    audit_data["violations"].append({
                        "id": v['id'],
                        "impact": v['impact'],
                        "help": v['help'],
                        "count": len(v['nodes']),
                        "recommendation_markdown": human_recommendation
                    })

    except Exception as e:
        st.error(f"Błąd krytyczny podczas audytu {url}: {str(e)}")
    finally:
        driver.quit()
        
    return audit_data

# ... (Reszta kodu UI bez zmian) ...
