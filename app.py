"""
Decision Support System — PT FBMI Lactalis, TIN IPB 2026
"""
import streamlit as st
from pathlib import Path
import base64
from modules.session import init_session, get_state, set_state, clear_session_data

LOGO        = "assets/lactalis_logo.png"
LOGO_SQUARE = "assets/lactalis_logo_square.png"
FAVICON     = LOGO_SQUARE if Path(LOGO_SQUARE).exists() else (LOGO if Path(LOGO).exists() else None)

st.set_page_config(
    page_title="Decision Support System",
    page_icon=FAVICON or ":material/factory:",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_session()

from modules.theme import inject_css, sidebar_brand
inject_css()

from modules.user_manager import (verify_session, create_session,
                                   verify_login, register, load_users, save_users)
import hashlib, re as _re

def _hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def _pw_valid(pw):
    if len(pw) < 8:                         return False, "Minimal 8 karakter."
    if not _re.search(r'[0-9]', pw):        return False, "Harus mengandung angka."
    if not _re.search(r'[^a-zA-Z0-9]', pw): return False, "Harus mengandung simbol."
    return True, ""

def _auto_login():
    if get_state("logged_in"): return True
    # Tidak ada logged_in di session → sesi browser baru
    # → bersihkan disk cache sesi sebelumnya supaya tidak muncul data lama
    p = Path("data/.session_token")
    if p.exists():
        try:
            ok, uname = verify_session(p.read_text().strip())
            if ok:
                clear_session_data()   # ← hapus sisa cache sesi lama
                users = load_users()
                udata = users.get(uname, {})
                set_state("logged_in", True)
                set_state("user_name", udata.get("name", ""))
                set_state("_username", uname)
                return True
        except Exception:
            pass
    return False

# ── LOGIN PAGE ──────────────────────────────────────────────────────────────
def auth_page():
    st.html(
        "<script>"
        "document.title='Decision Support System — Masuk';"
        "</script>",
        unsafe_allow_javascript=True,
    )
    logo_b64 = ""
    for p in [LOGO_SQUARE, LOGO]:
        if Path(p).exists():
            logo_b64 = base64.b64encode(open(p, "rb").read()).decode()
            break
    logo_img = (
        f'<img src="data:image/png;base64,{logo_b64}" '        f'style="width:260px;margin-bottom:36px;display:block;position:relative;z-index:1;" />'
        if logo_b64 else ""
    )

    # CSS: sembunyikan Streamlit chrome, kolom kiri full-height biru, kanan putih
    st.markdown(f"""
    <style>
    [data-testid="stHeader"],[data-testid="stSidebar"],
    [data-testid="stToolbar"],footer,#MainMenu {{
        display:none!important;
    }}
    html,body,[data-testid="stAppViewContainer"]{{
        margin:0!important;padding:0!important;
    }}
    section.main,.block-container{{
        padding:0!important;max-width:100%!important;margin:0!important;
    }}
    /* Pastikan horizontal block stretch full height, tanpa gap */
    [data-testid="stHorizontalBlock"]{{
        gap:0!important;
        align-items:stretch!important;
    }}
    /* Kolom kiri: background gradient, full viewport height */
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(1){{
        background:linear-gradient(150deg,#071952 0%,#088395 65%,#37B7C3 100%)!important;
        min-height:100vh!important;
        padding:0!important;
    }}
    /* Kolom kanan: putih, full viewport height, padding untuk konten */
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2){{
        background:#ffffff!important;
        min-height:100vh!important;
        padding:60px 64px!important;
        display:flex!important;
        flex-direction:column!important;
        justify-content:center!important;
    }}
    /* Elemen dalam kolom kanan tidak perlu padding tambahan */
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) .stTextInput,
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) .stButton,
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) p {{
        color:#071952;
    }}
    .lp-inner{{
        padding:56px 52px;
        min-height:100vh;
        display:flex;
        flex-direction:column;
        justify-content:center;
        position:relative;
        overflow:hidden;
        box-sizing:border-box;
    }}
    .lp-inner::before{{
        content:"";position:absolute;top:-100px;right:-100px;
        width:380px;height:380px;border-radius:50%;
        border:70px solid rgba(255,255,255,.06);pointer-events:none;
    }}
    .lp-inner::after{{
        content:"";position:absolute;bottom:-80px;left:-60px;
        width:280px;height:280px;border-radius:50%;
        border:55px solid rgba(255,255,255,.04);pointer-events:none;
    }}
    .lp-h1{{
        color:#fff!important;font-size:2.55rem;font-weight:900;
        line-height:1.22;margin:0 0 18px;
        text-transform:uppercase;text-align:left;
        position:relative;z-index:1;
    }}
    .lp-p{{
        color:rgba(255,255,255,.80);font-size:.98rem;line-height:1.6;
        max-width:340px;margin:0;text-align:left;
        position:relative;z-index:1;
    }}
    .lp-brand{{
        color:rgba(255,255,255,.42);font-size:.75rem;
        margin-top:48px;position:relative;z-index:1;
    }}
    </style>
    """, unsafe_allow_html=True)

    col_l, col_r = st.columns([1, 1], gap="small")

    with col_l:
        st.markdown(f"""
        <div class="lp-inner">
            {logo_img}
            <div class="lp-h1">SISTEM PENGAMBILAN<br>KEPUTUSAN<br>KAPASITAS PRODUKSI</div>
            <p class="lp-p">Platform terintegrasi pendukung keputusan kapasitas produksi PT FBMI.</p>
            <div class="lp-brand">PT FBMI &middot; Lactalis Group &middot; IPB University &middot; 2026</div>
        </div>
        """, unsafe_allow_html=True)

    with col_r:
        st.markdown(
            "<h2 style='font-size:2.2rem;font-weight:900;color:#071952;margin:0 0 8px;"
            "text-transform:uppercase;letter-spacing:.01em;'>SELAMAT DATANG</h2>"
            "<p style='color:#088395;font-size:.92rem;margin-bottom:28px;'>"
            "Masuk untuk mengakses sistem atau daftar akun baru.</p>",
            unsafe_allow_html=True,
        )

        login_tab, reg_tab = st.tabs(["Masuk", "Daftar Akun"])

        with login_tab:
            user = st.text_input("Username", key="l_user", placeholder="Masukkan username")
            pwd  = st.text_input("Password", type="password", key="l_pwd",
                                  placeholder="Masukkan password")
            if st.button("Masuk", type="primary", use_container_width=True, key="btn_login"):
                if not user.strip() or not pwd:
                    st.error("Isi username dan password.")
                else:
                    ok, udata = verify_login(user.strip(), pwd)
                    if ok:
                        clear_session_data()   # ← login baru → bersihkan cache lama
                        token = create_session(user.strip())
                        Path("data").mkdir(exist_ok=True)
                        Path("data/.session_token").write_text(token)
                        set_state("logged_in", True)
                        set_state("user_name", udata.get("name", ""))
                        set_state("_username", user.strip())
                        st.rerun()
                    else:
                        st.error("Username atau password salah.")

        with reg_tab:
            st.caption("Password: min. 8 karakter, mengandung angka dan simbol.")
            r_name = st.text_input("Nama Lengkap", key="r_name", placeholder="Nama lengkap Anda")
            r_user = st.text_input("Username",     key="r_user", placeholder="Pilih username unik")
            r_pwd  = st.text_input("Password",     type="password", key="r_pwd")
            r_pwd2 = st.text_input("Konfirmasi Password", type="password", key="r_pwd2")
            if st.button("Daftar Akun", type="primary", use_container_width=True, key="btn_reg"):
                if not r_name.strip():   st.error("Nama tidak boleh kosong.")
                elif not r_user.strip(): st.error("Username tidak boleh kosong.")
                elif r_pwd != r_pwd2:    st.error("Konfirmasi password tidak cocok.")
                else:
                    valid_pw, pw_msg = _pw_valid(r_pwd)
                    if not valid_pw:
                        st.error(f"Password tidak valid: {pw_msg}")
                    else:
                        ok, msg = register(r_user.strip(), r_pwd, r_name.strip())
                        if ok: st.success("Akun berhasil dibuat. Silakan masuk.")
                        else:  st.error(msg)

# ── MAIN APP ────────────────────────────────────────────────────────────────
if not _auto_login():
    auth_page()
    st.stop()

# Greeting
_uname   = get_state("_username") or ""
_uname_d = get_state("user_name") or ""
_first   = _uname_d.split()[0].title() if _uname_d else _uname
_greeting = "Hi, Admin!" if _uname.lower() == "admin" else f"Hi, {_first}!"

sidebar_brand()
MENUS = ["Analisis Demand", "Simulasi Kapasitas", "Evaluasi Kapasitas",
         "Alokasi Produksi", "Parameter & Katalog Investasi"]
with st.sidebar:
    st.markdown(
        f'<div style="color:#37B7C3;font-size:.86rem;font-weight:600;padding:0 0 10px 2px;">'
        f'{_greeting}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:.68rem;color:#EBF4F6;text-transform:uppercase;'
        'letter-spacing:.08em;opacity:.55;margin-bottom:4px;">Menu</div>',
        unsafe_allow_html=True)
    page = st.radio("", MENUS, label_visibility="collapsed", key="main_nav")

st.html(
    f"<script>"
    f"document.title='Decision Support System — {page}';"
    f"</script>",
    unsafe_allow_javascript=True,
)

_, _tr = st.columns([7, 1])
with _tr:
    _c1, _c2 = st.columns(2)
    with _c1:
        with st.popover("Profil", use_container_width=True):
            st.markdown(f"**{_uname_d or _uname}**")
            st.caption(f"Username: {_uname}")
            st.markdown("---")
            st.markdown("**Ubah Nama**")
            new_name = st.text_input("", value=_uname_d, key="pf_name",
                                      label_visibility="collapsed")
            if st.button("Simpan", key="btn_nm", type="primary"):
                users = load_users()
                if _uname in users:
                    users[_uname]["name"] = new_name.strip()
                    save_users(users)
                    set_state("user_name", new_name.strip())
                    st.success("Nama diperbarui.")
            st.markdown("**Ubah Password**")
            old_pw = st.text_input("Password lama", type="password", key="pf_old")
            new_pw = st.text_input("Password baru", type="password", key="pf_new",
                                    help="Min 8 karakter, angka, dan simbol.")
            if st.button("Simpan password", key="btn_pw"):
                v_ok, v_msg = _pw_valid(new_pw)
                if not v_ok: st.error(v_msg)
                else:
                    lok, _ = verify_login(_uname, old_pw)
                    if not lok: st.error("Password lama salah.")
                    else:
                        users = load_users()
                        users[_uname]["password"] = _hash_pw(new_pw)
                        save_users(users); st.success("Password diperbarui.")
    with _c2:
        if st.button("Keluar", use_container_width=True, key="btn_logout"):
            clear_session_data()   # ← logout → bersihkan cache supaya sesi berikutnya bersih
            Path("data/.session_token").unlink(missing_ok=True)
            set_state("logged_in", False)
            st.rerun()

st.markdown("---")

if page == "Analisis Demand":
    from views.demand_overview import render; render()
elif page == "Simulasi Kapasitas":
    from views.capacity_simulation import render; render()
elif page == "Evaluasi Kapasitas":
    from views.capacity_planning import render; render()
elif page == "Alokasi Produksi":
    from views.production_allocation import render; render()
else:
    from views.investment_catalog import render; render()
