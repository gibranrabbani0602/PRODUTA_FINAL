import itertools
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from io import BytesIO, StringIO

import numpy as np
import pandas as pd
from modules.des_input_builder import (
    ensure_sku_alias,
)
from modules.inventory_backlog import (
    build_inventory_backlog_table,
)
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

MIN_REMAINING_SHELF_MONTHS = 3

SETUP_RESERVE_PER_LOT_MINUTE = max(
    SETUP_PORT_BERUBAH,
    SETUP_PORT_SAMA,
)

FIXED_LOT_TON = 1.0
LOT_ROUNDING_EPSILON = 1e-9

DEFAULT_MAX_SCENARIOS = 100
DEFAULT_CANDIDATE_WINDOW = 60
DEFAULT_PLANNED_PREVIEW_ROWS = 5000

WEEKDAY_LABELS = [
    "Senin",
    "Selasa",
    "Rabu",
    "Kamis",
    "Jumat",
    "Sabtu",
    "Minggu",
]


# Kondisi operasi aktual yang digunakan untuk initialization.
# Urutan: Senin sampai Minggu.
INITIALIZATION_WEEKLY_HOURS = {
    "B": [16, 24, 24, 24, 24, 16, 16],
    "G": [16, 16, 16, 16, 16, 16, 16],
    "D": [16, 24, 24, 24, 24, 16, 16],
}


# Kondisi aktual bila ingin diuji kembali
# pada periode evaluation.
ACTUAL_WEEKLY_HOURS = {
    "B": [16, 24, 24, 24, 24, 16, 16],
    "G": [16, 16, 16, 16, 16, 16, 16],
    "D": [16, 24, 24, 24, 24, 16, 16],
}


# Usulan pola operasi manajemen.
# B dan G: Senin-Sabtu 16 jam, Minggu libur.
# D: setiap hari 24 jam.
MANAGEMENT_WEEKLY_HOURS = {
    "B": [16, 16, 16, 16, 16, 16, 0],
    "G": [16, 16, 16, 16, 16, 16, 0],
    "D": [24, 24, 24, 24, 24, 24, 24],
}


def validate_weekly_hours(profile):
    cleaned = {}

    for line in ["B", "G", "D"]:
        values = profile.get(line, [])

        if len(values) != 7:
            raise ValueError(
                f"Jadwal Line {line} harus berisi "
                "7 nilai, dari Senin sampai Minggu."
            )

        cleaned[line] = [
            min(max(float(value), 0.0), 24.0)
            for value in values
        ]

    return cleaned


def get_daily_line_hours(profile, line, calendar_date):
    profile = validate_weekly_hours(profile)

    weekday_index = pd.Timestamp(
        calendar_date
    ).weekday()

    return float(
        profile[line][weekday_index]
    )


def weekly_hours_from_days_and_hours(
    days_per_week,
    hours_per_day,
):
    days = min(max(int(days_per_week), 0), 7)
    hours = min(max(float(hours_per_day), 0.0), 24.0)

    return [
        hours if weekday_index < days else 0.0
        for weekday_index in range(7)
    ]


def get_evaluation_weekly_hours(scenario):
    """
    Mengambil pola jam kerja untuk periode evaluation.

    Untuk sementara, jika UI belum mengirim nama preset,
    pola dibentuk dari pilihan hari/minggu dan jam/hari
    yang sudah tersedia. Ini menjaga kompatibilitas
    dengan tampilan lama.
    """
    mode = str(
        scenario.get(
            "Evaluation Schedule Mode",
            "legacy",
        )
    ).strip().lower()

    if mode == "actual":
        return validate_weekly_hours(
            ACTUAL_WEEKLY_HOURS
        )

    if mode == "management":
        return validate_weekly_hours(
            MANAGEMENT_WEEKLY_HOURS
        )

    if mode == "custom":
        custom_profile = scenario.get(
            "Evaluation Weekly Hours",
            None,
        )

        if isinstance(custom_profile, dict):
            return validate_weekly_hours(
                custom_profile
            )

    return validate_weekly_hours({
        "B": weekly_hours_from_days_and_hours(
            scenario["Line B Days"],
            scenario["Line B Hours"],
        ),
        "G": weekly_hours_from_days_and_hours(
            scenario["Line G Days"],
            scenario["Line G Hours"],
        ),
        "D": weekly_hours_from_days_and_hours(
            scenario["Line D Days"],
            scenario["Line D Hours"],
        ),
    })


def get_evaluation_calendar_start(forecast_df):
    """
    Periode evaluation dimulai pada awal bulan tempat
    due date pertama evaluation berada.

    Contoh:
    demand April 2026 jatuh tempo 31 Maret 2026,
    sehingga kalender evaluation dimulai 1 Maret 2026.
    """
    evaluation_rows = forecast_df[
        forecast_df["DataRole"]
        .astype(str)
        .str.lower()
        .eq("evaluation")
    ].copy()

    if evaluation_rows.empty:
        raise ValueError(
            "Periode evaluation belum tersedia."
        )

    due_dates = pd.to_datetime(
        evaluation_rows["MonthDueDate"],
        errors="coerce",
    ).dropna()

    if due_dates.empty:
        raise ValueError(
            "Due date periode evaluation tidak dapat dibaca."
        )

    return (
        due_dates.min()
        .to_period("M")
        .to_timestamp()
        .normalize()
    )

REQUIRED_CONCEPTS = [
    "ItemName", "SkuId", "ForecastTon", "SkuGr", "SpeedD", "Speed",
    "IsChocolate", "port_type", "Allergen", "ShelfLife",
]
OPTIONAL_DEFAULTS = {"Qty": 1, "MonthIndex": 1, "SKU_Alias": "", "DataRole": "evaluation"}

COLUMN_ALIASES = {
    "ItemName": ["itemname", "item name", "nama sku", "namasku", "description", "deskripsi", "product", "product name", "namaproduk", "nama produk", "catatan sku", "catatansku"],
    "Qty": ["qty", "quantity", "jumlah", "jumlah batch", "jumlahbatch", "batch", "batches", "lot", "jumlah lot"],
    "SkuId": ["skuid", "sku id", "sku", "kode sku", "kodesku", "item code", "itemcode", "material", "material code"],
    "SKU_Alias": [
        "sku alias",
        "sku_alias",
        "alias sku",
        "kode anonim",
        "kode samaran",
    ],

    "DataRole": [
        "data role",
        "data_role",
        "role",
        "period role",
        "jenis periode",
    ],
    "ForecastTon": ["forecastton", "forecast ton", "forecast", "demand", "demand ton", "target demand ton", "targetdemandton", "ton", "tons", "tonase", "tonnage", "planned ton", "plannedton"],
    "SkuGr": ["skugr", "sku gr", "sku gram", "skugram", "gram", "gramasi", "grammage", "pack size", "packsize", "ukuran gram"],
    "SpeedD": ["speedd", "speed d", "speed line d", "speedlined", "ppm d", "ppmd", "line d speed"],
    "Speed": ["speed", "speed bg", "speed b g", "speed b/g", "speed line b g", "speed ppm", "ppm", "speed (ppm)", "speedb", "speedg", "speed b", "speed g"],
    "IsChocolate": ["ischocolate", "is chocolate", "chocolate", "coklat", "jenis coklat", "type coklat", "color", "colour", "warna", "colorsetup", "color setup"],
    "port_type": ["port_type", "port type", "port", "tipe port", "tipeport", "jenis port", "jenisport"],
    "Allergen": ["allergen", "alergen", "allergen level", "level allergen", "kode allergen", "kodealergen"],
    "ShelfLife": ["shelflife", "shelf life", "expired", "expiry", "umur simpan", "umursimpan", "masa simpan"],
    "Date": ["date","ds","tanggal","forecast date","forecastdate","period date","perioddate"],
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

def parse_exact_date_value(value):
    """
    Membaca tanggal tanpa memaksa tahun tertentu.

    Contoh yang dapat dibaca:
    - 2026-04-01
    - 01/04/2026
    - 2026-04
    - 04/2026
    - April 2026
    """
    if value is None or pd.isna(value):
        return pd.NaT

    if isinstance(value, (pd.Timestamp, datetime, np.datetime64)):
        return pd.Timestamp(value).normalize()

    # Angka 1, 2, 3, ... bukan tanggal.
    if isinstance(value, (int, float, np.integer, np.floating)):
        return pd.NaT

    text = translate_month_words(value)

    if text in ["", "nan", "none", "null", "-"]:
        return pd.NaT

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m",
        "%Y/%m",
        "%m-%Y",
        "%m/%Y",
        "%B %Y",
        "%b %Y",
    ]

    for fmt in formats:
        try:
            return pd.Timestamp(
                datetime.strptime(text.title(), fmt)
            ).normalize()
        except ValueError:
            pass

    # Jalan terakhir jika format tanggal tidak termasuk daftar di atas.
    return pd.to_datetime(
        text,
        errors="coerce",
        dayfirst=False,
    )


