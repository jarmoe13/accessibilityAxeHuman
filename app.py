import streamlit as st
import pandas as pd
import requests
import urllib.parse
import plotly.express as px
from datetime import datetime
import time
import shutil
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# --- CONFIGURATION ---
st.set_page_config(page_title="Lyreco Accessibility Monitor", layout="wide")

try:
    GOOGLE_KEY = st.secrets["GOOGLE_KEY"]
    WAVE_KEY = st.secrets["WAVE_KEY"]
except KeyError:
    st.error("⚠️ Missing API keys. Add GOOGLE_KEY and WAVE_KEY to Streamlit Secrets.")
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
SSO_LOGIN = "https://welcome.lyreco.com/lyreco-customers/login"
PAGE_LABELS = {"home": "Home", "category": "Category", "product": "Product", "login": "Login (SSO)"}

# --- SIDEBAR (OLD UI STYLE) ---
with st.sidebar:
    st.image("https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.svg", width=200)
    st.divider()
    st.markdown("### 📊 About This Tool")
    st.markdown(
        """
    Automated WCAG compliance monitoring for Lyreco e-commerce platforms.

    **Powered by:**
    - Google Lighthouse (40%)
    - WAVE by WebAIM (30%)
    - Axe-core (30%)

    **Coverage:**
    - 1 country (pilot)
    - 3 page types per country
    - 100+ accessibility checks
    """
    )
    st.divider()
    st.caption("Version 8.0 | January 2026")

st.title("Lyreco Accessibility Monitor")
st.caption("Multi-country WCAG compliance tracking with Axe-core")


# --- UTILS ---
def build_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1280,1024")
    service = Service(executable_path=shutil.which("chromedriver") or "/usr/bin/chromedriver")
    return webdriver.Chrome(service=service, options=chrome_options)

@st.cache_data(ttl=3600)
def fetch_axe_core():
    try:
        return requests.get("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js", timeout=10).text
    except: return None

# --- AUDIT ENGINE ---
def run_axe_test(driver, url):
    axe_script = fetch_axe_core()
    if not axe_script: return {"error": "Axe library fetch failed"}
    driver.get(url)
    time.sleep(5)
    driver.execute_script(axe_script)
    results = driver.execute_async_script("""
        const callback = arguments[arguments.length - 1];
        axe.run({ runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } })
            .then(res => callback(res))
            .catch(err => callback({error: err.toString()}));
    """)
    if "error" in results: return {"error": results["error"]}
    violations = results.get("violations", [])
    impact_map = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    violations.sort(key=lambda x: impact_map.get(x.get("impact"), 4))
    for v in violations: v["wcag_tag"] = AXE_TO_WCAG.get(v["id"], "WCAG General")
    return {"violations": violations, "counts": {k: sum(1 for v in violations if v.get("impact") == k) for k in impact_map}}

def perform_full_audit(url, page_type, country):
    # LH & WAVE Logic
    lh_score = 0
    try:
        api = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={urllib.parse.quote(url)}&category=accessibility&key={GOOGLE_KEY}"
        lh_score = requests.get(api, timeout=40).json()["lighthouseResult"]["categories"]["accessibility"]["score"] * 100
    except: pass
    
    w_err, w_con = 0, 0
    try:
        wave_res = requests.get(f"https://wave.webaim.org/api/request?key={WAVE_KEY}&url={url}", timeout=30).json()
        w_err = wave_res["categories"]["error"]["count"]
        w_con = wave_res["categories"]["contrast"]["count"]
    except: pass

    # Axe & Screen
    axe_data = {"violations": [], "counts": {"critical": 0, "serious": 0}}
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
    finally: driver.quit()

    wave_score = max(0, 100 - (w_err * 2 + w_con * 0.5))
    axe_score = max(0, 100 - (axe_data["counts"]["critical"] * 15 + axe_data["counts"]["serious"] * 5))
    final_score = round((lh_score * 0.4) + (wave_score * 0.3) + (axe_score * 0.3), 1)

    return {
        "Country": country, "Type": page_type, "Score": final_score, "LH": round(lh_score, 1), 
        "WAVE": round(wave_score, 1), "Axe": round(axe_score, 1), "Critical": axe_data["counts"]["critical"],
        "Serious": axe_data["counts"]["serious"], "URL": url, "Screenshot": screenshot_path,
        "Violations": axe_data["violations"], "Timestamp": datetime.now().strftime("%H:%M")
    }

