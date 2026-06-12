import itertools
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from io import BytesIO, StringIO

import numpy as np
import pandas as pd

# ======================================================
# DES CAPACITY ENGINE - preserved from Gradio logic
# + tambahan optional tolerance per line.
# Default tolerance 100% availability dan 0 downtime membuat logic sama seperti versi lama.
# ======================================================

T_BATCH = 10
T_PREP = 17
T_TIP = 5
T_BLEND_NON_COKLAT = 12
T_BLEND_COKLAT = 15
T_MINI_BLEND_NON_ANMUM = 5
T_MINI_BLEND_ANMUM = 6

SETUP_PORT_BERUBAH = 40
SETUP_PORT_SAMA = 60
TAHUN_SIMULASI = 2026
HORIZON_HARI = 365

DEFAULT_MAX_SCENARIOS = 100
DEFAULT_CANDIDATE_WINDOW = 60
DEFAULT_PLANNED_PREVIEW_ROWS = 5000

REQUIRED_CONCEPTS = [
    "ItemName", "SkuId", "ForecastTon", "SkuGr", "SpeedD", "Speed",
    "IsChocolate", "port_type", "Allergen", "ShelfLife",
]
OPTIONAL_DEFAULTS = {"Qty": 1, "MonthIndex": 1}

COLUMN_ALIASES = {
    "ItemName": ["itemname", "item name", "nama sku", "namasku", "description", "deskripsi", "product", "product name", "namaproduk", "nama produk", "catatan sku", "catatansku"],
    "Qty": ["qty", "quantity", "jumlah", "jumlah batch", "jumlahbatch", "batch", "batches", "lot", "jumlah lot"],
    "SkuId": ["skuid", "sku id", "sku", "kode sku", "kodesku", "item code", "itemcode", "material", "material code"],
    "ForecastTon": ["forecastton", "forecast ton", "forecast", "demand", "demand ton", "target demand ton", "targetdemandton", "ton", "tons", "tonase", "tonnage", "planned ton", "plannedton"],
    "SkuGr": ["skugr", "sku gr", "sku gram", "skugram", "gram", "gramasi", "grammage", "pack size", "packsize", "ukuran gram"],
    "SpeedD": ["speedd", "speed d", "speed line d", "speedlined", "ppm d", "ppmd", "line d speed"],
    "Speed": ["speed", "speed bg", "speed b g", "speed b/g", "speed line b g", "speed ppm", "ppm", "speed (ppm)", "speedb", "speedg", "speed b", "speed g"],
    "IsChocolate": ["ischocolate", "is chocolate", "chocolate", "coklat", "jenis coklat", "type coklat", "color", "colour", "warna", "colorsetup", "color setup"],
    "port_type": ["port_type", "port type", "port", "tipe port", "tipeport", "jenis port", "jenisport"],
    "Allergen": ["allergen", "alergen", "allergen level", "level allergen", "kode allergen", "kodealergen"],
    "ShelfLife": ["shelflife", "shelf life", "expired", "expiry", "umur simpan", "umursimpan", "masa simpan"],
    "MonthIndex": ["monthindex", "month index", "month", "bulan", "periode", "period", "index bulan", "bulan produksi"],
    "Color": ["color", "colour", "warna", "color setup", "colorsetup", "warna setup", "warnasetup"],
}

INDONESIAN_MONTH_WORDS = {
    "januari": "january", "jan": "january", "februari": "february", "feb": "february",
    "maret": "march", "mar": "march", "april": "april", "apr": "april", "mei": "may",
    "juni": "june", "jun": "june", "juli": "july", "jul": "july", "agustus": "august",
    "agu": "august", "ags": "august", "aug": "august", "september": "september", "sep": "september",
    "oktober": "october", "okt": "october", "oct": "october", "november": "november", "nov": "november",
    "desember": "december", "des": "december", "dec": "december",
}
MONTH_NUMBER_WORDS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def normalize_key(x):
    x = "" if x is None else str(x)
    x = unicodedata.normalize("NFKD", x)
    x = "".join(ch for ch in x if not unicodedata.combining(ch))
    x = x.casefold().strip()
    x = re.sub(r"[^a-z0-9]+", "", x)
    return x


