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
import ast # Do parsowania stringów na listy przy uploadzie

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
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False

    if not st.session_state["logged_in"]:
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.image("https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.svg", width=150)
            st.subheader("Lyreco WCAG Agent Login")
            user = st.text_input("User")
            pwd = st.text_input("Password", type="password")
            if st.button("Log in"):
                if user == "admin" and pwd == "admin2026":
                    st.session_state["logged_in"] = True
                    st.session_state["role"] = "admin"
                    st.rerun()
                elif user == "france" and pwd == "fr2026":
                    st.session_state["logged_in"] = True
                    st.session_state["role"] = "france"
                    st.rerun()
                else:
                    st.error("Invalid credentials")
        return False
    return True

# --- AI ADVISOR ---
def get_ai_recommendation(violation_data, page_context):
    prompt = f"""
    Analyze this WCAG violation for Lyreco {page_context}:
    Violation ID: {violation_data['id']}
    Impact: {violation_data['impact']}
    Description: {violation_data['description']}
    
    Apply the 10 Golden Rules (UX over checklists, business context, simple HTML fixes).
    One single, clear recommendation in English.
    """
    try:
        msg = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=500,
            system="Senior Accessibility Strategic Consultant for Lyreco.",
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except: return "AI Advisor busy..."

# --- AUDIT FUNCTIONS ---
def build_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,1024")
    return webdriver.Chrome(service=Service(shutil.which("chromedriver") or "/usr/bin/chromedriver"), options=opts)

@st.cache_data(ttl=3600)
def fetch_axe():
    return requests.get("https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js").text

