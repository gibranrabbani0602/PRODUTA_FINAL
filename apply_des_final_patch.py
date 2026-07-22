#!/usr/bin/env python3
"""
Patch final DES PRODUTA.

Jalankan dari root repository:
    python apply_des_final_patch.py

Yang dilakukan:
1. Membuat backup kedua file.
2. Memastikan semua anchor cocok tepat satu kali.
3. Menerapkan objective minimisasi projected peak utilization.
4. Mempertahankan parallel line cursor dan hard constraints.
5. Membuat setup transition eksplisit.
6. Mengurangi candidate window menjadi 12.
7. Menambahkan conservative backfilling.
8. Memperbaiki definisi/output/ranking utilisasi.
9. Menambahkan runtime profiling.
10. Mengurangi filter berulang pada inventory ledger.

Skrip berhenti sebelum menulis bila satu anchor tidak cocok.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent
DES_PATH = ROOT / "modules" / "des_simulation_engine.py"
LEDGER_PATH = ROOT / "modules" / "inventory_backlog.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"[{label}] anchor harus muncul tepat 1 kali, ditemukan {count} kali."
        )
    return text.replace(old, new, 1)


def insert_before_once(text: str, anchor: str, addition: str, label: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"[{label}] anchor harus muncul tepat 1 kali, ditemukan {count} kali."
        )
    return text.replace(anchor, addition + anchor, 1)


def patch_des(source: str) -> str:
    source = replace_once(
        source,
        """from datetime import datetime
from io import BytesIO, StringIO
""",
        """from datetime import datetime
from io import BytesIO, StringIO
from time import perf_counter
""",
        "import perf_counter",
    )

    source = replace_once(
        source,
        """DEFAULT_MAX_SCENARIOS = 100
DEFAULT_CANDIDATE_WINDOW = 60
DEFAULT_PLANNED_PREVIEW_ROWS = 5000
""",
        """DEFAULT_MAX_SCENARIOS = 100

# Urgent jobs selalu diperiksa seluruhnya.
# Nilai ini hanya membatasi later/backfill candidates.
DEFAULT_CANDIDATE_WINDOW = 12
DEFAULT_PLANNED_PREVIEW_ROWS = 5000

LINES = ("B", "G", "D")
LINE_RANK = {"B": 0, "G": 1, "D": 2}

ALLERGEN_TRIGGER_MODE = "increase"
PORT_CHANGE_ALONE_REQUIRES_SETUP = False
FIRST_JOB_SETUP_MINUTE = 0.0

TARGET_RUNTIME_SECONDS = 240.0
MAX_RUNTIME_SECONDS = 600.0
""",
        "constants",
    )

    source = replace_once(
        source,
        """def get_daily_line_hours(profile, line, calendar_date):
    profile = validate_weekly_hours(profile)

    weekday_index = pd.Timestamp(
        calendar_date
    ).weekday()

    return float(
        profile[line][weekday_index]
    )
""",
        """def get_daily_line_hours(profile, line, calendar_date):
    # Profile divalidasi satu kali sebelum loop kalender.
    # Jangan validasi ulang untuk setiap tanggal dan line.
    weekday_index = pd.Timestamp(
        calendar_date
    ).weekday()

    return float(
        profile[line][weekday_index]
    )
""",
        "daily hours validation",
    )

    source = replace_once(
        source,
        """def calc_setup(line_state, job):
    if line_state["last_sku"] is None:
        return 0
    allergen_up = job["Allergen"] > line_state["last_allergen"]
    color_change = job["Color Setup"] != line_state["last_color"]
    port_change = job["Port Type"] != line_state["last_port"]
    if not (allergen_up or color_change):
        return 0
    return SETUP_PORT_BERUBAH if port_change else SETUP_PORT_SAMA
""",
        """def _allergen_direction(previous_value, current_value):
    previous_value = float(previous_value)
    current_value = float(current_value)

    if current_value > previous_value:
        return "increase"
    if current_value < previous_value:
        return "decrease"
    return "same"


def _allergen_requires_setup(direction):
    if ALLERGEN_TRIGGER_MODE == "increase":
        return direction == "increase"
    if ALLERGEN_TRIGGER_MODE == "decrease":
        return direction == "decrease"
    if ALLERGEN_TRIGGER_MODE == "any_change":
        return direction != "same"

    raise ValueError(
        "ALLERGEN_TRIGGER_MODE tidak valid: "
        + str(ALLERGEN_TRIGGER_MODE)
    )


