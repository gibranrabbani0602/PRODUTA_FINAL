"""
forecast_bundle.py — Jalan tengah pemakaian model forecast tersimpan (.pkl).

Filosofi: bundle adalah SHORTCUT, bukan keharusan. Training langsung (di
forecast_engine.run_forecast) tetap menjadi jaring pengaman penuh. Per SKU,
sistem memilih:

  - SKU ada di bundle DAN histori tidak melampaui cutoff bundle  -> pakai
    model tersimpan (cepat, identik dengan hasil Colab).
  - selain itu (SKU baru / data lebih baru / bundle gagal)        -> None,
    sehingga pemanggil melakukan training langsung seperti biasa.

Modul ini TIDAK pernah melempar error yang menggagalkan forecast — setiap
kegagalan menghasilkan None dan pipeline jatuh mulus ke training langsung.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np

BUNDLE_PATH = Path("models/models_bundle.pkl")

# Cache proses agar bundle tidak di-load berulang dalam satu sesi
_CACHE: dict | None = None
_LOAD_TRIED = False
_LOAD_ERROR: str | None = None


def bundle_status() -> dict:
    """Ringkasan kondisi bundle untuk ditampilkan di UI (tanpa memuat ulang)."""
    b = _try_load()
    if not b:
        return {"available": False, "error": _LOAD_ERROR,
                "n_prophet": 0, "n_croston": 0, "cutoff": None, "saved_at": None}
    return {
        "available": True, "error": None,
        "n_prophet": len(b.get("prophet_models", {})),
        "n_croston": len(b.get("croston_models", {})),
        "skus": set(b.get("prophet_models", {}).keys())
                | set(b.get("croston_models", {}).keys()),
        "cutoff": b.get("cutoff"),
        "saved_at": b.get("saved_at"),
        "n_total": b.get("n_skus_total"),
    }


def _try_load() -> dict | None:
    """Muat bundle sekali, simpan di cache proses. Gagal -> None (aman)."""
    global _CACHE, _LOAD_TRIED, _LOAD_ERROR
    if _LOAD_TRIED:
        return _CACHE
    _LOAD_TRIED = True
    if not BUNDLE_PATH.exists():
        _LOAD_ERROR = "File models/models_bundle.pkl tidak ditemukan."
        _CACHE = None
        return None
    try:
        import joblib  # noqa
        _CACHE = joblib.load(BUNDLE_PATH)
        if not isinstance(_CACHE, dict):
            _LOAD_ERROR = "Format bundle tidak dikenali."
            _CACHE = None
    except Exception as e:  # dependency hilang / versi beda / korup
        _LOAD_ERROR = f"Bundle tidak dapat dimuat ({type(e).__name__}). "\
                      "Sistem memakai training langsung."
        _CACHE = None
    return _CACHE


def _bundle_cutoff(b: dict) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(str(b.get("cutoff")))
    except Exception:
        return None


def predict_from_bundle(sku: str, sku_data: pd.DataFrame, horizon: int,
                        use_bundle: bool = True) -> dict | None:
    """
    Coba hasilkan forecast SKU dari model tersimpan.

    Return dict {forecast, lower, upper, model_used, source} bila berhasil,
    atau None bila SKU harus diproses dengan training langsung.

    sku_data: DataFrame kolom ['ds','y'] (histori aktual SKU).
    """
    if not use_bundle:
        return None
    b = _try_load()
    if not b:
        return None

    # Guard kesegaran: bila data punya bulan lebih baru dari cutoff bundle,
    # model tersimpan dianggap basi untuk SKU ini -> training langsung.
    _cut = _bundle_cutoff(b)
    if _cut is not None and not sku_data.empty:
        try:
            _last = pd.to_datetime(sku_data["ds"]).max()
            if _last >= _cut:
                return None
        except Exception:
            pass

    last_date = pd.to_datetime(sku_data["ds"]).max()
    future = pd.date_range(start=last_date + pd.DateOffset(months=1),
                           periods=horizon, freq="MS")

    # ── Prophet tersimpan ────────────────────────────────────────────────
    pm = b.get("prophet_models", {})
    if sku in pm:
        try:
            model = pm[sku]
            fdf = model.make_future_dataframe(periods=horizon, freq="MS")
            fc = model.predict(fdf)
            fc = fc[fc["ds"] > last_date].head(horizon)
            if len(fc) == horizon:
                return {
                    "forecast": fc["yhat"].clip(lower=0).to_numpy(),
                    "lower":    fc["yhat_lower"].clip(lower=0).to_numpy(),
                    "upper":    fc["yhat_upper"].clip(lower=0).to_numpy(),
                    "model_used": "Prophet (model tersimpan)",
                    "source": "bundle",
                }
        except Exception:
            return None  # jatuh ke training langsung

    # ── Croston/SBA tersimpan ────────────────────────────────────────────
    cm = b.get("croston_models", {})
    if sku in cm:
        try:
            entry = cm[sku]
            # Format Colab: (StatsForecast_object, model_name)
            sf_obj, model_name = entry if isinstance(entry, tuple) else (entry, None)
            pred = sf_obj.predict(h=horizon)
            col = model_name if (model_name and model_name in pred.columns) else \
                  [c for c in pred.columns if c not in ("unique_id", "ds")][0]
            vals = np.asarray(pred[col]).clip(min=0)[:horizon]
            if len(vals) == horizon:
                return {
                    "forecast": vals,
                    "lower":    vals * 0.85,
                    "upper":    vals * 1.15,
                    "model_used": "CrostonSBA (model tersimpan)",
                    "source": "bundle",
                }
        except Exception:
            return None

    # SKU tidak ada di bundle -> training langsung
    return None
