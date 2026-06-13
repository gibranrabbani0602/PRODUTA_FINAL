"""
modules/capacity_model.py

Two redistribution models:
  Multiline conversion (B/G/BG):
    Proportional redistribution with 70/30 BIB/SSS split.
    All lines get rebalanced; D stays SSS-only.

  New line (new_line / multiline_BG_new):
    Additive model: existing lines keep current production,
    new line covers unmet demand at required utilization.
    More realistic operationally — new line is independently scheduled.
"""
import pandas as pd
import numpy as np

MULTILINE_FACTOR_SSS_BIB = 3.0
WOLF_VPC250_RATE         = 0.22   # ton/hr (220 kg/hr)
BIB_FRAC                 = 0.70
SSS_FRAC                 = 0.30
UTIL_OPTIMAL_LOW         = 0.30   # below this → over-invested
UTIL_OPTIMAL_HIGH        = 0.75   # above this → still stressed


def _practical_max(cap_b, cap_g, cap_d, cap_c,
                   util_b, util_g, util_d,
                   mod_b=False, mod_g=False, has_new=False):
    """
    Practical max capacity: each line runs at its pre-upgrade utilization rate.
    Modified/new lines use avg of B+G utilization as reference.
    This avoids inflating capacity by using an arbitrary 80% target.
    """
    avg_bg = (util_b + util_g) / 2 / 100
    prod_b = cap_b * (avg_bg if mod_b else util_b/100)
    prod_g = cap_g * (avg_bg if mod_g else util_g/100)
    prod_d = cap_d * (util_d / 100)   # D unchanged always
    prod_c = cap_c * avg_bg if has_new else 0
    return prod_b + prod_g + prod_d + prod_c


def _safe(row, key, default=0.0):
    v = str(row.get(key, default)).replace("%","").strip().replace("[cek P_D]","0")
    try: return float(v)
    except: return float(default)


def _cap100(tons, util_pct):
    return float(tons) / (float(util_pct)/100) if util_pct > 0 else 0.0


def _avg_post_util(upd: dict) -> float:
    """Weighted average utilization of all lines after upgrade."""
    utils = [upd.get("Util_Filling_B_new",0),
             upd.get("Util_Filling_G_new",0),
             upd.get("Util_Filling_D_new",0)]
    if "Util_Filling_C_new" in upd:
        utils.append(upd["Util_Filling_C_new"])
    valid = [u for u in utils if u > 0]
    return sum(valid)/len(valid) if valid else 0


def diagnose_bottleneck(row):
    utils = {
        "Line B": _safe(row,"Util_Filling_B"),
        "Line G": _safe(row,"Util_Filling_G"),
        "Line D": _safe(row,"Util_Filling_D"),
    }
    tons = {
        "Line B": _safe(row,"Tons_B"),
        "Line G": _safe(row,"Tons_G"),
        "Line D": _safe(row,"Tons_D"),
    }
    unmet  = _safe(row,"Unmet_Demand")
    target = max(_safe(row,"Target_Demand_Ton"), 1)
    ur     = unmet / target * 100
    primary = max(utils, key=utils.get)
    mu      = utils[primary]
    uvals   = list(utils.values())
    # Severity berbasis FIS score (gradasi, bukan threshold biner)
    from modules.fis_engine import compute_fis as _cfis, fis_severity_label as _fsl
    _score = _cfis(mu, ur, 100 - ur if ur > 0 else 100)
    sev    = _fsl(_score)
    return {"primary_bottleneck": primary, "max_util": round(mu,1),
            "severity": sev, "unmet_ratio": round(ur,1),
            "load_imbalance": round(max(uvals)-min(uvals),1),
            "utils": utils, "tons": tons}


