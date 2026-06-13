"""
Theme module — light theme with Lactalis branding.
Palet: #071952 #088395 #37B7C3 #EBF4F6 #FFFFFF
"""
import streamlit as st
from pathlib import Path

LOGO_PATH = Path("assets/lactalis_logo.png")

LIGHT_CSS = """
<style>
/* ─── Reset & Base ─────────────────────────────── */
html, body, [data-testid="stAppViewContainer"] {
  background-color: #FFFFFF !important;
}
[data-testid="stSidebar"] {
  background-color: #071952 !important;
}
[data-testid="stSidebar"] * {
  color: #EBF4F6 !important;
}

[data-testid="stSidebar"] .stButton button,
[data-testid="stSidebar"] .stButton button * {
  color: #071952 !important;
}

[data-testid="stSidebar"] .stRadio label {
  color: #EBF4F6 !important;
}
/* Sidebar input fields: teks harus terbaca di background gelap */
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] .stNumberInput input,
[data-testid="stSidebar"] .stTextInput input {
  color: #071952 !important;
  background-color: #EBF4F6 !important;
  border: 1px solid #37B7C3 !important;
}
[data-testid="stSidebar"] .stSelectbox > div > div {
  color: #071952 !important;
  background-color: #EBF4F6 !important;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] .stNumberInput label,
[data-testid="stSidebar"] .stTextInput label,
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown div {
  color: #EBF4F6 !important;
}
/* Slider track dan thumb di sidebar */
[data-testid="stSidebar"] .stSlider > div > div > div {
  background: #37B7C3 !important;
}
/* Success/info di sidebar */
[data-testid="stSidebar"] .stSuccess,
[data-testid="stSidebar"] .stInfo {
  background: rgba(55,183,195,0.15) !important;
  color: #EBF4F6 !important;
}
[data-testid="stSidebar"] .stSuccess p,
[data-testid="stSidebar"] .stInfo p {
  color: #EBF4F6 !important;
}
/* Toolbar / header atas tetap putih */
[data-testid="stHeader"] {
  background-color: #FFFFFF !important;
  border-bottom: 1px solid #EBF4F6;
}
/* Main area background */
section.main > div {
  background-color: #FFFFFF !important;
}
/* ─── Typography ────────────────────────────────── */
h1,h2,h3,h4,h5,h6 { color: #071952 !important; }
p, li, span, label { color: #071952; }

/* ─── Custom components ─────────────────────────── */
.page-title {
  font-size: 1.55rem; font-weight: 700; color: #071952;
  border-left: 4px solid #088395; padding: 6px 14px; margin-bottom: 1rem;
}
.section-title {
  font-size: .88rem; font-weight: 700; color: #088395;
  text-transform: uppercase; letter-spacing: .08em; margin: 1.2rem 0 .4rem;
}
.kpi-box {
  background: #EBF4F6; border: 1px solid #37B7C3; border-radius: 8px;
  padding: 14px 18px; border-left: 4px solid #088395; margin-bottom: 8px;
}
.kpi-label {
  font-size: .72rem; color: #088395; text-transform: uppercase; letter-spacing: .08em;
}
.kpi-value {
  font-size: 1.35rem; font-weight: 700; color: #071952; margin-top: 2px;
}
.dss-card {
  background: #EBF4F6; border: 1px solid #37B7C3; border-radius: 10px;
  padding: 1.2rem; margin-bottom: 12px;
}
.hero {
  background: #EBF4F6; border: 1px solid #37B7C3; border-radius: 12px;
  padding: 1.4rem 1.8rem; margin-bottom: 1.2rem;
}
.hero h2 { color: #071952 !important; margin: 0 0 .3rem; }
.hero p  { color: #088395; margin: 0; }
.note-box {
  background: #EBF4F6; border-left: 4px solid #088395;
  padding: 10px 14px; border-radius: 4px; color: #071952;
  font-size: .88rem; margin: .6rem 0;
}
.warn-box {
  background: #fff8e1; border-left: 4px solid #e6a817;
  padding: 10px 14px; border-radius: 4px; color: #7a5800;
  font-size: .88rem; margin: .6rem 0;
}
.badge-feasible   { background:#1a7f4b; color:#fff; padding:4px 14px; border-radius:6px; font-weight:700; }
.badge-infeasible { background:#c0392b; color:#fff; padding:4px 14px; border-radius:6px; font-weight:700; }
.badge-maintain   { background:#1a7f4b; color:#fff; padding:3px 10px; border-radius:4px; font-weight:600; font-size:.82rem; }
.badge-modify     { background:#e6a817; color:#fff; padding:3px 10px; border-radius:4px; font-weight:600; font-size:.82rem; }
/* ── File uploader fix: readable in dark sidebar ─────────────────── */
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
  background: rgba(55,183,195,0.12) !important;
  border: 1.5px dashed #37B7C3 !important;
  border-radius: 6px !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button {
  color: #EBF4F6 !important;
  border-color: #37B7C3 !important;
  background: rgba(8,131,149,0.25) !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] span,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] small,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] p {
  color: #EBF4F6 !important;
}
/* Main area file uploader */
[data-testid="stFileUploaderDropzone"] {
  background: rgba(55,183,195,0.06) !important;
  border: 1.5px dashed #088395 !important;
}
[data-testid="stFileUploaderDropzone"] button { color: #071952 !important; }

/* ── Sidebar contrast fixes: SEMUA teks harus terbaca di bg gelap ── */
/* Tombol di sidebar (mis. Hapus Data Simulasi) */
[data-testid="stSidebar"] .stButton button,
[data-testid="stSidebar"] button[kind="secondary"],
[data-testid="stSidebar"] button[kind="primary"] {
  color: #EBF4F6 !important;
  background: rgba(8,131,149,0.30) !important;
  border: 1px solid #37B7C3 !important;
}
[data-testid="stSidebar"] .stButton button:hover {
  color: #FFFFFF !important;
  background: rgba(8,131,149,0.55) !important;
  border-color: #EBF4F6 !important;
}
[data-testid="stSidebar"] .stButton button p,
[data-testid="stSidebar"] .stButton button span {
  color: inherit !important;
}
/* Alert/success/info/warning di sidebar — semua versi testid */
[data-testid="stSidebar"] [data-testid="stAlert"],
[data-testid="stSidebar"] [data-testid="stAlertContainer"],
[data-testid="stSidebar"] .stAlert {
  background: rgba(55,183,195,0.18) !important;
  border: 1px solid #37B7C3 !important;
  border-radius: 6px !important;
}
[data-testid="stSidebar"] [data-testid="stAlert"] p,
[data-testid="stSidebar"] [data-testid="stAlert"] span,
[data-testid="stSidebar"] [data-testid="stAlert"] div,
[data-testid="stSidebar"] [data-testid="stAlertContainer"] p,
[data-testid="stSidebar"] [data-testid="stAlertContainer"] span,
[data-testid="stSidebar"] [data-testid="stAlertContainer"] div,
[data-testid="stSidebar"] .stAlert p {
  color: #EBF4F6 !important;
}
/* File yang sudah ter-upload (nama file + ikon + tombol hapus) */
[data-testid="stSidebar"] [data-testid="stFileUploaderFile"],
[data-testid="stSidebar"] [data-testid="stFileUploaderFileName"],
[data-testid="stSidebar"] [data-testid="stFileUploaderFile"] span,
[data-testid="stSidebar"] [data-testid="stFileUploaderFile"] small,
[data-testid="stSidebar"] [data-testid="stFileUploaderFile"] div,
[data-testid="stSidebar"] [data-testid="stFileUploaderFile"] p {
  color: #EBF4F6 !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploaderDeleteBtn"] button,
[data-testid="stSidebar"] [data-testid="stFileUploaderDeleteBtn"] svg {
  color: #EBF4F6 !important;
  fill: #EBF4F6 !important;
}
/* Label semua jenis input di sidebar */
[data-testid="stSidebar"] .stNumberInput label,
[data-testid="stSidebar"] .stMultiSelect label,
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] .stCheckbox label,
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] label {
  color: #EBF4F6 !important;
}
/* Caption / small text di sidebar */
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] small {
  color: #B8D4D9 !important;
}
/* Ikon help (?) di sidebar */
[data-testid="stSidebar"] svg { fill: #B8D4D9; }
</style>
"""


def inject_css():
    st.markdown(LIGHT_CSS, unsafe_allow_html=True)


def sidebar_brand():
    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
        st.markdown("""
        <div style="text-align:center;padding:8px 0 12px;">
          <div style="font-size:.8rem;font-weight:700;color:#37B7C3;letter-spacing:.12em;">
            DECISION SUPPORT SYSTEM
          </div>
          <div style="font-size:.7rem;color:#EBF4F6;opacity:.8;">PT FBMI · Lactalis · IPB TIN</div>
        </div>""", unsafe_allow_html=True)


def hero(title, subtitle=""):
    st.markdown(f"""
    <div class="hero">
      <h2>{title}</h2>
      {"<p>" + subtitle + "</p>" if subtitle else ""}
    </div>""", unsafe_allow_html=True)


def note(text):
    st.markdown(f'<div class="note-box">{text}</div>', unsafe_allow_html=True)


def warning(text):
    st.markdown(f'<div class="warn-box">{text}</div>', unsafe_allow_html=True)
