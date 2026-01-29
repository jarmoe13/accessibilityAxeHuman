import streamlit as st
import pandas as pd
import requests
import urllib.parse
import plotly.express as px
from datetime import datetime
import time
import shutil

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

# --- CONFIGURATION ---
st.set_page_config(page_title="Lyreco Accessibility Monitor", layout="wide")

try:
    GOOGLE_KEY = st.secrets["GOOGLE_KEY"]
    WAVE_KEY = st.secrets["WAVE_KEY"]
except KeyError:
    st.error("⚠️ Missing API keys. Add GOOGLE_KEY and WAVE_KEY to Streamlit Secrets.")
    st.stop()

# --- COUNTRIES ---
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
    "Ireland": {
        "home": "https://shop.lyreco.ie/en",
        "category": "https://shop.lyreco.ie/en/list/001001/paper-envelopes/white-office-paper",
        "product": "https://shop.lyreco.ie/en/product/159.543/lyreco-white-a4-80gsm-copier-paper-box-of-5-reams-5x500-sheets-of-paper",
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
    },
    "Denmark": {
        "home": "https://shop.lyreco.dk/da",
        "category": "https://shop.lyreco.dk/da/list/001001/papir-kuverter/printerpapir-kopipapir",
        "product": "https://shop.lyreco.dk/da/product/159.543/kopipapir-til-sort-hvid-print-lyreco-copy-a4-80-g-pakke-a-5-x-500-ark",
    },
}

SSO_LOGIN = (
    "https://welcome.lyreco.com/lyreco-customers/login?scope=openid+"
    "lyreco.contacts.personalInfo%3Awrite%3Aself&client_id=2ddf9463-"
    "3e1e-462a-9f94-633e1e062ae8&response_type=code&state=4102a88f-"
    "fec5-46d1-b8d9-ea543ba0a385&redirect_uri=https%3A%2F%2Fshop.lyreco.fr%2F"
    "oidc-login-callback%2FaHR0cHMlM0ElMkYlMkZzaG9wLmx5cmVjby5mciUyRmZy&"
    "ui_locales=fr-FR&logo_uri=https%3A%2F%2Fshop.lyreco.fr"
)

PAGE_LABELS = {
    "home": "Home",
    "category": "Category",
    "product": "Product",
}

# --- HELPER FUNCTIONS ---
def safe_int(value):
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def safe_float(value):
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    chromedriver_path = shutil.which("chromedriver")
    if chromedriver_path:
        service = Service(chromedriver_path)
        return webdriver.Chrome(service=service, options=chrome_options)

    return webdriver.Chrome(options=chrome_options)


@st.cache_data(ttl=600, show_spinner=False)
def run_axe_test(url, max_retries=2):
    for attempt in range(max_retries + 1):
        driver = None
        try:
            driver = build_driver()
            driver.get(url)
            time.sleep(2)

            driver.execute_script(
                """
                return new Promise((resolve, reject) => {
                    const script = document.createElement('script');
                    script.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.7.2/axe.min.js';
                    script.onload = () => resolve(true);
                    script.onerror = () => reject(new Error('Failed to load axe-core'));
                    document.head.appendChild(script);
                });
                """
            )

            time.sleep(1)
            results = driver.execute_script("return axe.run();")

            violations = results.get("violations", [])
            passes = results.get("passes", [])

            critical = sum(1 for v in violations if v.get("impact") == "critical")
            serious = sum(1 for v in violations if v.get("impact") == "serious")
            moderate = sum(1 for v in violations if v.get("impact") == "moderate")
            minor = sum(1 for v in violations if v.get("impact") == "minor")

            return {
                "total_violations": len(violations),
                "critical": critical,
                "serious": serious,
                "moderate": moderate,
                "minor": minor,
                "passes": len(passes),
                "violations_details": violations[:5],
                "error": "",
            }
        except Exception as exc:
            if attempt >= max_retries:
                return {
                    "total_violations": 0,
                    "critical": 0,
                    "serious": 0,
                    "moderate": 0,
                    "minor": 0,
                    "passes": 0,
                    "violations_details": [],
                    "error": str(exc),
                }
        finally:
            if driver:
                driver.quit()


