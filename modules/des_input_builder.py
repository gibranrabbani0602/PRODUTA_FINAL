import re
import unicodedata
import pandas as pd

REQUIRED_OUTPUT_COLUMNS = [
    "Date",
    "ItemName",
    "Qty",
    "SkuId",
    "SKU_Alias",
    "ForecastTon",
    "SkuGr",
    "SpeedD",
    "Speed",
    "IsChocolate",
    "port_type",
    "Allergen",
    "ShelfLife",
    "MonthIndex",
    "DataRole",
]


def _norm(c):
    c = "" if c is None else str(c)
    c = unicodedata.normalize("NFKD", c)
    c = "".join(ch for ch in c if not unicodedata.combining(ch))
    c = c.casefold().strip()
    c = re.sub(r"[^a-z0-9]+", "", c)
    return c


def _rename_alias(df, alias):
    df = df.copy()
    lut = {_norm(c): c for c in df.columns}
    ren = {}
    for target, aliases in alias.items():
        for cand in [target] + aliases:
            k = _norm(cand)
            if k in lut:
                ren[lut[k]] = target
                break
    return df.rename(columns=ren)

def _format_gram_token(value):
    """
    Mengubah gramasi menjadi bagian kode alias.

    Contoh:
    1000.0 -> 1000
    27.5   -> 27p5
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "NA"

    if number.is_integer():
        return str(int(number))

    return (
        f"{number:g}"
        .replace(".", "p")
    )


def _package_token(value):
    """
    Mengubah jenis kemasan menjadi kode pendek anonim.
    """
    key = _norm(value)

    if "stick" in key:
        return "STK"

    if "sss" in key or "sachet" in key:
        return "SSS"

    if "bib" in key or "baginbox" in key:
        return "BIB"

    return "SKU"


def ensure_sku_alias(df):
    """
    Memastikan setiap SKU mempunyai satu alias tetap.

    Contoh:
    BIB-01-1000
    SSS-02-25

    Alias yang sudah tersedia pada master tetap dipertahankan.
    """
    df = df.copy()

    if "SKU_Alias" not in df.columns:
        df["SKU_Alias"] = ""

    df["SKU_Alias"] = (
        df["SKU_Alias"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    if "port_type" not in df.columns:
        df["port_type"] = ""

    sku_reference = (
        df[
            [
                "SkuId",
                "SkuGr",
                "port_type",
            ]
        ]
        .drop_duplicates(
            subset=["SkuId"],
            keep="first",
        )
        .copy()
    )

    sku_reference["PackageToken"] = (
        sku_reference["port_type"]
        .apply(_package_token)
    )

    sku_reference = (
        sku_reference
        .sort_values(
            by=[
                "PackageToken",
                "SkuId",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    sku_reference["AliasSequence"] = (
        sku_reference
        .groupby("PackageToken")
        .cumcount()
        + 1
    )

    generated_alias = {}

    for _, row in sku_reference.iterrows():
        sku_id = str(
            row["SkuId"]
        ).strip()

        package_token = str(
            row["PackageToken"]
        )

        gram_token = _format_gram_token(
            row["SkuGr"]
        )

        sequence = int(
            row["AliasSequence"]
        )

        generated_alias[sku_id] = (
            f"{package_token}-{sequence:02d}-{gram_token}"
        )

    missing_alias = (
        df["SKU_Alias"].eq("")
        | df["SKU_Alias"].str.lower().isin(
            [
                "nan",
                "none",
                "null",
            ]
        )
    )

    df.loc[
        missing_alias,
        "SKU_Alias",
    ] = (
        df.loc[
            missing_alias,
            "SkuId",
        ]
        .astype(str)
        .map(generated_alias)
    )

    alias_check = (
        df.groupby("SKU_Alias")["SkuId"]
        .nunique()
    )

    duplicate_aliases = alias_check[
        alias_check > 1
    ]

    if not duplicate_aliases.empty:
        raise ValueError(
            "Terdapat SKU_Alias yang digunakan oleh "
            "lebih dari satu SkuId: "
            + ", ".join(
                duplicate_aliases.index
                .astype(str)
                .tolist()[:20]
            )
        )

    return df
    
def standardize_forecast(forecast_df):
    alias = {
        "SkuId": ["sku", "sku_id", "name", "kode sku", "item code", "material"],
        "Date": ["date", "ds", "tanggal", "bulan", "period", "periode"],
        "ForecastTon": ["forecast", "demand_forecast", "demand", "ton", "tonase", "planned ton"],
        "DescriptionForecast": ["description", "deskripsi", "itemname", "item name", "product"],
        "ForecastLow": ["forecast_lower", "forecast_low", "lower", "demand_lower"],
        "ForecastHigh": ["forecast_upper", "forecast_high", "upper", "demand_upper"],
        "ModelUsed": ["model_used", "model"],
        "MAPE": ["mape_backtest", "mape"],
        "WMAPE": ["wmape_backtest", "wmape"],
    }
    df = _rename_alias(forecast_df, alias)
    miss = [c for c in ["SkuId", "Date", "ForecastTon"] if c not in df.columns]
    if miss:
        raise ValueError(f"Kolom forecast belum lengkap: {miss}. Kolom tersedia: {list(forecast_df.columns)}")
    df["SkuId"] = df["SkuId"].astype(str).str.strip()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["ForecastTon"] = pd.to_numeric(df["ForecastTon"], errors="coerce").fillna(0).clip(lower=0)
    df = df.dropna(subset=["Date"])
    df = df[(df["SkuId"].str.len() > 0) & (df["ForecastTon"] > 0)]
    if df.empty:
        raise ValueError("Forecast kosong setelah dibersihkan. Cek kolom sku/date/forecast.")
    months = df[["Date"]].drop_duplicates().sort_values("Date").reset_index(drop=True)
    months["MonthIndex"] = months.index + 1
    df = df.merge(months, on="Date", how="left")
    return df

def standardize_history(history_df):
    """
    Menstandarkan data kebutuhan aktual historis.

    Format yang dapat dibaca antara lain:
    ds | sku | description | y
    """
    alias = {
        "SkuId": [
            "sku",
            "sku id",
            "sku_id",
            "kode sku",
            "item code",
            "material",
        ],
        "Date": [
            "date",
            "ds",
            "tanggal",
            "bulan",
            "period",
            "periode",
        ],
        "ActualTon": [
            "y",
            "actual",
            "actual ton",
            "volume",
            "volume ton",
            "demand actual",
            "demand aktual",
            "ton",
            "tonase",
        ],
        "DescriptionHistory": [
            "description",
            "deskripsi",
            "itemname",
            "item name",
            "product",
        ],
    }

    df = _rename_alias(
        history_df,
        alias,
    )

    required = [
        "SkuId",
        "Date",
        "ActualTon",
    ]

    missing = [
        column
        for column in required
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            "Kolom data historis belum lengkap: "
            + str(missing)
            + ". Kolom tersedia: "
            + str(list(history_df.columns))
        )

    df["SkuId"] = (
        df["SkuId"]
        .astype(str)
        .str.strip()
    )

    df["Date"] = pd.to_datetime(
        df["Date"],
        errors="coerce",
    )

    df["Date"] = (
        df["Date"]
        .dt.to_period("M")
        .dt.to_timestamp()
    )

    df["ActualTon"] = (
        pd.to_numeric(
            df["ActualTon"],
            errors="coerce",
        )
        .fillna(0)
        .clip(lower=0)
    )

    df = df.dropna(
        subset=["Date"]
    )

    df = df[
        df["SkuId"].str.len() > 0
    ]

    df = (
        df.groupby(
            [
                "SkuId",
                "Date",
            ],
            as_index=False,
        )["ActualTon"]
        .sum()
    )

    if df.empty:
        raise ValueError(
            "Data historis kosong setelah dibersihkan."
        )

    return df
def standardize_master(master_df):
    alias = {
        "ItemName": ["item name", "nama produk", "nama sku", "description", "deskripsi", "product"],
        "SkuId": ["sku", "sku id", "sku_id", "kode sku", "item code", "material"],
        "SKU_Alias": ["sku alias", "sku_alias", "alias sku", "alias", "kode anonim", "kode samaran"],
        "SkuGr": ["sku gr", "sku gram", "gramasi", "grammage", "gram", "pack size"],
        "SpeedD": ["speed d", "speed line d", "ppm d", "line d speed"],
        "Speed": ["speed", "speed bg", "speed b/g", "speed b", "speed g", "ppm"],
        "IsChocolate": ["is chocolate", "chocolate", "coklat", "warna", "color", "colour"],
        "port_type": ["port type", "port", "tipe port", "jenis port", "packaging", "kemasan"],
        "Allergen": ["allergen", "alergen", "allergen level", "level allergen"],
        "ShelfLife": ["shelf life", "shelflife", "umur simpan", "masa simpan", "expiry"],
    }
    df = _rename_alias(master_df, alias)
    required = ["ItemName", "SkuId", "SkuGr", "SpeedD", "Speed", "IsChocolate", "port_type", "Allergen", "ShelfLife"]
    miss = [c for c in required if c not in df.columns]
    if miss:
        raise ValueError(f"Kolom master SKU capacity belum lengkap: {miss}. Kolom tersedia: {list(master_df.columns)}")
    df["SkuId"] = df["SkuId"].astype(str).str.strip()
    df["ItemName"] = df["ItemName"].astype(str).str.strip()
    for col in ["SkuGr", "SpeedD", "Speed", "Allergen", "ShelfLife"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["IsChocolate"] = df["IsChocolate"].astype(str).str.strip().str.lower()
    df["port_type"] = df["port_type"].astype(str).str.strip().str.upper()
    df = (
        df[df["SkuId"].str.len() > 0]
        .drop_duplicates(
            "SkuId",
            keep="first",
        )
        .reset_index(drop=True)
    )

    df = ensure_sku_alias(df)

    return df


def build_forecast_input_des(forecast_df, master_df, adjustment_pct=0.0, qty_default=1):
    fc = standardize_forecast(forecast_df)
    ms = standardize_master(master_df)
    fc = fc.copy()
    fc["DataRole"] = "evaluation"
    fc["ForecastTon"] = fc["ForecastTon"] * (1 + float(adjustment_pct) / 100)
    merged = fc.merge(ms, on="SkuId", how="left", suffixes=("_forecast", ""))
    missing = merged[merged["ItemName"].isna()]["SkuId"].drop_duplicates().astype(str).tolist()
    if missing:
        raise ValueError("SKU forecast belum ada di master SKU: " + ", ".join(missing[:30]))
    merged["Qty"] = int(qty_default)
    result = merged[REQUIRED_OUTPUT_COLUMNS].copy()
    for extra in ["DescriptionForecast", "ForecastLow", "ForecastHigh", "ModelUsed", "MAPE", "WMAPE"]:
        if extra in merged.columns:
            result[extra] = merged[extra]
    return result.sort_values(["MonthIndex", "SkuId"]).reset_index(drop=True)
 
def build_combined_des_input(
    forecast_df,
    master_df,
    history_df,
    initialization_months=12,
    adjustment_pct=0.0,
    qty_default=1,
):
    """
    Menggabungkan:
    - kebutuhan aktual historis sebagai initialization;
    - hasil forecast sebagai evaluation.

    Growth atau adjustment hanya dikenakan pada evaluation.
    """
    if history_df is None:
        raise ValueError(
            "Data historis diperlukan untuk membentuk "
            "periode initialization."
        )

    initialization_months = int(
        initialization_months
    )

    if initialization_months <= 0:
        raise ValueError(
            "Jumlah bulan initialization harus lebih dari 0."
        )

    forecast = standardize_forecast(
        forecast_df
    )

    master = standardize_master(
        master_df
    )

    history = standardize_history(
        history_df
    )

    evaluation_start = (
        forecast["Date"]
        .min()
        .to_period("M")
        .to_timestamp()
    )

    initialization_start = (
        evaluation_start
        - pd.DateOffset(
            months=initialization_months
        )
    )

    initialization_end = (
        evaluation_start
        - pd.DateOffset(months=1)
    )

    evaluation_skus = sorted(
        forecast["SkuId"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    initialization_dates = pd.date_range(
        start=initialization_start,
        end=initialization_end,
        freq="MS",
    )

    initialization_grid = (
        pd.MultiIndex.from_product(
            [
                evaluation_skus,
                initialization_dates,
            ],
            names=[
                "SkuId",
                "Date",
            ],
        )
        .to_frame(index=False)
    )

    history = history[
        history["SkuId"].isin(
            evaluation_skus
        )
    ].copy()

    history = history[
        history["Date"].between(
            initialization_start,
            initialization_end,
            inclusive="both",
        )
    ].copy()

    initialization = (
        initialization_grid
        .merge(
            history,
            on=[
                "SkuId",
                "Date",
            ],
            how="left",
        )
    )

    initialization["ForecastTon"] = (
        initialization["ActualTon"]
        .fillna(0)
        .astype(float)
    )

    initialization["DataRole"] = (
        "initialization"
    )

    evaluation = forecast.copy()

    evaluation["ForecastTon"] = (
        evaluation["ForecastTon"]
        * (
            1
            + float(adjustment_pct)
            / 100
        )
    )

    evaluation["DataRole"] = (
        "evaluation"
    )

    combined = pd.concat(
        [
            initialization[
                [
                    "SkuId",
                    "Date",
                    "ForecastTon",
                    "DataRole",
                ]
            ],
            evaluation[
                [
                    "SkuId",
                    "Date",
                    "ForecastTon",
                    "DataRole",
                ]
            ],
        ],
        ignore_index=True,
    )

    month_reference = (
        combined[
            ["Date"]
        ]
        .drop_duplicates()
        .sort_values(
            "Date"
        )
        .reset_index(drop=True)
    )

    month_reference["MonthIndex"] = (
        month_reference.index
        + 1
    )

    combined = combined.merge(
        month_reference,
        on="Date",
        how="left",
    )

    merged = combined.merge(
        master,
        on="SkuId",
        how="left",
    )

    missing_master = (
        merged[
            merged["ItemName"].isna()
        ]["SkuId"]
        .drop_duplicates()
        .astype(str)
        .tolist()
    )

    if missing_master:
        raise ValueError(
            "SKU berikut belum tersedia pada "
            "Master Data Asil: "
            + ", ".join(
                missing_master[:30]
            )
        )

    merged["Qty"] = int(
        qty_default
    )

    result = merged[
        REQUIRED_OUTPUT_COLUMNS
    ].copy()

    return (
        result.sort_values(
            [
                "Date",
                "SkuId",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )
