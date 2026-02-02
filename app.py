import streamlit as st
import pandas as pd
import requests
import urllib.parse
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import time
import shutil
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# --- CONFIGURATION ---
st.set_page_config(page_title="Lyreco WCAG Agent v9.0", layout="wide")

try:
    GOOGLE_KEY = st.secrets["GOOGLE_KEY"]
    WAVE_KEY = st.secrets["WAVE_KEY"]
except KeyError:
    st.error("⚠️ Missing API keys in Secrets (GOOGLE_KEY, WAVE_KEY).")
    st.stop()

# --- CONSTANTS & MAPPING ---
AXE_TO_WCAG = {
    "color-contrast": "SC 1.4.3 (Contrast Minimum)",
    "image-alt": "SC 1.1.1 (Non-text Content)",
    "label": "SC 3.3.2 (Labels or Instructions)",
    "button-name": "SC 4.1.2 (Name, Role, Value)",
    "link-name": "SC 2.4.4 (Link Purpose)",
    "html-has-lang": "SC 3.1.1 (Language of Page)",
    "document-title": "SC 2.4.2 (Page Titled)",
    "frame-title": "SC 2.4.1 (Bypass Blocks)",
    "list": "SC 1.3.1 (Info and Relationships)",
    "aria-allowed-attr": "SC 4.1.2 (Name, Role, Value)",
    "accesskeys": "SC 2.1.1 (Keyboard)"
}

COUNTRIES = {
    "France": {
        "home": "https://shop.lyreco.fr/fr",
        "category": "https://shop.lyreco.fr/fr/list/001001/papier-et-enveloppes/papier-blanc",
        "product": "https://shop.lyreco.fr/fr/product/157.796/papier-blanc-a4-lyreco-multi-purpose-80-g-ramette-500-feuilles",
    },
    "UK": {
        "home": "https://shop.lyreco.co.uk/",
        "category": "https://shop.lyreco.co.uk/en/list/001001/paper-envelopes/white-office-paper",
        "product": "https://shop.lyreco.co.uk/en/product/159.543/lyreco-white-a4-80gsm-copier-paper-box-of-5-reams-5x500-sheets-of-paper",
    },
    "Italy": {
        "home": "https://shop.lyreco.it/it",
        "category": "https://shop.lyreco.it/it/list/001001/carte-e-buste/carta-bianca",
        "product": "https://shop.lyreco.it/it/product/4.016.865/carta-bianca-lyreco-a4-75-g-mq-risma-500-fogli",
    }
}
PAGE_LABELS = {"home": "Home", "category": "Category", "product": "Product"}

# --- UTILS ---
def build_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1280,1024")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Accessibility Audit Agent)")
    
    service = Service(executable_path=shutil.which("chromedriver") or "/usr/bin/chromedriver")
    return webdriver.Chrome(service=service, options=chrome_options)

@st.cache_data(ttl=3600)
def fetch_axe_core():
    """Pobiera axe-core raz dla uniknięcia błędów sieciowych w browserze."""
    try:
        r = requests.get("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js", timeout=10)
        return r.text
    except: return None

# --- AUDIT ENGINE ---
def run_axe_test(driver, url):
    axe_script = fetch_axe_core()
    if not axe_script: return {"error": "Axe library fetch failed"}
    
    driver.get(url)
    time.sleep(5) # Czekamy na dynamiczny content Lyreco
    
    # Iniekcja biblioteki jako string - eliminuje błąd "Failed to load"
    driver.execute_script(axe_script)
    
    # Uruchomienie testu
    results = driver.execute_async_script("""
        const callback = arguments[arguments.length - 1];
        axe.run({ runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } })
            .then(res => callback(res))
            .catch(err => callback({error: err.toString()}));
    """)
    
    if "error" in results: return {"error": results["error"]}
    
    violations = results.get("violations", [])
    
    # GŁĘBOKIE SORTOWANIE (Critical > Serious > Moderate > Minor)
    impact_map = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    violations.sort(key=lambda x: impact_map.get(x.get("impact"), 4))
    
    # MAPOWANIE NA WCAG
    for v in violations:
        v["wcag_tag"] = AXE_TO_WCAG.get(v["id"], "WCAG General")

    return {
        "violations": violations,
        "counts": {
            "critical": sum(1 for v in violations if v.get("impact") == "critical"),
            "serious": sum(1 for v in violations if v.get("impact") == "serious"),
            "moderate": sum(1 for v in violations if v.get("impact") == "moderate"),
            "minor": sum(1 for v in violations if v.get("impact") == "minor")
        }
    }