def calculate_component_scores(lh_pct, w_err, w_con, axe_critical, axe_serious, axe_available):
    lh_score = safe_float(lh_pct)
    wave_penalty = (safe_int(w_err) * 1.2) + (safe_int(w_con) * 0.5)
    wave_score = max(0.0, 100 - wave_penalty)

    if axe_available:
        axe_penalty = (safe_int(axe_critical) * 10) + (safe_int(axe_serious) * 5)
        axe_score = max(0.0, 100 - axe_penalty)
    else:
        axe_score = None

    return lh_score, wave_score, axe_score


def calculate_weighted_score(lh_score, wave_score, axe_score):
    weights = {"lh": 0.4, "wave": 0.3, "axe": 0.3}
    total_weight = weights["lh"] + weights["wave"] + (weights["axe"] if axe_score is not None else 0)

    if total_weight == 0:
        return 0.0

    weighted_sum = (lh_score * weights["lh"]) + (wave_score * weights["wave"])
    if axe_score is not None:
        weighted_sum += axe_score * weights["axe"]

    return round(weighted_sum / total_weight, 1)


def generate_recommendations(score, w_err, w_con, aria_issues, alt_issues, axe_critical, axe_serious):
    recommendations = []

    if axe_critical > 0:
        recommendations.append(f"🔴 CRITICAL: Fix {axe_critical} critical axe-core violations")

    if axe_serious > 0:
        recommendations.append(f"🟠 HIGH: Resolve {axe_serious} serious axe-core violations")

    if aria_issues > 0:
        recommendations.append(f"🔴 CRITICAL: Fix {aria_issues} ARIA issues")

    if alt_issues > 0:
        recommendations.append(f"🔴 CRITICAL: Add alt text to {alt_issues} images")

    if w_con > 10:
        recommendations.append(f"🟡 HIGH: Fix {w_con} contrast issues (WCAG AA)")
    elif w_con > 0:
        recommendations.append(f"🟡 MEDIUM: Improve {w_con} contrast ratios")

    if w_err > 20:
        recommendations.append(f"🔴 HIGH: {w_err} accessibility errors detected")
    elif w_err > 5:
        recommendations.append(f"🟡 MEDIUM: {w_err} errors need attention")

    if score < 60:
        recommendations.append("⚠️ ACTION REQUIRED: Critical barriers present")
    elif score < 80:
        recommendations.append("📋 PLAN: Schedule fixes in next sprint")
    elif score >= 90:
        recommendations.append("✅ MAINTAIN: Monitor for regressions")

    return recommendations if recommendations else ["✅ No major issues detected"]