def describe_transition(previous_state, job):
    first_job = previous_state["last_sku"] is None

    if first_job:
        return {
            "first_job": True,
            "same_sku": False,
            "allergen_direction": "same",
            "allergen_up": False,
            "allergen_trigger": False,
            "color_change": False,
            "port_change": False,
            "setup_required": FIRST_JOB_SETUP_MINUTE > 0,
            "setup_minutes": float(FIRST_JOB_SETUP_MINUTE),
            "rule": "FIRST_JOB_NO_SETUP",
        }

    same_sku = (
        str(job["SKU"])
        == str(previous_state["last_sku"])
    )

    allergen_direction = _allergen_direction(
        previous_state["last_allergen"],
        job["Allergen"],
    )
    allergen_trigger = _allergen_requires_setup(
        allergen_direction
    )
    color_change = (
        str(job["Color Setup"])
        != str(previous_state["last_color"])
    )
    port_change = (
        str(job["Port Type"])
        != str(previous_state["last_port"])
    )

    if same_sku:
        setup_required = False
        setup_minutes = 0.0
        rule = "SAME_SKU_NO_SETUP"
    else:
        setup_required = (
            allergen_trigger
            or color_change
            or (
                PORT_CHANGE_ALONE_REQUIRES_SETUP
                and port_change
            )
        )

        if not setup_required:
            setup_minutes = 0.0
            rule = "NO_SETUP"
        elif port_change:
            setup_minutes = float(
                SETUP_PORT_BERUBAH
            )
            rule = "SETUP_40_PORT_BERUBAH"
        else:
            setup_minutes = float(
                SETUP_PORT_SAMA
            )
            rule = "SETUP_60_PORT_SAMA"

    return {
        "first_job": False,
        "same_sku": same_sku,
        "allergen_direction": allergen_direction,
        # Nama lama dipertahankan agar export tidak rusak.
        "allergen_up": (
            allergen_direction == "increase"
        ),
        "allergen_trigger": allergen_trigger,
        "color_change": color_change,
        "port_change": port_change,
        "setup_required": setup_required,
        "setup_minutes": setup_minutes,
        "rule": rule,
    }


def calc_setup(line_state, job):
    return describe_transition(
        line_state,
        job,
    )["setup_minutes"]
""",
        "global setup transition",
    )

    source = replace_once(
        source,
        """    def describe_transition(
        previous_state,
        job,
    ):
        first_job = (
            previous_state[
                "last_sku"
            ]
            is None
        )

        if first_job:
            return {
                "allergen_up": False,
                "color_change": False,
                "port_change": False,
                "rule": "FIRST_JOB_NO_SETUP",
            }

        allergen_up = (
            float(job["Allergen"])
            > float(
                previous_state[
                    "last_allergen"
                ]
            )
        )

        color_change = (
            job["Color Setup"]
            != previous_state[
                "last_color"
            ]
        )

        port_change = (
            job["Port Type"]
            != previous_state[
                "last_port"
            ]
        )

        if not (
            allergen_up
            or color_change
        ):
            rule = "NO_SETUP"

        elif port_change:
            rule = (
                "SETUP_40_PORT_BERUBAH"
            )

        else:
            rule = (
                "SETUP_60_PORT_SAMA"
            )

        return {
            "allergen_up": allergen_up,
            "color_change": color_change,
            "port_change": port_change,
            "rule": rule,
        }

