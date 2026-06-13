import pandas as pd
import streamlit as st
import plotly.express as px
from io import StringIO

from modules.theme import hero, note, warning
from modules.session import get_state, set_state, clear_capacity_results
from modules.io_utils import read_table, first_existing_file
from modules.des_simulation_engine import (
    run_des_simulation,
    export_to_excel_bytes,
    estimate_scenario_count,
    make_growth_options,
    DEFAULT_PLANNED_PREVIEW_ROWS,
)

BLUE_SEQ = ["#004B83", "#22B8E8", "#7DD3FC", "#55C3E8"]


def _load_capacity_input_upload_or_default():
    default_file = first_existing_file("data/capacity_input_here")
    default_df = read_table(default_file) if default_file else pd.DataFrame()
    uploaded = st.file_uploader("Upload ForecastInput DES siap pakai", type=["csv", "xlsx", "xls"], key="capacity_input_upload")
    if uploaded is not None:
        return read_table(uploaded), f"upload: {uploaded.name}"
    if not default_df.empty:
        return default_df, f"folder: {default_file}"
    return pd.DataFrame(), ""


def _summary_cards(result_df, meta):
    if result_df.empty:
        return
    best = result_df.iloc[0]
    c1, c2, c3, c4, c5 = st.columns(5)
    
    scenario_label = best.get("Scenario", best.get("Scenario_ID", best.get("Label", "-")))
    c1.metric("Best Scenario", str(scenario_label)[:38])    
    c2.metric("Tons Finished", f"{best['Tons Finished']:,.2f}")
    c3.metric("Unmet Demand", f"{best['Unmet Demand Ton']:,.2f}")
    c4.metric("Finished Ratio", f"{best['Finished Ratio (%)']:,.2f}%")
    c5.metric("Bottleneck", best["Bottleneck Area"])
    st.success(f"Simulation completed · Products: {meta.get('products_analyzed', 0):,} · Scenarios evaluated: {len(result_df):,} · Holiday days: {meta.get('holiday_days', 0):,}")


def _plot_outputs(result_df):
    if result_df.empty:
        return
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Production Output", "Line Utilization", "Heatmap", "Scenario Map", "Bottleneck"])
    with tab1:
        fig_ton = px.bar(result_df, x="Scenario", y=["Tons Finished", "Unmet Demand Ton"], barmode="group", title="Tons Finished vs Unmet Demand", text_auto=".2s", color_discrete_sequence=["#004B83", "#55C3E8"])
        fig_ton.update_layout(xaxis_tickangle=-45, height=540, plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig_ton, use_container_width=True)
    with tab2:
        util_long = result_df.melt(id_vars=["Scenario"], value_vars=["Util Filling B (%)", "Util Filling G (%)", "Util Filling D (%)"], var_name="Line", value_name="Utilization (%)")
        fig_util = px.bar(util_long, x="Scenario", y="Utilization (%)", color="Line", barmode="group", title="Filling Line Utilization (Fill + Setup)", text_auto=".1f", color_discrete_sequence=BLUE_SEQ)
        fig_util.update_layout(xaxis_tickangle=-45, height=540, plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig_util, use_container_width=True)
    with tab3:
        heatmap_df = result_df[["Util Filling B (%)", "Util Filling G (%)", "Util Filling D (%)"]].T
        fig_heatmap = px.imshow(heatmap_df, labels=dict(x="Scenario", y="Line", color="Utilization %"), x=result_df["Scenario"], y=["Filling B", "Filling G", "Filling D"], title="Utilization Heatmap", text_auto=".1f", aspect="auto", color_continuous_scale=["#EAF7FD", "#55C3E8", "#004B83"])
        fig_heatmap.update_layout(height=500, plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig_heatmap, use_container_width=True)
    with tab4:
        fig_gap = px.scatter(result_df, x="Finished Ratio (%)", y="Unmet Demand Ton", size="Tons Finished", color="Bottleneck Area", hover_name="Scenario", title="Scenario Positioning", color_discrete_sequence=BLUE_SEQ)
        fig_gap.update_layout(height=520, plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig_gap, use_container_width=True)
    with tab5:
        bottleneck_count = result_df["Bottleneck Area"].value_counts().reset_index()
        bottleneck_count.columns = ["Bottleneck Area", "Count"]
        fig_bottleneck = px.pie(bottleneck_count, names="Bottleneck Area", values="Count", title="Bottleneck Distribution", hole=0.45, color_discrete_sequence=BLUE_SEQ)
        fig_bottleneck.update_layout(height=470, plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig_bottleneck, use_container_width=True)