def run_audit(url, page_type, country, deploy_version="", run_axe=True):
    lh_val = 0.0
    err = 0
    con = 0
    aria_issues = 0
    alt_issues = 0
    failed_audits = []

    axe_critical = 0
    axe_serious = 0
    axe_total = 0
    axe_error = ""

    # Lighthouse
    try:
        url_enc = urllib.parse.quote(url)
        lh_api = (
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={url_enc}&category=accessibility&onlyCategories=accessibility"
            f"&strategy=desktop&key={GOOGLE_KEY}"
        )
        r_lh = requests.get(lh_api, timeout=45)

        if r_lh.status_code == 200:
            d = r_lh.json()
            score_value = (
                d.get("lighthouseResult", {})
                .get("categories", {})
                .get("accessibility", {})
                .get("score")
            )
            if score_value is not None:
                lh_val = float(score_value) * 100

            audits = d.get("lighthouseResult", {}).get("audits", {})
            for audit_id, audit_data in audits.items():
                score_val = audit_data.get("score", 1)
                if score_val is not None and score_val < 1:
                    title = audit_data.get("title", "Unknown")
                    failed_audits.append(title)
                    if "aria" in audit_id.lower():
                        aria_issues += 1
                    if "image-alt" in audit_id or "alt" in str(title).lower():
                        alt_issues += 1
    except Exception as exc:
        st.warning(f"⚠️ Lighthouse error: {str(exc)[:80]}")

    # WAVE
    try:
        wave_api = f"https://wave.webaim.org/api/request?key={WAVE_KEY}&url={url}"
        r_w = requests.get(wave_api, timeout=35)

        if r_w.status_code == 200:
            dw = r_w.json()
            if "categories" in dw:
                if "error" in dw["categories"]:
                    err = safe_int(dw["categories"]["error"].get("count"))
                if "contrast" in dw["categories"]:
                    con = safe_int(dw["categories"]["contrast"].get("count"))
    except Exception as exc:
        st.warning(f"⚠️ WAVE error: {str(exc)[:80]}")

    # Axe-core
    if run_axe:
        axe_results = run_axe_test(url)
        axe_critical = axe_results["critical"]
        axe_serious = axe_results["serious"]
        axe_total = axe_results["total_violations"]
        axe_error = axe_results.get("error", "")

        if axe_error:
            st.info(f"⚠️ Axe-core unavailable for {country} {page_type}: {axe_error[:80]}")

    lh_score, wave_score, axe_score = calculate_component_scores(
        lh_val, err, con, axe_critical, axe_serious, run_axe and not axe_error
    )

    score = calculate_weighted_score(lh_score, wave_score, axe_score)
    recommendations = generate_recommendations(
        score,
        err,
        con,
        aria_issues,
        alt_issues,
        axe_critical,
        axe_serious,
    )

    return {
        "Country": str(country),
        "Page Type": str(page_type),
        "URL": str(url),
        "Score": float(score),
        "Lighthouse": float(lh_val),
        "WAVE Errors": int(err),
        "Contrast Issues": int(con),
        "ARIA Issues": int(aria_issues),
        "Alt Text Issues": int(alt_issues),
        "Axe Critical": int(axe_critical),
        "Axe Serious": int(axe_serious),
        "Axe Total Violations": int(axe_total),
        "Axe Error": axe_error,
        "Top Failed Audits": "; ".join(failed_audits[:3]) if failed_audits else "",
        "Recommendations": " | ".join(recommendations),
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Deploy_Version": str(deploy_version) if deploy_version else "",
    }


def display_dashboard(df):
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Average Score", f"{df['Score'].mean():.1f}")
    with col2:
        st.metric("Axe Critical", int(df["Axe Critical"].sum()))
    with col3:
        st.metric("Axe Serious", int(df["Axe Serious"].sum()))
    with col4:
        st.metric("ARIA Issues", int(df["ARIA Issues"].sum()))
    with col5:
        st.metric("Contrast Issues", int(df["Contrast Issues"].sum()))

    st.divider()

    st.subheader("🗺️ Score Heatmap by Country & Page")

    def color_score(val):
        if pd.isna(val):
            return ""
        val = safe_float(val)
        if val >= 95:
            return "background-color: #00cc66; color: white"
        if val >= 90:
            return "background-color: #66ff99"
        if val >= 80:
            return "background-color: #ffff66"
        if val >= 60:
            return "background-color: #ffcc66"
        return "background-color: #ff6666; color: white"

    pivot_df = df.pivot_table(values="Score", index="Country", columns="Page Type", aggfunc="first")
    styled_df = pivot_df.style.applymap(color_score).format("{:.1f}", na_rep="N/A")
    st.dataframe(styled_df, use_container_width=True)

    st.divider()

    st.subheader("📋 Detailed Results")

    country_filter = st.multiselect(
        "Filter by Country",
        options=df["Country"].unique().tolist(),
        default=df["Country"].unique().tolist(),
    )

    filtered_df = df[df["Country"].isin(country_filter)]
    display_cols = [
        "Country",
        "Page Type",
        "Score",
        "Lighthouse",
        "WAVE Errors",
        "Axe Critical",
        "Axe Serious",
        "Contrast Issues",
        "ARIA Issues",
    ]
    st.dataframe(filtered_df[display_cols], use_container_width=True)

    st.divider()

    st.subheader("🎯 Priority Actions")

    critical = df[df["Score"] < 80].sort_values("Score")
    if len(critical) > 0:
        for _, row in critical.iterrows():
            with st.expander(f"⚠️ {row['Country']} - {row['Page Type']} (Score: {row['Score']:.1f})"):
                st.markdown(f"**URL:** {row['URL']}")
                st.markdown(
                    f"**Lighthouse:** {row['Lighthouse']:.1f} | "
                    f"**WAVE Errors:** {safe_int(row['WAVE Errors'])} | "
                    f"**Axe Critical:** {safe_int(row['Axe Critical'])} | "
                    f"**Axe Serious:** {safe_int(row['Axe Serious'])}"
                )
                st.markdown("**Recommendations:**")
                recs = str(row["Recommendations"]).split(" | ")
                for i, rec in enumerate(recs, 1):
                    if rec and rec != "nan":
                        st.markdown(f"{i}. {rec}")
    else:
        st.success("✅ No critical issues! All pages score 80+")

    st.divider()

    st.subheader("📊 Score Distribution by Country")
    chart_df = df[df["Country"] != "Global"].copy()
    if len(chart_df) > 0:
        fig = px.bar(
            chart_df,
            x="Country",
            y="Score",
            color="Page Type",
            barmode="group",
            title="Accessibility Scores by Country and Page Type",
        )
        fig.update_layout(yaxis_range=[0, 100])
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Full Report (CSV)",
        data=csv,
        file_name=f"lyreco_audit_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )


