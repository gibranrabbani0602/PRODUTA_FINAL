"""
ANALISIS DEMAND & FORECASTING
==========================================================================
LOGIKA YANG BENAR (setelah analisis mendalam):

DATA YANG ADA DI DATABASE:
  · data/forecast/sku_classification.csv  → 60 SKU yang Ubay pilih untuk di-forecast
                                             (aktif + internal production)
                                             berisi: sku, p, CV², model, segment
  · data/forecast/unified_forecast_output.csv → 20 SKU yang SUDAH SELESAI di-forecast
                                                (18 Prophet + 2 CrostonSBA)
  · data/forecast/prophet_forecast_output.csv → 18 SKU Prophet saja

FAKTA PENTING:
  · Raw data (Volume F24-F26) = 79 SKU total FBMI (semua: internal+maklon, aktif+tidak aktif)
  · 60 SKU di sku_classification = subset aktif+internal yang Ubay pilih untuk di-forecast
  · 18-20 SKU di forecast output = yang SUDAH SELESAI Ubay proses
  · Sisa 40 SKU belum diproses (pipeline dalam pengembangan)

ALUR TAB FORECASTING YANG BENAR:
  1. Upload raw historical → tampilkan SUMMARY per SKU (pivot/ringkasan, bukan long format)
     → informasi: jumlah SKU, periode, total volume
     → TIDAK tampilkan klasifikasi pola (itu info terpisah)
  2. Panel info: jika ada sku_classification.csv → tampilkan 1x sebagai "Status Klasifikasi SKU"
     → ini konteks, bukan hasil dari raw upload
  3. Parameter + Jalankan Forecast
  4. Hasil: tampilkan forecast yang ada dengan jelas + metrik akurasi per SKU
     → kalau pipeline belum jalan → load existing output + note status
  5. Export CSV → untuk tab Visualisasi Demand
  6. Visualisasi langsung
==========================================================================
"""

import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

from modules.session import get_state, set_state
from modules.io_utils import read_table
from modules.raw_volume_parser import parse_raw_volume

# ─── Palet ───────────────────────────────────────────────────────────────────
PKG_COLORS   = {"SSS": "#37B7C3", "BIB": "#071952", "STICKPACK": "#088395"}
SEG_COLORS   = {"SMOOTH":"#088395","ERRATIC":"#37B7C3","INTERMITTENT":"#e6a817","LUMPY":"#c0392b"}
MODEL_COLORS = {"Prophet":"#088395","CrostonSBA":"#071952","Croston":"#37B7C3"}
CHART_BG     = "#FFFFFF"
FONT_COLOR   = "#071952"

MASTER_SKU_PATH  = Path("data/masters/master_sku.csv")
MASTER_DATA_PATH = Path("data/masters/master_data.xlsx")

# Nama kolom user-friendly (berlaku global)
COL_LABELS = {
    "date":"Tanggal","ds":"Tanggal","sku":"Kode SKU","description":"Deskripsi Produk",
    "y":"Volume (ton)","forecast":"Forecast (ton)",
    "forecast_lower":"Batas Bawah (ton)","forecast_upper":"Batas Atas (ton)",
    "mape_backtest":"MAPE (%)","wmape_backtest":"WMAPE (%)","bias_pct":"Bias (%)",
    "demand_pattern":"Pola Permintaan","model_used":"Model","model":"Model",
    "holiday_adjusted":"Koreksi Lebaran","segment":"Pola Permintaan",
    "p":"Interval Rata-rata (p)","CV Squared":"CV² (Variasi)","category":"Brand",
    "_pkg":"Jenis Kemasan","SkuId":"Kode SKU","ItemName":"Nama Produk",
    "port_type":"Tipe Lini","Speed":"Kecepatan (ton/jam)","SpeedD":"Kecepatan Bersih",
    "ForecastTon":"Forecast (ton)","MonthIndex":"Periode",
}