def render():

    def _disp(df, n=None):
        """Bulatkan kolom numerik (2 desimal) untuk tampilan tabel yang rapi."""
        d = df.head(n).copy() if n else df.copy()
        for c in d.select_dtypes(include="number").columns:
            d[c] = pd.to_numeric(d[c], errors="coerce").round(2)
        return d
    st.markdown(
        '<div class="page-title">SIMULASI KAPASITAS</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="color:#088395;font-size:.88rem;margin:-12px 0 18px 0;">'
        'Simulasi kapasitas lini produksi berbasis Discrete Event Simulation.</p>',
        unsafe_allow_html=True,
    )

    st.markdown("<div class='section-title'>Input Data</div>", unsafe_allow_html=True)
    source = st.radio(
        "Sumber Data Input",
        ["Dari Demand & Forecasting", "Upload file"],
        horizontal=True,
    )
    if source == "Dari Demand & Forecasting":
        forecast_input = get_state("forecast_input_des")
        source_note = "session: Demand Overview"
    else:
        forecast_input, source_note = _load_capacity_input_upload_or_default()

    pasted = st.text_area("Tempel data", height=80, placeholder="Opsional. Paste tabel CSV/TSV dari Excel.")
    if pasted.strip():
        try:
            forecast_input = pd.read_csv(StringIO(pasted), sep=None, engine="python")
            source_note = "pasted text"
        except Exception:
            st.error("Paste tabel belum bisa dibaca.")

    if forecast_input is None or forecast_input.empty:
        warning("Data input belum tersedia. Buat dari Demand Overview atau upload file.")
    else:
        st.caption(f"Input source: {source_note}")
        st.dataframe(_disp(forecast_input, 120), use_container_width=True, hide_index=True)

    st.markdown("<div class='section-title'>Konfigurasi Lini</div>", unsafe_allow_html=True)
    bcol, gcol, dcol = st.columns(3)
    with bcol:
        st.markdown("**Line B**")
        b_days  = st.multiselect("Hari kerja/minggu B", [5, 6, 7], default=[6], key="b_days")
        b_hours = st.multiselect("Jam kerja/hari B", [8, 16, 24], default=[16], key="b_hours")
        b_down  = st.number_input("Downtime B (hari/bulan)", 0, 10, 0, 1)
    with gcol:
        st.markdown("**Line G**")
        g_days  = st.multiselect("Hari kerja/minggu G", [5, 6, 7], default=[6], key="g_days")
        g_hours = st.multiselect("Jam kerja/hari G", [8, 16, 24], default=[16], key="g_hours")
        g_down  = st.number_input("Downtime G (hari/bulan)", 0, 10, 0, 1)
    with dcol:
        st.markdown("**Line D**")
        d_days  = st.multiselect("Hari kerja/minggu D", [5, 6, 7], default=[7], key="d_days")
        d_hours = st.multiselect("Jam kerja/hari D", [8, 16, 24], default=[24], key="d_hours")
        d_down  = st.number_input("Downtime D (hari/bulan)", 0, 10, 0, 1)
    b_avail = g_avail = d_avail = 100  # Availability tetap 100% (dihapus dari UI)

    st.markdown("<div class='section-title'>Business Scenario</div>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        batch_options = st.multiselect("Batch Mode", ["B35", "BLOSS"], default=["B35", "BLOSS"])
    with c2:
        gmin  = st.number_input("Growth min (%)", value=0.0, step=1.0, key="gmin")
        gmax  = st.number_input("Growth max (%)", value=0.0, step=1.0, key="gmax")
        gstep = st.number_input("Growth step (%)", min_value=1.0, value=5.0, step=1.0, key="gstep")
        growth_options = make_growth_options("range", gmin=gmin, gmax=gmax, step=gstep)
    with c3:
        st.caption(f"Growth: {', '.join(str(int(g))+'%' for g in growth_options)}" if growth_options else "—")
    with c4:
        max_scenarios = st.number_input("Max skenario", min_value=1, max_value=500, value=100, step=10)

    h1, h2 = st.columns([1, 2])
    with h1:
        holiday_cutoff = st.slider("Jumlah hari libur tahunan", 0, 40, 16, 1)
    with h2:
        holiday_dates = st.text_area("Tanggal libur manual opsional", placeholder="Contoh: 2026-01-01, 2026-03-20, 2026-12-25", height=80)

    total_possible = estimate_scenario_count(b_days, b_hours, g_days, g_hours, d_days, d_hours, batch_options, growth_options)
    st.caption(f"Estimasi kombinasi: {total_possible:,}. App menjalankan maksimal {int(max_scenarios):,} skenario.")

    run_col, clear_col = st.columns([1, 1])
    with run_col:
        run = st.button("Run DES Simulation")
    with clear_col:
        if st.button("Clear hasil lama"):
            clear_capacity_results()
            st.rerun()

    if run:
        try:
            if forecast_input is None or forecast_input.empty:
                st.error("ForecastInput DES belum tersedia.")
                st.stop()
            if any(len(x) == 0 for x in [b_days, b_hours, g_days, g_hours, d_days, d_hours, batch_options, growth_options]):
                st.error("Pilih minimal satu opsi pada setiap parameter.")
                st.stop()
            with st.spinner("Menjalankan DES simulation..."):
                result_df, scenario_df, planned_jobs_df, input_df, meta = run_des_simulation(
                    forecast_input,
                    b_days, b_hours, g_days, g_hours, d_days, d_hours,
                    batch_options, growth_options,
                    holiday_cutoff_days=holiday_cutoff,
                    holiday_dates_text=holiday_dates,
                    max_scenarios=int(max_scenarios),
                    b_downtime=b_down, g_downtime=g_down, d_downtime=d_down,
                )
                excel_bytes, excel_name = export_to_excel_bytes(result_df, scenario_df, planned_jobs_df, input_df, "Simulasi DES Capacity")
            set_state("simulation_result", result_df)
            set_state("scenario_config", scenario_df)
            set_state("planned_jobs", planned_jobs_df)
            set_state("input_data", input_df)
            set_state("export_bytes", {"bytes": excel_bytes, "name": excel_name})
            st.success("DES simulation selesai.")
        except Exception as e:
            st.error("Gagal menjalankan DES simulation.")
            st.exception(e)

    result_df = get_state("simulation_result")
    scenario_df = get_state("scenario_config")
    planned_jobs_df = get_state("planned_jobs")
    input_df = get_state("input_data")
    export_payload = get_state("export_bytes")

    st.markdown("<div class='section-title'>Simulation Output</div>", unsafe_allow_html=True)
    if result_df is None or result_df.empty:
        warning("Belum ada hasil simulation. Jalankan <b>Run DES Simulation</b> setelah input tersedia.")
        return

    _summary_cards(result_df, {"products_analyzed": len(input_df) if input_df is not None else 0, "holiday_days": holiday_cutoff})
    data_tabs = st.tabs(["Simulation Result", "Scenario Configuration", "Production Plan", "Input", "Charts", "Export Result"])
    with data_tabs[0]:
        st.dataframe(_disp(result_df), use_container_width=True, hide_index=True)
    with data_tabs[1]:
        st.dataframe(_disp(scenario_df), use_container_width=True, hide_index=True)
    with data_tabs[2]:
        st.dataframe(_disp(planned_jobs_df, DEFAULT_PLANNED_PREVIEW_ROWS), use_container_width=True, hide_index=True)
        st.caption(f"Preview dibatasi {DEFAULT_PLANNED_PREVIEW_ROWS:,} baris. Export Excel untuk data lengkap.")
    with data_tabs[3]:
        st.dataframe(_disp(input_df, 2000), use_container_width=True, hide_index=True)
    with data_tabs[4]:
        _plot_outputs(result_df)
    with data_tabs[5]:
        if export_payload:
            st.download_button("Download Excel Result", data=export_payload["bytes"], file_name=export_payload["name"], mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.info("Export belum tersedia. Run simulation terlebih dahulu.")

    # ── Export CSV Skenario untuk Capacity Planning ──────────────────────────────
    st.markdown("<div class='section-title'>Export CSV Skenario</div>", unsafe_allow_html=True)
    st.caption(
        "Download file CSV hasil simulasi ini untuk digunakan langsung sebagai input "        "di menu Capacity Planning, tanpa perlu mengulang pipeline dari awal."
    )
    if result_df is not None and not result_df.empty:
        # Pastikan kolom yang dibutuhkan ada sebelum export
        export_sim = result_df.copy()
        csv_bytes = export_sim.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Unduh CSV Skenario DES",
            data=csv_bytes,
            file_name="simulation_scenarios_DES.csv",
            mime="text/csv",
            help="File ini dapat diupload langsung ke menu Capacity Planning.",
        )
        st.info(f"File berisi {len(export_sim)} skenario dengan {len(export_sim.columns)} kolom.")
    else:
        st.info("Jalankan DES Simulation terlebih dahulu untuk mengaktifkan export.")
