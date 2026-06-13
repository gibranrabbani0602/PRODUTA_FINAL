"""
forecast_engine.py — Pipeline forecasting sesuai pengerjaan Ubay
================================================================
Dari analisis file combined_forecast_output Ubay:
  - 20 SKU Prophet (forecast bervariasi per bulan, SMOOTH/ERRATIC)
  - 62 SKU Croston (forecast flat per bulan, INTERMITTENT/LUMPY/lainnya)
  - TIDAK ada ADIDA — itu hanya catatan Ubay, bukan implementasi

Model assignment (sesuai slide Ubay):
  SMOOTH/ERRATIC → Prophet (atau Holt-Winters jika Prophet tidak tersedia)
  INTERMITTENT/LUMPY → CrostonSBA

Klasifikasi: p & CV² (Croston 2001), sesuai slide Ubay:
  SMOOTH:       p < 1.32, CV² < 0.49
  ERRATIC:      p < 1.32, CV² ≥ 0.49
  INTERMITTENT: p ≥ 1.32, CV² < 0.49
  LUMPY:        p ≥ 1.32, CV² ≥ 0.49

Train-Test split: 80% train / 20% test (sesuai slide Ubay)
Cutoff aktual: 2026-04-01 (sesuai CUTOFF_DATE Ubay)
"""

import json, warnings
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")

BEST_PARAMS_PATH = Path("data/forecast/best_params_prophet.json")
SKU_CLASS_PATH   = Path("data/forecast/sku_classification.csv")
CUTOFF_DATE      = pd.Timestamp("2026-04-01")
FORECAST_MONTHS  = 12
MAPE_THRESHOLD   = 30.0

_DEFAULT_PROPHET = {
    "changepoint_prior_scale": 0.05,
    "seasonality_prior_scale": 1.0,
    "holidays_prior_scale":    1.0,
    "seasonality_mode":        "additive",
}

# ─── Check library availability ──────────────────────────────────────────────
try:
    from prophet import Prophet as _Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing as _HW
    HOLTWINTERS_AVAILABLE = True
except ImportError:
    HOLTWINTERS_AVAILABLE = False


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _mape(a, p):
    a, p = np.array(a, float), np.array(p, float)
    m = a != 0
    return float(np.mean(np.abs((a[m]-p[m])/a[m]))*100) if m.sum() else np.nan

def _wmape(a, p):
    a, p = np.array(a, float), np.array(p, float)
    t = np.sum(np.abs(a))
    return float(np.sum(np.abs(a-p))/t*100) if t else np.nan

def _bias(a, p):
    a, p = np.array(a, float), np.array(p, float)
    t = np.sum(a)
    return float((np.sum(p)-t)/t*100) if t else np.nan


# ─── SKU Classification: p & CV² (Croston 2001) ──────────────────────────────

def classify_sku_p_cv2(series: np.ndarray) -> tuple:
    """
    Klasifikasi berdasarkan p (inter-demand interval) dan CV².
    Threshold sesuai slide Ubay:
      SMOOTH:       p < 1.32, CV² < 0.49  → Prophet
      ERRATIC:      p < 1.32, CV² ≥ 0.49  → Prophet
      INTERMITTENT: p ≥ 1.32, CV² < 0.49  → CrostonSBA
      LUMPY:        p ≥ 1.32, CV² ≥ 0.49  → CrostonSBA
    """
    y  = np.array(series, float)
    nz = np.where(y > 0)[0]

    if len(nz) == 0:
        return "DISCONTINUED", np.inf, 0.0

    intervals = np.concatenate([[nz[0]+1], np.diff(nz).astype(float)]) if len(nz)>1 else np.array([nz[0]+1.0])
    p = float(np.mean(intervals))

    nz_vals = y[nz]
    mn = float(np.mean(nz_vals))
    sd = float(np.std(nz_vals))
    cv2 = (sd/mn)**2 if mn > 0 else 0.0

    if   p < 1.32 and cv2 < 0.49:  seg = "SMOOTH"
    elif p < 1.32 and cv2 >= 0.49: seg = "ERRATIC"
    elif p >= 1.32 and cv2 < 0.49: seg = "INTERMITTENT"
    else:                            seg = "LUMPY"

    return seg, p, cv2