def estimate_upgrade(row, option, new_line_days=7, new_line_hrs=24):
    target   = max(_safe(row,"Target_Demand_Ton"), 1)
    tons_b   = _safe(row,"Tons_B")
    tons_g   = _safe(row,"Tons_G")
    tons_d   = _safe(row,"Tons_D")
    util_b   = _safe(row,"Util_Filling_B")
    util_g   = _safe(row,"Util_Filling_G")
    util_d   = _safe(row,"Util_Filling_D")
    tons_fin = _safe(row,"Tons_Finished")
    unmet    = _safe(row,"Unmet_Demand")

    # Per-line schedule from new simulation format (formula Asil #15-16)
    b_days = max(_safe(row,"B_Days", _safe(row,"Days_Per_Week",7)), 1)
    b_hrs  = max(_safe(row,"B_Hours",_safe(row,"Working_Hours",24)), 1)
    g_days = max(_safe(row,"G_Days", _safe(row,"Days_Per_Week",7)), 1)
    g_hrs  = max(_safe(row,"G_Hours",_safe(row,"Working_Hours",24)), 1)
    d_days = max(_safe(row,"D_Days",7), 1)
    d_hrs  = max(_safe(row,"D_Hours",24), 1)

    # Calibrated throughput from simulation (Asil formula #27):
    # Processing_hrs = util/100 × (days/7 × 349) × hours
    # rate = tons / processing_hrs  → ton/hr
    b_eff_hrs = max((util_b/100) * (b_days/7 * 349) * b_hrs, 0.1)
    g_eff_hrs = max((util_g/100) * (g_days/7 * 349) * g_hrs, 0.1)
    rate_b = tons_b / b_eff_hrs   # ton/hr (calibrated from simulation)
    rate_g = tons_g / g_eff_hrs
    rate_new = (rate_b + rate_g) / 2  # new line same machine type as B/G

    cap_b = _cap100(tons_b, util_b)
    cap_g = _cap100(tons_g, util_g)
    cap_d = _cap100(tons_d, util_d)
    # New line capacity calibrated from simulation B/G rate (not theoretical machine spec)
    cap_c_full = rate_new * (new_line_days/7 * 349) * new_line_hrs
    # Derived fallback values (for backward compat with helper functions)
    eff_days = max(b_days, g_days)
    work_hrs = max(b_hrs, g_hrs)

    # ── Model selection ────────────────────────────────────────────────────────
    if option in ("multiline_G","multiline_B","multiline_BG"):
        return _multiline_redist(
            option, target, tons_b, tons_g, tons_d,
            util_b, util_g, util_d, cap_b, cap_g, cap_d,
            tons_fin, False, 0.0, 0.0
        )
    elif option == "new_line":
        return _additive_new_line(
            tons_b, tons_g, tons_d, util_b, util_g, util_d,
            cap_b, cap_g, cap_d, cap_c_full, tons_fin, unmet, target,
            eff_days, work_hrs
        )
    elif option == "multiline_BG_new":
        # BG multiline fully covers all demand. New line = capacity reserve for future growth.
        upd = _multiline_redist(
            "multiline_BG", target, tons_b, tons_g, tons_d,
            util_b, util_g, util_d, cap_b, cap_g, cap_d,
            tons_fin, False, 0.0, 0.0
        )
        # Line C: 0 current production (no demand left), but exists as reserve capacity.
        # Calibrate C capacity from avg B/G rate (same as new_line option).
        cap_c_cal = rate_new * (new_line_days/7 * 349) * new_line_hrs   # calibrated C capacity
        upd["Tons_C_new"]         = 0.0   # no current demand, fully covered by B+G
        upd["Util_Filling_C_new"] = 0.0   # idle — reserve for future demand
        upd["Cap_C_100"]          = round(cap_c_cal, 2)
        # Practical max includes C at avg B/G util (future growth potential)
        cap_b_new = cap_b * MULTILINE_FACTOR_SSS_BIB
        cap_g_new = cap_g * MULTILINE_FACTOR_SSS_BIB
        avg_util  = (util_b + util_g) / 2 / 100
        pmax = _practical_max(cap_b_new, cap_g_new, cap_d, cap_c_cal,
                              util_b, util_g, util_d, True, True, True)
        upd["Max_Capacity_Practical"] = round(pmax, 2)
        upd["Practical_Headroom"]     = round(max(0, pmax - target), 2)
        upd["_note_c"] = (
            "Lini baru tidak memiliki beban produksi saat ini — seluruh demand "
            "sudah dipenuhi oleh B+G multiline. Lini baru berperan sebagai "
            "kapasitas cadangan untuk pertumbuhan demand di masa mendatang."
        )
        return upd
    return {}