def perform_full_audit(url, page_type, country):
    # LH & WAVE Score calculation
    lh = 0
    try:
        r = requests.get(f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={urllib.parse.quote(url)}&category=accessibility&key={GOOGLE_KEY}").json()
        lh = r["lighthouseResult"]["categories"]["accessibility"]["score"] * 100
    except: pass
    
    w_err, w_con = 0, 0
    try:
        r = requests.get(f"https://wave.webaim.org/api/request?key={WAVE_KEY}&url={url}").json()
        w_err = r["categories"]["error"]["count"]
        w_con = r["categories"]["contrast"]["count"]
    except: pass

    # Axe & Screenshot
    axe_data = {"violations": [], "counts": {"critical": 0, "serious": 0}}
    shot = ""
    driver = build_driver()
    try:
        driver.get(url)
        time.sleep(5) # Czekamy aż strona się załaduje
        
        # Opcjonalnie: Tu można by dodać JS usuwający banner, ale zmieniliśmy logikę liczenia punktów
        driver.execute_script(fetch_axe())
        res = driver.execute_async_script("const cb = arguments[arguments.length - 1]; axe.run().then(r => cb(r));")
        violations = res.get("violations", [])
        axe_data = {"violations": violations, "counts": {"critical": sum(1 for v in violations if v.get("impact") == "critical"), "serious": sum(1 for v in violations if v.get("impact") == "serious")}}
        if axe_data["counts"]["critical"] > 0:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                driver.save_screenshot(tmp.name)
                shot = tmp.name
    finally: driver.quit()

    # --- SCORE CALCULATION (SOFTENED) ---
    wave_s = max(0, 100 - (w_err * 2 + w_con * 0.5))
    
    # ZMIANA TUTAJ: Złagodzone kary za błędy Axe, aby zniwelować wpływ bannera
    # Było: Critical * 15, Serious * 5
    # Jest: Critical * 5, Serious * 2
    axe_s = max(0, 100 - (axe_data["counts"]["critical"] * 5 + axe_data["counts"]["serious"] * 2))
    
    final = round((lh * 0.4) + (wave_s * 0.3) + (axe_s * 0.3), 1)

    return {"Country": country, "Type": page_type, "Score": final, "Critical": axe_data["counts"]["critical"], "Serious": axe_data["counts"]["serious"], "URL": url, "Screenshot": shot, "Violations": violations}

# --- DASHBOARD ---
def display_results(df):
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg Score (Soft)", f"{df['Score'].mean():.1f}")
    m2.metric("Blockers", int(df["Critical"].sum()))
    m3.metric("Issues", int(df["Serious"].sum()))
    m4.metric("Markets", len(df["Country"].unique()))

    with st.expander("ℹ️ Scoring Logic (Softened Mode)"):
        st.markdown("""
        **Temporarily adjusted formula to ignore overlay/banner interference:**
        - **Google Lighthouse (40%)**: Standard technical check.
        - **WAVE (30%)**: Structure & Contrast.
        - **Axe-core (30%)**: 
            - *Penalty reduced:* Critical violation = **-5 points** (was -15)
            - *Penalty reduced:* Serious violation = **-2 points** (was -5)
        
        *This helps to see the 'real' score of the content behind blocking popups.*
        """)

    st.subheader("Market Compliance Heatmap")
    pivot = df.pivot_table(index="Country", columns="Type", values="Score")
    st.dataframe(pivot.style.background_gradient(cmap="RdYlGn", low=0.4, high=0.9), use_container_width=True)

    # DETAILED TABLE
    st.subheader("❌ Detailed WCAG Violations")
    violation_rows = []
    for _, row in df.iterrows():
        # Handle case where Violations might be stringified if loaded from CSV
        violations = row["Violations"]
        if isinstance(violations, str):
            try: violations = ast.literal_eval(violations)
            except: violations = []
            
        for v in violations:
            violation_rows.append({
                "Country": row["Country"],
                "Page": row["Type"],
                "Impact": v.get("impact", "minor").capitalize(),
                "WCAG Mapping": AXE_TO_WCAG.get(v["id"], "General Accessibility"),
                "Description": v["help"],
                "Element Count": len(v.get("nodes", []))
            })
    
    if violation_rows:
        v_df = pd.DataFrame(violation_rows)
        impact_order = {"Critical": 0, "Serious": 1, "Moderate": 2, "Minor": 3}
        v_df["sort_idx"] = v_df["Impact"].map(impact_order)
        v_df = v_df.sort_values(by=["sort_idx", "Country"]).drop(columns=["sort_idx"])
        
        st.dataframe(
            v_df, 
            column_config={
                "Impact": st.column_config.TextColumn("Impact", help="Severity of the issue"),
                "Element Count": st.column_config.NumberColumn("Occurrences")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.success("No violations found! 🎉")

    crit_df = df[df["Screenshot"] != ""]
    if not crit_df.empty:
        st.subheader("🖼️ Visual Proof")
        cols = st.columns(3)
        for i, (_, row) in enumerate(crit_df.iterrows()):
            with cols[i % 3]: st.image(row["Screenshot"], caption=f"{row['Country']} - {row['Type']}")

    st.subheader("🧠 AI Advisor Deep Dive")
    for _, row in df.iterrows():
        violations = row["Violations"]
        if isinstance(violations, str):
            try: violations = ast.literal_eval(violations)
            except: violations = []

        if violations:
            with st.expander(f"Strategy: {row['Country']} - {row['Type']}"):
                for v in violations[:1]: 
                    st.write(f"**Issue:** {v['help']}")
                    st.write(get_ai_recommendation(v, row['Type']))

# --- MAIN ---
if check_password():
    with st.sidebar:
        st.image("https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.svg", width=180)
        st.write(f"Logged: **{st.session_state['role'].upper()}**")
        
        if "last_res" in st.session_state:
            st.divider()
            csv = st.session_state["last_res"].to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Full Report (CSV)",
                data=csv,
                file_name=f"lyreco_audit_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv',
                use_container_width=True
            )
            
        if st.button("Logout", use_container_width=True):
            st.session_state["logged_in"] = False
            st.rerun()
      
    tab1, tab2 = st.tabs(["🚀 New Audit", "📂 History"])
    
    with tab1:
        c1, c2 = st.columns(2)
        options = list(COUNTRIES.keys()) if st.session_state["role"] == "admin" else ["France"]
        sel_countries = c1.multiselect("Countries", options, default=options)
        sel_types = c2.multiselect("Pages", ["home", "category", "product", "login"], default=["home", "product"])

        if st.button("Run Audit (Soft Mode)", type="primary"):
            results = []
            for c in sel_countries:
                for t in sel_types:
                    st.write(f"Checking {c} {t}...")
                    results.append(perform_full_audit(COUNTRIES[c].get(t, SSO_LOGIN), t, c))
            st.session_state["last_res"] = pd.DataFrame(results)
            st.rerun()

        if "last_res" in st.session_state:
            display_results(st.session_state["last_res"])

    with tab2:
        up = st.file_uploader("Upload CSV")
        if up: 
            df = pd.read_csv(up)
            st.session_state["last_res"] = df
            display_results(df)

with st.expander("📊 Scoring Calculation (Adjusted)"):
    st.markdown(
        """
        ### Lyreco Accessibility Score (Softened)

        **⚠️ NOTE: Scoring has been adjusted to account for 'Getsitecontrol' banner interference.**
        
        **New Weights:**
        - **Critical Errors:** -5 points (Standard is -15)
        - **Serious Errors:** -2 points (Standard is -5)
        
        This allows us to see the quality of the underlying page code without the score being zeroed out by the overlay.
        """
    )

st.divider()