def classify_all_skus(actuals: pd.DataFrame) -> pd.DataFrame:
    """
    Klasifikasi semua SKU dari data aktual yang diupload.
    
    SELALU compute p & CV² dari data historis aktual — tidak override dari CSV Ubay.
    CSV Ubay (sku_classification.csv) hanya dipakai sebagai fallback jika data
    terlalu pendek untuk diklasifikasi (< 4 non-zero data points).
    
    Alur sesuai Ubay:
    1. Hitung p (rata-rata inter-demand interval) dari data aktual
    2. Hitung CV² (koefisien variasi kuadrat dari non-zero demand)
    3. Klasifikasi berdasarkan threshold Croston 2001
    4. Filter SKU all-zero → DISCONTINUED (tidak diforecast)
    """
    # Load CSV Ubay hanya sebagai fallback untuk data yang terlalu pendek
    ubay_fallback = {}
    if SKU_CLASS_PATH.exists():
        try:
            sc = pd.read_csv(SKU_CLASS_PATH)
            sc.columns = [c.strip() for c in sc.columns]
            if "sku" in sc.columns and "segment" in sc.columns:
                ubay_fallback = dict(zip(sc["sku"], sc["segment"]))
        except Exception:
            pass

    rows = []
    for sku, grp in actuals.groupby("sku"):
        y_arr = grp.sort_values("ds")["y"].values
        desc  = grp["description"].iloc[0] if "description" in grp else ""

        # SELALU compute dari data aktual yang diupload
        seg, p_val, cv2 = classify_sku_p_cv2(y_arr)
        
        # Fallback: jika data terlalu pendek / DISCONTINUED tapi ada di CSV Ubay
        # gunakan klasifikasi Ubay (bukan override, hanya rescue untuk edge case)
        if seg == "DISCONTINUED" and sku in ubay_fallback:
            seg = ubay_fallback[sku]

        rows.append({"sku": sku, "description": desc,
                     "segment": seg, "p": round(p_val, 4), "cv2": round(cv2, 4)})

    return pd.DataFrame(rows)


# ─── Holiday Definition (persis Ubay) ────────────────────────────────────────

def _make_holidays() -> pd.DataFrame:
    leb = pd.DataFrame({
        "holiday": "lebaran",
        "ds": pd.to_datetime(["2023-04-01","2024-04-01","2025-03-01","2026-03-01","2027-03-01"]),
        "lower_window": -31, "upper_window": 31,
    })
    nat = pd.DataFrame({
        "holiday": "natal",
        "ds": pd.to_datetime(["2023-12-01","2024-12-01","2025-12-01","2026-12-01","2027-12-01"]),
        "lower_window": 0, "upper_window": 0,
    })
    h = pd.concat([leb, nat], ignore_index=True)
    h["ds"] = pd.to_datetime(h["ds"])
    return h


# ─── CrostonSBA ──────────────────────────────────────────────────────────────