def _multiline_redist(option, target,
                       tons_b, tons_g, tons_d,
                       util_b, util_g, util_d,
                       cap_b, cap_g, cap_d,
                       tons_fin, has_new, cap_c_full, unmet):
    """Proportional 70/30 redistribution for multiline conversions."""
    cap_b_new = cap_b * MULTILINE_FACTOR_SSS_BIB if "B" in option.split("_")[-1].upper() or "BG" in option else cap_b
    # Simpler: check option string
    cap_b_new = cap_b * MULTILINE_FACTOR_SSS_BIB if option in ("multiline_B","multiline_BG","multiline_BG_new") else cap_b
    cap_g_new = cap_g * MULTILINE_FACTOR_SSS_BIB if option in ("multiline_G","multiline_BG","multiline_BG_new") else cap_g
    cap_c     = cap_c_full if has_new else 0.0

    # SSS/BIB demand split from current production
    bg_prod = tons_b + tons_g
    sss_curr = bg_prod * SSS_FRAC + tons_d
    bib_curr = bg_prod * BIB_FRAC
    tot_curr = max(sss_curr + bib_curr, 1)
    sss_dem  = target * (sss_curr / tot_curr)
    bib_dem  = target * (bib_curr / tot_curr)

    # Capacity by product type
    tot_bib_cap = cap_b_new*BIB_FRAC + cap_g_new*BIB_FRAC + cap_c*BIB_FRAC
    tot_sss_cap = cap_b_new*SSS_FRAC + cap_g_new*SSS_FRAC + cap_d + cap_c*SSS_FRAC
    tot_sys_cap = tot_bib_cap + tot_sss_cap

    if tot_sys_cap >= target:
        tb_bib = bib_dem * (cap_b_new*BIB_FRAC / max(tot_bib_cap,1))
        tg_bib = bib_dem * (cap_g_new*BIB_FRAC / max(tot_bib_cap,1))
        tc_bib = bib_dem * (cap_c*BIB_FRAC / max(tot_bib_cap,1)) if has_new else 0
        tb_sss = sss_dem * (cap_b_new*SSS_FRAC / max(tot_sss_cap,1))
        tg_sss = sss_dem * (cap_g_new*SSS_FRAC / max(tot_sss_cap,1))
        td_sss = sss_dem * (cap_d / max(tot_sss_cap,1))
        tc_sss = sss_dem * (cap_c*SSS_FRAC / max(tot_sss_cap,1)) if has_new else 0
        tons_b_new = tb_bib + tb_sss
        tons_g_new = tg_bib + tg_sss
        tons_d_new = td_sss
        tons_c_new = tc_bib + tc_sss if has_new else 0
        fin = target; unm = 0.0; fr = 100.0
    else:
        sc = tot_sys_cap / target
        tons_b_new = cap_b_new * sc; tons_g_new = cap_g_new * sc
        tons_d_new = cap_d * sc;    tons_c_new = cap_c * sc if has_new else 0
        fin = min(target, tons_b_new+tons_g_new+tons_d_new+tons_c_new)
        unm = max(0, target-fin); fr = fin/target*100

    ub = tons_b_new/cap_b_new*100 if cap_b_new > 0 else 0
    ug = tons_g_new/cap_g_new*100 if cap_g_new > 0 else 0
    ud = tons_d_new/cap_d*100     if cap_d     > 0 else util_d
    uc = tons_c_new/cap_c*100     if (has_new and cap_c>0) else 0

    mod_b = option in ("multiline_B","multiline_BG","multiline_BG_new")
    mod_g = option in ("multiline_G","multiline_BG","multiline_BG_new")
    pmax = _practical_max(cap_b_new, cap_g_new, cap_d, cap_c if has_new else 0,
                          util_b, util_g, util_d, mod_b, mod_g, has_new)
    r = {
        "Tons_Finished_new":   round(fin,2),  "Unmet_Demand_new":    round(unm,2),
        "Finished_Ratio_new":  round(fr,2),   "Additional_Capacity": round(fin-tons_fin,2),
        "Max_Capacity_Practical": round(pmax, 2),
        "Tons_B_new":          round(tons_b_new,2), "Tons_G_new": round(tons_g_new,2),
        "Tons_D_new":          round(tons_d_new,2),
        "Util_Filling_B_new":  round(ub,1),   "Util_Filling_G_new":  round(ug,1),
        "Util_Filling_D_new":  round(ud,1),
    }
    if has_new:
        r["Tons_C_new"] = round(tons_c_new,2)
        r["Util_Filling_C_new"] = round(uc,1)
    r["Practical_Headroom"] = round(max(0, pmax - target), 2)
    return r