def perform_full_audit(url, page_type, country):
    # 1. Lighthouse
    lh_score = 0
    try:
        api = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={urllib.parse.quote(url)}&category=accessibility&key={GOOGLE_KEY}"
        lh_res = requests.get(api, timeout=40).json()
        lh_score = lh_res["lighthouseResult"]["categories"]["accessibility"]["score"] * 100
    except: pass

    # 2. WAVE
    w_err, w_con = 0, 0
    try:
        wave_api = f"https://wave.webaim.org/api/request?key={WAVE_KEY}&url={url}"
        w_res = requests.get(wave_api, timeout=30).json()
        w_err = w_res["categories"]["error"]["count"]
        w_con = w_res["categories"]["contrast"]["count"]
    except: pass

    # 3. Axe & Screenshot
    axe_data = {"violations": [], "counts": {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}}
    screenshot_path = ""
    
    driver = build_driver()
    try:
        axe_res = run_axe_test(driver, url)
        if "error" not in axe_res:
            axe_data = axe_res
            if axe_data["counts"]["critical"] > 0:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    driver.save_screenshot(tmp.name)
                    screenshot_path = tmp.name
    finally:
        driver.quit()

    # Calculation
    wave_score = max(0, 100 - (w_err * 2 + w_con * 0.5))
    axe_score = max(0, 100 - (axe_data["counts"]["critical"] * 15 + axe_data["counts"]["serious"] * 5))
    final_score = round((lh_score * 0.4) + (wave_score * 0.3) + (axe_score * 0.3), 1)

    return {
        "Country": country,
        "Type": page_type,
        "Score": final_score,
        "LH": round(lh_score, 1),
        "WAVE": round(wave_score, 1),
        "Axe": round(axe_score, 1),
        "Critical": axe_data["counts"]["critical"],
        "Serious": axe_data["counts"]["serious"],
        "Moderate": axe_data["counts"]["moderate"],
        "Minor": axe_data["counts"]["minor"],
        "URL": url,
        "Screenshot": screenshot_path,
        "Violations": axe_data["violations"],
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

# --- UI COMPONENTS ---
def display_trend(current_df, historical_df):
    if historical_df is None: return
    
    old_avg = historical_df["Score"].mean()
    new_avg = current_df["Score"].mean()
    delta = new_avg - old_avg
    
    st.metric("Global Accessibility Trend", f"{new_avg:.1f}", f"{delta:+.1f} vs previous")

def display_gallery(df):
    st.subheader("🖼️ Critical Violations Gallery")
    crit_pages = df[df["Screenshot"] != ""]
    if crit_pages.empty:
        st.success("No critical visual errors found! 🎉")
    else:
        cols = st.columns(3)
        for i, (_, row) in enumerate(crit_pages.iterrows()):
            with cols[i % 3]:
                st.image(row["Screenshot"], caption=f"{row['Country']} - {row['Type']}")
                st.error(f"Score: {row['Score']} | Critical: {row['Critical']}")

def display_dashboard(df):
    # Filtering Sidebar
    st.sidebar.header("📊 Filter Results")
    min_score = st.sidebar.slider("Min Score", 0, 100, 0)
    filtered_df = df[df["Score"] >= min_score]

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg Score", f"{filtered_df['Score'].mean():.1f}")
    m2.metric("Total Critical", int(filtered_df["Critical"].sum()))
    m3.metric("Total Serious", int(filtered_df["Serious"].sum()))
    m4.metric("Analyzed Pages", len(filtered_df))

    # Heatmap Table
    st.subheader("📍 Results Overview")
    st.dataframe(
        filtered_df[["Country", "Type", "Score", "LH", "WAVE", "Axe", "Critical", "Serious"]]
        .sort_values("Score")
        .style.background_gradient(cmap="RdYlGn", subset=["Score"], low=0, high=1),
        use_container_width=True
    )

    # Gallery
    display_gallery(filtered_df)

    # Detailed Violations with Mapping
    st.subheader("🔍 Deep Dive: Axe Violations")
    for _, row in filtered_df.iterrows():
        if row["Violations"]:
            with st.expander(f"Details: {row['Country']} - {row['Type']} (Score: {row['Score']})"):
                for v in row["Violations"]:
                    # Deep Sort & WCAG Mapping displayed
                    impact_color = {"critical": "🔴", "serious": "🟠", "moderate": "🟡", "minor": "⚪"}
                    st.markdown(f"**{impact_color.get(v['impact'])} {v['impact'].upper()}**: {v['help']}")
                    st.caption(f"Target: `{v['wcag_tag']}` | ID: `{v['id']}`")
                    with st.indent():
                        st.write(v["description"])

# --- MAIN APP ---
st.title("🛡️ Lyreco Accessibility Agent")

tab1, tab2 = st.tabs(["🚀 New Audit", "📜 History & Trend"])

with tab1:
    with st.expander("Audit Settings", expanded=True):
        c1, c2 = st.columns(2)
        selected_countries = c1.multiselect("Select Markets", list(COUNTRIES.keys()), default=["France"])
        selected_types = c2.multiselect("Page Types", ["home", "category", "product"], default=["home", "product"])

    if st.button("Start Global Audit", type="primary"):
        results = []
        total = len(selected_countries) * len(selected_types)
        progress = st.progress(0)
        
        idx = 0
        for country in selected_countries:
            for p_type in selected_types:
                url = COUNTRIES[country][p_type]
                st.write(f"Testing {country} {p_type}...")
                results.append(perform_full_audit(url, PAGE_LABELS[p_type], country))
                idx += 1
                progress.progress(idx / total)
        
        st.session_state["last_results"] = pd.DataFrame(results)
        st.success("Audit Completed!")

    if "last_results" in st.session_state:
        # Check for trend if history uploaded
        hist = st.session_state.get("historical_df")
        if hist is not None:
            display_trend(st.session_state["last_results"], hist)
        
        display_dashboard(st.session_state["last_results"])
        st.download_button("Export Results (CSV)", st.session_state["last_results"].to_csv(index=False), "audit_report.csv")

with tab2:
    st.subheader("Historical Comparison")
    uploaded_file = st.file_uploader("Upload previous audit CSV to enable Trend Analysis", type="csv")
    if uploaded_file:
        st.session_state["historical_df"] = pd.read_csv(uploaded_file)
        st.success("Historical data loaded! Go to 'New Audit' to see trends.")
        st.dataframe(st.session_state["historical_df"])
