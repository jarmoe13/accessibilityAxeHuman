import streamlit as st
import pandas as pd
import requests
import urllib.parse
from datetime import datetime
import time
import shutil
import tempfile
import os
from pathlib import Path
import anthropic
import ast 
from fpdf import FPDF # 👈 Nowa biblioteka do PDF

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# --- CONFIGURATION ---
st.set_page_config(page_title="Lyreco Accessibility Monitor", layout="wide")

# Custom CSS for Lyreco Branding
st.markdown("""
    <style>
    div.stButton > button:first-child {
        background-color: #2D2E87;
        color: white;
        border-radius: 5px;
        border: none;
    }
    div.stButton > button:hover {
        background-color: #1a1b5e;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

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

# --- PDF GENERATOR (W3C Style) ---
class PDFReport(FPDF):
    def header(self):
        # Logo handling
        try:
            # Check if logo exists locally, if not try to download (or skip)
            # For this demo, we assume we can't easily download in header loop without caching
            # so we will use a text header mainly, or handle logo in the main body.
            pass
        except: pass
        
        self.set_font('Arial', 'B', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, 'Lyreco Accessibility Audit Report', 0, 1, 'R')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

    def chapter_title(self, label):
        self.set_font('Arial', 'B', 16)
        self.set_text_color(45, 46, 135) # Lyreco Blue
        self.cell(0, 10, label, 0, 1, 'L')
        self.ln(4)

    def chapter_body(self, text):
        self.set_font('Arial', '', 11)
        self.set_text_color(0)
        self.multi_cell(0, 6, text)
        self.ln()

def generate_w3c_pdf(df):
    pdf = PDFReport()
    pdf.add_page()
    
    # 1. HEADER & LOGO
    # Download logo to temp file for PDF inclusion
    logo_path = "lyreco_logo.png"
    try:
        if not os.path.exists(logo_path):
            img_data = requests.get("https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.png").content
            with open(logo_path, 'wb') as handler:
                handler.write(img_data)
        pdf.image(logo_path, x=10, y=8, w=40)
    except: pass
    
    pdf.ln(20)
    pdf.set_font('Arial', 'B', 24)
    pdf.cell(0, 15, "Accessibility Evaluation Report", 0, 1, 'L')
    
    pdf.set_font('Arial', '', 12)
    pdf.cell(0, 8, f"Date: {datetime.now().strftime('%B %d, %Y')}", 0, 1, 'L')
    pdf.cell(0, 8, f"Auditor: Lyreco Automated Agent (v8.0)", 0, 1, 'L')
    pdf.ln(10)

    # 2. EXECUTIVE SUMMARY
    pdf.chapter_title("1. Executive Summary")
    avg_score = df['Score'].mean()
    
    verdict = "Non-Compliant"
    if avg_score >= 90: verdict = "Excellent Compliance"
    elif avg_score >= 80: verdict = "Good Compliance"
    elif avg_score >= 60: verdict = "Partial Compliance"
    
    summary_text = (
        f"This report presents the results of an automated accessibility evaluation of the Lyreco e-commerce platform across selected markets. "
        f"The overall accessibility score is {avg_score:.1f}/100, categorized as '{verdict}'. "
        f"The evaluation highlights {int(df['Critical'].sum())} critical blockers and {int(df['Serious'].sum())} serious issues that require immediate attention to meet WCAG 2.1 AA standards."
    )
    pdf.chapter_body(summary_text)

    # 3. SCOPE OF EVALUATION
    pdf.chapter_title("2. Scope of Evaluation")
    pdf.chapter_body("The following pages and markets were included in this audit:")
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(40, 10, "Market", 1, 0, 'C', 1)
    pdf.cell(40, 10, "Page Type", 1, 0, 'C', 1)
    pdf.cell(110, 10, "URL (Truncated)", 1, 1, 'C', 1)
    
    pdf.set_font('Arial', '', 9)
    for _, row in df.iterrows():
        pdf.cell(40, 8, row['Country'], 1)
        pdf.cell(40, 8, row['Type'], 1)
        short_url = (row['URL'][:55] + '...') if len(row['URL']) > 55 else row['URL']
        pdf.cell(110, 8, short_url, 1, 1)
    pdf.ln(10)

    # 4. DETAILED FINDINGS
    pdf.chapter_title("3. Detailed Findings")
    pdf.chapter_body("The following critical and serious violations were detected using Axe-core.")

    for _, row in df.iterrows():
        violations = row["Violations"]
        if isinstance(violations, str):
            try: violations = ast.literal_eval(violations)
            except: violations = []
        
        # Filter for Critical/Serious only for the PDF to keep it clean
        serious_violations = [v for v in violations if v.get('impact') in ['critical', 'serious']]
        
        if serious_violations:
            pdf.set_font('Arial', 'B', 11)
            pdf.set_text_color(45, 46, 135)
            pdf.cell(0, 10, f"{row['Country']} - {row['Type']} (Score: {row['Score']})", 0, 1)
            
            pdf.set_font('Arial', 'B', 9)
            pdf.set_text_color(0)
            pdf.set_fill_color(220, 220, 220)
            pdf.cell(30, 8, "Impact", 1, 0, 'C', 1)
            pdf.cell(60, 8, "Issue ID", 1, 0, 'C', 1)
            pdf.cell(100, 8, "Description", 1, 1, 'C', 1)
            
            pdf.set_font('Arial', '', 8)
            for v in serious_violations:
                impact = v.get('impact', 'minor').upper()
                pdf.set_text_color(200, 0, 0) if impact == 'CRITICAL' else pdf.set_text_color(0)
                pdf.cell(30, 8, impact, 1, 0, 'C')
                
                pdf.set_text_color(0)
                pdf.cell(60, 8, v['id'], 1, 0)
                pdf.cell(100, 8, v['help'], 1, 1)
            pdf.ln(5)
            
    return pdf.output(dest='S').encode('latin-1', 'replace')

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

    axe_data = {"violations": [], "counts": {"critical": 0, "serious": 0}}
    shot = ""
    driver = build_driver()
    try:
        driver.get(url)
        time.sleep(5)
        
        driver.execute_script(fetch_axe())
        res = driver.execute_async_script("const cb = arguments[arguments.length - 1]; axe.run().then(r => cb(r));")
        violations = res.get("violations", [])
        axe_data = {"violations": violations, "counts": {"critical": sum(1 for v in violations if v.get("impact") == "critical"), "serious": sum(1 for v in violations if v.get("impact") == "serious")}}
        if axe_data["counts"]["critical"] > 0:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                driver.save_screenshot(tmp.name)
                shot = tmp.name
    finally: driver.quit()

    wave_s = max(0, 100 - (w_err * 2 + w_con * 0.5))
    axe_s = max(0, 100 - (axe_data["counts"]["critical"] * 5 + axe_data["counts"]["serious"] * 2))
    
    final = round((lh * 0.4) + (wave_s * 0.3) + (axe_s * 0.3), 1)

    return {"Country": country, "Type": page_type, "Score": final, "Critical": axe_data["counts"]["critical"], "Serious": axe_data["counts"]["serious"], "URL": url, "Screenshot": shot, "Violations": violations}

# --- DASHBOARD ---
def display_results(df):
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg Score", f"{df['Score'].mean():.1f}")
    m2.metric("Blockers", int(df["Critical"].sum()))
    m3.metric("Issues", int(df["Serious"].sum()))
    m4.metric("Markets", len(df["Country"].unique()))

    st.subheader("Market Compliance Heatmap")
    pivot = df.pivot_table(index="Country", columns="Type", values="Score")
    st.dataframe(pivot.style.background_gradient(cmap="RdYlGn", low=0.4, high=0.9), use_container_width=True)

    st.subheader("❌ Detailed WCAG Violations")
    violation_rows = []
    for _, row in df.iterrows():
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
        st.subheader("🖼️ Visual Proof (Critical Issues)")
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
            
            # 1. CSV Download
            csv = st.session_state["last_res"].to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Data (CSV)",
                data=csv,
                file_name=f"lyreco_audit_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv',
                use_container_width=True
            )
            
            # 2. PDF Download (NEW)
            try:
                pdf_bytes = generate_w3c_pdf(st.session_state["last_res"])
                st.download_button(
                    label="📄 Download Report (PDF)",
                    data=pdf_bytes,
                    file_name=f"Lyreco_W3C_Report_{datetime.now().strftime('%Y%m%d')}.pdf",
                    mime='application/pdf',
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"PDF Gen Error: {e}")
            
        if st.button("Logout", use_container_width=True):
            st.session_state["logged_in"] = False
            st.rerun()
      
    tab1, tab2 = st.tabs(["🚀 New Audit", "📂 History"])
    
    with tab1:
        c1, c2 = st.columns(2)
        options = list(COUNTRIES.keys()) if st.session_state["role"] == "admin" else ["France"]
        sel_countries = c1.multiselect("Countries", options, default=options)
        sel_types = c2.multiselect("Pages", ["home", "category", "product", "login"], default=["home", "product"])

        if st.button("Run Audit", type="primary"):
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
