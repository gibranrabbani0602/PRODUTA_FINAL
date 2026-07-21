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
    WEEKDAY_LABELS,
    INITIALIZATION_WEEKLY_HOURS,
)
from modules.inventory_backlog import (
    build_inventory_backlog_table,
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

    highest_utilization = float(
        best.get(
            "Highest Utilization (%)",
            max(
                best["Util Filling B (%)"],
                best["Util Filling G (%)"],
                best["Util Filling D (%)"],
            ),
        )
    )

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Best Scenario",
        str(best["Scenario"])[:38],
    )

    c2.metric(
        "On-Time Demand Fulfillment",
        f"{best['On-Time Demand Fulfillment (%)']:,.2f}%",
    )

    c3.metric(
        "Late Demand",
        f"{best['Late Demand Ton']:,.2f} ton",
    )

    c4, c5, c6 = st.columns(3)

    c4.metric(
        "Ending Backlog",
        f"{best['Ending Backlog Ton']:,.2f} ton",
    )

    c5.metric(
        "Ending Inventory",
        f"{best['Ending Inventory Ton']:,.2f} ton",
    )

    c6.metric(
        "Highest Utilization",
        str(best["Bottleneck Area"]),
        f"{highest_utilization:,.2f}%",
    )    
    
    holiday_value = best.get(
        "Holiday Days",
        meta.get("holiday_days", 0),
    )

    holiday_days = (
        0
        if pd.isna(holiday_value)
        else int(holiday_value)
    )

    sku_count = int(
        meta.get("sku_analyzed", 0)
    )

    input_records = int(
        meta.get("input_records", 0)
    )

    period_count = int(
        meta.get("period_count", 0)
    )

    scenario_count = int(
        meta.get(
            "scenarios_evaluated",
            len(result_df),
        )
    )

    st.success(
        f"Simulation completed · "
        f"SKU analyzed: {sku_count:,} · "
        f"Input records: {input_records:,} · "
        f"Periods: {period_count:,} · "
        f"Scenarios evaluated: {scenario_count:,} · "
        f"Holiday days: {holiday_days:,}"
    )