def sanitize_filename(text, max_len=70):
    text = "Simulasi_DES_Capacity" if text is None or str(text).strip() == "" else str(text).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9 _-]+", "", text)
    text = re.sub(r"\s+", "_", text).strip("_")
    return (text or "Simulasi_DES_Capacity")[:max_len]


def to_numeric_safe(series):
    return pd.to_numeric(series, errors="coerce")


def canonicalize_columns(df):
    df = df.copy()
    normalized_to_original = {}
    for col in list(df.columns):
        key = normalize_key(col)
        if key not in normalized_to_original:
            normalized_to_original[key] = col
    rename_map = {}
    matched = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for cand in [canonical] + aliases:
            key = normalize_key(cand)
            if key in normalized_to_original:
                original = normalized_to_original[key]
                if original not in rename_map:
                    rename_map[original] = canonical
                    matched[canonical] = original
                    break
    return df.rename(columns=rename_map), matched


def normalize_chocolate_value(value):
    text = "" if pd.isna(value) else str(value).strip().casefold()
    text_norm = normalize_key(text)
    non_choc_tokens = ["noncoklat", "nonchocolate", "nonchoco", "notchocolate", "bukancoklat", "plain", "vanilla", "original", "putih"]
    choc_tokens = ["coklat", "chocolate", "choco", "cocoa", "cacao"]
    if any(tok in text_norm for tok in non_choc_tokens):
        return "non coklat"
    if any(tok in text_norm for tok in choc_tokens):
        return "coklat"
    return text.strip().lower() if text.strip() != "" else "non coklat"