def _additive_new_line(tons_b, tons_g, tons_d,
                        util_b, util_g, util_d,
                        cap_b, cap_g, cap_d,
                        cap_c_full, tons_fin, unmet, target,
                        eff_days, work_hrs):
    """
    New line (same type as B/G): proportional redistribution.
    Capacity calibrated from average B/G throughput rate so new line
    has comparable capacity to existing B and G.
    All lines share demand proportionally — new line gets its fair share.
    """
    # Use cap_c_full directly — already calibrated from simulation B/G rate
    # × user-selected new_line_days and new_line_hrs in estimate_upgrade
    # This is the key: cap_c must reflect the chosen new line schedule
    cap_c = cap_c_full

    # Use same 70/30 redistribution as multiline, now with 4 lines (B,G,D,C)
    bg_prod = tons_b + tons_g
    sss_curr = bg_prod * SSS_FRAC + tons_d
    bib_curr = bg_prod * BIB_FRAC
    tot_curr = max(sss_curr + bib_curr, 1)
    sss_dem  = target * (sss_curr / tot_curr)
    bib_dem  = target * (bib_curr / tot_curr)

    # BIB: B, G, C  (D cannot handle BIB)
    tot_bib = cap_b*BIB_FRAC + cap_g*BIB_FRAC + cap_c*BIB_FRAC
    tb_bib  = bib_dem * (cap_b*BIB_FRAC / max(tot_bib,1))
    tg_bib  = bib_dem * (cap_g*BIB_FRAC / max(tot_bib,1))
    tc_bib  = bib_dem * (cap_c*BIB_FRAC / max(tot_bib,1))

    # SSS: B, G, D, C
    tot_sss = cap_b*SSS_FRAC + cap_g*SSS_FRAC + cap_d + cap_c*SSS_FRAC
    tb_sss  = sss_dem * (cap_b*SSS_FRAC / max(tot_sss,1))
    tg_sss  = sss_dem * (cap_g*SSS_FRAC / max(tot_sss,1))
    td_sss  = sss_dem * (cap_d / max(tot_sss,1))
    tc_sss  = sss_dem * (cap_c*SSS_FRAC / max(tot_sss,1))

    tons_b_new = tb_bib + tb_sss
    tons_g_new = tg_bib + tg_sss
    tons_d_new = td_sss
    tons_c_new = tc_bib + tc_sss

    fin = target; unm = 0.0; fr = 100.0
    if (cap_b + cap_g + cap_d + cap_c) < target:
        fin = min(target, tons_b_new+tons_g_new+tons_d_new+tons_c_new)
        unm = max(0, target-fin); fr = fin/target*100

    ub = tons_b_new/cap_b*100 if cap_b>0 else 0
    ug = tons_g_new/cap_g*100 if cap_g>0 else 0
    ud = tons_d_new/cap_d*100 if cap_d>0 else util_d
    uc = tons_c_new/cap_c*100 if cap_c>0 else 0

    pmax = _practical_max(cap_b, cap_g, cap_d, cap_c,
                          util_b, util_g, util_d, False, False, True)
    return {
        "Tons_Finished_new":   round(fin,2),   "Unmet_Demand_new":    round(unm,2),
        "Finished_Ratio_new":  round(fr,2),    "Additional_Capacity": round(fin-tons_fin,2),
        "Max_Capacity_Practical": round(pmax, 2),
        "Practical_Headroom":  round(max(0, pmax - target), 2),
        "Tons_B_new":  round(tons_b_new,2), "Tons_G_new": round(tons_g_new,2),
        "Tons_D_new":  round(tons_d_new,2), "Tons_C_new": round(tons_c_new,2),
        "Util_Filling_B_new":  round(ub,1), "Util_Filling_G_new": round(ug,1),
        "Util_Filling_D_new":  round(ud,1), "Util_Filling_C_new": round(uc,1),
        "Cap_C_100":   round(cap_c,2),
    }


def score_option(upd: dict, fin_result: dict, util_warn: float = 65.0) -> dict:
    """
    Score upgrade option for ranking:
      1. Coverage (demand fully met = mandatory)
      2. Post-upgrade utilization balance (closer to 50-65% = better)
      3. NPV (higher = better)
      4. Headroom adequacy (15-50% above demand = ideal)
    """
    fr     = upd.get("Finished_Ratio_new", 0)
    avg_u  = _avg_post_util(upd)
    npv    = fin_result.get("npv", 0) if fin_result else 0
    target_assumed = 11944  # fallback; will be overridden when called

    # Coverage: must be >= 99% to be viable
    coverage_ok = fr >= 99.0

    # Utilization score: 0-1, peaks at util_warn%
    u_ideal = util_warn
    u_score = 1.0 - abs(avg_u - u_ideal) / 100.0
    u_score = max(0, u_score)

    # NPV normalised
    npv_score = min(npv / 5e9, 1.0) if npv > 0 else 0.0

    # Composite (coverage is gate, rest weighted)
    composite = (0.5 * u_score + 0.5 * npv_score) if coverage_ok else 0.0

    return {
        "coverage_ok": coverage_ok,
        "avg_util": round(avg_u, 1),
        "npv": npv,
        "composite": round(composite, 4),
    }


def recommend_options(diag, unmet_demand):
    ur = diag["unmet_ratio"]
    f  = MULTILINE_FACTOR_SSS_BIB
    cap_c_period = WOLF_VPC250_RATE * 349 * 24  # ~1,843 ton/period

    opts = []
    opts.append({"id":"multiline_G","tier":1,
        "label":"Konversi Line G → Multiline (SSS+BIB, 6 jalur)",
        "impact_desc":"Kapasitas meningkat signifikan. Utilisasi terdistribusi ulang ke semua lini.",
        "rationale":"Paling efisien: G sudah menangani SSS+BIB. Utilisasi D lebih tinggi dari B/G karena D hanya SSS.",
        "components":_ml_comps()})
    opts.append({"id":"multiline_B","tier":1,
        "label":"Konversi Line B → Multiline (SSS+BIB, 6 jalur)",
        "impact_desc":"Kapasitas meningkat signifikan. Hasil redistribusi setara dengan opsi konversi G.",
        "rationale":"Alternatif setara Opsi G. Dipilih berdasarkan kemudahan modifikasi fisik.",
        "components":_ml_comps()})
    if ur > 15:
        opts.append({"id":"multiline_BG","tier":2,
            "label":"Konversi Line B + G → Multiline (SSS+BIB)",
            "impact_desc":"Kapasitas kedua lini meningkat. Headroom demand besar untuk pertumbuhan jangka menengah.",
            "rationale":f"Unmet {ur:.1f}%. Dua lini di-upgrade untuk cadangan pertumbuhan demand jangka menengah.",
            "components":_ml_comps(qty=2)})
    opts.append({"id":"new_line","tier":3,
        "label":"Tambah Lini Baru (SSS+BIB)",
        "impact_desc":("Kapasitas tambahan dari lini independen. "
                       "Lini existing tidak terganggu. "
                       "Utilisasi terdistribusi proporsional setelah lini baru beroperasi."),
        "rationale":"Tidak mengganggu jadwal lini existing. Utilisasi lini existing tetap, headroom lebih terkontrol.",
        "components":_nl_comps()})
    if ur > 25:
        opts.append({"id":"multiline_BG_new","tier":4,
            "label":"Konversi B+G Multiline + Lini Baru",
            "impact_desc":"Kapasitas sistem maksimum. Lini baru menambah headroom.",
            "rationale":f"Unmet {ur:.1f}% sangat tinggi.",
            "components":_ml_comps(qty=2)+_nl_comps()})
    return opts


def _ml_comps(qty=1):
    return [
        {"key":"micro_auger",          "qty":6*qty},
        {"key":"filler_multilane_sss", "qty":1*qty},
        {"key":"conveyor_z",           "qty":6*qty},
        {"key":"conveyor_multistrand", "qty":1*qty},
    ]

def _nl_comps():
    return [
        {"key":"feeder_screw",        "qty":1},
        {"key":"auger_dosing",        "qty":1},
        {"key":"filler_vertikal_uni", "qty":1},
        {"key":"conveyor_z",          "qty":1},
        {"key":"conveyor_belt",       "qty":1},
        {"key":"checkweigher",        "qty":1},
        {"key":"xray",                "qty":1},
    ]