def _rl(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns ke label user-friendly."""
    return df.rename(columns={c: COL_LABELS.get(c, c.replace("_"," ").title()) for c in df.columns})


# ─── Loaders ─────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def _load_pkg_map() -> dict:
    if not MASTER_SKU_PATH.exists():
        return {}
    try:
        df = pd.read_csv(MASTER_SKU_PATH, encoding="latin-1", on_bad_lines="skip")
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={df.columns[0]: "sku"})
        df["sku"] = df["sku"].astype(str).str.strip()
        result = {}
        for _, row in df.iterrows():
            pt = str(row.get("PORT TYPE","")).strip().upper()
            if "STICK" in pt: result[row["sku"]] = "STICKPACK"
            elif "SSS" in pt: result[row["sku"]] = "SSS"
            else:             result[row["sku"]] = "BIB"
        return result
    except Exception:
        return {}

@st.cache_data(show_spinner=False)
def _load_master_data_db() -> pd.DataFrame | None:
    if not MASTER_DATA_PATH.exists():
        return None
    try:
        return pd.read_excel(MASTER_DATA_PATH)
    except Exception:
        return None

@st.cache_data(show_spinner=False)
def _load_sku_class() -> pd.DataFrame | None:
    p = Path("data/forecast/sku_classification.csv")
    return pd.read_csv(p) if p.exists() else None

@st.cache_data(show_spinner=False)
def _load_backtest_results() -> pd.DataFrame | None:
    p = Path("data/forecast/backtest_results.csv")
    return pd.read_csv(p) if p.exists() else None

@st.cache_data(show_spinner=False)
def _load_skipped_skus() -> pd.DataFrame | None:
    p = Path("data/forecast/skipped_skus.csv")
    return pd.read_csv(p) if p.exists() else None

def _load_existing_forecast() -> pd.DataFrame | None:
    """Load forecast output terbaik yang tersedia (unified > prophet)."""
    for fname in ["unified_forecast_output.csv","prophet_forecast_output.csv"]:
        p = Path(f"data/forecast/{fname}")
        if p.exists():
            try:
                df = pd.read_csv(p)
                if not df.empty:
                    return df
            except Exception:
                pass
    return None


# ─── Utility ─────────────────────────────────────────────────────────────────
def _clean_fc(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {}
    for c in df.columns:
        cl = c.strip().lower()
        if cl in ("date","ds"):              col_map[c] = "date"
        elif cl == "forecast":               col_map[c] = "forecast"
        elif cl == "forecast_lower":         col_map[c] = "forecast_lower"
        elif cl == "forecast_upper":         col_map[c] = "forecast_upper"
        elif cl in ("sku","skuid"):          col_map[c] = "sku"
        elif cl in ("description","descriptionforecast",
                    "item_name","itemname"): col_map[c] = "description"
        elif cl == "mape_backtest":          col_map[c] = "mape_backtest"
        elif cl == "wmape_backtest":         col_map[c] = "wmape_backtest"
        elif cl == "bias_pct":               col_map[c] = "bias_pct"
        elif cl == "demand_pattern":         col_map[c] = "demand_pattern"
        elif cl == "model_used":             col_map[c] = "model_used"
        elif cl == "holiday_adjusted":       col_map[c] = "holiday_adjusted"
    df = df.rename(columns=col_map)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "forecast" in df.columns:
        df["forecast"] = pd.to_numeric(df["forecast"], errors="coerce").fillna(0).clip(lower=0)
    return df

def _filter_active(df: pd.DataFrame) -> pd.DataFrame:
    if "sku" in df.columns and "forecast" in df.columns:
        tot = df.groupby("sku")["forecast"].sum()
        df  = df[df["sku"].isin(tot[tot > 0].index)].copy()
    return df

def _add_pkg(df: pd.DataFrame, pkg_map: dict) -> pd.DataFrame:
    df = df.copy()
    df["_pkg"] = df["sku"].apply(lambda x: pkg_map.get(str(x).strip(),"BIB")) if "sku" in df.columns else "BIB"
    return df

def _chart_kw(**kw) -> dict:
    base = dict(
        plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG, font_color=FONT_COLOR,
        margin=dict(l=10,r=10,t=36,b=10),
        legend=dict(orientation="h",y=-0.24,font=dict(size=11)),
        font=dict(family="Inter, sans-serif"),
    )
    base.update(kw)
    return base

def _kpi(col, lbl, val, note=""):
    col.markdown(
        f'<div class="kpi-box"><div class="kpi-label">{lbl}</div>'
        f'<div class="kpi-value">{val}</div>'
        f'{"<div style=font-size:.7rem;color:#088395;margin-top:4px;>"+note+"</div>" if note else ""}'
        f'</div>', unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def render():
    st.markdown('<div class="page-title">ANALISIS DEMAND & FORECASTING</div>', unsafe_allow_html=True)
    st.markdown(
        '<p style="color:#088395;font-size:.88rem;margin:-12px 0 18px 0;">'
        "Forecasting permintaan, konversi ke input simulasi DES, dan visualisasi kapasitas.</p>",
        unsafe_allow_html=True,
    )
    tab1, tab2, tab3 = st.tabs(["FORECASTING", "INPUT SIMULASI DES", "VISUALISASI DEMAND"])
    with tab1: _tab_forecasting()
    with tab2: _tab_des_input()
    with tab3: _tab_visualisasi()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — FORECASTING
# ══════════════════════════════════════════════════════════════════════════════
def _tab_forecasting():
    pkg_map = _load_pkg_map()

    # ── A: Upload Data Historis ──────────────────────────────────────────
    st.markdown("<div class='section-title'>DATA HISTORIS</div>", unsafe_allow_html=True)
    st.caption("Upload data historis volume permintaan bulanan.")

    raw_file = st.file_uploader(
        "Upload Data Historis", type=["csv","xlsx","xls","tsv"],
        key="raw_hist_upload", label_visibility="collapsed",
    )

    if raw_file:
        try:
            try:
                raw_df = parse_raw_volume(raw_file)
            except Exception:
                raw_df = read_table(raw_file)
            set_state("forecast_raw", raw_df)

            # Detect kolom
            ds_col = next((c for c in ["ds","date"] if c in raw_df.columns), None)
            y_col  = next((c for c in ["y","volume","forecast"] if c in raw_df.columns), None)

            if ds_col and y_col and "sku" in raw_df.columns:
                raw_df[ds_col] = pd.to_datetime(raw_df[ds_col], errors="coerce")
                raw_df[y_col]  = pd.to_numeric(raw_df[y_col], errors="coerce").fillna(0)

                n_sku   = raw_df["sku"].nunique()
                n_per   = raw_df[ds_col].dropna().nunique()
                tot_vol = raw_df[y_col].sum()
                d_min   = raw_df[ds_col].min().strftime("%b %Y")
                d_max   = raw_df[ds_col].max().strftime("%b %Y")

                st.success(
                    f"Data historis dimuat: **{n_sku} SKU**, **{n_per} periode** "
                    f"({d_min} – {d_max}), total volume: **{tot_vol:,.0f} ton**."
                )

                # Preview: PIVOT / SUMMARY per SKU — jauh lebih berguna dari long format
                with st.expander("Ringkasan per SKU"):
                    summary = (
                        raw_df[raw_df[y_col] > 0]
                        .groupby(["sku","description"] if "description" in raw_df.columns else ["sku"])
                        .agg(
                            **{
                                "Total Volume (ton)": (y_col, "sum"),
                                "Rata-rata/Bulan (ton)": (y_col, "mean"),
                                "Bulan Aktif": (y_col, lambda x: (x > 0).sum()),
                            }
                        )
                        .reset_index()
                        .sort_values("Total Volume (ton)", ascending=False)
                    )
                    summary["Total Volume (ton)"]    = summary["Total Volume (ton)"].round(2)
                    summary["Rata-rata/Bulan (ton)"] = summary["Rata-rata/Bulan (ton)"].round(2)
                    summary.columns = [COL_LABELS.get(c,c) for c in summary.columns]
                    st.dataframe(summary, use_container_width=True, hide_index=True)
            else:
                st.success(f"Data dimuat: {len(raw_df):,} baris.")
                with st.expander("Preview"):
                    st.dataframe(_rl(raw_df.head(30)), use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Gagal membaca file: {e}")

    raw_df  = get_state("forecast_raw")
    has_raw = isinstance(raw_df, pd.DataFrame) and not raw_df.empty

    if not has_raw:
        st.markdown(
            '<div class="note-box">Upload data historis untuk mengaktifkan pipeline forecast.</div>',
            unsafe_allow_html=True,
        )

    # ── B: Info tentang cutoff & SKU scope ──────────────────────────────────
    from modules.forecast_engine import CUTOFF_DATE, SKU_CLASS_PATH
    sc_df = _load_sku_class()
    if has_raw and sc_df is not None:
        n_class = len(sc_df)
        seg_ct  = sc_df["segment"].value_counts() if "segment" in sc_df.columns else {}
        st.markdown(
            f'<div class="note-box">'
            f'Metode forecast disesuaikan dengan pola permintaan SKU. '
            f'SKU berpola smooth dan erratic diproses menggunakan Prophet, '
            f'sedangkan SKU berpola intermittent dan lumpy diproses menggunakan CrostonSBA. '
            f'Cutoff data aktual: <b>{CUTOFF_DATE.strftime("%b %Y")}</b>. '
            f'Periode forecast: <b>{CUTOFF_DATE.strftime("%b %Y")} – '
            f'{(CUTOFF_DATE + pd.DateOffset(months=12)).strftime("%b %Y")}</b>.'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── C: Parameter & Run ───────────────────────────────────────────────
    st.markdown("<div class='section-title'>PARAMETER FORECAST</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1: method  = st.selectbox("Metode", ["Auto (Prophet + CrostonSBA)","Prophet","CrostonSBA"], key="fc_meth")
    with c2: horizon = st.number_input("Horizon (bulan)", 3, 36, 12, 1, key="fc_hor")

    # Status library
    from modules.forecast_engine import get_prophet_status
    lib_status = get_prophet_status()
    if not lib_status["prophet"] and has_raw:
        if lib_status["holtwinters"]:
            st.markdown(
                '<div class="warn-box">'
                '<b>Prophet tidak tersedia.</b> SKU SMOOTH/ERRATIC akan menggunakan '
                '<b>Holt-Winters</b> (trend + seasonality, tidak flat). '
                'Untuk akurasi optimal sesuai Ubay, install Prophet: '
                '<code>pip install prophet</code></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="warn-box">'
                '<b>Perhatian:</b> Library forecasting (statsmodels/Prophet) tidak terinstall '
                'di environment ini. Kemungkinan Python 3.14 yang digunakan belum kompatibel. '
                'Semua SKU sementara menggunakan CrostonSBA. '
                'Solusi: gunakan Python 3.11/3.12 dan jalankan '
                '<code>pip install statsmodels prophet</code></div>',
                unsafe_allow_html=True,
            )
    elif lib_status["prophet"] and has_raw:
        st.markdown(
            '<div class="note-box" style="border-left-color:#1a7f4b;">'
            'Prophet tersedia. SMOOTH/ERRATIC → Prophet, INTERMITTENT/LUMPY → CrostonSBA.'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Model tersimpan (bundle) — opsional, dengan fallback otomatis ────────
    _use_bundle = False
    try:
        from modules.forecast_bundle import bundle_status
        _bs = bundle_status()
    except Exception:
        _bs = {"available": False, "error": "Modul bundle tidak tersedia."}
    if _bs.get("available"):
        _saved = str(_bs.get("saved_at", ""))[:10]
        _cut   = str(_bs.get("cutoff", ""))[:10]
        st.markdown(
            f'<div class="note-box" style="border-left-color:#088395;">'
            f'<b>Model tersimpan tersedia.</b> {_bs["n_prophet"]} model Prophet + '
            f'{_bs["n_croston"]} model CrostonSBA (cutoff {_cut}, disimpan {_saved}). '
            f'Bila diaktifkan, SKU yang sudah terlatih memakai model ini (lebih cepat); '
            f'SKU baru atau data yang lebih baru otomatis dilatih ulang.</div>',
            unsafe_allow_html=True)
        _use_bundle = st.toggle(
            "Gunakan model tersimpan bila tersedia (disarankan)",
            value=True, key="fc_use_bundle",
            help="Aktif: SKU dalam bundle dipakai langsung (cepat, identik hasil "
                 "training awal). SKU di luar bundle / data lebih baru tetap "
                 "dilatih langsung. Nonaktif: semua SKU dilatih langsung.")
    elif _bs.get("error"):
        # Saat bundle tidak dapat dimuat, sistem otomatis memakai pelatihan
        # langsung. Pesan teknis tidak relevan bagi pengguna — cukup
        # informasikan secara ringkas bahwa proses tetap berjalan normal.
        st.caption("Forecast menggunakan pelatihan langsung pada data yang diunggah.")

    btn_disabled = not has_raw
    if st.button("Jalankan Forecast", type="primary", disabled=btn_disabled, key="btn_run_fc"):
        try:
            from modules.forecast_engine import run_forecast
            with st.spinner("Memproses pipeline forecast..."):
                fc_result = run_forecast(raw_df, horizon_months=horizon, method=method,
                                         use_bundle=_use_bundle)
            fc_result = _clean_fc(fc_result)
            set_state("forecast_output", fc_result)
            set_state("fc_is_existing", False)   # ini hasil run beneran
            st.success(f"Forecast selesai: {fc_result['sku'].nunique()} SKU.")
        except NotImplementedError:
            # Pipeline belum ada → load existing output
            existing = _load_existing_forecast()
            if existing is not None:
                existing = _clean_fc(existing)
                set_state("forecast_output", existing)
                set_state("fc_is_existing", True)
            else:
                st.info("Pipeline forecast sedang dalam pengembangan.")
        except Exception as e:
            st.error(f"Error: {e}")

    # ── D: Tampilkan hasil forecast ──────────────────────────────────────
    fc_df  = get_state("forecast_output")
    has_fc = isinstance(fc_df, pd.DataFrame) and not fc_df.empty

    if not has_fc:
        if not btn_disabled:
            st.markdown(
                '<div class="note-box">Klik "Jalankan Forecast" untuk memproses data.</div>',
                unsafe_allow_html=True,
            )
        return

    fc_df     = _clean_fc(fc_df)
    fc_df     = _filter_active(fc_df)
    is_exist  = get_state("fc_is_existing")

    st.markdown("<div class='section-title'>HASIL FORECAST</div>", unsafe_allow_html=True)

    # Penjelasan jujur tentang status output
    if is_exist:
        n_done  = fc_df["sku"].nunique() if "sku" in fc_df.columns else "?"
        n_total = len(sc_df) if sc_df is not None else 60
        st.markdown(
            f'<div class="note-box">'
            f'Output forecast yang tersedia saat ini: <b>{n_done} dari {n_total} SKU</b> '
            f'telah selesai diproses. Pipeline untuk SKU yang tersisa sedang dalam pengembangan.'
            f'</div>', unsafe_allow_html=True,
        )

    # KPI ringkas
    has_mape  = "mape_backtest"  in fc_df.columns
    has_wmape = "wmape_backtest" in fc_df.columns
    has_model = "model_used"     in fc_df.columns
    has_patt  = "demand_pattern" in fc_df.columns
    n_sku     = fc_df["sku"].nunique() if "sku" in fc_df.columns else "-"
    n_mon     = fc_df["date"].dropna().nunique() if "date" in fc_df.columns else "-"

    avg_acc = None
    acc_lbl = "AKURASI (MAPE)"
    # Tentukan metrik: Prophet/HW → MAPE, majority Croston → WMAPE
    if has_model and "sku" in fc_df.columns:
        mc = fc_df.drop_duplicates("sku")["model_used"].value_counts()
        n_croston = mc.get("CrostonSBA", 0)
        n_phw     = mc.get("Prophet", 0) + mc.get("Holt-Winters", 0)
        if n_croston > n_phw:
            acc_lbl = "AKURASI (WMAPE)"
            if has_wmape:
                avg_acc = fc_df.groupby("sku")["wmape_backtest"].first().dropna().mean()
        else:
            acc_lbl = "AKURASI (MAPE)"
            if has_mape:
                avg_acc = fc_df.groupby("sku")["mape_backtest"].first().dropna().mean()
    elif has_mape and "sku" in fc_df.columns:
        avg_acc = fc_df.groupby("sku")["mape_backtest"].first().dropna().mean()

    k1, k2, k3, k4 = st.columns(4)
    _kpi(k1, "SKU SELESAI",   str(n_sku))
    _kpi(k2, "HORIZON",       f"{n_mon} bulan")
    _kpi(k3, acc_lbl,         f"{avg_acc:.1f}%" if avg_acc is not None else "N/A")
    if has_model and "sku" in fc_df.columns:
        mct = fc_df.drop_duplicates("sku")["model_used"].value_counts()
        _kpi(k4, "MODEL", " | ".join(f"{m}: {n}" for m, n in mct.items()))
    else:
        _kpi(k4, "MODEL", "Prophet + CrostonSBA")

    # Tabel per SKU — filter
    if "sku" in fc_df.columns:
        all_skus = sorted(fc_df["sku"].dropna().unique().tolist())
        sel      = st.multiselect("Filter Kode SKU", all_skus, default=all_skus[:5], key="fc_filter")
        tbl      = fc_df[fc_df["sku"].isin(sel)].copy() if sel else fc_df.copy()
    else:
        tbl = fc_df.copy()

    if "date" in tbl.columns:
        tbl["date"] = tbl["date"].dt.strftime("%Y-%m-%d")
    for col in ["forecast","forecast_lower","forecast_upper","mape_backtest","wmape_backtest","bias_pct"]:
        if col in tbl.columns:
            tbl[col] = pd.to_numeric(tbl[col], errors="coerce").round(4)
    drop_cols = [c for c in ["_pkg"] if c in tbl.columns]
    st.dataframe(_rl(tbl.drop(columns=drop_cols, errors="ignore")), use_container_width=True, hide_index=True)

    # Tabel akurasi per SKU
    if (has_mape or has_wmape) and "sku" in fc_df.columns:
        with st.expander("Akurasi Model per SKU"):
            acc_cols = ["sku"] + \
                (["description"]    if "description"    in fc_df.columns else []) + \
                (["demand_pattern"] if has_patt          else []) + \
                (["model_used"]     if has_model         else []) + \
                (["wmape_backtest"] if has_wmape         else []) + \
                (["mape_backtest"]  if has_mape          else []) + \
                (["bias_pct"]       if "bias_pct" in fc_df.columns else [])
            acc = fc_df.drop_duplicates("sku")[acc_cols].sort_values(
                "wmape_backtest" if has_wmape else "mape_backtest", na_position="last"
            )
            for col in ["mape_backtest","wmape_backtest","bias_pct"]:
                if col in acc.columns:
                    acc[col] = acc[col].round(2)
            st.dataframe(_rl(acc), use_container_width=True, hide_index=True)

    # Distribusi model (jika ada kolom model_used & demand_pattern)
    if has_model and has_patt and "sku" in fc_df.columns:
        with st.expander("Distribusi Model & Pola Permintaan", expanded=False):
            mc1, mc2 = st.columns(2)
            with mc1:
                seg_ct = fc_df.drop_duplicates("sku")["demand_pattern"].value_counts().reset_index()
                seg_ct.columns = ["Pola","Jumlah SKU"]
                fig_s = px.bar(seg_ct, x="Pola", y="Jumlah SKU", color="Pola",
                               color_discrete_map=SEG_COLORS, height=240)
                fig_s.update_layout(**_chart_kw(showlegend=False))
                st.plotly_chart(fig_s, use_container_width=True, key="seg_bar_t1")
            with mc2:
                mod_ct = fc_df.drop_duplicates("sku")["model_used"].value_counts().reset_index()
                mod_ct.columns = ["Model","Jumlah SKU"]
                fig_m = px.pie(mod_ct, names="Model", values="Jumlah SKU",
                               color="Model", color_discrete_map=MODEL_COLORS, height=240, hole=0.4)
                fig_m.update_layout(**_chart_kw(showlegend=True))
                st.plotly_chart(fig_m, use_container_width=True, key="mod_pie_t1")

    # Export CSV
    st.markdown("<div class='section-title'>EXPORT FORECAST</div>", unsafe_allow_html=True)
    st.caption("Download untuk digunakan di tab VISUALISASI DEMAND.")
    exp = fc_df.copy()
    if "date" in exp.columns:
        exp["date"] = exp["date"].dt.strftime("%Y-%m-%d")
    exp = exp.drop(columns=[c for c in ["_pkg"] if c in exp.columns], errors="ignore")
    st.download_button(
        "Unduh Forecast CSV", data=exp.to_csv(index=False).encode(),
        file_name="forecast_output.csv", mime="text/csv", key="dl_fc",
    )

    # Visualisasi langsung
    st.markdown("<div class='section-title'>VISUALISASI DEMAND</div>", unsafe_allow_html=True)
    _render_charts(_add_pkg(fc_df, pkg_map), ctx="t1")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — INPUT SIMULASI DES
# ══════════════════════════════════════════════════════════════════════════════
def _tab_des_input():
    st.markdown("<div class='section-title'>OUTPUT FORECAST</div>", unsafe_allow_html=True)
    fc_df  = get_state("forecast_output")
    has_fc = isinstance(fc_df, pd.DataFrame) and not fc_df.empty

    if has_fc:
        fc_df = _clean_fc(_filter_active(fc_df))
        n_sku = fc_df["sku"].nunique() if "sku" in fc_df.columns else "?"
        st.success(f"Forecast tersedia — {n_sku} SKU aktif.")
    else:
        st.caption("Belum ada forecast. Upload langsung di sini:")
        fc_up = st.file_uploader("Upload Forecast", type=["csv","xlsx"], key="fc_des_bridge",
                                  label_visibility="collapsed")
        if fc_up:
            try:
                df_tmp = _clean_fc(_filter_active(read_table(fc_up)))
                set_state("forecast_output", df_tmp)
                fc_df = df_tmp; has_fc = True
                st.success(f"Forecast dimuat: {len(df_tmp):,} baris.")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal: {e}")
    if not has_fc:
        return

    # Master Data Asil
    st.markdown("<div class='section-title'>MASTER DATA SKU</div>", unsafe_allow_html=True)
    ma_sess = get_state("master_data_asil")
    if isinstance(ma_sess, pd.DataFrame) and not ma_sess.empty:
        master_asil = ma_sess; src = "upload sebelumnya"
    else:
        master_asil = _load_master_data_db(); src = "database"
        if master_asil is not None:
            set_state("master_data_asil", master_asil)

    if master_asil is not None:
        st.markdown(
            f'<div class="note-box">Master Data aktif: <b>{len(master_asil)}</b> SKU ({src}).</div>',
            unsafe_allow_html=True,
        )
        with st.expander("Ganti atau muat ulang Master Data", expanded=False):
            opt = st.radio("Sumber", ["Database","Upload Manual"], horizontal=True, key="des_opt")
            if opt == "Upload Manual":
                mf = st.file_uploader("Upload Master Data", type=["xlsx","xls","csv"],
                                      key="ma_up", label_visibility="collapsed")
                if mf:
                    try:
                        ma_new = read_table(mf)
                        set_state("master_data_asil", ma_new)
                        master_asil = ma_new
                        _load_master_data_db.clear()
                        st.success(f"Diperbarui: {len(ma_new)} SKU.")
                    except Exception as e:
                        st.error(f"Gagal: {e}")
            else:
                if st.button("Muat ulang", key="des_reload"):
                    _load_master_data_db.clear()
                    db = _load_master_data_db()
                    if db is not None:
                        set_state("master_data_asil", db)
                        master_asil = db
                        st.success("Dimuat dari database.")
            sc = [c for c in ["SkuId","ItemName","port_type","Speed","SpeedD"] if c in master_asil.columns]
            if sc:
                st.dataframe(_rl(master_asil[sc].head(12)), use_container_width=True, hide_index=True)
    else:
        st.warning("Master Data tidak tersedia. Upload di bawah.")
        mf = st.file_uploader("Upload Master Data", type=["xlsx","xls","csv"],
                              key="ma_up_req", label_visibility="collapsed")
        if mf:
            try:
                master_asil = read_table(mf)
                set_state("master_data_asil", master_asil)
                st.success(f"Dimuat: {len(master_asil)} SKU.")
            except Exception as e:
                st.error(f"Gagal: {e}")
    if master_asil is None:
        return

    st.markdown("<div class='section-title'>PARAMETER</div>", unsafe_allow_html=True)
    p1, p2 = st.columns(2)
    with p1: adj = st.slider("Penyesuaian Forecast (%)", -30.0, 30.0, 0.0, 0.5, key="des_adj")
    with p2: qty = st.number_input("Qty minimum per SKU-bulan", 1, 100, 1, key="des_qty")

    if st.button("Generate Input Simulasi DES", type="primary", key="btn_des"):
        try:
            with st.spinner("Membuat input simulasi..."):
                result = _build_des(fc_df, master_asil, adj, qty)
            set_state("forecast_input_des", result)
            st.success(f"Input simulasi berhasil: {len(result):,} baris.")
            m1,m2,m3,m4 = st.columns(4)
            nm = result["MonthIndex"].nunique() if "MonthIndex" in result.columns else 1
            _kpi(m1,"JUMLAH SKU",result["SkuId"].nunique() if "SkuId" in result.columns else "?")
            _kpi(m2,"JUMLAH BULAN",nm)
            if "ForecastTon" in result.columns:
                _kpi(m3,"TOTAL FORECAST",f"{result['ForecastTon'].sum():,.1f} ton")
                _kpi(m4,"RATA-RATA/BULAN",f"{result['ForecastTon'].sum()/nm:,.1f} ton")
            st.dataframe(_rl(result.head(30)), use_container_width=True, hide_index=True)
            st.download_button("Unduh ForecastInput DES",
                data=result.to_csv(index=False).encode(),
                file_name="ForecastInput_DES.csv", mime="text/csv", key="dl_des")
        except Exception as e:
            import traceback
            st.error(f"Error: {e}")
            with st.expander("Detail"):
                st.code(traceback.format_exc())


def _build_des(fc_df, master, adj_pct, qty_def):
    ma = master.copy()
    cm = {}
    for c in ma.columns:
        cs = c.strip()
        if cs in ["SkuId","sku","SKU","ItemCode"]: cm[c] = "SkuId"
        elif cs == "ItemName": cm[c] = "ItemName"
    ma = ma.rename(columns=cm)
    if "SkuId" not in ma.columns:
        raise ValueError("Master Data tidak memiliki kolom SkuId.")
    fc = fc_df.copy()
    fc["forecast"] = (fc["forecast"] * (1 + adj_pct/100)).clip(lower=0)
    merged = fc.merge(ma, left_on="sku", right_on="SkuId", how="inner")
    if merged.empty:
        raise ValueError("Tidak ada SKU yang cocok. Cek kolom SkuId di Master Data.")
    rows = []
    for _, row in merged.iterrows():
        ton = float(row.get("forecast",0))
        rows.append({
            "ItemName":row.get("ItemName",row.get("description","")),
            "Qty":max(int(round(ton)),qty_def) if ton>0 else qty_def,
            "SkuId":row.get("SkuId",row["sku"]),
            "ForecastTon":round(ton,6),
            "SkuGr":row.get("SkuGr",""), "SpeedD":row.get("SpeedD",0),
            "Speed":row.get("Speed",0), "IsChocolate":row.get("IsChocolate",""),
            "port_type":row.get("port_type",""), "Allergen":row.get("Allergen",""),
            "ShelfLife":row.get("ShelfLife",""),
            "MonthIndex":str(row.get("date",""))[:10],
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — VISUALISASI DEMAND
# ══════════════════════════════════════════════════════════════════════════════
def _tab_visualisasi():
    pkg_map = _load_pkg_map()
    has_db  = MASTER_SKU_PATH.exists()

    with st.expander(
        "Master SKU — " + ("aktif dari database" if has_db else "belum tersedia"),
        expanded=not has_db,
    ):
        if has_db:
            try:
                msku = pd.read_csv(MASTER_SKU_PATH, encoding="latin-1", on_bad_lines="skip")
                msku.columns = [c.strip() for c in msku.columns]
                msku = msku.rename(columns={msku.columns[0]: "Kode SKU"})
                sc = [c for c in ["Kode SKU","SIZE (g)","PORT TYPE"] if c in msku.columns]
                st.markdown(f"**{len(msku)} SKU tersedia.**")
                st.dataframe(msku[sc].head(12), use_container_width=True, hide_index=True)
            except Exception:
                pass
        opt = st.radio("Sumber Master SKU", ["Database","Upload Manual"],
                       horizontal=True, key="viz_msku_opt")
        if opt == "Upload Manual":
            mf = st.file_uploader("Upload master_sku.csv", type=["csv","xlsx"],
                                  key="msku_viz", label_visibility="collapsed")
            if mf:
                try:
                    df_new = read_table(mf)
                    Path("data/masters").mkdir(parents=True, exist_ok=True)
                    df_new.to_csv(MASTER_SKU_PATH, index=False)
                    _load_pkg_map.clear(); st.success("Diperbarui."); st.rerun()
                except Exception as e:
                    st.error(f"Gagal: {e}")

    pkg_map = _load_pkg_map()
    st.markdown("<div class='section-title'>DATA FORECAST</div>", unsafe_allow_html=True)
    st.caption("Upload file forecast dari export tab FORECASTING, atau gunakan data sesi ini.")

    fc_sess = get_state("forecast_output")
    has_s   = isinstance(fc_sess, pd.DataFrame) and not fc_sess.empty
    viz_df  = None

    if has_s:
        if st.checkbox("Gunakan data dari tab FORECASTING", value=True, key="viz_sess"):
            viz_df = fc_sess.copy()

    if viz_df is None:
        vf = st.file_uploader("Upload CSV Forecast", type=["csv","xlsx"],
                               key="viz_up", label_visibility="collapsed")
        if vf:
            try:
                viz_df = read_table(vf)
                set_state("viz_cache", viz_df)
            except Exception as e:
                st.error(f"Gagal: {e}")
        else:
            c = get_state("viz_cache")
            if isinstance(c, pd.DataFrame) and not c.empty:
                viz_df = c

    if viz_df is None:
        st.markdown(
            '<div class="note-box">Upload file forecast untuk menampilkan visualisasi.</div>',
            unsafe_allow_html=True,
        )
        return

    viz_df = _clean_fc(viz_df)
    viz_df = _filter_active(viz_df)
    viz_df = _add_pkg(viz_df, pkg_map)
    _render_charts(viz_df, ctx="t3")


# ══════════════════════════════════════════════════════════════════════════════
# SHARED CHARTS (ctx unik agar tidak duplikat key)
# ══════════════════════════════════════════════════════════════════════════════
def _render_charts(df: pd.DataFrame, ctx: str = "x"):
    has_date = "date" in df.columns
    has_val  = "forecast" in df.columns
    has_sku  = "sku" in df.columns
    has_desc = "description" in df.columns
    label_col = "description" if has_desc else ("sku" if has_sku else None)

    if not has_val or label_col is None:
        st.dataframe(df, use_container_width=True, hide_index=True)
        return

    has_mape  = "mape_backtest"  in df.columns
    has_wmape = "wmape_backtest" in df.columns

    # KPI
    st.markdown("<div class='section-title'>RINGKASAN</div>", unsafe_allow_html=True)
    n_sku   = df["sku"].nunique() if has_sku else "-"
    n_month = int(df["date"].dropna().nunique()) if has_date else 1
    avg_m   = df["forecast"].sum() / max(n_month,1)
    period  = ""
    if has_date:
        mn,mx = df["date"].dropna().min(), df["date"].dropna().max()
        if pd.notna(mn) and pd.notna(mx):
            period = f"{mn.strftime('%b %Y')} – {mx.strftime('%b %Y')}"

    acc,acc_l = None,"AKURASI"
    if has_wmape and has_sku:
        acc = df.groupby("sku")["wmape_backtest"].first().dropna().mean(); acc_l="WMAPE RATA-RATA"
    elif has_mape and has_sku:
        acc = df.groupby("sku")["mape_backtest"].first().dropna().mean(); acc_l="MAPE RATA-RATA"

    k1,k2,k3,k4 = st.columns(4)
    _kpi(k1,"SKU AKTIF",str(n_sku))
    _kpi(k2,"VOLUME/BULAN",f"{avg_m:,.1f} ton")
    _kpi(k3,"PERIODE",period or "-")
    _kpi(k4,acc_l,f"{acc:.1f}%" if acc is not None else "N/A")

    if acc and acc > 30:
        st.markdown(
            '<div class="warn-box">Rata-rata akurasi rendah — beberapa SKU perlu verifikasi manual.</div>',
            unsafe_allow_html=True,
        )

    # Bar + Donut
    st.markdown(
        "<div class='section-title'>VOLUME PER SKU & PROPORSI KEMASAN</div>",
        unsafe_allow_html=True,
    )
    ch1, ch2 = st.columns([3,2])
    if has_date:
        months = sorted(df["date"].dropna().unique())
        sel_m  = st.selectbox("Tampilkan untuk:", months,
                              format_func=lambda x: pd.Timestamp(x).strftime("%b %Y"),
                              key=f"mon_{ctx}")
        m_df    = df[df["date"]==sel_m].sort_values("forecast").tail(20)
        pie_src = df[df["date"]==sel_m]
        bl      = pd.Timestamp(sel_m).strftime("%b %Y")
    else:
        m_df    = df.groupby([label_col,"_pkg"])["forecast"].sum().reset_index().sort_values("forecast").tail(20)
        pie_src = df; bl = "Semua"

    with ch1:
        fig_b = px.bar(m_df, y=label_col, x="forecast", color="_pkg", orientation="h",
                       color_discrete_map=PKG_COLORS, height=460,
                       labels={"forecast":"Volume (ton)","_pkg":"Jenis Kemasan"})
        fig_b.update_layout(**_chart_kw(yaxis_title="",xaxis_title=f"Volume ton ({bl})"))
        st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{ctx}")

    with ch2:
        ps = pie_src.groupby("_pkg")["forecast"].sum().reset_index()
        ps.columns = ["Kemasan","Volume"]
        ps = ps[ps["Volume"]>0]
        fig_p = go.Figure(data=[go.Pie(
            labels=ps["Kemasan"], values=ps["Volume"], hole=0.45,
            marker_colors=[PKG_COLORS.get(k,"#EBF4F6") for k in ps["Kemasan"]],
            textinfo="percent+label", textfont_size=12,
        )])
        fig_p.update_layout(plot_bgcolor=CHART_BG, paper_bgcolor=CHART_BG, font_color=FONT_COLOR,
                            showlegend=False, margin=dict(l=10,r=10,t=36,b=10), height=460,
                            title=dict(text=f"Proporsi Kemasan — {bl}", x=0.5, font=dict(size=13)))
        st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{ctx}")

    # Top 10
    st.markdown("<div class='section-title'>TOP 10 PRODUK — RATA-RATA PER BULAN</div>", unsafe_allow_html=True)
    top10 = df.groupby([label_col,"_pkg"])["forecast"].mean().reset_index()
    top10.columns = ["Produk","Kemasan","Rata-rata (ton/bln)"]
    top10 = top10.sort_values("Rata-rata (ton/bln)").tail(10)
    fig_t = px.bar(top10, y="Produk", x="Rata-rata (ton/bln)", color="Kemasan",
                   orientation="h", color_discrete_map=PKG_COLORS, height=380)
    fig_t.update_layout(**_chart_kw(yaxis_title="",xaxis_title="Rata-rata ton/bulan"))
    st.plotly_chart(fig_t, use_container_width=True, key=f"top10_{ctx}")

    # Tren
    if has_date:
        st.markdown("<div class='section-title'>TREN BULANAN PER JENIS KEMASAN</div>", unsafe_allow_html=True)
        trend = df.groupby(["date","_pkg"])["forecast"].sum().reset_index()
        trend.columns = ["Tanggal","Kemasan","Volume (ton)"]
        fig_l = px.line(trend, x="Tanggal", y="Volume (ton)", color="Kemasan",
                        markers=True, color_discrete_map=PKG_COLORS, height=300)
        fig_l.update_layout(**_chart_kw(xaxis_title="",yaxis_title="Volume (ton)"))
        st.plotly_chart(fig_l, use_container_width=True, key=f"trend_{ctx}")