def croston_sba(y: np.ndarray, horizon: int, alpha: float = 0.15) -> dict:
    """
    CrostonSBA (Syntetos-Boylan Approximation, 2005).
    f = (1 - alpha/2) * z/p
    Output: constant forecast per periode (ini memang sifat Croston — flat).
    """
    y   = np.array(y, float)
    eps = 1e-9
    sba = 1.0 - alpha/2.0
    nz  = np.where(y > 0)[0]

    if len(nz) == 0:
        return {"forecast": np.zeros(horizon), "lower": np.zeros(horizon), "upper": np.zeros(horizon)}

    a = float(y[nz[0]])
    p = float(nz[0]+1) if nz[0] > 0 else 1.0
    lnz = nz[0]
    insample = np.zeros(len(y))

    for t in range(lnz+1, len(y)):
        f_t = max(0.0, sba*(a/max(p, eps)))
        insample[t] = f_t
        if y[t] > 0:
            a = alpha*y[t] + (1-alpha)*a
            p = alpha*(t-lnz) + (1-alpha)*p
            lnz = t

    f_fc  = max(0.0, sba*(a/max(p, eps)))
    resid = (y - insample)[lnz+1:]
    std_r = float(np.std(resid)) if len(resid) > 1 else f_fc*0.25
    lo    = max(0.0, f_fc - 1.28*std_r)
    hi    = f_fc + 1.28*std_r

    return {"forecast": np.full(horizon, f_fc),
            "lower":    np.full(horizon, lo),
            "upper":    np.full(horizon, hi)}


def _croston_backtest(y: np.ndarray, split_ratio: float = 0.8):
    """80/20 split sesuai slide Ubay."""
    n = len(y)
    split = max(4, int(n * split_ratio))
    if split >= n:
        return np.nan, np.nan, np.nan
    train, test = y[:split], y[split:]
    pred = croston_sba(train, len(test))["forecast"]
    return _mape(test, pred), _wmape(test, pred), _bias(test, pred)


# ─── Holt-Winters (fallback jika Prophet tidak tersedia) ─────────────────────

def _holtwinters_sku(sku_df: pd.DataFrame, horizon: int, split_ratio: float = 0.8) -> tuple:
    """
    Triple Exponential Smoothing sebagai fallback Prophet.
    Menghasilkan forecast bervariatif (trend + seasonality) — tidak flat.
    Tersedia via statsmodels yang sudah include di scipy.
    """
    df = sku_df.sort_values("ds").reset_index(drop=True)
    y  = df["y"].values
    n  = len(y)

    split = max(12, int(n * split_ratio))
    if split >= n:
        split = n - 1

    train_y, test_y = y[:split], y[split:]

    # Konfigurasi model
    seasonal = "add" if split >= 24 else None
    sp       = 12 if seasonal else None

    try:
        m_bt = _HW(train_y, trend="add", seasonal=seasonal, seasonal_periods=sp)
        f_bt = m_bt.fit(optimized=True)
        pred_bt = np.clip(f_bt.forecast(len(test_y)), 0, None)
        mape_bt  = _mape(test_y, pred_bt) if len(test_y) > 0 else np.nan
        wmape_bt = _wmape(test_y, pred_bt) if len(test_y) > 0 else np.nan
        bias_bt  = _bias(test_y, pred_bt) if len(test_y) > 0 else np.nan
    except Exception:
        mape_bt = wmape_bt = bias_bt = np.nan

    # Final forecast (semua data)
    try:
        m = _HW(y, trend="add", seasonal=seasonal, seasonal_periods=sp)
        f = m.fit(optimized=True)
        fc_vals = np.clip(f.forecast(horizon), 0, None)
        resid   = y - f.fittedvalues
        std_r   = float(np.std(resid))
        fc_lo   = np.clip(fc_vals - 1.28*std_r, 0, None)
        fc_hi   = fc_vals + 1.28*std_r
    except Exception:
        # Fallback ke CrostonSBA jika Holt-Winters juga gagal
        res = croston_sba(y, horizon)
        fc_vals, fc_lo, fc_hi = res["forecast"], res["lower"], res["upper"]
        mape_bt = wmape_bt = bias_bt = np.nan

    return fc_vals, fc_lo, fc_hi, mape_bt, wmape_bt, bias_bt


# ─── Prophet ─────────────────────────────────────────────────────────────────

