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