def parse_period_date_value(value):
    """
    Mengubah tanggal menjadi tanggal pertama pada bulan tersebut.

    Contoh:
    15 April 2026 -> 1 April 2026
    """
    dt = parse_exact_date_value(value)

    if pd.isna(dt):
        return pd.NaT

    return pd.Timestamp(
        year=int(dt.year),
        month=int(dt.month),
        day=1,
    )


def build_simulation_calendar(forecast_df):
    """
    Membentuk kalender simulasi.

    Kalender dimulai dari awal bulan tempat due date
    pertama berada. Dengan demikian kebutuhan bulan
    pertama tetap mempunyai waktu untuk diproduksi
    sebelum tanggal jatuh temponya.
    """
    if "Date" not in forecast_df.columns:
        raise ValueError(
            "Kolom Date belum tersedia sehingga kalender "
            "simulasi tidak dapat dibentuk."
        )

    if forecast_df["Date"].isna().any():
        raise ValueError(
            "Sebagian tanggal periode kosong atau "
            "tidak dapat dibaca."
        )

    due_dates = pd.Series(dtype="datetime64[ns]")

    if "MonthDueDate" in forecast_df.columns:
        due_dates = (
            pd.to_datetime(
                forecast_df["MonthDueDate"],
                errors="coerce",
            )
            .dropna()
        )

    if not due_dates.empty:
        simulation_start = (
            due_dates.min()
            .to_period("M")
            .to_timestamp()
        )
    else:
        simulation_start = pd.Timestamp(
            forecast_df["Date"].min()
        ).normalize()

    last_period = pd.Timestamp(
        forecast_df["Date"].max()
    ).normalize()

    simulation_end = (
        last_period
        + pd.offsets.MonthEnd(1)
    )

    calendar_dates = pd.date_range(
        start=simulation_start,
        end=simulation_end,
        freq="D",
    )

    return (
        simulation_start,
        simulation_end,
        calendar_dates,
    )



def clean_prepared_input(df):
    df, matched = canonicalize_columns(df)

    if "IsChocolate" not in df.columns and "Color" in df.columns:
        df["IsChocolate"] = df["Color"]

    missing = [
        c for c in REQUIRED_CONCEPTS
        if c not in df.columns
    ]

    if missing:
        available = ", ".join(
            [str(c) for c in df.columns]
        )
        raise ValueError(
            "ForecastInput belum lengkap. "
            "Kolom konsep yang belum terbaca: "
            + str(missing)
            + ". Kolom yang terbaca: "
            + available
        )

    for col, default_value in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default_value

    # --------------------------------------------------
    # MEMBACA PERIODE
    # --------------------------------------------------
    # Jalur 1:
    # Input hasil Demand & Forecasting memiliki kolom Date.
    #
    # Jalur 2:
    # File lama dapat menyimpan tanggal pada kolom MonthIndex.
    if "Date" in df.columns:
        period_raw = df["Date"].copy()
        period_source = "Date"
    else:
        period_raw = df["MonthIndex"].copy()
        period_source = "MonthIndex"

    df["MonthInputRaw"] = period_raw.astype(str)

    df["Date"] = period_raw.apply(
        parse_period_date_value
    )

    if df["Date"].isna().any():
        bad_values = (
            period_raw[df["Date"].isna()]
            .astype(str)
            .drop_duplicates()
            .head(5)
            .tolist()
        )

        raise ValueError(
            "Tanggal periode tidak dapat dibaca dari kolom "
            f"{period_source}. Contoh nilai bermasalah: "
            + ", ".join(bad_values)
            + ". Input harus memiliki kolom Date atau "
              "MonthIndex yang berisi tanggal sebenarnya."
        )

    # Ambil daftar bulan yang unik dan urutkan.
    unique_months = pd.DatetimeIndex(
        sorted(df["Date"].drop_duplicates())
    )

    if len(unique_months) == 0:
        raise ValueError(
            "Tidak ada periode forecast yang dapat digunakan."
        )

    # Memastikan tidak ada bulan yang hilang di tengah horizon.
    expected_months = pd.date_range(
        start=unique_months.min(),
        end=unique_months.max(),
        freq="MS",
    )

    if not unique_months.equals(expected_months):
        actual_set = set(unique_months)
        missing_months = [
            d.strftime("%Y-%m")
            for d in expected_months
            if d not in actual_set
        ]

        raise ValueError(
            "Periode forecast tidak berurutan. "
            "Bulan yang belum tersedia: "
            + ", ".join(missing_months)
        )

    # April 2026 dapat menjadi 1,
    # Mei 2026 menjadi 2,
    # dan seterusnya sesuai input.
    month_map = {
        month: index + 1
        for index, month in enumerate(unique_months)
    }

    simulation_start = unique_months.min()

    df["MonthIndex"] = (
        df["Date"]
        .map(month_map)
        .astype(int)
    )

    df["MonthInputMode"] = "calendar_date"
    
    df["DemandMonth"] = (
        df["Date"]
        .dt.strftime("%Y-%m")
    )

    due_dates = (
        df["Date"]
        + pd.offsets.MonthEnd(0)
    ).dt.normalize()

    df["MonthDueDate"] = (
        due_dates
        .dt.strftime("%Y-%m-%d")
    )

    calendar_reference_start = (
        due_dates.min()
        .to_period("M")
        .to_timestamp()
    )
    
    df["MonthDueDay"] = (
        (
            due_dates
            - calendar_reference_start
        ).dt.days
        + 1
    ).astype(int)

   

    # --------------------------------------------------
    # MEMBERSIHKAN KOLOM NUMERIK
    # --------------------------------------------------
    numeric_cols = [
        "Qty",
        "ForecastTon",
        "SkuGr",
        "SpeedD",
        "Speed",
        "Allergen",
        "ShelfLife",
        "MonthIndex",
    ]

    for col in numeric_cols:
        df[col] = (
            to_numeric_safe(df[col])
            .fillna(0)
        )

    df["ItemName"] = (
        df["ItemName"]
        .astype(str)
        .str.strip()
    )

    df["SkuId"] = (
        df["SkuId"]
        .astype(str)
        .str.strip()
    )

    df["SKU_Alias"] = (
        df["SKU_Alias"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["DataRole"] = (
        df["DataRole"]
        .fillna("evaluation")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    role_aliases = {
        "forecast": "evaluation",
        "main": "evaluation",
        "evaluasi": "evaluation",
        "historical": "initialization",
        "history": "initialization",
        "historis": "initialization",
        "warmup": "initialization",
        "warm-up": "initialization",
    }

    df["DataRole"] = (
        df["DataRole"]
        .replace(role_aliases)
    )

    valid_roles = {
        "initialization",
        "evaluation",
    }

    invalid_roles = sorted(
        set(df["DataRole"].unique())
        - valid_roles
    )

    if invalid_roles:
        raise ValueError(
            "DataRole hanya boleh berisi "
            "'initialization' atau 'evaluation'. "
            "Nilai yang tidak dikenali: "
            + ", ".join(
                str(value)
                for value in invalid_roles
            )
        )

    df["IsChocolate"] = (
        df["IsChocolate"]
        .apply(normalize_chocolate_value)
    )

    df["port_type"] = (
        df["port_type"]
        .astype(str)
        .str.strip()
    )

    df = ensure_sku_alias(df)

    if "Color" in df.columns:
        df["ColorForSetup"] = (
            df["Color"]
            .astype(str)
            .apply(normalize_chocolate_value)
        )
    else:
        df["ColorForSetup"] = df["IsChocolate"]

    df = (
        df[df["ForecastTon"] > 0]
        .reset_index(drop=True)
    )

    if len(df) == 0:
        raise ValueError(
            "ForecastInput terbaca, tetapi semua "
            "ForecastTon kosong atau 0."
        )

    return df


def parse_holiday_dates(
    text,
    simulation_start,
    simulation_end,
):
    """
    Membaca daftar tanggal libur manual.

    Contoh input:
    2026-04-10, 2026-05-01, 2026-12-25
    """
    if text is None or str(text).strip() == "":
        return set()

    dates = set()
    outside_horizon = []

    values = (
        str(text)
        .replace("\n", ",")
        .replace(";", ",")
        .split(",")
    )

    for value in values:
        value = value.strip()

        if not value:
            continue

        dt = parse_exact_date_value(value)

        if pd.isna(dt):
            raise ValueError(
                f"Tanggal libur tidak dapat dibaca: {value}"
            )

        dt = pd.Timestamp(dt).normalize()

        if simulation_start <= dt <= simulation_end:
            dates.add(dt)
        else:
            outside_horizon.append(
                dt.strftime("%Y-%m-%d")
            )

    if outside_horizon:
        raise ValueError(
            "Tanggal libur berikut berada di luar horizon simulasi: "
            + ", ".join(outside_horizon)
        )

    return dates


def make_estimated_holiday_set(
    calendar_dates,
    holiday_count,
):
    """
    Membentuk estimasi hari tutup produksi secara deterministik.

    Hari tutup ditempatkan merata pada Senin-Jumat agar
    benar-benar menjadi tambahan kehilangan hari produksi,
    bukan bertumpuk dengan hari Minggu yang mungkin memang
    sudah tidak digunakan oleh beberapa lini.
    """
    n = int(holiday_count or 0)

    if n <= 0:
        return set()

    calendar_index = pd.DatetimeIndex(
        calendar_dates
    ).normalize()

    # Senin = 0 sampai Jumat = 4
    eligible_dates = calendar_index[
        calendar_index.weekday < 5
    ]

    if n > len(eligible_dates):
        raise ValueError(
            "Jumlah hari libur estimasi melebihi jumlah "
            "hari kerja yang tersedia dalam horizon."
        )

    # Mengambil titik tengah dari n bagian horizon.
    # Cara ini tidak otomatis memilih hari pertama.
    selected_indices = np.floor(
        (np.arange(n) + 0.5)
        * len(eligible_dates)
        / n
    ).astype(int)

    return {
        pd.Timestamp(
            eligible_dates[index]
        ).normalize()
        for index in selected_indices
    }


def make_holiday_set(
    calendar_dates,
    holiday_mode="none",
    holiday_cutoff_days=0,
    holiday_dates_text="",
):
    """
    Mode hari libur:
    - none      : tidak ada hari libur tambahan
    - manual    : memakai tanggal yang dimasukkan pengguna
    - estimated : membentuk estimasi berdasarkan jumlah hari
    """
    simulation_start = pd.Timestamp(
        calendar_dates[0]
    ).normalize()

    simulation_end = pd.Timestamp(
        calendar_dates[-1]
    ).normalize()

    mode = str(
        holiday_mode or "none"
    ).strip().lower()

    if mode == "none":
        return set()

    if mode == "manual":
        manual_dates = parse_holiday_dates(
            holiday_dates_text,
            simulation_start,
            simulation_end,
        )

        if not manual_dates:
            raise ValueError(
                "Mode tanggal libur manual dipilih, "
                "tetapi belum ada tanggal yang dimasukkan."
            )

        return manual_dates

    if mode == "estimated":
        return make_estimated_holiday_set(
            calendar_dates,
            holiday_cutoff_days,
        )

    raise ValueError(
        f"Mode hari libur tidak dikenali: {holiday_mode}"
    )    
   
def make_monthly_downtime_set(
    downtime_days_per_month,
    calendar_dates,
):
    n = int(downtime_days_per_month or 0)

    if n <= 0:
        return set()

    dates = pd.DatetimeIndex(
        calendar_dates
    ).normalize()

    periods = dates.to_period("M")
    downtime_dates = set()

    for period in periods.unique():
        month_dates = dates[periods == period]

        for dt in month_dates[:n]:
            downtime_dates.add(
                pd.Timestamp(dt).normalize()
            )

    return downtime_dates


def is_line_working(
    calendar_date,
    days_per_week,
    holiday_date_set,
    downtime_date_set=None,
):
    downtime_date_set = downtime_date_set or set()

    calendar_date = pd.Timestamp(
        calendar_date
    ).normalize()

    if (
        calendar_date in holiday_date_set
        or calendar_date in downtime_date_set
    ):
        return False

    # weekday():
    # Senin = 0
    # Selasa = 1
    # ...
    # Minggu = 6
    #
    # 5D = Senin-Jumat
    # 6D = Senin-Sabtu
    # 7D = setiap hari
    return calendar_date.weekday() < int(days_per_week)


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


def generate_scenarios(
    b_days_options,
    b_hours_options,
    g_days_options,
    g_hours_options,
    d_days_options,
    d_hours_options,
    batch_options,
    growth_percent_options,
    max_scenarios=DEFAULT_MAX_SCENARIOS,
    b_downtime=0,
    g_downtime=0,
    d_downtime=0,
):
    growth_options = [
        g / 100
        for g in growth_percent_options
    ]

    combos_iter = itertools.product(
        b_days_options,
        b_hours_options,
        g_days_options,
        g_hours_options,
        d_days_options,
        d_hours_options,
        batch_options,
        growth_options,
    )

    rows = []

    for idx, combo in enumerate(combos_iter):
        if idx >= int(max_scenarios):
            break

        (
            b_days,
            b_hours,
            g_days,
            g_hours,
            d_days,
            d_hours,
            batch_limit_input,
            growth,
        ) = combo

        batch_limit_input = int(batch_limit_input)

        batch_label = (
            "BLOSS"
            if batch_limit_input == 0
            else f"B{batch_limit_input}"
        )

        batch_limit = (
            999999
            if batch_limit_input == 0
            else batch_limit_input
        )

        scenario_code = (
            f"B{b_days}D-{b_hours}H | "
            f"G{g_days}D-{g_hours}H | "
            f"D{d_days}D-{d_hours}H | "
            f"{batch_label} | "
            f"G{int(growth * 100)}%"
        )

        rows.append({
            "Scenario": scenario_code,
            "Line B Days": int(b_days),
            "Line B Hours": float(b_hours),
            "Line G Days": int(g_days),
            "Line G Hours": float(g_hours),
            "Line D Days": int(d_days),
            "Line D Hours": float(d_hours),
            "Batch Mode": batch_label,
            "Batch Limit Input": batch_limit_input,
            "Batch Limit per Day": int(batch_limit),
            "Growth": float(growth),
            "Line B Downtime Days/Month": int(b_downtime),
            "Line G Downtime Days/Month": int(g_downtime),
            "Line D Downtime Days/Month": int(d_downtime),
        })

    return pd.DataFrame(rows)

def expand_jobs(forecast_df, growth):
    """
    Membentuk pekerjaan produksi dengan ukuran maksimum 1 ton.

    Setiap kebutuhan SKU-periode dipecah menjadi beberapa lot:
    - lot penuh berukuran 1 ton; dan
    - satu lot parsial terakhir bila masih ada sisa kebutuhan.

    Total tonase pekerjaan selalu sama dengan demand. Dengan demikian,
    initialization maupun evaluation tidak menciptakan stok hanya karena
    pembulatan lot. Pertumbuhan hanya diterapkan pada periode evaluation.
    """
    jobs = []

    working_df = forecast_df.copy()

    working_df["Base Demand Ton"] = (
        working_df["ForecastTon"]
        .astype(float)
        .clip(lower=0)
    )

    is_evaluation = (
        working_df["DataRole"]
        .astype(str)
        .str.lower()
        .eq("evaluation")
    )

    working_df["Growth Demand Ton"] = (
        working_df["Base Demand Ton"]
    )

    working_df.loc[
        is_evaluation,
        "Growth Demand Ton",
    ] = (
        working_df.loc[
            is_evaluation,
            "Base Demand Ton",
        ]
        * (1 + float(growth))
    )

    working_df = working_df.sort_values(
        by=[
            "SkuId",
            "Date",
            "MonthIndex",
        ],
        ascending=[
            True,
            True,
            True,
        ],
        kind="stable",
    ).reset_index(drop=True)

    grouped_skus = working_df.groupby(
        "SkuId",
        sort=True,
    )

    for sku_id, sku_df in grouped_skus:
        cumulative_demand_ton = 0.0
        cumulative_job_ton = 0.0
        lot_number = 0

        sku_df = sku_df.sort_values(
            by=[
                "Date",
                "MonthIndex",
            ],
            ascending=[
                True,
                True,
            ],
            kind="stable",
        )

        for _, row in sku_df.iterrows():
            monthly_demand_ton = max(
                float(row["Growth Demand Ton"]),
                0.0,
            )

            if monthly_demand_ton <= LOT_ROUNDING_EPSILON:
                continue

            cumulative_demand_ton += monthly_demand_ton

            due_date = pd.to_datetime(
                row.get(
                    "MonthDueDate",
                    "",
                ),
                errors="coerce",
            )

            if pd.isna(due_date):
                due_date = (
                    pd.Timestamp(row["Date"])
                    - pd.Timedelta(days=1)
                )

            due_date = pd.Timestamp(
                due_date
            ).normalize()

            shelf_life_months = int(
                np.floor(
                    max(
                        float(row["ShelfLife"]),
                        0.0,
                    )
                )
            )

            usable_age_months = max(
                shelf_life_months
                - MIN_REMAINING_SHELF_MONTHS,
                0,
            )

            virtual_production_date = (
                due_date
                - pd.Timedelta(days=1)
            )

            virtual_usable_until = (
                virtual_production_date
                + pd.DateOffset(
                    months=usable_age_months
                )
            ).normalize()

            item_name = str(
                row["ItemName"]
            )

            is_anmum = (
                "anm" in normalize_key(item_name)
                or "anmum" in normalize_key(item_name)
            )

            demand_remaining = monthly_demand_ton

            while demand_remaining > LOT_ROUNDING_EPSILON:
                lot_number += 1

                batch_ton = min(
                    FIXED_LOT_TON,
                    demand_remaining,
                )

                # Hindari residu floating point seperti
                # 0.3999999997 pada lot parsial terakhir.
                batch_ton = round(
                    float(batch_ton),
                    9,
                )

                demand_remaining = max(
                    demand_remaining - batch_ton,
                    0.0,
                )

                cumulative_job_ton += batch_ton

                jobs.append({
                    "Lot ID": (
                        f"{sku_id}-L{lot_number:04d}"
                    ),
                    "Lot Number": lot_number,

                    "Item Name": row["ItemName"],
                    "SKU": row["SkuId"],
                    "SKU Alias": row.get(
                        "SKU_Alias",
                        "",
                    ),
                    "Data Role": row.get(
                        "DataRole",
                        "evaluation",
                    ),

                    "Forecast Ton": monthly_demand_ton,
                    "Demand Month Ton": monthly_demand_ton,
                    "Cumulative Demand Ton": (
                        cumulative_demand_ton
                    ),
                    "Required Cumulative Lots": (
                        lot_number
                    ),

                    "Batch Ton": batch_ton,
                    "Lot Type": (
                        "Full"
                        if abs(
                            batch_ton - FIXED_LOT_TON
                        ) <= LOT_ROUNDING_EPSILON
                        else "Partial"
                    ),

                    "SKU Gram": float(
                        row["SkuGr"]
                    ),
                    "Speed BG": float(
                        row["Speed"]
                    ),
                    "Speed D": float(
                        row["SpeedD"]
                    ),

                    "Chocolate Type": (
                        row["IsChocolate"]
                    ),
                    "Color Setup": (
                        row["ColorForSetup"]
                    ),
                    "Port Type": (
                        row["port_type"]
                    ),
                    "Allergen": float(
                        row["Allergen"]
                    ),
                    "Shelf Life": float(
                        row["ShelfLife"]
                    ),

                    "Month Index": float(
                        row["MonthIndex"]
                    ),
                    "Month Input Raw": row.get(
                        "MonthInputRaw",
                        "",
                    ),
                    "Month Input Mode": row.get(
                        "MonthInputMode",
                        "calendar_date",
                    ),
                    "Month Due Date": row.get(
                        "MonthDueDate",
                        due_date.strftime(
                            "%Y-%m-%d"
                        ),
                    ),
                    "Month Due Day": row.get(
                        "MonthDueDay",
                        np.nan,
                    ),

                    "Virtual Usable Until": (
                        virtual_usable_until.strftime(
                            "%Y-%m-%d"
                        )
                    ),
                    "Lot Creation Reason": (
                        "Exact demand requirement"
                    ),

                    "Mini Blend Minute": (
                        T_MINI_BLEND_ANMUM
                        if is_anmum
                        else T_MINI_BLEND_NON_ANMUM
                    ),
                })

        if abs(
            cumulative_job_ton
            - cumulative_demand_ton
        ) > 1e-6:
            raise ValueError(
                "Total lot tidak sama dengan demand untuk SKU "
                + str(sku_id)
            )

    jobs_df = pd.DataFrame(jobs)

    if jobs_df.empty:
        return jobs_df

    return jobs_df.sort_values(
        by=[
            "Month Index",
            "SKU",
            "Lot Number",
            "Allergen",
            "Color Setup",
            "Port Type",
        ],
        ascending=[
            True,
            True,
            True,
            True,
            True,
            True,
        ],
        kind="stable",
    ).reset_index(drop=True)


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

def assign_capacity_release_dates(
    jobs_df,
    calendar_dates,
    working_days_by_line,
    daily_capacity_minutes,
    evaluation_start_date,
):
    """
    Menentukan kapan suatu kelompok lot mulai boleh
    masuk antrean produksi.

    Tanggal dihitung mundur dari due date berdasarkan:
    1. estimasi kebutuhan menit filling dan setup;
    2. kalender kerja harian masing-masing lini;
    3. kompatibilitas SKU terhadap lini;
    4. batas minimum sisa shelf life tiga bulan;
    5. batas awal periode initialization/evaluation.
    """
    jobs = jobs_df.copy()

    if jobs.empty:
        return jobs

    jobs["Due Date"] = pd.to_datetime(
        jobs["Month Due Date"],
        errors="coerce",
    ).dt.normalize()

    if jobs["Due Date"].isna().any():
        raise ValueError(
            "Sebagian Month Due Date tidak dapat dibaca."
        )

    calendar_index = pd.DatetimeIndex(
        calendar_dates
    ).normalize()

    calendar_start = pd.Timestamp(
        calendar_index.min()
    ).normalize()

    evaluation_start_date = pd.Timestamp(
        evaluation_start_date
    ).normalize()

    estimated_minutes = []
    compatibility = []

    for _, job in jobs.iterrows():
        possible_fill_minutes = []

        speed_bg = float(
            job["Speed BG"]
        )

        speed_d = float(
            job["Speed D"]
        )

        sku_gram = float(
            job["SKU Gram"]
        )

        batch_ton = float(
            job["Batch Ton"]
        )

        can_bg = (
            speed_bg > 0
            and sku_gram > 0
        )

        can_d = (
            speed_d > 0
            and sku_gram > 0
        )

        if can_bg:
            fill_bg = (
                batch_ton
                * 1_000_000
                / sku_gram
                / speed_bg
            )

            possible_fill_minutes.append(
                fill_bg
            )

        if can_d:
            fill_d = (
                batch_ton
                * 1_000_000
                / sku_gram
                / speed_d
            )

            possible_fill_minutes.append(
                fill_d
            )

        if not possible_fill_minutes:
            raise ValueError(
                "SKU tidak mempunyai kecepatan yang "
                "valid pada Line B/G maupun Line D: "
                + str(job["SKU"])
            )

        estimated_minutes.append(
            min(possible_fill_minutes)
            + SETUP_RESERVE_PER_LOT_MINUTE
        )

        if can_bg and can_d:
            compatibility.append("FLEXIBLE")
        elif can_bg:
            compatibility.append("BG_ONLY")
        else:
            compatibility.append("D_ONLY")

    jobs["Estimated Work Minute"] = (
        estimated_minutes
    )

    jobs["Line Compatibility"] = (
        compatibility
    )

    jobs["Capacity Release Date"] = pd.NaT
    jobs["Earliest Shelf Date"] = pd.NaT
    jobs["Release Date"] = pd.NaT
    jobs["Release Capacity Warning"] = False

    for due_date, due_group in jobs.groupby(
        "Due Date",
        sort=True,
    ):
        bg_only_work = (
            due_group.loc[
                due_group[
                    "Line Compatibility"
                ].eq("BG_ONLY"),
                "Estimated Work Minute",
            ]
            .sum()
        )

        d_only_work = (
            due_group.loc[
                due_group[
                    "Line Compatibility"
                ].eq("D_ONLY"),
                "Estimated Work Minute",
            ]
            .sum()
        )

        total_work = (
            due_group[
                "Estimated Work Minute"
            ]
            .sum()
        )

        group_roles = (
            due_group["Data Role"]
            .fillna("evaluation")
            .astype(str)
            .str.strip()
            .str.lower()
        )

        group_start_date = (
            evaluation_start_date
            if group_roles.eq("evaluation").all()
            else calendar_start
        )

        # Seluruh skenario memakai jendela perencanaan yang sama.
        # Demand suatu bulan mulai boleh diproduksi sejak awal
        # bulan tempat due date berada.
        #
        # Contoh:
        # Demand April 2026 due 31 Maret 2026
        # mulai dijadwalkan sejak 1 Maret 2026.
        capacity_release_date = max(
            pd.Timestamp(
                group_start_date
            ).normalize(),
            pd.Timestamp(
                due_date
            )
            .to_period("M")
            .to_timestamp()
            .normalize(),
        )

        enough_capacity = True
        
        for job_index in due_group.index:
            shelf_life_months = int(
                np.floor(
                    max(
                        float(
                            jobs.at[
                                job_index,
                                "Shelf Life",
                            ]
                        ),
                        0,
                    )
                )
            )

            usable_age_months = max(
                shelf_life_months
                - MIN_REMAINING_SHELF_MONTHS,
                0,
            )

            earliest_shelf_date = (
                pd.Timestamp(due_date)
                - pd.DateOffset(
                    months=usable_age_months
                )
            ).normalize()

            data_role = str(
                jobs.at[
                    job_index,
                    "Data Role",
                ]
            ).strip().lower()

            role_start_date = (
                evaluation_start_date
                if data_role == "evaluation"
                else calendar_start
            )

            final_release_date = max(
                pd.Timestamp(
                    capacity_release_date
                ),
                pd.Timestamp(
                    earliest_shelf_date
                ),
                pd.Timestamp(
                    role_start_date
                ),
            )

            jobs.at[
                job_index,
                "Capacity Release Date",
            ] = capacity_release_date

            jobs.at[
                job_index,
                "Earliest Shelf Date",
            ] = earliest_shelf_date

            jobs.at[
                job_index,
                "Release Date",
            ] = final_release_date

            jobs.at[
                job_index,
                "Release Capacity Warning",
            ] = not enough_capacity

    return (
        jobs.sort_values(
            by=[
                "Release Date",
                "Due Date",
                "SKU",
                "Lot Number",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def simulate_one_scenario(forecast_df, scenario, holiday_day_set, candidate_window=DEFAULT_CANDIDATE_WINDOW):
    scenario_code = scenario["Scenario"]
    batch_mode = scenario["Batch Mode"]
    batch_limit_per_day = int(scenario["Batch Limit per Day"])
    growth = float(scenario["Growth"])

    simulation_start, simulation_end, calendar_dates = (
        build_simulation_calendar(forecast_df)
    )

    evaluation_start_date = (
        get_evaluation_calendar_start(
            forecast_df
        )
    )

    evaluation_mask = (
        forecast_df["DataRole"]
        .astype(str)
        .str.lower()
        .eq("evaluation")
    )

    target_demand = (
        forecast_df.loc[
            evaluation_mask,
            "ForecastTon",
        ].sum()
        * (1 + growth)
    )

    initialization_demand = (
        forecast_df.loc[
            ~evaluation_mask,
            "ForecastTon",
        ].sum()
    )
    jobs_df = expand_jobs(forecast_df, growth)
    if len(jobs_df) == 0:
        return {}, pd.DataFrame()
    initialization_profile = (
        validate_weekly_hours(
            INITIALIZATION_WEEKLY_HOURS
        )
    )

    evaluation_profile = (
        get_evaluation_weekly_hours(
            scenario
        )
    )

    evaluation_calendar_dates = [
        pd.Timestamp(date).normalize()
        for date in calendar_dates
        if (
            pd.Timestamp(date).normalize()
            >= evaluation_start_date
        )
    ]

    downtime_sets = {}

    for line in ["B", "G", "D"]:
        active_evaluation_dates = [
            date
            for date in evaluation_calendar_dates
            if get_daily_line_hours(
                evaluation_profile,
                line,
                date,
            ) > 0
        ]

        downtime_sets[line] = (
            make_monthly_downtime_set(
                scenario.get(
                    f"Line {line} Downtime Days/Month",
                    0,
                ),
                active_evaluation_dates,
            )
        )

    daily_capacity_minutes = {
        line: {}
        for line in ["B", "G", "D"]
    }

    working_days_by_line = {
        line: set()
        for line in ["B", "G", "D"]
    }

    for calendar_date in calendar_dates:
        calendar_date = pd.Timestamp(
            calendar_date
        ).normalize()

        profile = (
            initialization_profile
            if calendar_date < evaluation_start_date
            else evaluation_profile
        )

        for line in ["B", "G", "D"]:
            daily_hours = get_daily_line_hours(
                profile,
                line,
                calendar_date,
            )

            if (
                calendar_date in holiday_day_set
                or calendar_date in downtime_sets[line]
            ):
                daily_hours = 0.0

            capacity_minutes = (
                float(daily_hours)
                * 60.0
            )

            daily_capacity_minutes[line][
                calendar_date
            ] = capacity_minutes

            if capacity_minutes > 0:
                working_days_by_line[line].add(
                    calendar_date
                )

    working_days_by_line = {
        line: frozenset(dates)
        for line, dates
        in working_days_by_line.items()
    }

    jobs_df = assign_capacity_release_dates(
        jobs_df=jobs_df,
        calendar_dates=calendar_dates,
        working_days_by_line=working_days_by_line,
        daily_capacity_minutes=daily_capacity_minutes,
        evaluation_start_date=evaluation_start_date,
    )

    pending_jobs = jobs_df.to_dict(
        "records"
    )

    ready_jobs = []
    pending_position = 0

    line_state = {
        line: {
            "used_today": 0,
            "processing": 0,
            "setup": 0,
            "tons": 0,
            "evaluation_processing": 0,
            "evaluation_setup": 0,
            "evaluation_tons": 0,
            "last_sku": None,
            "last_port": None,
            "last_allergen": 0,
            "last_color": None,
        }
        for line in ["B", "G", "D"]
    }
    planned_jobs = []
    seq = 1

    for calendar_day, calendar_date in enumerate(
        calendar_dates,
        start=1,
    ):
        calendar_date = pd.Timestamp(
            calendar_date
        ).normalize()
        # OPTIMIZATION: precompute active lines untuk hari ini (O(1) set lookup × 3)
        active_lines = [
            line
            for line in ["B", "G", "D"]
            if calendar_date in working_days_by_line[line]
        ]

        for line in active_lines:
            line_state[line]["used_today"] = 0

        new_jobs_added = False

        while (
            pending_position
            < len(pending_jobs)
            and pd.Timestamp(
                pending_jobs[
                    pending_position
                ]["Release Date"]
            ).normalize()
            <= calendar_date
        ):
            ready_jobs.append(
                pending_jobs[
                    pending_position
                ]
            )

            pending_position += 1
            new_jobs_added = True

        if new_jobs_added:
            ready_jobs.sort(
                key=lambda job: (
                    pd.Timestamp(
                        job["Due Date"]
                    ),
                    str(job["SKU"]),
                    int(job["Lot Number"]),
                )
            )

        # OPTIMIZATION: skip hari tidak aktif; break jika semua demand terjadwal
        if not active_lines:
            continue
        if (
            not ready_jobs
            and pending_position
            >= len(pending_jobs)
        ):
            break

        if not ready_jobs:
            continue

        count_batch_today = 0
        while ready_jobs:
            best_idx = (
                best_line
            ) = best_finish = best_setup = best_tfill = best_speed = None
            for idx, job in enumerate(
            for idx, job in enumerate(
                ready_jobs
            ):
                job_role = str(
                    job.get(
                        "Data Role",
                        "evaluation",
                    )
                ).strip().lower()

                if (
                    job_role == "evaluation"
                    and batch_limit_per_day < 999999
                    and count_batch_today
                    >= batch_limit_per_day
                ):
                    continue

                candidates = []

                for line in active_lines:
                    cap_mins = float(
                        daily_capacity_minutes[line].get(
                            calendar_date,
                            0.0,
                        )
                    )
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
            job = ready_jobs.pop(best_idx)
            tblend = T_BLEND_COKLAT if str(job["Chocolate Type"]).lower() == "coklat" else T_BLEND_NON_COKLAT
            line_state[best_line]["used_today"] = best_finish
            line_state[best_line]["processing"] += best_tfill
            line_state[best_line]["setup"] += best_setup
            line_state[best_line]["tons"] += job["Batch Ton"]

            scheduled_role = str(
                job.get(
                    "Data Role",
                    "evaluation",
                )
            ).strip().lower()

            if scheduled_role == "evaluation":
                line_state[best_line][
                    "evaluation_processing"
                ] += best_tfill

                line_state[best_line][
                    "evaluation_setup"
                ] += best_setup

                line_state[best_line][
                    "evaluation_tons"
                ] += job["Batch Ton"]

            line_state[best_line]["last_sku"] = job["SKU"]
            line_state[best_line]["last_port"] = job["Port Type"]
            line_state[best_line]["last_allergen"] = job["Allergen"]
            line_state[best_line]["last_color"] = job["Color Setup"]
            planned_jobs.append({
                "Scenario": scenario_code, "Sequence": seq, "Calendar Day": calendar_day, "Calendar Date": calendar_date.strftime("%Y-%m-%d"), "Line": best_line,
                "Item Name": job["Item Name"],
                "SKU": job["SKU"],
                "SKU Alias": job.get(
                    "SKU Alias",
                    "",
                ),
                "Data Role": job.get(
                    "Data Role",
                    "evaluation",
                ),
                "Batch Ton": round(
                    float(job["Batch Ton"]),
                    9,
                ),
                "Setup Minute": round(best_setup, 2), "Batching Note Minute": T_BATCH, "Prep Note Minute": T_PREP,
                "Tip Note Minute": T_TIP, "Mini Blend Note Minute": job["Mini Blend Minute"], "Blend Note Minute": tblend,
                "Fill Minute": round(best_tfill, 2), "Used Capacity Minute": round(best_setup + best_tfill, 2),
                "Speed": best_speed, "Port Type": job["Port Type"], "Allergen": job["Allergen"], "Color Setup": job["Color Setup"],
                "Shelf Life": job["Shelf Life"], "Month Index": job["Month Index"], "Month Input Raw": job.get("Month Input Raw", ""),
                "Month Input Mode": job.get("Month Input Mode", "sequence"), "Month Due Date": job.get("Month Due Date", ""), "Month Due Day": job.get("Month Due Day", np.nan),
                "Release Date": (
                    pd.Timestamp(
                        job["Release Date"]
                    )
                    .strftime("%Y-%m-%d")
                ),

                "Due Date": (
                    pd.Timestamp(
                        job["Due Date"]
                    )
                    .strftime("%Y-%m-%d")
                ),

                "Capacity Release Date": (
                    pd.Timestamp(
                        job[
                            "Capacity Release Date"
                        ]
                    )
                    .strftime("%Y-%m-%d")
                ),

                "Earliest Shelf Date": (
                    pd.Timestamp(
                        job[
                            "Earliest Shelf Date"
                        ]
                    )
                    .strftime("%Y-%m-%d")
                ),
            })
            seq += 1

            if scheduled_role == "evaluation":
                count_batch_today += 1
    planned_jobs_df = pd.DataFrame(
        planned_jobs
    )
    stock_backlog_df = (
        build_inventory_backlog_table(
            forecast_df=forecast_df,
            planned_jobs_df=planned_jobs_df,
            scenario_code=scenario_code,
            growth=growth,
        )
    )

    if stock_backlog_df.empty:
        demand_fulfilled_ton = 0.0
        demand_fulfillment_pct = 0.0
        on_time_fulfilled_ton = 0.0
        on_time_fulfillment_pct = 0.0
        late_demand_ton = 0.0
        ending_backlog_ton = 0.0
        ending_inventory_ton = 0.0
        expired_inventory_ton = 0.0
        sku_period_on_time_pct = 0.0
        late_sku_periods = 0
        maximum_delay_days = 0
    else:
        evaluation_ledger = (
            stock_backlog_df[
                stock_backlog_df["Data Role"]
                .astype(str)
                .str.lower()
                .eq("evaluation")
            ]
            .copy()
        )

        evaluation_demand_ton = (
            evaluation_ledger["Demand Ton"]
            .sum()
        )

        demand_fulfilled_ton = (
            evaluation_ledger[
                "Total Fulfilled Ton"
            ]
            .sum()
        )

        on_time_fulfilled_ton = (
            evaluation_ledger[
                "On-Time Fulfilled Ton"
            ]
            .sum()
        )

        late_demand_ton = (
            evaluation_ledger[
                "Late Demand Ton"
            ]
            .sum()
        )

        ending_backlog_ton = (
            evaluation_ledger[
                "Final Backlog Ton"
            ]
            .sum()
        )

        demand_fulfillment_pct = (
            demand_fulfilled_ton
            / evaluation_demand_ton
            * 100
            if evaluation_demand_ton > 0
            else 0.0
        )

        on_time_fulfillment_pct = (
            on_time_fulfilled_ton
            / evaluation_demand_ton
            * 100
            if evaluation_demand_ton > 0
            else 0.0
        )

        late_sku_periods = int(
            (
                evaluation_ledger[
                    "Late Demand Ton"
                ]
                > LOT_ROUNDING_EPSILON
            )
            .sum()
        )

        sku_period_on_time_pct = (
            (
                len(evaluation_ledger)
                - late_sku_periods
            )
            / len(evaluation_ledger)
            * 100
            if len(evaluation_ledger) > 0
            else 0.0
        )

        maximum_delay_days = int(
            pd.to_numeric(
                evaluation_ledger[
                    "Delay Days"
                ],
                errors="coerce",
            )
            .fillna(0)
            .max()
        )

        latest_sku_rows = (
            stock_backlog_df
            .sort_values(
                by=[
                    "SKU",
                    "Due Date",
                ],
                kind="stable",
            )
            .groupby(
                "SKU",
                sort=False,
            )
            .tail(1)
        )

        ending_inventory_ton = (
            latest_sku_rows[
                "Inventory After Due Ton"
            ]
            .sum()
        )

        expired_inventory_ton = (
            latest_sku_rows[
                "Expired Inventory Until Due Ton"
            ]
            .sum()
        )

    all_demand_on_time = (
        late_demand_ton
        <= LOT_ROUNDING_EPSILON
        and ending_backlog_ton
        <= LOT_ROUNDING_EPSILON
    )
    if planned_jobs_df.empty:
        initialization_production = 0.0
        evaluation_production = 0.0
    else:
        production_roles = (
            planned_jobs_df["Data Role"]
            .fillna("evaluation")
            .astype(str)
            .str.strip()
            .str.lower()
        )

        initialization_production = (
            planned_jobs_df.loc[
                production_roles.eq(
                    "initialization"
                ),
                "Batch Ton",
            ]
            .sum()
        )

        evaluation_production = (
            planned_jobs_df.loc[
                production_roles.eq(
                    "evaluation"
                ),
                "Batch Ton",
            ]
            .sum()
        )

    total_production = (
        initialization_production
        + evaluation_production
    )

    tons_b = line_state["B"][
        "evaluation_tons"
    ]
    tons_g = line_state["G"][
        "evaluation_tons"
    ]
    tons_d = line_state["D"][
        "evaluation_tons"
    ]

    finished_ton = evaluation_production

    unmet_demand = max(
        target_demand
        - evaluation_production,
        0,
    )

    finished_ratio = (
        evaluation_production
        / target_demand
        * 100
        if target_demand > 0
        else 0
    )

    evaluation_available = {
        line: sum(
            minutes
            for date, minutes
            in daily_capacity_minutes[line].items()
            if date >= evaluation_start_date
        )
        for line in ["B", "G", "D"]
    }

    util_b = (
        (
            line_state["B"]["evaluation_processing"]
            + line_state["B"]["evaluation_setup"]
        )
        / evaluation_available["B"]
        * 100
        if evaluation_available["B"] > 0
        else 0
    )

    util_g = (
        (
            line_state["G"]["evaluation_processing"]
            + line_state["G"]["evaluation_setup"]
        )
        / evaluation_available["G"]
        * 100
        if evaluation_available["G"] > 0
        else 0
    )

    util_d = (
        (
            line_state["D"]["evaluation_processing"]
            + line_state["D"]["evaluation_setup"]
        )
        / evaluation_available["D"]
        * 100
        if evaluation_available["D"] > 0
        else 0
    )
    util_dict = {"Filling B": util_b, "Filling G": util_g, "Filling D": util_d}
    bottleneck = max(util_dict, key=util_dict.get)
    return {
        "Scenario": scenario_code,
        "Simulation Start": simulation_start.strftime("%Y-%m-%d"),
        "Simulation End": simulation_end.strftime("%Y-%m-%d"),
        "Evaluation Calendar Start": (
            evaluation_start_date.strftime(
                "%Y-%m-%d"
            )
        ),
        "Initialization Schedule": (
            "Kondisi aktual historis"
        ),
        "Evaluation Schedule Mode": scenario.get(
            "Evaluation Schedule Mode",
            "legacy",
        ),
        "Holiday Mode": scenario.get(
            "Holiday Mode",
            "none",
        ),
        "Holiday Days": int(
            scenario.get(
                "Holiday Days",
                0,
            )
        ),
        "Holiday Dates": scenario.get(
            "Holiday Dates",
            "",
        ),
        "Horizon Days": len(calendar_dates),
        "Line B Days": scenario["Line B Days"], "Line B Hours": scenario["Line B Hours"],
        "Line G Days": scenario["Line G Days"], "Line G Hours": scenario["Line G Hours"],
        "Line D Days": scenario["Line D Days"], "Line D Hours": scenario["Line D Hours"],
        "Batch Mode": scenario["Batch Mode"], "Growth": scenario["Growth"],
        "Line B Downtime Days/Month": scenario.get("Line B Downtime Days/Month", 0), "Line G Downtime Days/Month": scenario.get("Line G Downtime Days/Month", 0), "Line D Downtime Days/Month": scenario.get("Line D Downtime Days/Month", 0),
        "Initialization Demand Ton": round(
            initialization_demand,
            2,
        ),
        "Evaluation Demand Ton": round(
            target_demand,
            2,
        ),
        "Target Demand Ton": round(
            target_demand,
            2,
        ),
                "Demand Fulfilled Ton": round(
                    demand_fulfilled_ton,
                    2,
                ),
                "Demand Fulfillment (%)": round(
                    demand_fulfillment_pct,
                    2,
                ),
                "On-Time Fulfilled Ton": round(
                    on_time_fulfilled_ton,
                    2,
                ),
                "On-Time Demand Fulfillment (%)": round(
                    on_time_fulfillment_pct,
                    2,
                ),
                "Late Demand Ton": round(
                    late_demand_ton,
                    2,
                ),
                "On-Time Demand Fulfillment (%)": round(
                    on_time_fulfillment_pct,
                    2,
                ),
                "Late Demand Ton": round(
                    late_demand_ton,
                    2,
                ),
                "On-Time Unmet Demand Ton": round(
                    late_demand_ton,
                    2,
                ),
                "Ending Backlog Ton": round(
                    ending_backlog_ton,
                    2,
                ),
                "Ending Backlog Ton": round(
                    ending_backlog_ton,
                    2,
                ),
                "Ending Inventory Ton": round(
                    ending_inventory_ton,
                    2,
                ),
                "Expired Inventory Ton": round(
                    expired_inventory_ton,
                    2,
                ),
                "SKU-Period On Time (%)": round(
                    sku_period_on_time_pct,
                    2,
                ),
                "Late SKU-Periods": int(
                    late_sku_periods
                ),
                "Maximum Delay Days": int(
                    maximum_delay_days
                ),
                "On-Time Status": (
                    "Seluruh Demand Tepat Waktu"
                    if all_demand_on_time
                    else "Terdapat Demand Terlambat"
                ),
        "Initialization Production Ton": round(
            initialization_production,
            2,
        ),
        "Evaluation Production Ton": round(
            evaluation_production,
            2,
        ),
        "Total Production Ton": round(
            total_production,
            2,
        ),
        "Planned Ton": round(
            evaluation_production,
            2,
        ),
        "Tons Finished": round(
            evaluation_production,
            2,
        ),
        "Planning Ratio (%)": round(finished_ratio, 2), "Finished Ratio (%)": round(finished_ratio, 2), "Unmet Demand Ton": round(unmet_demand, 2),
        "Tons B": round(tons_b, 2), "Tons G": round(tons_g, 2), "Tons D": round(tons_d, 2),
        "Util Filling B (%)": round(util_b, 2), "Util Filling G (%)": round(util_g, 2), "Util Filling D (%)": round(util_d, 2),
        "Setup Minute B": round(
            line_state["B"]["evaluation_setup"],
            2,
        ),
        "Setup Minute G": round(
            line_state["G"]["evaluation_setup"],
            2,
        ),
        "Setup Minute D": round(
            line_state["D"]["evaluation_setup"],
            2,
        ),
        "Bottleneck Area": bottleneck,
        "Planner Status": "Target Terpenuhi" if finished_ton >= target_demand - 0.05 else "Target Tidak Terpenuhi",
        "Capacity Status": "Kapasitas Mencukupi" if unmet_demand <= 0.05 else "Kapasitas Tidak Mencukupi",
    }, planned_jobs_df


def run_des_simulation(
    forecast_input_df,
    b_days_options,
    b_hours_options,
    g_days_options,
    g_hours_options,
    d_days_options,
    d_hours_options,
    batch_options,
    growth_percent_options,
    holiday_cutoff_days=0,
    holiday_dates_text="",
    max_scenarios=DEFAULT_MAX_SCENARIOS,
    b_downtime=0,
    g_downtime=0,
    d_downtime=0,
    holiday_mode="none",
    evaluation_schedule_mode=None,
    evaluation_weekly_hours=None,
    # Backward-compat: availability diterima tetapi diabaikan
    b_availability=100,
    g_availability=100,
    d_availability=100,
):
    """
    Jalankan DES simulation untuk semua skenario.
    Menjalankan seluruh skenario secara berurutan agar aman pada keterbatasan RAM Streamlit Cloud.
    """
    forecast_df = clean_prepared_input(
        forecast_input_df
    )

    simulation_start, simulation_end, calendar_dates = (
        build_simulation_calendar(forecast_df)
    )

    scenario_df = generate_scenarios(
        b_days_options,
        b_hours_options,
        g_days_options,
        g_hours_options,
        d_days_options,
        d_hours_options,
        batch_options,
        growth_percent_options,
        max_scenarios=max_scenarios,
        b_downtime=b_downtime,
        g_downtime=g_downtime,
        d_downtime=d_downtime,
    )

    # Kompatibilitas sementara dengan UI jadwal preset yang
    # masih ada pada kondisi GitHub saat ini. Ketika parameter
    # tidak dikirim, skenario tetap memakai konfigurasi faktorial
    # hari kerja dan jam kerja per lini (mode legacy).
    if evaluation_schedule_mode is not None:
        schedule_mode = str(
            evaluation_schedule_mode
        ).strip().lower()

        valid_schedule_modes = {
            "actual",
            "management",
            "custom",
        }

        if schedule_mode not in valid_schedule_modes:
            raise ValueError(
                "Mode jadwal evaluation harus actual, "
                "management, atau custom."
            )

        scenario_df["Evaluation Schedule Mode"] = (
            schedule_mode
        )

        if schedule_mode == "custom":
            validated_weekly_hours = (
                validate_weekly_hours(
                    evaluation_weekly_hours or {}
                )
            )

            scenario_df["Evaluation Weekly Hours"] = [
                {
                    line: list(hours)
                    for line, hours
                    in validated_weekly_hours.items()
                }
                for _ in range(len(scenario_df))
            ]
        else:
            scenario_df["Evaluation Weekly Hours"] = [
                None
                for _ in range(len(scenario_df))
            ]

        schedule_label = {
            "actual": "KONDISI AKTUAL",
            "management": "USULAN MANAJEMEN",
            "custom": "JADWAL KHUSUS",
        }[schedule_mode]

        scenario_df["Scenario"] = scenario_df.apply(
            lambda row: (
                f"{schedule_label} | "
                f"{row['Batch Mode']} | "
                f"G{int(float(row['Growth']) * 100)}%"
            ),
            axis=1,
        )

    evaluation_start_date = (
        get_evaluation_calendar_start(
            forecast_df
        )
    )

    evaluation_calendar_dates = [
        pd.Timestamp(date).normalize()
        for date in calendar_dates
        if (
            pd.Timestamp(date).normalize()
            >= evaluation_start_date
        )
    ]

    holiday_set = make_holiday_set(
        evaluation_calendar_dates,
        holiday_mode=holiday_mode,
        holiday_cutoff_days=holiday_cutoff_days,
        holiday_dates_text=holiday_dates_text,
    )
    holiday_dates_used = sorted(
        pd.Timestamp(date).strftime("%Y-%m-%d")
        for date in holiday_set
    )
    
    holiday_dates_text_used = ", ".join(
        holiday_dates_used
    )
    
    scenario_df["Holiday Mode"] = holiday_mode
    scenario_df["Holiday Days"] = len(holiday_set)
    scenario_df["Holiday Dates"] = holiday_dates_text_used
        
    scenario_list = [row.to_dict() for _, row in scenario_df.iterrows()]
    
    # Jalankan skenario secara berurutan agar aman
    # untuk keterbatasan RAM Streamlit Cloud.
    results = []

    for scenario in scenario_list:
        result, _ = simulate_one_scenario(
            forecast_df,
            pd.Series(scenario),
            holiday_set,
            DEFAULT_CANDIDATE_WINDOW,
        )

        if result:
            results.append(result)

    result_df = pd.DataFrame(results)

    if not result_df.empty:
        result_df[
            "Highest Utilization (%)"
        ] = result_df[
            [
                "Util Filling B (%)",
                "Util Filling G (%)",
                "Util Filling D (%)",
            ]
        ].max(axis=1)
        
        result_df[
            "Total Weekly Operating Hours"
        ] = (
            result_df["Line B Days"]
            * result_df["Line B Hours"]
            + result_df["Line G Days"]
            * result_df["Line G Hours"]
            + result_df["Line D Days"]
            * result_df["Line D Hours"]
        )

        result_df = (
            result_df.sort_values(
                by=[
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
                kind="stable",
            )
            .reset_index(drop=True)
        )

        # Detail Production Plan hanya dibuat ulang
        # untuk skenario terbaik agar RAM tidak penuh.
        best_scenario_code = str(
            result_df.iloc[0]["Scenario"]
        )

        best_scenario = next(
            scenario
            for scenario in scenario_list
            if str(scenario["Scenario"])
            == best_scenario_code
        )

        _, planned_jobs_df = simulate_one_scenario(
            forecast_df,
            pd.Series(best_scenario),
            holiday_set,
            DEFAULT_CANDIDATE_WINDOW,
        )

    else:
        planned_jobs_df = pd.DataFrame()
    sku_count = (
        int(forecast_df["SkuId"].nunique())
        if "SkuId" in forecast_df.columns
        else 0
    )

    input_record_count = int(
        len(forecast_df)
    )

    period_count = (
        int(forecast_df["MonthIndex"].nunique())
        if "MonthIndex" in forecast_df.columns
        else 0
    )

    meta = {
        "scenarios_evaluated": len(result_df),

        "sku_analyzed": sku_count,
        "input_records": input_record_count,
        "period_count": period_count,

        "holiday_days": len(holiday_set),
        "holiday_mode": holiday_mode,
        "holiday_dates": holiday_dates_used,

        "simulation_start": simulation_start.strftime(
            "%Y-%m-%d"
        ),
        "simulation_end": simulation_end.strftime(
            "%Y-%m-%d"
        ),
        "horizon_days": len(calendar_dates),
    }                      
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