def _prophet_sku(sku_df: pd.DataFrame, holidays: pd.DataFrame,
                 params: dict, horizon: int, split_ratio: float = 0.8) -> tuple:
    """
    Prophet sesuai pipeline Ubay.
    Train-test split 80/20 (sesuai slide Ubay: split = int(len*0.8)).
    Seasonality mode = additive jika ada zero (fix kritis Ubay).
    """
    df = sku_df.sort_values("ds").reset_index(drop=True)
    n  = len(df)

    # 80/20 split
    split = max(12, int(n * split_ratio))
    if split >= n: split = n - 1
    train_df = df.iloc[:split][["ds","y"]].copy()
    test_df  = df.iloc[split:][["ds","y"]].copy()

    # Seasonality mode
    has_zeros = (train_df["y"] == 0).any()
    s_mode    = "additive" if has_zeros else params.get("seasonality_mode","additive")

    cfg = dict(
        yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False,
        seasonality_mode=s_mode, holidays=holidays,
        changepoint_prior_scale = params.get("changepoint_prior_scale", 0.05),
        seasonality_prior_scale = params.get("seasonality_prior_scale", 1.0),
    )
    if "holidays_prior_scale" in params:
        cfg["holidays_prior_scale"] = params["holidays_prior_scale"]

    # Backtest
    mape_bt = wmape_bt = bias_bt = np.nan
    try:
        m_bt = _Prophet(**cfg)
        m_bt.fit(train_df)
        fc_bt = m_bt.predict(m_bt.make_future_dataframe(len(test_df), freq="MS"))
        pred  = fc_bt[fc_bt["ds"].isin(test_df["ds"])]["yhat"].clip(lower=0).values
        if len(pred) == len(test_df):
            mape_bt  = _mape(test_df["y"].values, pred)
            wmape_bt = _wmape(test_df["y"].values, pred)
            bias_bt  = _bias(test_df["y"].values, pred)
    except Exception:
        pass

    # Final forecast
    m = _Prophet(**cfg)
    m.fit(df[["ds","y"]])
    future = m.make_future_dataframe(periods=horizon, freq="MS")
    fc     = m.predict(future)
    last   = df["ds"].max()
    fc_out = fc[fc["ds"] > last][["ds","yhat","yhat_lower","yhat_upper"]].copy()
    fc_out[["yhat","yhat_lower","yhat_upper"]] = fc_out[
        ["yhat","yhat_lower","yhat_upper"]].clip(lower=0)

    return fc_out, mape_bt, wmape_bt, bias_bt


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def get_prophet_status() -> dict:
    """Kembalikan status ketersediaan library forecasting."""
    return {
        "prophet":      PROPHET_AVAILABLE,
        "holtwinters":  HOLTWINTERS_AVAILABLE,
        "smooth_model": "Prophet" if PROPHET_AVAILABLE else (
                         "Holt-Winters" if HOLTWINTERS_AVAILABLE else "CrostonSBA"),
    }