# --- DASHBOARD UI ---
def display_dashboard(df):
    # 1. LYRECO SCORE WRAPPER (Educational info for Business)
    with st.expander("ℹ️ How we calculate the Lyreco Accessibility Score"):
        st.markdown("""
        The **Lyreco Accessibility Score** is a weighted average of three industry-standard tools:
        - **40% Lighthouse:** Checks technical compliance and mobile readiness.
        - **30% WAVE:** Focuses on structural errors and color contrast.
        - **30% Axe-core:** The gold standard for WCAG 2.1 A/AA compliance. 
        
        *Penalties: Critical errors in Axe-core reduce the score significantly (-15 pts per error) as they represent total blockers for disabled users.*
        """)

    # 2. TREND (If history exists)
    if "historical_df" in st.session_state:
        old_avg = st.session_state["historical_df"]["Score"].mean()
        new_avg = df["Score"].mean()
        st.metric("Global Score Trend", f"{new_avg:.1f}", f"{new_avg - old_avg:+.1f} vs previous")

    # 3. MAIN METRICS
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg Score", f"{df['Score'].mean():.1f}")
    m2.metric("Critical Blockers", int(df["Critical"].sum()))
    m3.metric("Serious Issues", int(df["Serious"].sum()))
    m4.metric("Market Coverage", f"{len(df['Country'].unique())} Countries")

    # 4. HEATMAP TABLE
    st.subheader("Market Compliance Heatmap")
    pivot = df.pivot_table(index="Country", columns="Type", values="Score")
    st.dataframe(pivot.style.background_gradient(cmap="RdYlGn", low=0.4, high=0.9), use_container_width=True)

    # 5. GALLERY (CRITICAL ERRORS)
    st.subheader("🖼️ Visual Proof (Critical Errors)")
    crit_df = df[df["Screenshot"] != ""]
    if not crit_df.empty:
        cols = st.columns(3)
        for i, (_, row) in enumerate(crit_df.iterrows()):
            with cols[i % 3]:
                st.image(row["Screenshot"], caption=f"{row['Country']} - {row['Type']}")
    
    # 6. DETAILED VIOLATIONS (Deep Sorting + WCAG Mapping)
    st.subheader("🔍 WCAG Deep Dive (by Severity)")
    for _, row in df.iterrows():
        if row["Violations"]:
            with st.expander(f"{row['Country']} - {row['Type']} | Score: {row['Score']}"):
                for v in row["Violations"]:
                    impact_icon = "🔴" if v['impact'] == "critical" else "🟠"
                    st.markdown(f"{impact_icon} **{v['help']}**")
                    st.caption(f"Standard: {v['wcag_tag']} | [Rule ID: {v['id']}]")
                    st.write(f"*{v['description']}*")

# --- MAIN APP LAYOUT ---
st.title("Lyreco Digital Accessibility Monitor")

with st.expander("📊 How We Calculate Accessibility Score"):
    st.markdown(
        """
        ### Lyreco Accessibility Score (0-100)

        **New Formula (v8.0):**

        **🔍 Google Lighthouse (40%)**
        - Tests 40+ accessibility rules
        - Checks ARIA, semantic HTML, keyboard navigation

        **🌊 WAVE by WebAIM (30%)**
        - Detects critical errors (missing alt text, broken forms)
        - Color contrast failures
        - Penalties: 1.2 points per error, 0.5 per contrast issue

        **⚡ Axe-core (30%)**
        - Deep WCAG 2.1 compliance testing
        - Heavy penalties: Critical violation = -10 points, Serious = -5 points
        - Industry-standard tool used by Microsoft, Google, Adobe

        **📈 Score Ranges:**
        - 🟢🟢 95-100: Excellent
        - 🟢 90-95: Good
        - 🟡🟢 80-90: Fair
        - 🟡 60-80: Needs improvement
        - 🔴 <60: Critical issues

        ⚠️ *Automated tools catch ~70% of issues. Manual testing required for full compliance.*
        """
    )

st.divider()



tab1, tab2 = st.tabs(["🚀 New Audit", "📂 History"])

with tab1:
    with st.expander("Audit Settings", expanded=True):
        c1, c2 = st.columns(2)
        # DEFAULT: ALL SELECTED
        country_selection = c1.multiselect("Select Countries", list(COUNTRIES.keys()), default=list(COUNTRIES.keys()))
        types_selection = c2.multiselect("Select Page Types", ["home", "category", "product", "login"], default=["home", "category", "product", "login"])

    if st.button("Run Global Audit", type="primary"):
        results = []
        total_tasks = len(country_selection) * len([t for t in types_selection if t != 'login'])
        if 'login' in types_selection: total_tasks += 1
        
        progress = st.progress(0)
        idx = 0
        
        # Audit Login if selected
        if 'login' in types_selection:
            st.write("Auditing Global Login...")
            results.append(perform_full_audit(SSO_LOGIN, "Login (SSO)", "Global"))
            idx += 1
            progress.progress(idx/total_tasks)
            
        # Audit Markets
        for country in country_selection:
            for p_type in types_selection:
                if p_type == 'login': continue
                st.write(f"Auditing {country} - {p_type}...")
                results.append(perform_full_audit(COUNTRIES[country][p_type], PAGE_LABELS[p_type], country))
                idx += 1
                progress.progress(idx/total_tasks)
        
        st.session_state["last_results"] = pd.DataFrame(results)
        st.success("Audit Complete!")

    if "last_results" in st.session_state:
        display_dashboard(st.session_state["last_results"])
        st.download_button("Export CSV", st.session_state["last_results"].to_csv(index=False), "lyreco_audit.csv")

with tab2:
    st.subheader("Historical Trend Analysis")
    uploaded = st.file_uploader("Upload previous CSV report", type="csv")
    if uploaded:
        st.session_state["historical_df"] = pd.read_csv(uploaded)
        st.success("History loaded. Run a new audit to see comparison.")