def _plot_outputs(result_df):
    if result_df.empty:
        return

    result_df = result_df.copy()

    # Kompatibilitas untuk hasil simulasi lama yang
    # belum memiliki kolom On-Time Unmet Demand Ton.
    if "On-Time Unmet Demand Ton" not in result_df.columns:
        if "Late Demand Ton" in result_df.columns:
            result_df["On-Time Unmet Demand Ton"] = (
                pd.to_numeric(
                    result_df["Late Demand Ton"],
                    errors="coerce",
                )
                .fillna(0.0)
            )
        else:
            result_df["On-Time Unmet Demand Ton"] = 0.0

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Production Output",
            "Line Utilization",
            "Heatmap",
            "Scenario Map",
            "Bottleneck",
        ]
    )  

    with tab1:
        fig_ton = px.bar(
            result_df,
            x="Scenario",
            y=[
                "Tons Finished",
                "On-Time Unmet Demand Ton",
            ],
            barmode="group",
            title="Tons Finished vs On-Time Unmet Demand",
            text_auto=".2s",
            color_discrete_sequence=[
                "#004B83",
                "#55C3E8",
            ],
        )
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
        fig_gap = px.scatter(
            result_df,
            x="Finished Ratio (%)",
            y="On-Time Unmet Demand Ton",
            size="Tons Finished",
            color="Bottleneck Area",
            hover_name="Scenario",
            title="Scenario Positioning",
            labels={
                "Finished Ratio (%)": "Total Demand Fulfillment (%)",
                "On-Time Unmet Demand Ton": (
                    "Demand Belum Terpenuhi Saat Due Date (ton)"
                ),
            },
            color_discrete_sequence=BLUE_SEQ,
        )
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
    
    st.markdown(
        "<div class='section-title'>Jadwal Initialization</div>",
        unsafe_allow_html=True,
    )
    
    st.caption(
        "Initialization menggunakan satu kondisi operasi aktual "
        "yang sama untuk seluruh skenario."
    )
    
    initialization_table = pd.DataFrame({
        "Hari": WEEKDAY_LABELS,
        "Line B (jam)": INITIALIZATION_WEEKLY_HOURS["B"],
        "Line G (jam)": INITIALIZATION_WEEKLY_HOURS["G"],
        "Line D (jam)": INITIALIZATION_WEEKLY_HOURS["D"],
    })
    
    st.dataframe(
        initialization_table,
        width="stretch",
        hide_index=True,
    )
    
    st.markdown(
        "<div class='section-title'>Konfigurasi Evaluation</div>",
        unsafe_allow_html=True,
    )
    
    st.caption(
        "Pilih beberapa hari kerja dan jam kerja. "
        "Sistem akan membentuk kombinasi skenario secara faktorial."
    )
    
    bcol, gcol, dcol = st.columns(3)
    
    with bcol:
        st.markdown("**Line B**")
    
        b_days = st.multiselect(
            "Hari kerja/minggu B",
            options=[5, 6, 7],
            default=[6, 7],
            key="des_b_days",
        )
    
        b_hours = st.multiselect(
            "Jam kerja/hari B",
            options=[8, 16, 24],
            default=[16, 24],
            key="des_b_hours",
        )
    
    with gcol:
        st.markdown("**Line G**")
    
        g_days = st.multiselect(
            "Hari kerja/minggu G",
            options=[5, 6, 7],
            default=[6, 7],
            key="des_g_days",
        )
    
        g_hours = st.multiselect(
            "Jam kerja/hari G",
            options=[8, 16, 24],
            default=[16, 24],
            key="des_g_hours",
        )
    
    with dcol:
        st.markdown("**Line D**")
    
        d_days = st.multiselect(
            "Hari kerja/minggu D",
            options=[5, 6, 7],
            default=[7],
            key="des_d_days",
        )
    
        d_hours = st.multiselect(
            "Jam kerja/hari D",
            options=[8, 16, 24],
            default=[24],
            key="des_d_hours",
        )
    
    evaluation_schedule_mode = None
    evaluation_weekly_hours = None
    
    st.info(
        "Konfigurasi awal menghasilkan "
        "2 × 2 × 2 × 2 × 1 × 1 = 16 skenario evaluation."
    )    
    st.markdown("**Downtime lini pada periode evaluation**")
    
    bcol, gcol, dcol = st.columns(3)
    
    with bcol:
        b_down = st.number_input(
            "Downtime B (hari/bulan)",
            min_value=0,
            max_value=10,
            value=0,
            step=1,
        )
    
    with gcol:
        g_down = st.number_input(
            "Downtime G (hari/bulan)",
            min_value=0,
            max_value=10,
            value=0,
            step=1,
        )
    
    with dcol:
        d_down = st.number_input(
            "Downtime D (hari/bulan)",
            min_value=0,
            max_value=10,
            value=0,
            step=1,
        )
    
    b_avail = g_avail = d_avail = 100

    st.markdown("<div class='section-title'>Business Scenario</div>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        batch_limit = st.number_input(
            "Batas total lot per hari",
            min_value=0,
            max_value=500,
            value=0,
            step=1,
            help=(
                "0 = tanpa batas atau BLOSS. "
                "Nilai lainnya adalah maksimum total lot "
                "seluruh lini dalam satu hari."
            ),
        )
    
        batch_options = [int(batch_limit)]
    with c2:
        gmin  = st.number_input("Growth min (%)", value=0.0, step=1.0, key="gmin")
        gmax  = st.number_input("Growth max (%)", value=0.0, step=1.0, key="gmax")
        gstep = st.number_input("Growth step (%)", min_value=1.0, value=5.0, step=1.0, key="gstep")
        growth_options = make_growth_options("range", gmin=gmin, gmax=gmax, step=gstep)
    with c3:
        st.caption(f"Growth: {', '.join(str(int(g))+'%' for g in growth_options)}" if growth_options else "—")
    with c4:
        max_scenarios = st.number_input("Max skenario", min_value=1, max_value=500, value=100, step=10)

    st.markdown(
        "<div class='section-title'>Kalender Hari Libur</div>",
        unsafe_allow_html=True,
    )
    
    holiday_mode_label = st.radio(
        "Metode penetapan hari libur",
        [
            "Tanpa hari libur tambahan",
            "Tanggal libur manual",
            "Estimasi jumlah hari tutup produksi",
        ],
        horizontal=True,
    )
    
    holiday_mode_map = {
        "Tanpa hari libur tambahan": "none",
        "Tanggal libur manual": "manual",
        "Estimasi jumlah hari tutup produksi": "estimated",
    }
    
    holiday_mode = holiday_mode_map[
        holiday_mode_label
    ]
    
    holiday_cutoff = 0
    holiday_dates = ""
    
    if holiday_mode == "manual":
        holiday_dates = st.text_area(
            "Tanggal libur produksi",
            placeholder=(
                "Contoh: 2026-04-10, 2026-05-01, "
                "2026-12-25, 2027-01-01"
            ),
            height=100,
            help=(
                "Pisahkan tanggal dengan koma, titik koma, "
                "atau baris baru."
            ),
        )
    
        st.caption(
            "Gunakan tanggal ketika kegiatan produksi benar-benar "
            "ditutup. Tanggal harus berada di dalam horizon simulasi."
        )
    
    elif holiday_mode == "estimated":
        holiday_cutoff = st.slider(
            "Jumlah hari tutup produksi yang diestimasi",
            min_value=0,
            max_value=40,
            value=16,
            step=1,
        )
    
        st.caption(
            "Tanggal akan disebarkan secara deterministik dan merata "
            "pada hari Senin–Jumat sepanjang horizon simulasi. "
            "Gunakan pilihan ini hanya ketika jumlah hari diketahui, "
            "tetapi tanggal pastinya tidak tersedia."
        )
    
    else:
        st.info(
            "Model tidak akan menambahkan hari libur di luar "
            "jadwal kerja mingguan dan downtime lini."
        )
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
        clear_capacity_results()
    
        try:
            if forecast_input is None or forecast_input.empty:
                st.error("ForecastInput DES belum tersedia.")
                st.stop()
            if any(len(x) == 0 for x in [b_days, b_hours, g_days, g_hours, d_days, d_hours, batch_options, growth_options]):
                st.error("Pilih minimal satu opsi pada setiap parameter.")
                st.stop()
            if (
                holiday_mode == "manual"
                and not holiday_dates.strip()
            ):
                st.error(
                    "Masukkan minimal satu tanggal libur produksi."
                )
                st.stop()
            with st.spinner("Menjalankan DES simulation..."):
                result_df, scenario_df, planned_jobs_df, input_df, meta = run_des_simulation(
                    forecast_input,
                    b_days, b_hours, g_days, g_hours, d_days, d_hours,
                    batch_options, growth_options,
                    holiday_cutoff_days=holiday_cutoff,
                    holiday_dates_text=holiday_dates,
                    holiday_mode=holiday_mode,
                    evaluation_schedule_mode=(
                        evaluation_schedule_mode
                    ),
                    evaluation_weekly_hours=(
                        evaluation_weekly_hours
                    ),
                    max_scenarios=int(max_scenarios),
                    b_downtime=b_down, g_downtime=g_down, d_downtime=d_down,
                )
                
                if result_df.empty:
                    raise ValueError(
                        "Simulasi tidak menghasilkan skenario."
                    )

                best_result_row = result_df.iloc[0]

                stock_backlog_df = (
                    build_inventory_backlog_table(
                        forecast_df=input_df,
                        planned_jobs_df=planned_jobs_df,
                        scenario_code=best_result_row[
                            "Scenario"
                        ],
                        growth=float(
                            best_result_row.get(
                                "Growth",
                                0.0,
                            )
                        ),
                    )
                )
                
                excel_bytes, excel_name = export_to_excel_bytes(result_df, scenario_df, planned_jobs_df, input_df, "Simulasi DES Capacity")
            set_state("simulation_result", result_df)
            set_state("scenario_config", scenario_df)
            set_state("planned_jobs", planned_jobs_df)
            set_state("input_data", input_df)
            set_state(
                "stock_backlog",
                stock_backlog_df,
            )
            set_state("export_bytes", {"bytes": excel_bytes, "name": excel_name})
            st.success("DES simulation selesai.")
        except Exception as e:
            st.error("Gagal menjalankan DES simulation.")
            st.exception(e)

    result_df = get_state("simulation_result")
    scenario_df = get_state("scenario_config")
    planned_jobs_df = get_state("planned_jobs")
    input_df = get_state("input_data")
    stock_backlog_df = get_state(
        "stock_backlog"
    )
    export_payload = get_state("export_bytes")

    st.markdown("<div class='section-title'>Simulation Output</div>", unsafe_allow_html=True)
    if result_df is None or result_df.empty:
        warning(
            "Belum ada hasil simulation. Jalankan "
            "<b>Run DES Simulation</b> setelah input tersedia."
        )
        return

    required_result_columns = {
        "Scenario",
        "Tons Finished",
        "Unmet Demand Ton",
        "Finished Ratio (%)",
        "Bottleneck Area",
    }

    missing_result_columns = (
        required_result_columns
        - set(result_df.columns)
    )

    if missing_result_columns:
        clear_capacity_results()

        warning(
            "Struktur hasil lama tidak cocok dengan "
            "versi model terbaru. Silakan jalankan ulang "
            "<b>Run DES Simulation</b>."
        )

        return

    if (
        input_df is not None
        and not input_df.empty
    ):
        sku_count = (
            int(input_df["SkuId"].nunique())
            if "SkuId" in input_df.columns
            else 0
        )

        input_record_count = int(
            len(input_df)
        )

        period_count = (
            int(input_df["MonthIndex"].nunique())
            if "MonthIndex" in input_df.columns
            else 0
        )

    else:
        sku_count = 0
        input_record_count = 0
        period_count = 0

    summary_meta = {
        "sku_analyzed": sku_count,
        "input_records": input_record_count,
        "period_count": period_count,
        "scenarios_evaluated": len(result_df),
        "holiday_days": int(
            result_df.iloc[0].get(
                "Holiday Days",
                0,
            )
        ),
    }

    _summary_cards(
        result_df,
        summary_meta,
    )
    
    data_tabs = st.tabs(
        [
            "Simulation Result",
            "Scenario Configuration",
            "Production Plan",
            "Stock & Backlog",
            "Input",
            "Charts",
            "Export Result",
        ]
    )
    with data_tabs[0]:
        st.dataframe(
            _disp(result_df),
            width="stretch",
            hide_index=True,
        )
    
    with data_tabs[1]:
        st.dataframe(
            _disp(scenario_df),
            width="stretch",
            hide_index=True,
        )
    
    with data_tabs[2]:
        alias_options = sorted(
            planned_jobs_df["SKU Alias"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
    
        selected_alias = st.selectbox(
            "Filter SKU Alias",
            options=["Semua SKU"] + alias_options,
        )
    
        filtered_plan = planned_jobs_df.copy()
    
        if selected_alias != "Semua SKU":
            filtered_plan = filtered_plan[
                filtered_plan["SKU Alias"].eq(
                    selected_alias
                )
            ]
    
        st.dataframe(
            _disp(
                filtered_plan,
                DEFAULT_PLANNED_PREVIEW_ROWS,
            ),
            width="stretch",
            hide_index=True,
        )
    
    with data_tabs[3]:
        if (
            stock_backlog_df is None
            or stock_backlog_df.empty
        ):
            st.info(
                "Tabel stok dan backlog belum tersedia."
            )
        else:
            evaluation_stock = (
                stock_backlog_df[
                    stock_backlog_df[
                        "Data Role"
                    ].eq("evaluation")
                ]
                .copy()
            )
    
            st.caption(
                "Tampilan periode evaluasi. "
                "Periode initialization tetap digunakan "
                "untuk membentuk stok awal."
            )
    
            st.dataframe(
                _disp(evaluation_stock, 5000),
                width="stretch",
                hide_index=True,
            )
    
    with data_tabs[4]:
        st.dataframe(
            _disp(input_df, 2000),
            width="stretch",
            hide_index=True,
        )
    
    with data_tabs[5]:
        _plot_outputs(result_df)
    
    with data_tabs[6]:
        if export_payload:
            st.download_button(
                "Download Excel Result",
                data=export_payload["bytes"],
                file_name=export_payload["name"],
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
            )
        else:
            st.info(
                "Export belum tersedia. "
                "Run simulation terlebih dahulu."
            )   
    best_result = result_df.iloc[0]
    
    holiday_mode_used = str(
        best_result.get(
            "Holiday Mode",
            "none",
        )
    )
    
    holiday_dates_used = str(
        best_result.get(
            "Holiday Dates",
            "",
        )
    )
    
    holiday_mode_display = {
        "none": "Tanpa hari libur tambahan",
        "manual": "Tanggal manual",
        "estimated": "Estimasi jumlah hari tutup produksi",
    }.get(
        holiday_mode_used,
        holiday_mode_used,
    )
    
    with st.expander(
        "Lihat kalender hari libur yang digunakan"
    ):
        st.write(
            f"**Metode:** {holiday_mode_display}"
        )
    
        if holiday_dates_used:
            st.write(
                f"**Tanggal:** {holiday_dates_used}"
            )
        else:
            st.write(
                "**Tanggal:** tidak ada hari libur tambahan"
            )
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