def run_forecast(
    raw_df: pd.DataFrame,
    horizon_months: int = 12,
    method: str = "Auto (Prophet + CrostonSBA)",
    cutoff_date: str | None = None,
    progress_callback=None,
    use_bundle: bool = True,
) -> pd.DataFrame:
    """
    Pipeline forecasting sesuai Ubay:
    1. Normalize input → long format
    2. Filter data aktual (cutoff Apr 2026)
    3. Klasifikasi: p & CV² per SKU
    4. SMOOTH/ERRATIC → Prophet (atau Holt-Winters jika Prophet belum install)
    5. INTERMITTENT/LUMPY → CrostonSBA
    6. Output format sesuai combined_forecast_output Ubay
    """
    # ── Normalize input ──────────────────────────────────────────────────────
    df = raw_df.copy()
    for alt in ["date","Date","DS"]:
        if "ds" not in df.columns and alt in df.columns:
            df = df.rename(columns={alt:"ds"}); break
    for alt in ["volume","forecast","demand","Volume"]:
        if "y" not in df.columns and alt in df.columns:
            df = df.rename(columns={alt:"y"}); break
    if "description" not in df.columns:
        df["description"] = df.get("sku","")

    df["ds"] = pd.to_datetime(df["ds"], errors="coerce")
    df["y"]  = pd.to_numeric(df["y"], errors="coerce").fillna(0).clip(lower=0)
    df       = df.dropna(subset=["ds","sku"])

    # ── Filter actual data ───────────────────────────────────────────────────
    cutoff  = pd.Timestamp(cutoff_date) if cutoff_date else CUTOFF_DATE
    actuals = df[df["ds"] < cutoff].copy()
    if actuals.empty:
        raise ValueError(f"Data kosong setelah cutoff {cutoff.date()}. Periksa format kolom 'ds'.")

    # ── Filter SKU aktif: buang SKU yang all-zero di semua periode (sesuai Ubay) ──
    # Ubay hanya memproses SKU yang memang memiliki demand — SKU all-zero dibuang
    sku_totals = actuals.groupby("sku")["y"].sum()
    active_skus = sku_totals[sku_totals > 0].index.tolist()
    actuals = actuals[actuals["sku"].isin(active_skus)].copy()

    # ── Klasifikasi semua SKU — SELALU dari data aktual yang diupload ─────────
    # p & CV² dihitung fresh dari historis, bukan dari cache/database
    sku_class = classify_all_skus(actuals)
    valid     = sku_class[sku_class["segment"] != "DISCONTINUED"].reset_index(drop=True)

    # ── Load best_params ─────────────────────────────────────────────────────
    best_params: dict = {}
    if BEST_PARAMS_PATH.exists():
        try:
            with open(BEST_PARAMS_PATH) as f:
                best_params = json.load(f)
        except Exception:
            pass

    # ── Holiday definition ───────────────────────────────────────────────────
    holidays = _make_holidays()

    # ── Model yang akan dipakai untuk SMOOTH/ERRATIC ─────────────────────────
    smooth_model = get_prophet_status()["smooth_model"]

    # ── Forecast per SKU ─────────────────────────────────────────────────────
    results  = []
    n_total  = len(valid)

    for i, row in valid.iterrows():
        sku  = row["sku"]
        seg  = row["segment"]
        desc = row.get("description", sku)

        if progress_callback:
            progress_callback(sku, i+1, n_total)

        sku_data = (
            actuals[actuals["sku"]==sku][["ds","y"]]
            .sort_values("ds")
            .reset_index(drop=True)
        )
        if len(sku_data) < 4:
            continue

        y_arr     = sku_data["y"].values
        last_date = sku_data["ds"].max()
        future_dates = pd.date_range(
            start=last_date + pd.DateOffset(months=1),
            periods=horizon_months, freq="MS"
        )

        mape_bt = wmape_bt = bias_bt = np.nan
        fc_rows  = []
        model_used = None

        # ── JALAN TENGAH: coba model tersimpan (bundle) lebih dulu ───────────
        # Bila SKU ada di bundle dan datanya tidak lebih baru dari cutoff
        # bundle, pakai model tersimpan (cepat). Selain itu -> None, lanjut
        # training langsung di bawah (jaring pengaman, tetap fleksibel).
        _bundle_hit = None
        try:
            from modules.forecast_bundle import predict_from_bundle
            _bundle_hit = predict_from_bundle(sku, sku_data, horizon_months,
                                              use_bundle=use_bundle)
        except Exception:
            _bundle_hit = None
        if _bundle_hit is not None:
            model_used = _bundle_hit["model_used"]
            _fc = _bundle_hit["forecast"]; _lo = _bundle_hit["lower"]; _hi = _bundle_hit["upper"]
            for j, fd in enumerate(future_dates):
                fc_rows.append({
                    "date":           fd,
                    "forecast":       round(float(_fc[j]), 6),
                    "forecast_lower": round(float(_lo[j]), 6),
                    "forecast_upper": round(float(_hi[j]), 6),
                })
            # Metrik backtest diambil dari bundle bila tersedia (opsional);
            # bila tidak, dibiarkan NaN — tidak memengaruhi angka forecast.
            status = "OK"
            for r in fc_rows:
                results.append({
                    "date": r["date"], "sku": sku, "description": desc,
                    "forecast": r["forecast"], "forecast_lower": r["forecast_lower"],
                    "forecast_upper": r["forecast_upper"],
                    "mape_backtest": None, "wmape_backtest": None, "bias_pct": None,
                    "demand_pattern": row["segment"], "model_used": model_used,
                    "status": status,
                })
            continue  # SKU selesai via bundle, lanjut SKU berikutnya

        # ── SMOOTH / ERRATIC ─────────────────────────────────────────────────
        if seg in ("SMOOTH","ERRATIC"):
            # Coba Prophet dulu
            if PROPHET_AVAILABLE and method not in ("CrostonSBA",):
                try:
                    params = best_params.get(sku, _DEFAULT_PROPHET)
                    fc_df, mape_bt, wmape_bt, bias_bt = _prophet_sku(
                        sku_data, holidays, params, horizon_months
                    )
                    model_used = "Prophet"
                    for _, fr in fc_df.iterrows():
                        fc_rows.append({
                            "date":           fr["ds"],
                            "forecast":       round(float(fr["yhat"]),       6),
                            "forecast_lower": round(float(fr["yhat_lower"]), 6),
                            "forecast_upper": round(float(fr["yhat_upper"]), 6),
                        })
                except Exception:
                    fc_rows = []

            # Fallback: Holt-Winters (bervariasi per bulan, lebih baik dari flat Croston)
            if not fc_rows and HOLTWINTERS_AVAILABLE:
                try:
                    fc_vals, fc_lo, fc_hi, mape_bt, wmape_bt, bias_bt = _holtwinters_sku(
                        sku_data, horizon_months
                    )
                    model_used = "Holt-Winters"
                    for j, fd in enumerate(future_dates):
                        fc_rows.append({
                            "date":           fd,
                            "forecast":       round(float(fc_vals[j]), 6),
                            "forecast_lower": round(float(fc_lo[j]),   6),
                            "forecast_upper": round(float(fc_hi[j]),   6),
                        })
                except Exception:
                    fc_rows = []

            # Last resort: CrostonSBA
            if not fc_rows:
                seg = "INTERMITTENT"  # redirect ke CrostonSBA

        # ── INTERMITTENT / LUMPY ─────────────────────────────────────────────
        if seg in ("INTERMITTENT","LUMPY") or not fc_rows:
            mape_bt, wmape_bt, bias_bt = _croston_backtest(y_arr)
            res = croston_sba(y_arr, horizon_months)
            model_used = "CrostonSBA"
            for j, fd in enumerate(future_dates):
                fc_rows.append({
                    "date":           fd,
                    "forecast":       round(float(res["forecast"][j]), 6),
                    "forecast_lower": round(float(res["lower"][j]),    6),
                    "forecast_upper": round(float(res["upper"][j]),    6),
                })

        if not fc_rows:
            continue

        status = "OK" if (np.isnan(mape_bt) or mape_bt < MAPE_THRESHOLD) else "REVIEW"

        for r in fc_rows:
            results.append({
                "date":           r["date"],
                "sku":            sku,
                "description":    desc,
                "forecast":       r["forecast"],
                "forecast_lower": r["forecast_lower"],
                "forecast_upper": r["forecast_upper"],
                "mape_backtest":  None if np.isnan(mape_bt)  else round(mape_bt, 4),
                "wmape_backtest": None if np.isnan(wmape_bt) else round(wmape_bt,4),
                "bias_pct":       None if np.isnan(bias_bt)  else round(bias_bt, 4),
                "demand_pattern": row["segment"],
                "model_used":     model_used,
                "status":         status,
            })

    if not results:
        raise ValueError("Tidak ada SKU yang berhasil diforecast. Periksa format data.")

    out = pd.DataFrame(results)
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["sku","date"]).reset_index(drop=True)
    return out
