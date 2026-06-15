"""
Unified session — bridge between Asil's get_state/set_state and student's get/set_
"""
import streamlit as st
import pandas as pd
import pickle
from pathlib import Path

CACHE_VERSION = "v20250606"
CACHE_DIR     = Path("data/cache")

DEFAULTS = {
    "logged_in":         False,
    "user_name":         "",
    "forecast_raw":      pd.DataFrame(),
    "forecast_output":   pd.DataFrame(),
    "master_sku":        pd.DataFrame(),
    "forecast_input_des":pd.DataFrame(),
    "simulation_result": pd.DataFrame(),
    "simulation":        pd.DataFrame(),   # student uses this key
    "scenario_config":   pd.DataFrame(),
    "planned_jobs":      pd.DataFrame(),
    "input_data":        pd.DataFrame(),
    "export_bytes":      None,
    "_cap_bytes":        b"",
    "_cap_name":         "",
    "_vol_bytes":        b"",
    "_vol_name":         "",
    "ml4":               [],
}

def init_session():
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── Keys yang di-persist ke disk (survive server restart dalam sesi yang sama)
_DISK_PERSIST_SIM = {"simulation_result", "scenario_config", "planned_jobs", "input_data"}
_NO_DISK_CACHE    = {"simulation", "export_bytes"}


# ──────────────────────────────────────────────────────────────────────────────
# Asil-compatible
# ──────────────────────────────────────────────────────────────────────────────
def get_state(key):
    init_session()
    v = st.session_state.get(key, DEFAULTS.get(key))
    # Baca dari disk HANYA jika session state memang kosong
    # (proteksi: disk cache sudah dibersihkan saat login baru)
    if isinstance(v, pd.DataFrame) and v.empty and key in _DISK_PERSIST_SIM:
        p = CACHE_DIR / f"{key}.pkl"
        if p.exists():
            try:
                with open(p, "rb") as f:
                    payload = pickle.load(f)
                if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION:
                    st.session_state[key] = payload["data"]
                    return payload["data"]
            except Exception:
                p.unlink(missing_ok=True)
    return v

def set_state(key, val):
    init_session()
    st.session_state[key] = val
    # Persist ke disk untuk survive navigasi antar menu di sesi yang sama
    if key in _DISK_PERSIST_SIM:
        _persist_to_disk(key, val)

def _persist_to_disk(key, val):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_DIR / f"{key}.pkl", "wb") as f:
            pickle.dump({"version": CACHE_VERSION, "data": val}, f)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Student-compatible
# ──────────────────────────────────────────────────────────────────────────────
def get(key):
    init_session()
    v = st.session_state.get(key)
    if v is not None:
        if isinstance(v, pd.DataFrame) and not v.empty: return v
        if isinstance(v, pd.DataFrame): pass
        elif isinstance(v, (bytes, bytearray)) and len(v) > 0: return v
        elif isinstance(v, list) and len(v) > 0: return v
        elif v: return v
    if key in _NO_DISK_CACHE:
        return DEFAULTS.get(key, pd.DataFrame())
    p = CACHE_DIR / f"{key}.pkl"
    if p.exists():
        try:
            with open(p, "rb") as f:
                payload = pickle.load(f)
            if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION:
                st.session_state[key] = payload["data"]
                return payload["data"]
        except Exception:
            p.unlink(missing_ok=True)
    return DEFAULTS.get(key, pd.DataFrame())

def set_(key, val):
    init_session()
    st.session_state[key] = val
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_DIR / f"{key}.pkl", "wb") as f:
            pickle.dump({"version": CACHE_VERSION, "data": val}, f)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Cache management
# ──────────────────────────────────────────────────────────────────────────────
def clear_capacity_results():
    """Clear hanya data kapasitas/simulasi dari session."""
    for k in ["simulation_result", "scenario_config", "planned_jobs",
              "input_data", "export_bytes", "simulation"]:
        st.session_state[k] = DEFAULTS.get(k, pd.DataFrame())

def clear_session_data():
    """
    Bersihkan SEMUA data simulasi dari memori dan disk cache.
    Dipanggil saat: login baru (sesi berbeda) atau logout.
    
    Filosofi: data hanya hidup dalam 1 sesi login.
    Kalau relog → data lama wajib hilang supaya sistem tidak
    menampilkan 'hantu' dari sesi sebelumnya.
    """
    # Reset session_state untuk semua key simulasi
    sim_keys = [
        "simulation_result", "scenario_config", "planned_jobs",
        "input_data", "simulation", "export_bytes",
        "forecast_raw", "forecast_output", "forecast_input_des",
        "_cap_bytes", "_cap_name", "_vol_bytes", "_vol_name", "ml4",
    ]
    for k in sim_keys:
        if k in st.session_state:
            st.session_state[k] = DEFAULTS.get(k, pd.DataFrame())
    # Hapus semua file .pkl dari disk cache
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for pkl in CACHE_DIR.glob("*.pkl"):
            pkl.unlink(missing_ok=True)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Upload widget
# ──────────────────────────────────────────────────────────────────────────────
def upload_widget(key: str, label: str, loader_fn, key_suffix: str = "",
                  file_types=None):
    """File uploader with session cache. Returns DataFrame.
    File yang baru diunggah SELALU diproses ulang (mengganti cache lama),
    sehingga pengguna bebas berganti file kapan pun."""
    file_types = file_types or ["csv", "xlsx", "xls", "tsv"]
    cached = get(key)
    has_cache = isinstance(cached, pd.DataFrame) and not cached.empty

    uploaded = st.file_uploader(
        f"Upload {label}", type=file_types,
        key=f"uw_{key}{key_suffix}",
        label_visibility="collapsed")

    if uploaded is not None:
        # Identitas file (nama + ukuran) untuk deteksi pergantian file
        _fid = f"{uploaded.name}:{getattr(uploaded, 'size', 0)}"
        _last = get(f"{key}__fid")
        if _fid != _last:
            # File baru/berbeda → proses ulang, ganti cache lama
            try:
                import io as _io
                raw = uploaded.read()
                df = loader_fn(_io.BytesIO(raw))
                set_(key, df)
                set_(f"{key}__fid", _fid)
                st.success(f"{label} dimuat: {uploaded.name} ({len(df)} baris)")
                return df
            except Exception as e:
                st.error(f"Gagal membaca file: {e}")
                return cached if has_cache else pd.DataFrame()
        else:
            # File yang sama dengan yang sudah diproses
            st.success(f"{label} aktif: {uploaded.name} ({len(cached)} baris)")
            return cached
    else:
        # Tidak ada file di uploader saat ini
        if has_cache:
            st.caption(f"{label} dari sesi sebelumnya masih aktif "
                       f"({len(cached)} baris). Unggah file untuk mengganti.")
        return cached if has_cache else pd.DataFrame()