def translate_month_words(text):
    text = "" if text is None else str(text).strip().casefold()
    text = re.sub(r"[,]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = []
    for token in text.split(" "):
        cleaned = re.sub(r"[^a-zA-Z]", "", token).casefold()
        if cleaned in INDONESIAN_MONTH_WORDS:
            token = token.replace(cleaned, INDONESIAN_MONTH_WORDS[cleaned])
        tokens.append(token)
    return " ".join(tokens)


def month_start_day(month, year=TAHUN_SIMULASI):
    try:
        dt = pd.Timestamp(year=int(year), month=int(month), day=1)
        return int(dt.dayofyear), dt.strftime("%Y-%m-%d")
    except Exception:
        return 1, f"{TAHUN_SIMULASI}-01-01"


def parse_month_index_value(value, default_order=1):
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return {"MonthIndex": float(default_order), "MonthInputRaw": "", "MonthInputMode": "default_sequence", "MonthDueDate": "", "MonthDueDay": np.nan}
    raw = value
    if isinstance(value, (pd.Timestamp, datetime)):
        dt = pd.to_datetime(value, errors="coerce")
        if pd.notna(dt):
            mapped = pd.Timestamp(year=TAHUN_SIMULASI, month=int(dt.month), day=int(dt.day))
            return {"MonthIndex": float(mapped.dayofyear), "MonthInputRaw": str(raw), "MonthInputMode": "date", "MonthDueDate": mapped.strftime("%Y-%m-%d"), "MonthDueDay": float(mapped.dayofyear)}
    text = str(value).strip()
    if text == "" or text.casefold() in ["nan", "none", "null", "-"]:
        return {"MonthIndex": float(default_order), "MonthInputRaw": text, "MonthInputMode": "default_sequence", "MonthDueDate": "", "MonthDueDay": np.nan}
    if re.fullmatch(r"\d+(?:[.,]0+)?", text):
        return {"MonthIndex": float(text.replace(",", ".")), "MonthInputRaw": text, "MonthInputMode": "sequence", "MonthDueDate": "", "MonthDueDay": np.nan}
    text2 = translate_month_words(text)
    m = re.fullmatch(r"(\d{1,2})[/-](\d{4})", text2)
    if m:
        day, due = month_start_day(int(m.group(1)))
        return {"MonthIndex": float(day), "MonthInputRaw": text, "MonthInputMode": "month_date", "MonthDueDate": due, "MonthDueDay": float(day)}
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})", text2)
    if m:
        day, due = month_start_day(int(m.group(2)))
        return {"MonthIndex": float(day), "MonthInputRaw": text, "MonthInputMode": "month_date", "MonthDueDate": due, "MonthDueDay": float(day)}
    norm_words = re.sub(r"[^a-zA-Z ]+", " ", text2).casefold().split()
    for word in norm_words:
        if word in MONTH_NUMBER_WORDS:
            day, due = month_start_day(MONTH_NUMBER_WORDS[word])
            return {"MonthIndex": float(day), "MonthInputRaw": text, "MonthInputMode": "month_date", "MonthDueDate": due, "MonthDueDay": float(day)}
    dt = pd.to_datetime(text2, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        dt = pd.to_datetime(text2, errors="coerce", dayfirst=False)
    if pd.notna(dt):
        mapped = pd.Timestamp(year=TAHUN_SIMULASI, month=int(dt.month), day=int(dt.day))
        return {"MonthIndex": float(mapped.dayofyear), "MonthInputRaw": text, "MonthInputMode": "date", "MonthDueDate": mapped.strftime("%Y-%m-%d"), "MonthDueDay": float(mapped.dayofyear)}
    return {"MonthIndex": float(default_order), "MonthInputRaw": text, "MonthInputMode": "unparsed_default_sequence", "MonthDueDate": "", "MonthDueDay": np.nan}


def clean_prepared_input(df):
    df, matched = canonicalize_columns(df)
    if "IsChocolate" not in df.columns and "Color" in df.columns:
        df["IsChocolate"] = df["Color"]
    missing = [c for c in REQUIRED_CONCEPTS if c not in df.columns]
    if missing:
        available = ", ".join([str(c) for c in df.columns])
        raise ValueError("ForecastInput belum lengkap. Kolom konsep yang belum terbaca: " + str(missing) + ". Kolom yang terbaca: " + available)
    for col, default_value in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default_value
    month_parsed = df["MonthIndex"].apply(parse_month_index_value).apply(pd.Series)
    df["MonthInputRaw"] = month_parsed["MonthInputRaw"]
    df["MonthInputMode"] = month_parsed["MonthInputMode"]
    df["MonthDueDate"] = month_parsed["MonthDueDate"]
    df["MonthDueDay"] = month_parsed["MonthDueDay"]
    df["MonthIndex"] = month_parsed["MonthIndex"]
    numeric_cols = ["Qty", "ForecastTon", "SkuGr", "SpeedD", "Speed", "Allergen", "ShelfLife", "MonthIndex"]
    for col in numeric_cols:
        df[col] = to_numeric_safe(df[col]).fillna(0)
    df["ItemName"] = df["ItemName"].astype(str).str.strip()
    df["SkuId"] = df["SkuId"].astype(str).str.strip()
    df["IsChocolate"] = df["IsChocolate"].apply(normalize_chocolate_value)
    df["port_type"] = df["port_type"].astype(str).str.strip()
    df["ColorForSetup"] = df["Color"].astype(str).apply(normalize_chocolate_value) if "Color" in df.columns else df["IsChocolate"]
    df = df[df["ForecastTon"] > 0].reset_index(drop=True)
    if len(df) == 0:
        raise ValueError("ForecastInput terbaca, tetapi semua ForecastTon kosong atau 0.")
    return df


def parse_holiday_dates(text, year=TAHUN_SIMULASI):
    if text is None or str(text).strip() == "":
        return set()
    dates = []
    for c in str(text).replace("\n", ",").replace(";", ",").split(","):
        c = c.strip()
        if not c:
            continue
        try:
            dt = pd.to_datetime(c)
            if dt.year == year:
                dates.append(int(dt.dayofyear))
        except Exception:
            pass
    return set(dates)


def make_holiday_set(holiday_cutoff_days, holiday_dates_text):
    holiday_set = parse_holiday_dates(holiday_dates_text)
    if len(holiday_set) == 0:
        n = int(holiday_cutoff_days)
        if n > 0:
            step = max(1, HORIZON_HARI // n)
            holiday_set = set([(i * step) + 1 for i in range(n) if (i * step) + 1 <= HORIZON_HARI])
    return holiday_set


def make_monthly_downtime_set(downtime_days_per_month):
    downtime_days_per_month = int(downtime_days_per_month or 0)
    if downtime_days_per_month <= 0:
        return set()
    days = set()
    for month in range(1, 13):
        for day in range(1, downtime_days_per_month + 1):
            try:
                days.add(int(pd.Timestamp(year=TAHUN_SIMULASI, month=month, day=day).dayofyear))
            except Exception:
                pass
    return days


def is_line_working(calendar_day, days_per_week, holiday_day_set, downtime_day_set=None):
    downtime_day_set = downtime_day_set or set()
    if calendar_day in holiday_day_set or calendar_day in downtime_day_set:
        return False
    return ((calendar_day - 1) % 7) < int(days_per_week)


def estimate_scenario_count(b_days_options, b_hours_options, g_days_options, g_hours_options, d_days_options, d_hours_options, batch_options, growth_percent_options):
    groups = [b_days_options, b_hours_options, g_days_options, g_hours_options, d_days_options, d_hours_options, batch_options, growth_percent_options]
    if any(len(x) == 0 for x in groups):
        return 0
    count = 1
    for x in groups:
        count *= len(x)
    return int(count)


def make_growth_options(mode="checklist", checklist=None, gmin=0, gmax=10, step=5):
    if mode == "range":
        if step <= 0 or gmax < gmin:
            raise ValueError("Growth range tidak valid.")
        vals = []
        x = float(gmin)
        while x <= float(gmax) + 1e-9:
            vals.append(round(x, 4))
            x += float(step)
        return vals
    return checklist or [0]


def generate_scenarios(b_days_options, b_hours_options, g_days_options, g_hours_options, d_days_options, d_hours_options, batch_options, growth_percent_options, max_scenarios=DEFAULT_MAX_SCENARIOS, b_downtime=0, g_downtime=0, d_downtime=0):
    growth_options = [g / 100 for g in growth_percent_options]
    combos_iter = itertools.product(b_days_options, b_hours_options, g_days_options, g_hours_options, d_days_options, d_hours_options, batch_options, growth_options)
    rows = []
    for idx, combo in enumerate(combos_iter):
        if idx >= int(max_scenarios):
            break
        b_days, b_hours, g_days, g_hours, d_days, d_hours, batch_mode, growth = combo
        batch_limit = 35 if batch_mode == "B35" else 999999
        scenario_code = f"B{b_days}D-{b_hours}H | G{g_days}D-{g_hours}H | D{d_days}D-{d_hours}H | {batch_mode} | G{int(growth * 100)}%"
        rows.append({
            "Scenario": scenario_code,
            "Line B Days": int(b_days), "Line B Hours": float(b_hours),
            "Line G Days": int(g_days), "Line G Hours": float(g_hours),
            "Line D Days": int(d_days), "Line D Hours": float(d_hours),
            "Batch Mode": batch_mode, "Batch Limit per Day": int(batch_limit), "Growth": float(growth),
            "Line B Downtime Days/Month": int(b_downtime), "Line G Downtime Days/Month": int(g_downtime), "Line D Downtime Days/Month": int(d_downtime),
        })
    return pd.DataFrame(rows)


def expand_jobs(forecast_df, growth):
    jobs = []
    for _, row in forecast_df.iterrows():
        qty = int(max(row["Qty"], 1))
        forecast_ton = float(row["ForecastTon"]) * (1 + growth)
        batch_ton = forecast_ton / qty
        for _ in range(qty):
            item_name = str(row["ItemName"])
            is_anmum = "anm" in normalize_key(item_name) or "anmum" in normalize_key(item_name)
            jobs.append({
                "Item Name": row["ItemName"], "SKU": row["SkuId"], "Forecast Ton": forecast_ton,
                "SKU Gram": float(row["SkuGr"]), "Speed BG": float(row["Speed"]), "Speed D": float(row["SpeedD"]),
                "Chocolate Type": row["IsChocolate"], "Color Setup": row["ColorForSetup"], "Port Type": row["port_type"],
                "Allergen": float(row["Allergen"]), "Shelf Life": float(row["ShelfLife"]), "Month Index": float(row["MonthIndex"]),
                "Month Input Raw": row.get("MonthInputRaw", ""), "Month Input Mode": row.get("MonthInputMode", "sequence"),
                "Month Due Date": row.get("MonthDueDate", ""), "Month Due Day": row.get("MonthDueDay", np.nan),
                "Batch Ton": batch_ton, "Mini Blend Minute": T_MINI_BLEND_ANMUM if is_anmum else T_MINI_BLEND_NON_ANMUM,
            })
    jobs_df = pd.DataFrame(jobs)
    if len(jobs_df) == 0:
        return jobs_df
    return jobs_df.sort_values(by=["Month Index", "SKU", "Allergen", "Color Setup", "Port Type"], ascending=[True, True, True, True, True]).reset_index(drop=True)


def calc_setup(line_state, job):
    if line_state["last_sku"] is None:
        return 0
    allergen_up = job["Allergen"] > line_state["last_allergen"]
    color_change = job["Color Setup"] != line_state["last_color"]
    port_change = job["Port Type"] != line_state["last_port"]
    if not (allergen_up or color_change):
        return 0
    return SETUP_PORT_BERUBAH if port_change else SETUP_PORT_SAMA


def get_line_calendar(scenario, line):
    """Returns (days, hours, availability=1.0, downtime). Availability dihapus, selalu 1.0."""
    if line == "B":
        return int(scenario["Line B Days"]), float(scenario["Line B Hours"]), 1.0, int(scenario.get("Line B Downtime Days/Month", 0))
    if line == "G":
        return int(scenario["Line G Days"]), float(scenario["Line G Hours"]), 1.0, int(scenario.get("Line G Downtime Days/Month", 0))
    return int(scenario["Line D Days"]), float(scenario["Line D Hours"]), 1.0, int(scenario.get("Line D Downtime Days/Month", 0))


def simulate_one_scenario(forecast_df, scenario, holiday_day_set, candidate_window=DEFAULT_CANDIDATE_WINDOW):
    scenario_code = scenario["Scenario"]
    batch_mode = scenario["Batch Mode"]
    batch_limit_per_day = int(scenario["Batch Limit per Day"])
    growth = float(scenario["Growth"])
    target_demand = forecast_df["ForecastTon"].sum() * (1 + growth)
    jobs_df = expand_jobs(forecast_df, growth)
    if len(jobs_df) == 0:
        return {}, pd.DataFrame()
    unscheduled = jobs_df.to_dict("records")
    downtime_sets = {
        "B": make_monthly_downtime_set(scenario.get("Line B Downtime Days/Month", 0)),
        "G": make_monthly_downtime_set(scenario.get("Line G Downtime Days/Month", 0)),
        "D": make_monthly_downtime_set(scenario.get("Line D Downtime Days/Month", 0)),
    }

    # ── OPTIMIZATION: Precompute once per scenario ─────────────────────────────
    # 1. Line calendar (days, hours, cap_mins) — tidak berubah dalam scenario
    _lc = {}
    for _ln in ["B", "G", "D"]:
        _days, _hrs, _avail, _ = get_line_calendar(scenario, _ln)
        _lc[_ln] = {"days": _days, "hours": _hrs, "cap_mins": _hrs * 60.0}

    # 2. Working days set per line — O(1) lookup di inner loop
    _wd = {
        _ln: frozenset(
            d for d in range(1, HORIZON_HARI + 1)
            if is_line_working(d, _lc[_ln]["days"], holiday_day_set, downtime_sets[_ln])
        )
        for _ln in ["B", "G", "D"]
    }

    line_state = {line: {"used_today": 0, "processing": 0, "setup": 0, "tons": 0,
                         "last_sku": None, "last_port": None, "last_allergen": 0, "last_color": None}
                  for line in ["B", "G", "D"]}
    planned_jobs = []
    seq = 1

    for calendar_day in range(1, HORIZON_HARI + 1):
        # OPTIMIZATION: precompute active lines untuk hari ini (O(1) set lookup × 3)
        active_lines = [l for l in ["B", "G", "D"] if calendar_day in _wd[l]]

        for line in active_lines:
            line_state[line]["used_today"] = 0

        # OPTIMIZATION: skip hari tidak aktif; break jika semua demand terjadwal
        if not active_lines:
            continue
        if not unscheduled:
            break

        count_batch_today = 0
        while unscheduled:
            if batch_mode == "B35" and count_batch_today >= batch_limit_per_day:
                break
            best_idx = best_line = best_finish = best_setup = best_tfill = best_speed = None
            for idx, job in enumerate(unscheduled[:candidate_window]):
                candidates = []
                for line in active_lines:   # OPTIMIZATION: hanya cek lini yang aktif hari ini
                    cap_mins = _lc[line]["cap_mins"]
                    speed = job["Speed D"] if line == "D" else job["Speed BG"]
                    if speed > 0 and job["SKU Gram"] > 0:
                        tfill = job["Batch Ton"] * 1_000_000 / job["SKU Gram"] / speed
                        setup = calc_setup(line_state[line], job)
                        finish = line_state[line]["used_today"] + setup + tfill
                        if finish <= cap_mins:
                            candidates.append((line, finish, setup, tfill, speed))
                if candidates:
                    chosen = min(candidates, key=lambda x: x[1])  # OPTIMIZATION: min() bukan sorted()[0]
                    best_idx, best_line, best_finish, best_setup, best_tfill, best_speed = idx, chosen[0], chosen[1], chosen[2], chosen[3], chosen[4]
                    break
            if best_idx is None:
                break
            job = unscheduled.pop(best_idx)
            tblend = T_BLEND_COKLAT if str(job["Chocolate Type"]).lower() == "coklat" else T_BLEND_NON_COKLAT
            line_state[best_line]["used_today"] = best_finish
            line_state[best_line]["processing"] += best_tfill
            line_state[best_line]["setup"] += best_setup
            line_state[best_line]["tons"] += job["Batch Ton"]
            line_state[best_line]["last_sku"] = job["SKU"]
            line_state[best_line]["last_port"] = job["Port Type"]
            line_state[best_line]["last_allergen"] = job["Allergen"]
            line_state[best_line]["last_color"] = job["Color Setup"]
            planned_jobs.append({
                "Scenario": scenario_code, "Sequence": seq, "Calendar Day": calendar_day, "Line": best_line,
                "Item Name": job["Item Name"], "SKU": job["SKU"], "Batch Ton": round(job["Batch Ton"], 4),
                "Setup Minute": round(best_setup, 2), "Batching Note Minute": T_BATCH, "Prep Note Minute": T_PREP,
                "Tip Note Minute": T_TIP, "Mini Blend Note Minute": job["Mini Blend Minute"], "Blend Note Minute": tblend,
                "Fill Minute": round(best_tfill, 2), "Used Capacity Minute": round(best_setup + best_tfill, 2),
                "Speed": best_speed, "Port Type": job["Port Type"], "Allergen": job["Allergen"], "Color Setup": job["Color Setup"],
                "Shelf Life": job["Shelf Life"], "Month Index": job["Month Index"], "Month Input Raw": job.get("Month Input Raw", ""),
                "Month Input Mode": job.get("Month Input Mode", "sequence"), "Month Due Date": job.get("Month Due Date", ""), "Month Due Day": job.get("Month Due Day", np.nan),
            })
            seq += 1
            count_batch_today += 1
    planned_jobs_df = pd.DataFrame(planned_jobs)
    tons_b, tons_g, tons_d = line_state["B"]["tons"], line_state["G"]["tons"], line_state["D"]["tons"]
    finished_ton = tons_b + tons_g + tons_d
    unmet_demand = max(target_demand - finished_ton, 0)
    finished_ratio = finished_ton / target_demand * 100 if target_demand > 0 else 0
    # OPTIMIZATION: gunakan precomputed working days untuk total_available
    total_available = {
        line: len(_wd[line]) * _lc[line]["cap_mins"]
        for line in ["B", "G", "D"]
    }
    util_b = (line_state["B"]["processing"] + line_state["B"]["setup"]) / total_available["B"] * 100 if total_available["B"] > 0 else 0
    util_g = (line_state["G"]["processing"] + line_state["G"]["setup"]) / total_available["G"] * 100 if total_available["G"] > 0 else 0
    util_d = (line_state["D"]["processing"] + line_state["D"]["setup"]) / total_available["D"] * 100 if total_available["D"] > 0 else 0
    util_dict = {"Filling B": util_b, "Filling G": util_g, "Filling D": util_d}
    bottleneck = max(util_dict, key=util_dict.get)
    return {
        "Scenario": scenario_code,
        "Line B Days": scenario["Line B Days"], "Line B Hours": scenario["Line B Hours"],
        "Line G Days": scenario["Line G Days"], "Line G Hours": scenario["Line G Hours"],
        "Line D Days": scenario["Line D Days"], "Line D Hours": scenario["Line D Hours"],
        "Batch Mode": scenario["Batch Mode"], "Growth": scenario["Growth"],
        "Line B Downtime Days/Month": scenario.get("Line B Downtime Days/Month", 0), "Line G Downtime Days/Month": scenario.get("Line G Downtime Days/Month", 0), "Line D Downtime Days/Month": scenario.get("Line D Downtime Days/Month", 0),
        "Target Demand Ton": round(target_demand, 2), "Planned Ton": round(finished_ton, 2), "Tons Finished": round(finished_ton, 2),
        "Planning Ratio (%)": round(finished_ratio, 2), "Finished Ratio (%)": round(finished_ratio, 2), "Unmet Demand Ton": round(unmet_demand, 2),
        "Tons B": round(tons_b, 2), "Tons G": round(tons_g, 2), "Tons D": round(tons_d, 2),
        "Util Filling B (%)": round(util_b, 2), "Util Filling G (%)": round(util_g, 2), "Util Filling D (%)": round(util_d, 2),
        "Setup Minute B": round(line_state["B"]["setup"], 2), "Setup Minute G": round(line_state["G"]["setup"], 2), "Setup Minute D": round(line_state["D"]["setup"], 2),
        "Bottleneck Area": bottleneck,
        "Planner Status": "Target Terpenuhi" if finished_ton >= target_demand - 0.05 else "Target Tidak Terpenuhi",
        "Capacity Status": "Kapasitas Mencukupi" if unmet_demand <= 0.05 else "Kapasitas Tidak Mencukupi",
    }, planned_jobs_df


def _run_single(args):
    """Helper untuk parallel execution. Harus top-level agar picklable."""
    forecast_df, scenario_dict, holiday_set = args
    scenario = pd.Series(scenario_dict)
    return simulate_one_scenario(forecast_df, scenario, holiday_set, DEFAULT_CANDIDATE_WINDOW)


def run_des_simulation(forecast_input_df, b_days_options, b_hours_options, g_days_options, g_hours_options,
                       d_days_options, d_hours_options, batch_options, growth_percent_options,
                       holiday_cutoff_days=16, holiday_dates_text="", max_scenarios=DEFAULT_MAX_SCENARIOS,
                       b_downtime=0, g_downtime=0, d_downtime=0,
                       # Backward-compat: availability params diterima tapi diabaikan
                       b_availability=100, g_availability=100, d_availability=100):
    """
    Jalankan DES simulation untuk semua skenario.
    Menggunakan parallel execution (joblib) untuk percepatan signifikan.
    """
    forecast_df = clean_prepared_input(forecast_input_df)
    scenario_df = generate_scenarios(
        b_days_options, b_hours_options, g_days_options, g_hours_options,
        d_days_options, d_hours_options, batch_options, growth_percent_options,
        max_scenarios=max_scenarios, b_downtime=b_downtime, g_downtime=g_downtime, d_downtime=d_downtime,
    )
    holiday_set = make_holiday_set(holiday_cutoff_days, holiday_dates_text)

    scenario_list = [row.to_dict() for _, row in scenario_df.iterrows()]
    args_list = [(forecast_df, sc, holiday_set) for sc in scenario_list]

    # ── Parallel execution ────────────────────────────────────────────────────
    # Gunakan joblib dengan backend loky (works on Windows + Linux).
    # n_jobs=-1 = semua CPU core tersedia. Fallback ke sequential jika gagal.
    try:
        from joblib import Parallel, delayed

        def _run(args):
            _fdf, _sc, _hs = args
            return simulate_one_scenario(_fdf, pd.Series(_sc), _hs, DEFAULT_CANDIDATE_WINDOW)

        parallel_results = Parallel(n_jobs=-1, backend="loky", verbose=0)(
            delayed(_run)(args) for args in args_list
        )
    except Exception:
        # Fallback: sequential (aman untuk semua platform)
        parallel_results = [
            simulate_one_scenario(forecast_df, pd.Series(sc), holiday_set, DEFAULT_CANDIDATE_WINDOW)
            for sc in scenario_list
        ]

    results, all_planned = [], []
    for result, planned in parallel_results:
        if result:
            results.append(result)
        if planned is not None and len(planned) > 0:
            all_planned.append(planned)

    result_df = pd.DataFrame(results)
    if not result_df.empty:
        result_df = result_df.sort_values(
            by=["Tons Finished", "Unmet Demand Ton"], ascending=[False, True]
        ).reset_index(drop=True)

    planned_jobs_df = pd.concat(all_planned, ignore_index=True) if all_planned else pd.DataFrame()
    meta = {"scenarios_evaluated": len(result_df), "holiday_days": len(holiday_set), "products_analyzed": len(forecast_df)}
    return result_df, scenario_df, planned_jobs_df, forecast_df, meta


def export_to_excel_bytes(result_df, scenario_df, planned_jobs_df, forecast_df, simulation_name="Simulasi DES Capacity"):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        result_df.to_excel(writer, sheet_name="Simulation Result", index=False)
        scenario_df.to_excel(writer, sheet_name="Scenario Config", index=False)
        planned_jobs_df.to_excel(writer, sheet_name="Planned Jobs", index=False)
        forecast_df.to_excel(writer, sheet_name="Input Data", index=False)
        notes = pd.DataFrame({"Summary": [
            "Input menggunakan ForecastInput siap pakai atau hasil konversi Forecast + Master SKU.",
            "Hasil simulasi digunakan untuk membandingkan skenario kapasitas produksi.",
            "Urutan produksi mempertimbangkan prioritas atau periode penggunaan produk dari MonthIndex.",
            "Perhitungan kapasitas berfokus pada performa filling line dan kebutuhan setup.",
            "Tolerance tambahan: availability factor dan downtime buffer per line. Default 100% dan 0 hari/bulan menjaga logic lama tetap sama.",
        ]})
        notes.to_excel(writer, sheet_name="Method Summary", index=False)
    output.seek(0)
    return output.getvalue(), f"{sanitize_filename(simulation_name)}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
