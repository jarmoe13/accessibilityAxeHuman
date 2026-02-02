import streamlit as st
import pandas as pd
import requests
import urllib.parse
from datetime import datetime
import time
import shutil
import tempfile
from pathlib import Path
import anthropic

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# --- CONFIGURATION ---
st.set_page_config(page_title="Lyreco Accessibility Monitor", layout="wide")

# Load secrets
try:
    GOOGLE_KEY = st.secrets["GOOGLE_KEY"]
    WAVE_KEY = st.secrets["WAVE_KEY"]
    CLAUDE_KEY = st.secrets["CLAUDE_KEY"]
except KeyError:
    st.error("⚠️ Missing API keys. Please add GOOGLE_KEY, WAVE_KEY, and CLAUDE_KEY to Streamlit Secrets.")
    st.stop()

client = anthropic.Anthropic(api_key=CLAUDE_KEY)

# --- DATA & MAPPING ---
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
    },
    "Poland": {
        "home": "https://shop.lyreco.pl/pl",
        "category": "https://shop.lyreco.pl/pl/list/001001/papier-i-koperty/papiery-biale-uniwersalne",
        "product": "https://shop.lyreco.pl/pl/product/159.543/papier-do-drukarki-lyreco-copy-a4-80-g-m-5-ryz-po-500-arkuszy",
    }
}
SSO_LOGIN = "https://welcome.lyreco.com/lyreco-customers/login"
PAGE_LABELS = {"home": "Home", "category": "Category", "product": "Product", "login": "Login (SSO)"}

# --- AUTHENTICATION SYSTEM ---
def check_password():
    """Returns True if the user had the correct password."""
    def login_form():
        with st.form("Credentials"):
            st.image("https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.svg", width=150)
            st.subheader("Lyreco WCAG Agent Login")
            user = st.text_input("User", placeholder="admin or france")
            pwd = st.text_input("Password", type="password")
            if st.form_submit_button("Log in"):
                # In production, move these to st.secrets for safety
                if user == "admin" and pwd == "admin2026":
                    st.session_state["logged_in"] = True
                    st.session_state["role"] = "admin"
                    st.rerun()
                elif user == "france" and pwd == "fr2026":
                    st.session_state["logged_in"] = True
                    st.session_state["role"] = "france"
                    st.rerun()
                else:
                    st.error("Invalid user or password")

    if "logged_in" not in st.session_state:
        login_form()
        return False
    return True

# --- AI ADVISOR (10 GOLDEN RULES) ---
def get_ai_recommendation(violation_data, page_context):
    prompt = f"""
    Analyze this WCAG violation for Lyreco {page_context}:
    Violation ID: {violation_data['id']} | Impact: {violation_data['impact']}
    Description: {violation_data['description']}
    
    Follow these 10 Golden Rules for your advice:
    1. Prioritize UX over checklists. 2. Align with Business (Lyreco e-commerce). 
    3. Consider simple tech (HTML first). 4. Simplicity is king. 
    5. Use Semantic HTML. 6. Concept over coding. 7. Use plain language. 
    8. One problem, one solution. 9. Use WCAG Techniques. 10. Be a partner.

    Output format:
    - Strategic Advice: (1-2 clear sentences)
    - Recommended Fix: (A simple HTML snippet if relevant)
    """
    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=600,
            system="You are a Senior Accessibility Strategic Consultant. You focus on UX and business-friendly fixes over dry technical checklists.",
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"AI Advisor unavailable: {str(e)}"