with st.sidebar:
    st.image(
        "https://cdn-s1.lyreco.com/staticswebshop/pictures/looknfeel/FRFR/logo.svg",
        width=200,
    )
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
        - 6 countries
        - 3 page types per country
        - 100+ accessibility checks
        """
    )
    st.divider()
    st.caption("Version 8.0 | January 2026")

st.title("Lyreco Accessibility Monitor")
st.caption("Multi-country WCAG compliance tracking with Axe-core")

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

tab1, tab2 = st.tabs(["🚀 Run New Audit", "📂 Upload Previous Results"])

with tab1:
    st.subheader("Run Multi-Country Audit")

    deploy_version = st.text_input("Deploy Version (optional)", placeholder="e.g., Sprint-15, v2.5")

    country_selection = st.multiselect(
        "Select Countries to Audit",
        options=list(COUNTRIES.keys()),
        default=list(COUNTRIES.keys()),
    )

    run_axe_tests = st.checkbox("Include Axe-core tests (slower but more accurate)", value=True)

    if st.button("🚀 Start Audit", type="primary"):
        if not country_selection:
            st.warning("Please select at least one country")
        else:
            results = []
            total_audits = 1 + (len(country_selection) * 3)
            progress_bar = st.progress(0)
            status_text = st.empty()
            current = 0

            status_text.text("🔍 Auditing Global SSO Login...")
            results.append(run_audit(SSO_LOGIN, "Login (SSO)", "Global", deploy_version, run_axe_tests))
            current += 1
            progress_bar.progress(current / total_audits)

            for country in country_selection:
                pages = COUNTRIES[country]
                for page_type, url in pages.items():
                    status_text.text(f"🔍 Auditing {country} - {PAGE_LABELS.get(page_type, page_type)}...")
                    results.append(run_audit(url, PAGE_LABELS.get(page_type, page_type), country, deploy_version, run_axe_tests))
                    current += 1
                    progress_bar.progress(current / total_audits)

            progress_bar.empty()
            status_text.empty()

            df = pd.DataFrame(results)
            st.success(f"✅ Audit complete! Tested {len(results)} pages")
            st.divider()
            display_dashboard(df)

with tab2:
    st.subheader("Upload Previous Audit Results")

    uploaded_file = st.file_uploader("Upload CSV from previous audit", type="csv")

    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        st.success(f"✅ Loaded {len(df)} audit results")
        st.divider()
        display_dashboard(df)
    else:
        st.info("👆 Upload a CSV file to view historical results")

st.divider()
st.caption("Version 8.0 - Lighthouse + WAVE + Axe-core")