""",
        "",
        "remove duplicate inner transition",
    )

    source = replace_once(
        source,
        """    working_days_by_line = {
        line: frozenset(dates)
        for line, dates
        in working_days_by_line.items()
    }

    jobs_df = assign_capacity_release_dates(
""",
        """    working_days_by_line = {
        line: frozenset(dates)
        for line, dates
        in working_days_by_line.items()
    }

    # Denominator ditetapkan sebelum scheduling agar objective
    # dan output memakai basis kapasitas yang sama.
    total_available = {
        line: float(sum(
            daily_capacity_minutes[line].values()
        ))
        for line in LINES
    }

    evaluation_available = {
        line: float(sum(
            minutes
            for date, minutes
            in daily_capacity_minutes[line].items()
            if date >= evaluation_start_date
        ))
        for line in LINES
    }

    for line in LINES:
        if evaluation_available[line] <= 0:
            raise ValueError(
                f"Line {line} tidak memiliki kapasitas evaluation."
            )

    jobs_df = assign_capacity_release_dates(
""",
        "capacity denominators",
    )

    source = replace_once(
        source,
        """    line_rank = {
        "B": 0,
        "G": 1,
        "D": 2,
    }
""",
        """    line_rank = LINE_RANK
""",
        "line rank",
    )

    source = insert_before_once(
        source,
        """    planned_jobs = []
    seq = 1
""",
        """    def projected_utilization_metrics(
        selected_line,
        projection,
        job_role,
    ):
        projected = {}

        for line in LINES:
            if job_role == "evaluation":
                work_minutes = (
                    line_state[line][
                        "evaluation_processing"
                    ]
                    + line_state[line][
                        "evaluation_setup"
                    ]
                )
                denominator = (
                    evaluation_available[line]
                )
            else:
                work_minutes = (
                    line_state[line]["processing"]
                    + line_state[line]["setup"]
                )
                denominator = total_available[line]

            if line == selected_line:
                # Gunakan seluruh required work, termasuk bagian
                # overtime, agar utilisasi tidak turun secara semu.
                work_minutes += float(
                    projection["required_minutes"]
                )

            projected[line] = (
                work_minutes
                / denominator
                * 100.0
                if denominator > 0
                else float("inf")
            )

        values = tuple(
            projected[line]
            for line in LINES
        )

        return {
            "by_line": projected,
            "peak": max(values),
            "total": sum(values),
            "spread": max(values) - min(values),
        }

""",
        "projected utilization helper",
    )

    source = replace_once(
        source,
        """                candidate_key = (
                    # 1. Hindari dan minimalkan keterlambatan.
                    1
                    if late_minutes
                    > LOT_ROUNDING_EPSILON
                    else 0,

                    round(
                        late_minutes,
                        6,
                    ),

                    # 2. Dahulukan due date terdekat.
                    due_date,

                    # 3. Hindari dan minimalkan overtime.
                    1
                    if overtime_minutes
                    > LOT_ROUNDING_EPSILON
                    else 0,

                    round(
                        overtime_minutes,
                        6,
                    ),

                    # 4. Lindungi SKU dengan pilihan lini terbatas.
                    compatibility_count,

                    # 5. Lindungi shelf window yang lebih sempit.
                    shelf_window_days,

                    # 6. Seimbangkan lini berdasarkan
                    # waktu selesai yang diproyeksikan.
                    pd.Timestamp(
                        projection[
                            "finish_datetime"
                        ]
                    ),

                    # 7. Bila waktu selesai sama,
                    # pilih yang dapat mulai lebih awal.
                    pd.Timestamp(
                        projection[
                            "start_datetime"
                        ]
                    ),

                    # 8. Hindari menunggu bila hasilnya setara.
                    wait_flag,

                    # 9. Minimalkan setup setelah
                    # beban antarlini diseimbangkan.
                    round(
                        setup_minutes,
                        6,
                    ),

                    # 10. Residual hanya menjadi tie-break akhir.
                    # Jangan digunakan untuk memilih lini utama.
                    round(
                        residual_minutes,
                        6,
                    ),

                    # Tie-break deterministik.
                    line_rank[line],
                    str(job["SKU"]),
                    int(job["Lot Number"]),
                )
""",
        """                util_metrics = (
                    projected_utilization_metrics(
                        selected_line=line,
                        projection=projection,
                        job_role=job_role,
                    )
                )

                candidate_key = (
                    # Hard feasibility: keterlambatan tetap
                    # lebih penting daripada objective kapasitas.
                    1
                    if late_minutes
                    > LOT_ROUNDING_EPSILON
                    else 0,
                    round(late_minutes, 6),

                    # Objective utama setelah feasibility.
                    round(util_metrics["peak"], 8),
                    round(util_metrics["total"], 8),

                    # Kurangi work tambahan yang dapat dihindari.
                    round(setup_minutes, 6),

                    # Seimbangkan B, G, dan D.
                    round(util_metrics["spread"], 8),

                    # Overtime tidak boleh dipakai untuk
                    # membuat utilization terlihat lebih rendah.
                    1
                    if overtime_minutes
                    > LOT_ROUNDING_EPSILON
                    else 0,
                    round(overtime_minutes, 6),

                    # Lindungi SKU yang sulit dialokasikan.
                    compatibility_count,
                    shelf_window_days,

                    # Due date sudah dikendalikan oleh urgent pool
                    # dan conservative backfilling.
                    due_date,

                    pd.Timestamp(
                        projection["finish_datetime"]
                    ),
                    pd.Timestamp(
                        projection["start_datetime"]
                    ),
                    wait_flag,
                    round(residual_minutes, 6),

                    # Tie-break deterministik.
                    line_rank[line],
                    str(job["SKU"]),
                    int(job["Lot Number"]),
                )
""",
        "candidate objective",
    )

    source = replace_once(
        source,
        """                    "projection": projection,
                    "compatibility_count": (
""",
        """                    "projection": projection,
                    "projected_utilization": (
                        util_metrics
                    ),
                    "compatibility_count": (
""",
        "candidate utilization audit",
    )

    source = replace_once(
        source,
        """        candidate_pool = (
            urgent_jobs
            + later_jobs[
                :additional_candidate_count
            ]
        )

        candidates = (
            evaluate_candidates(
                candidate_pool,
                eligible_lines,
            )
        )
""",
        """        later_shortlist = later_jobs[
            :additional_candidate_count
        ]

        candidate_pool = (
            urgent_jobs
            + later_shortlist
        )

        urgent_candidates = (
            evaluate_candidates(
                urgent_jobs,
                eligible_lines,
            )
        )

        safe_backfill_candidates = []

        if urgent_candidates:
            urgent_reservation_time = min(
                pd.Timestamp(
                    candidate[
                        "projection"
                    ]["start_datetime"]
                )
                for candidate
                in urgent_candidates
            )

            later_candidates = (
                evaluate_candidates(
                    later_shortlist,
                    eligible_lines,
                )
            )

            safe_backfill_candidates = [
                candidate
                for candidate
                in later_candidates
                if (
                    pd.Timestamp(
                        candidate[
                            "projection"
                        ]["finish_datetime"]
                    )
                    <= urgent_reservation_time
                    and float(
                        candidate[
                            "projection"
                        ]["late_minutes"]
                    )
                    <= LOT_ROUNDING_EPSILON
                )
            ]

            candidates = (
                urgent_candidates
                + safe_backfill_candidates
            )
        else:
            # Bila urgent job belum feasible pada line yang
            # sedang kosong, izinkan shortlist lain.
            candidates = evaluate_candidates(
                later_shortlist,
                eligible_lines,
            )
""",
        "conservative backfilling",
    )

    source = replace_once(
        source,
        """    evaluation_available = {
        line: sum(
            minutes
            for date, minutes
            in daily_capacity_minutes[line].items()
            if date >= evaluation_start_date
        )
        for line in ["B", "G", "D"]
    }

    util_b = (
        line_state["B"][
            "evaluation_regular_busy"
        ]
        / evaluation_available["B"]
        * 100
        if evaluation_available["B"] > 0
        else 0
    )

    util_g = (
        line_state["G"][
            "evaluation_regular_busy"
        ]
        / evaluation_available["G"]
        * 100
        if evaluation_available["G"] > 0
        else 0
    )

    util_d = (
        line_state["D"][
            "evaluation_regular_busy"
        ]
        / evaluation_available["D"]
        * 100
        if evaluation_available["D"] > 0
        else 0
    )    
    util_dict = {"Filling B": util_b, "Filling G": util_g, "Filling D": util_d}
""",
        """    def effective_utilization(line):
        used_minutes = (
            line_state[line][
                "evaluation_processing"
            ]
            + line_state[line][
                "evaluation_setup"
            ]
        )

        return (
            used_minutes
            / evaluation_available[line]
            * 100.0
            if evaluation_available[line] > 0
            else 0.0
        )

    def regular_utilization(line):
        return (
            line_state[line][
                "evaluation_regular_busy"
            ]
            / evaluation_available[line]
            * 100.0
            if evaluation_available[line] > 0
            else 0.0
        )

    def overtime_ratio(line):
        return (
            line_state[line][
                "evaluation_overtime"
            ]
            / evaluation_available[line]
            * 100.0
            if evaluation_available[line] > 0
            else 0.0
        )

    util_b = effective_utilization("B")
    util_g = effective_utilization("G")
    util_d = effective_utilization("D")

    regular_util_b = regular_utilization("B")
    regular_util_g = regular_utilization("G")
    regular_util_d = regular_utilization("D")

    overtime_ratio_b = overtime_ratio("B")
    overtime_ratio_g = overtime_ratio("G")
    overtime_ratio_d = overtime_ratio("D")

    utilization_spread = (
        max(util_b, util_g, util_d)
        - min(util_b, util_g, util_d)
    )

    total_setup_minute = sum(
        line_state[line]["evaluation_setup"]
        for line in LINES
    )

    util_dict = {
        "Filling B": util_b,
        "Filling G": util_g,
        "Filling D": util_d,
    }
""",
        "effective utilization",
    )

    source = replace_once(
        source,
        """        "Util Filling B (%)": round(util_b, 2), "Util Filling G (%)": round(util_g, 2), "Util Filling D (%)": round(util_d, 2),
""",
        """        "Util Filling B (%)": round(util_b, 2),
        "Util Filling G (%)": round(util_g, 2),
        "Util Filling D (%)": round(util_d, 2),

        "Regular Util Filling B (%)": round(
            regular_util_b,
            2,
        ),
        "Regular Util Filling G (%)": round(
            regular_util_g,
            2,
        ),
        "Regular Util Filling D (%)": round(
            regular_util_d,
            2,
        ),

        "Overtime Ratio B (%)": round(
            overtime_ratio_b,
            2,
        ),
        "Overtime Ratio G (%)": round(
            overtime_ratio_g,
            2,
        ),
        "Overtime Ratio D (%)": round(
            overtime_ratio_d,
            2,
        ),

        "Utilization Spread (%)": round(
            utilization_spread,
            2,
        ),
        "Total Setup Minute": round(
            total_setup_minute,
            2,
        ),
""",
        "utilization output",
    )

    source = replace_once(
        source,
        """    scenario_list = [row.to_dict() for _, row in scenario_df.iterrows()]

    # Jalankan skenario secara berurutan agar aman
""",
        """    scenario_list = [
        row.to_dict()
        for _, row in scenario_df.iterrows()
    ]

    run_started = perf_counter()
    scenario_runtimes = []

    # Jalankan skenario secara berurutan agar aman
""",
        "run timer start",
    )

    source = replace_once(
        source,
        """    for scenario in scenario_list:
        result, scenario_planned_jobs_df = (
""",
        """    for scenario in scenario_list:
        scenario_started = perf_counter()

        result, scenario_planned_jobs_df = (
""",
        "scenario timer start",
    )

    source = replace_once(
        source,
        """        if not result:
            continue

        results.append(result)
""",
        """        if not result:
            continue

        scenario_runtime = (
            perf_counter()
            - scenario_started
        )
        result["Runtime Second"] = round(
            scenario_runtime,
            3,
        )
        scenario_runtimes.append(
            scenario_runtime
        )

        results.append(result)
""",
        "scenario timer result",
    )

    source = replace_once(
        source,
        """        # Urutan ini sama dengan urutan hasil akhir.
        current_rank_key = (
            -float(
                result[
                    "On-Time Demand Fulfillment (%)"
                ]
            ),
            -float(
                result[
                    "SKU-Period On Time (%)"
                ]
            ),
            float(
                result[
                    "Ending Backlog Ton"
                ]
            ),
            float(
                result[
                    "Late Demand Ton"
                ]
            ),
            int(
                result[
                    "Maximum Delay Days"
                ]
            ),
            total_weekly_hours,
            highest_utilization,
            str(result["Scenario"]),
        )
""",
        """        utilization_spread = float(
            result.get(
                "Utilization Spread (%)",
                0.0,
            )
        )
        total_setup_minute = float(
            result.get(
                "Total Setup Minute",
                0.0,
            )
        )
        total_overtime_minute = float(
            result.get(
                "Total Overtime Minute",
                0.0,
            )
        )

        # Harus identik dengan sort_values hasil akhir.
        current_rank_key = (
            -float(
                result[
                    "On-Time Demand Fulfillment (%)"
                ]
            ),
            -float(
                result[
                    "SKU-Period On Time (%)"
                ]
            ),
            float(
                result["Ending Backlog Ton"]
            ),
            float(
                result["Late Demand Ton"]
            ),
            int(
                result["Maximum Delay Days"]
            ),
            highest_utilization,
            utilization_spread,
            total_setup_minute,
            total_overtime_minute,
            total_weekly_hours,
            str(result["Scenario"]),
        )
""",
        "best scenario rank",
    )

    source = replace_once(
        source,
        """                by=[
                    "On-Time Demand Fulfillment (%)",
                    "SKU-Period On Time (%)",
                    "Ending Backlog Ton",
                    "Late Demand Ton",
                    "Maximum Delay Days",
                    "Total Weekly Operating Hours",
                    "Highest Utilization (%)",
                    "Scenario",
                ],
                ascending=[
                    False,
                    False,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                ],
""",
        """                by=[
                    "On-Time Demand Fulfillment (%)",
                    "SKU-Period On Time (%)",
                    "Ending Backlog Ton",
                    "Late Demand Ton",
                    "Maximum Delay Days",
                    "Highest Utilization (%)",
                    "Utilization Spread (%)",
                    "Total Setup Minute",
                    "Total Overtime Minute",
                    "Total Weekly Operating Hours",
                    "Scenario",
                ],
                ascending=[
                    False,
                    False,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                    True,
                ],
""",
        "result ranking",
    )

    source = replace_once(
        source,
        """    meta = {
        "scenarios_evaluated": len(result_df),
""",
        """    total_runtime = (
        perf_counter()
        - run_started
    )

    meta = {
        "scenarios_evaluated": len(result_df),
        "runtime_seconds": round(
            total_runtime,
            3,
        ),
        "mean_scenario_runtime_seconds": round(
            (
                sum(scenario_runtimes)
                / len(scenario_runtimes)
            )
            if scenario_runtimes
            else 0.0,
            3,
        ),
        "runtime_target_met": (
            total_runtime
            <= TARGET_RUNTIME_SECONDS
        ),
        "runtime_max_met": (
            total_runtime
            <= MAX_RUNTIME_SECONDS
        ),
""",
        "runtime meta",
    )

    return source


def patch_ledger(source: str) -> str:
    source = replace_once(
        source,
        """    production_df = production_df[
        production_df["Production Date"].notna()
        & (production_df["Batch Ton"] > EPSILON)
    ].copy()

    records = []
""",
        """    production_df = production_df[
        production_df["Production Date"].notna()
        & (production_df["Batch Ton"] > EPSILON)
    ].copy()

    # Hindari memfilter seluruh tabel produksi untuk setiap SKU.
    production_groups = {
        str(sku): group
        for sku, group
        in production_df.groupby(
            "SKU",
            sort=False,
        )
    }

    empty_production_df = (
        production_df.iloc[0:0]
    )

    records = []
""",
        "ledger production groups",
    )

    source = replace_once(
        source,
        """        sku_production_df = production_df[
            production_df["SKU"].eq(
                str(sku_id)
            )
        ].copy()
""",
        """        sku_production_df = (
            production_groups.get(
                str(sku_id),
                empty_production_df,
            )
        )
""",
        "ledger repeated filter",
    )

    return source


def main() -> int:
    if not DES_PATH.exists():
        print(f"File tidak ditemukan: {DES_PATH}")
        return 1

    if not LEDGER_PATH.exists():
        print(f"File tidak ditemukan: {LEDGER_PATH}")
        return 1

    des_original = DES_PATH.read_text(
        encoding="utf-8"
    )
    ledger_original = LEDGER_PATH.read_text(
        encoding="utf-8"
    )

    try:
        des_patched = patch_des(
            des_original
        )
        ledger_patched = patch_ledger(
            ledger_original
        )
    except Exception as exc:
        print("PATCH DIBATALKAN.")
        print(str(exc))
        print(
            "Tidak ada file yang ditulis. "
            "Pastikan branch masih sama dengan main "
            "commit ab9302cc."
        )
        return 2

    backup_suffix = ".bak_before_final_des_patch"
    des_backup = DES_PATH.with_name(
        DES_PATH.name + backup_suffix
    )
    ledger_backup = LEDGER_PATH.with_name(
        LEDGER_PATH.name + backup_suffix
    )

    shutil.copy2(
        DES_PATH,
        des_backup,
    )
    shutil.copy2(
        LEDGER_PATH,
        ledger_backup,
    )

    DES_PATH.write_text(
        des_patched,
        encoding="utf-8",
    )
    LEDGER_PATH.write_text(
        ledger_patched,
        encoding="utf-8",
    )

    print("PATCH BERHASIL.")
    print(f"DES    : {DES_PATH}")
    print(f"Ledger : {LEDGER_PATH}")
    print(f"Backup : {des_backup}")
    print(f"Backup : {ledger_backup}")
    print()
    print("Lanjutkan dengan:")
    print(
        "python -m py_compile "
        "modules/des_simulation_engine.py "
        "modules/inventory_backlog.py"
    )
    print("streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