# --- AUDIT ENGINE ---
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
    try: return requests.get("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js", timeout=10).text
    except: return None

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
    # Lighthouse
    lh_score = 0
    try:
        api = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={urllib.parse.quote(url)}&category=accessibility&key={GOOGLE_KEY}"
        lh_score = requests.get(api, timeout=45).json()["lighthouseResult"]["categories"]["accessibility"]["score"] * 100
    except: pass
    
    # WAVE
    w_err, w_con = 0, 0
    try:
        wave_res = requests.get(f"https://wave.webaim.org/api/request?key={WAVE_KEY}&url={url}", timeout=35).json()
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

    # Scores
    wave_score = max(0, 100 - (w_err * 2 + w_con * 0.5))
    axe_score = max(0, 100 - (axe_data["counts"]["critical"] * 15 + axe_data["counts"]["serious"] * 5))
    final_score = round((lh_score * 0.4) + (wave_score * 0.3) + (axe_score * 0.3), 1)

    return {
        "Country": country, "Type": page_type, "Score": final_score, "LH": round(lh_score, 1), 
        "WAVE": round(wave_score, 1), "Axe": round(axe_score, 1), "Critical": axe_data["counts"]["critical"],
        "Serious": axe_data["counts"]["serious"], "URL": url, "Screenshot": screenshot_path,
        "Violations": axe_data["violations"], "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

# --- DASHBOARD UI ---
def display_dashboard(df):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Average Score", f"{df['Score'].mean():.1f}")
    col2.metric("Critical Blockers", int(df["Critical"].sum()))
    col3.metric("Serious Issues", int(df["Serious"].sum()))
    col4.metric("Market Coverage", f"{len(df['Country'].unique())} Markets")

    st.divider()
    st.subheader("Market Compliance Heatmap")
    pivot = df.pivot_table(index="Country", columns="Type", values="Score")
    st.dataframe(pivot.style.background_gradient(cmap="RdYlGn", low=0.4, high=0.9), use_container_width=True)

    st.subheader("🖼️ Visual Proof (Critical Errors)")
    crit_df = df[df["Screenshot"] != ""]
    if not crit_df.empty:
        cols = st.columns(3)
        for i, (_, row) in enumerate(crit_df.iterrows()):
            with cols[i % 3]: st.image(row["Screenshot"], caption=f"{row['Country']} - {row['Type']}")
    
    st.subheader("🧠 AI Strategic Advisor (10 Golden Rules Applied)")
    for _, row in df.iterrows():
        if row["Violations"]:
            with st.expander(f"WCAG Strategy: {row['Country']} - {row['Type']} (Score: {row['Score']})"):
                critical_only = [v for v in row["Violations"] if v['impact'] == 'critical']
                if not critical_only: critical_only = row["Violations"][:1]
                
                for v in critical_only:
                    st.markdown(f"### Issue: {v['help']}")
                    with st.spinner("Claude is analyzing UX strategy..."):
                        rec = get_ai_recommendation(v, f"{row['Country']} {row['Type']}")
                        st.markdown(rec)
                    st.divider()

# --- MAIN APP LOGIC ---
if check_password():
    # Role-based restriction
    is_admin = st.session_state["role"] == "admin"
    available_countries = list(COUNTRIES.keys()) if is_admin else ["France"]

    # Sidebar
    with st.sidebar:
        st.image("https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.svg", width=180)
        st.divider()
        st.markdown(f"👤 **Logged in as:** `{st.session_state['role'].upper()}`")
        if st.button("Log out"):
            del st.session_state["logged_in"]
            st.rerun()
        st.divider()
        st.markdown("### 📊 About This Tool")
        st.caption("WCAG 2.1 A/AA Compliance Monitor")

    st.title("🛡️ Lyreco Accessibility Monitor")
    st.caption(f"Strategy-first audit tool | Access level: {st.session_state['role']}")

    tab1, tab2 = st.tabs(["🚀 New Audit", "📂 History"])

    with tab1:
        with st.expander("Audit Settings", expanded=True):
            c1, c2 = st.columns(2)
            country_selection = c1.multiselect("Select Countries", available_countries, default=available_countries)
            types_selection = c2.multiselect("Select Page Types", ["home", "category", "product", "login"], default=["home", "category", "product", "login"])

        if st.button("Run Audit", type="primary"):
            results = []
            progress = st.progress(0)
            total = len(country_selection) * len([t for t in types_selection if t != 'login'])
            if 'login' in types_selection: total += 1
            
            idx = 0
            if 'login' in types_selection:
                st.write("Auditing Global Login...")
                results.append(perform_full_audit(SSO_LOGIN, "Login (SSO)", "Global"))
                idx += 1
                progress.progress(idx/total)

            for country in country_selection:
                for p_type in types_selection:
                    if p_type == 'login': continue
                    st.write(f"Auditing {country} - {p_type}...")
                    results.append(perform_full_audit(COUNTRIES[country][p_type], PAGE_LABELS[p_type], country))
                    idx += 1
                    progress.progress(idx/total)

            st.session_state["last_results"] = pd.DataFrame(results)
            st.success("Audit Complete!")

        if "last_results" in st.session_state:
            display_dashboard(st.session_state["last_results"])
            st.download_button("Export CSV", st.session_state["last_results"].to_csv(index=False), "lyreco_audit.csv")

    with tab2:
        st.subheader("Historical Results")
        uploaded = st.file_uploader("Upload CSV report", type="csv")
        if uploaded:
            hist_df = pd.read_csv(uploaded)
            display_dashboard(hist_df)
