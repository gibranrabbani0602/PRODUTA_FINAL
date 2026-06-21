"""
Evaluasi Kapasitas — 2 tab: Evaluasi Skenario | Diagnosa & Rekomendasi
Sistem bersifat generik: jumlah dan identitas lini dibaca dari kolom data CSV,
tidak di-hardcode. Setiap parameter mengikuti catalog dari Parameter & Katalog Investasi.
"""
import re as _re
import json as _json_cp
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from modules.session import get, set_, get_state, set_state, upload_widget
from modules.data_loader import load_simulation, _normalize_sim_columns as _ncols
from modules.fis_engine import compute_fis, fis_severity_label
from modules.capacity_model import diagnose_bottleneck
from modules.scenario_ranking import (scenario_sort_tuple, efficiency_score,
    schedule_factor)
from modules.financial_calc import (compute_financial, DEFAULT_PARAMS, MACHINES, fmt_rp)


def _rebalance_pool(pool, demand_by_fmt):
    """Rebalans proporsional sadar-format.
    pool: list dict {id, cap, alloc:{fmt:frac}} — kapasitas & alokasi format tiap lini.
    demand_by_fmt: {fmt: total_demand_ton} — total permintaan per format dalam pool.
    Untuk SETIAP format, permintaannya dibagi ke lini yang memproduksinya
    SECARA PROPORSIONAL terhadap kapasitas-format lini itu (cap × alokasi).
    Hasil: {id: {"load", "util", "cap"}}. Utilisasi antar-lini bervariasi alami
    karena tiap lini memikul campuran format berbeda (tidak dipaksa sama)."""
    res = {p["id"]: {"load": 0.0, "cap": float(p.get("cap", 0))} for p in pool}
    for fmt, dem in demand_by_fmt.items():
        servers = []
        for p in pool:
            fc = float(p.get("cap", 0)) * float(p.get("alloc", {}).get(fmt, 0.0))
            if fc > 0:
                servers.append((p["id"], fc))
        tot = sum(fc for _, fc in servers)
        if tot <= 0:
            continue
        for pid, fc in servers:
            res[pid]["load"] += dem * fc / tot
    for pid in res:
        c = res[pid]["cap"]
        res[pid]["util"] = (res[pid]["load"] / c * 100.0) if c > 0 else 0.0
    return res


def _pool_demand_by_fmt(pool_lids, line_analysis, line_cfg, extra_by_lid=None):
    """Total permintaan per format di seluruh pool. Permintaan tiap lini
    dipecah menurut alokasi formatnya."""
    extra_by_lid = extra_by_lid or {}
    dbf = {}
    for lid in pool_lids:
        dem = float(line_analysis.get(lid, {}).get("dem_m", 0)) + float(extra_by_lid.get(lid, 0.0))
        alloc = line_cfg.get(lid, {}).get("alloc", {})
        if not alloc:
            # fallback: format dibagi rata bila alokasi belum diset
            fmts = line_cfg.get(lid, {}).get("formats", []) or ["SSS"]
            alloc = {f: 1.0 / len(fmts) for f in fmts}
        for fmt, frac in alloc.items():
            dbf[fmt] = dbf.get(fmt, 0.0) + dem * frac
    return dbf


def render():

    # ── Helper ──────────────────────────────────────────────────────────────
    def _s(row, key, d=0.0):
        v = str(row.get(key, d)).replace("%","").strip().replace("[cek P_D]","0")
        try:    return float(v)
        except: return float(d)

    def _to_month(tons_period, horizon_months):
        """Konversi tonase periode data menjadi rata-rata bulanan berdasarkan horizon."""
        h = max(float(horizon_months), 1e-9)
        return float(tons_period) / h

    def _load_catalog():
        _cp = Path("data/machine_catalog.json")
        if _cp.exists():
            try: return _json_cp.load(open(_cp, encoding="utf-8"))
            except: pass
        return {}

    def _line_status(util, limit, tol):
        """Status 3 tingkat: AMAN / WASPADA (dalam toleransi) / KRITIS."""
        u, l, t = float(util), float(limit), float(tol)
        if u <= l:        return "AMAN"
        if u <= l + t:    return "WASPADA"
        return "KRITIS"

    def _decide_dyn(util_by_lid, unmet_ratio, limits, util_tol, unmet_tol):
        """
        Keputusan MAINTAIN/MODIFY berbasis aturan transparan.
        Pelampauan batas dalam zona toleransi (WASPADA) tidak memicu MODIFY —
        cukup dipantau. MODIFY hanya jika ada lini KRITIS atau unmet signifikan.
        """
        reasons, watch = [], []
        if float(unmet_ratio) > float(unmet_tol):
            reasons.append(f"Unmet demand {float(unmet_ratio):.1f}% melewati toleransi {float(unmet_tol):.1f}%")
        for lid, uv in util_by_lid.items():
            lim = float(limits.get(lid, 85))
            stt = _line_status(uv, lim, util_tol)
            if stt == "KRITIS":
                reasons.append(f"Utilisasi Line {lid} {float(uv):.1f}% melewati batas {lim:.0f}% di luar toleransi")
            elif stt == "WASPADA":
                watch.append(f"Line {lid} {float(uv):.1f}% sedikit di atas batas {lim:.0f}% (dalam toleransi — pantau)")
        if reasons:
            return "MODIFY", reasons + watch
        if watch:
            return "MAINTAIN", ["Demand terpenuhi"] + watch
        return "MAINTAIN", ["Demand terpenuhi dan seluruh lini dalam batas utilisasi"]

    def _sort_key_dyn(tons_finished, unmet_ton, util_by_lid, limits, util_tol, target, fis_score=0.0):
        util_max = max(util_by_lid.values()) if util_by_lid else 0
        over = any(_line_status(v, limits.get(lid, 85), util_tol) == "KRITIS"
                   for lid, v in util_by_lid.items())
        eff = efficiency_score(util_max, over, target)
        return (-round(float(tons_finished), 2), round(float(unmet_ton), 2),
                round(eff, 2), round(float(fis_score), 4))

    # ── Load catalog & params ────────────────────────────────────────────────
    _cat = _load_catalog()
    _cat_machines = _cat.get("machines", MACHINES)
    _gp = _cat.get("global_params", DEFAULT_PARAMS)
    _oh_pct = (sum(it.get("pct", 0) for it in _cat.get("capex_overhead_items", []))
               or sum(_cat.get("capex_overhead", {}).values()) or 0.18)
    _opex_man = _cat.get("opex_manpower", {})  # legacy fallback
    params = {k: _gp.get(k, DEFAULT_PARAMS.get(k)) for k in DEFAULT_PARAMS}
    # Seluruh OPEX paket (maintenance, manpower, item lain) dihitung lewat
    # opex_items per-paket pada analisis intervensi — set 0 agar tidak ganda.
    params["maintenance_annual"] = 0.0
    params["calc_config"] = _cat.get("calc_config", {})

    # ── Rendemen proses pada kapasitas efektif (opsional, default nonaktif) ──
    # Bila aktif, kapasitas dihitung basis produk jadi = nominal x rendemen.
    # Tidak dobel-hitung: angka DES & kapasitas mesin berbasis throughput
    # (material diproses), rendemen mengubahnya menjadi basis produk jadi.
    _yield_on = bool(params["calc_config"].get("apply_yield_to_capacity", False))
    _cap_yield = (float(_gp.get("effective_yield_pct", 98.0)) / 100.0) if _yield_on else 1.0
    _prev_df = get_state("simulation_result")
    _prev_n  = _ncols(_prev_df.copy()) if isinstance(_prev_df, pd.DataFrame) and not _prev_df.empty else pd.DataFrame()
    _util_pat = _re.compile(r'Util_Filling_(\w+)')
    _prev_lids = sorted(set(
        m.group(1) for col in _prev_n.columns if (m := _util_pat.match(col))
    )) if not _prev_n.empty else []

    # ── Sidebar ─────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### Data")
        st.markdown('<div style="color:#37B7C3;font-size:.78rem;font-weight:700;'
                    'letter-spacing:.06em;margin-bottom:4px;">SUMBER DATA</div>',
                    unsafe_allow_html=True)
        _new_file = upload_widget("simulation", "Hasil Simulasi", load_simulation)

        # Validasi toleran: cukup ada kolom utilisasi (apa pun namanya) atau
        # data DES yang tidak kosong. TIDAK menghapus hasil DES yang sudah ada
        # hanya karena kolom tidak persis cocok — kehilangan data DES setelah
        # navigasi antar menu sebelumnya berakar dari pembersihan agresif ini.
        def _valid_sim(df):
            if not isinstance(df, pd.DataFrame) or df.empty: return False
            # Terima jika ada kolom utilisasi dengan nilai > 0 ATAU kolom inti DES ada
            _has_util = any((pd.to_numeric(df[c], errors="coerce").fillna(0) > 0).any()
                            for c in df.columns if "Util" in str(c))
            _has_core = any(str(c) in df.columns
                            for c in ["Tons Finished", "Tons_Finished", "Scenario", "Scenario_ID"])
            return _has_util or _has_core

        if _valid_sim(_new_file):
            set_state("simulation_result", _new_file)
        else:
            _ses = get_state("simulation_result")
            if _valid_sim(_ses):
                st.caption(f"{len(_ses)} skenario aktif dari sesi ini.")
            # Catatan: bila tidak valid, biarkan apa adanya — JANGAN dikosongkan.
            # Pengosongan hanya lewat tombol "Hapus Data Simulasi" di bawah.

        if st.button("Hapus Data Simulasi", key="clr_cp",
                     help="Kosongkan data simulasi yang sedang dimuat"):
            set_state("simulation_result", pd.DataFrame())
            set_("simulation", pd.DataFrame())
            for _pkl in ["data/cache/simulation.pkl"]: Path(_pkl).unlink(missing_ok=True)
            st.rerun()

        st.markdown("---")
        st.markdown("### Parameter Evaluasi")

        # Horizon data: dideteksi dari kolom CSV jika tersedia, selain itu diatur manual
        _hor_detected = None
        if not _prev_n.empty:
            for _hc in ["Horizon_Months","Period_Months","Months","Horizon_Bulan"]:
                if _hc in _prev_n.columns:
                    try:
                        _hv = float(pd.to_numeric(_prev_n[_hc], errors="coerce").dropna().iloc[0])
                        if _hv > 0: _hor_detected = _hv; break
                    except Exception: pass
        if _hor_detected:
            _horizon_months = _hor_detected
            st.caption(f"Horizon data: {_horizon_months:.0f} bulan (terdeteksi dari data).")
        else:
            _horizon_months = st.number_input(
                "Horizon Data (bulan)", 1, 60, 12, 1, key="cp_horizon_v3",
                help="Rentang waktu yang dicakup data simulasi. Dipakai untuk "
                     "mengkonversi tonase ke basis bulanan.")

        # Batas utilisasi per lini yang terdeteksi dari data
        _util_limits = {}
        if _prev_lids:
            for _lid in _prev_lids:
                _util_limits[_lid] = st.number_input(
                    f"Batas Util Line {_lid} (%)", 60, 100, 85, 1,
                    key=f"cp_lim_v3_{_lid}",
                    help=f"Batas utilisasi sehat Line {_lid}")
        else:
            st.caption("Muat data simulasi untuk melihat parameter per lini.")

        _util_target = st.number_input("Target Utilisasi Ideal (%)", 60, 90, 80, 1,
            key="cp_target_v3",
            help="Titik tengah zona operasional sehat — dipakai untuk "
                 "ranking efisiensi dan sizing rekomendasi mesin")
        _util_tol = st.number_input("Toleransi Pelampauan Util (poin %)", 0.0, 10.0, 3.0, 0.5,
            key="cp_utol_v3", format="%.1f",
            help="Pelampauan batas hingga sebesar ini berstatus WASPADA "
                 "(dipantau, belum perlu investasi). Lebih dari ini berstatus "
                 "KRITIS dan memicu rekomendasi investasi.")
        _unmet_tol = st.number_input("Toleransi Unmet Demand (%)", 0.0, 5.0, 1.0, 0.1,
            key="cp_unmet_v3", format="%.1f",
            help="Unmet demand di atas nilai ini dianggap signifikan "
                 "(setara service level minimal 99%)")

    # ── Header ──────────────────────────────────────────────────────────────
    st.markdown('<div class="page-title">EVALUASI KAPASITAS</div>', unsafe_allow_html=True)
    st.caption("Pemeringkatan skenario kapasitas produksi, analisis kebutuhan, dan evaluasi kelayakan investasi.")

    # ── Data loading ─────────────────────────────────────────────────────────
    def _valid_df(df):
        if not isinstance(df, pd.DataFrame) or df.empty: return False
        # Toleran: terima kolom utilisasi apa pun bentuknya (Util_Filling_X,
        # "Util Filling X (%)", dll.) selama ada nilai > 0.
        return any((pd.to_numeric(df[c], errors="coerce").fillna(0) > 0).any()
                   for c in df.columns if "Util" in str(c))

    _des  = get_state("simulation_result")
    _norm = _ncols(_des.copy()) if isinstance(_des, pd.DataFrame) and not _des.empty else pd.DataFrame()
    _upl  = get("simulation")
    _normU= _ncols(_upl.copy()) if isinstance(_upl, pd.DataFrame) and not _upl.empty else pd.DataFrame()

    if _valid_df(_norm):     sim_df = _norm
    elif _valid_df(_normU):  sim_df = _normU; set_state("simulation_result", _normU)
    else:                    sim_df = pd.DataFrame()

    if sim_df.empty:
        st.info("Upload hasil simulasi di sidebar, atau jalankan Simulasi Kapasitas terlebih dahulu.")
        st.stop()

    # ── Deteksi lini dari data yang dimuat ──────────────────────────────────
    _lids = sorted(set(
        m.group(1) for col in sim_df.columns if (m := _util_pat.match(col))
    ))
    if not _lids:
        st.error("Kolom utilisasi tidak ditemukan di data. Pastikan format CSV sesuai."); st.stop()

    # Jika util_limits belum ada untuk lini yang baru terdeteksi, pakai default
    for _lid in _lids:
        if _lid not in _util_limits:
            _util_limits[_lid] = st.session_state.get(f"cp_lim_v3_{_lid}", 85)

    # Kolom jadwal per lini
    _sched_keys = {lid: (f"{lid}_Days", f"{lid}_Hours") for lid in _lids}

    # ── Validasi & dedup ──────────────────────────────────────────────────
    mask = pd.Series([True] * len(sim_df))
    if "Tons_Finished" in sim_df.columns:
        mask &= pd.to_numeric(sim_df["Tons_Finished"], errors="coerce").fillna(0) > 0
    sim_clean = sim_df[mask].reset_index(drop=True)
    if sim_clean.empty:
        st.error("Tidak ada skenario valid."); st.stop()

    _scen_col = next((c for c in ["Scenario_ID","Scenario","Label"] if c in sim_clean.columns), None)
    if _scen_col and sim_clean[_scen_col].duplicated().any():
        n0 = len(sim_clean)
        sim_clean = sim_clean.drop_duplicates(subset=[_scen_col]).reset_index(drop=True)
        st.warning(f"CSV berisi {n0} baris — ditampilkan {len(sim_clean)} skenario unik.")

    # ── Hitung rank_df ────────────────────────────────────────────────────
    results = []
    for _, row in sim_clean.iterrows():
        # Utilisasi & tonase per lini (generik)
        _util = {lid: _s(row, f"Util_Filling_{lid}") for lid in _lids}
        _tons = {lid: _s(row, f"Tons_{lid}") for lid in _lids}
        _days = {lid: float(str(row.get(_sched_keys[lid][0],7)).replace(",","") or 7) for lid in _lids}
        _hrs  = {lid: float(str(row.get(_sched_keys[lid][1],24)).replace(",","") or 24) for lid in _lids}

        umx  = max(_util.values()) if _util else 0
        tgt  = max(_s(row,"Target_Demand_Ton"), 1)
        unm  = _s(row,"Unmet_Demand"); fr = _s(row,"Finished_Ratio"); ur = unm/tgt*100
        score = float(compute_fis(umx, ur, fr))
        level, _reasons = _decide_dyn(_util, ur, _util_limits, _util_tol, _unmet_tol)
        severity = fis_severity_label(score)

        _bmode  = str(row.get("Batch_Mode") or row.get("WO_Mode","")).strip()
        _growth = str(row.get("Growth","0")).strip()
        _tons_fin = _s(row,"Tons_Finished")
        _tgt_ton  = tgt

        # Label ringkas: tampilkan semua lini yang terdeteksi
        _parts = " · ".join(f"{lid}:{int(_days[lid])}D/{int(_hrs[lid])}H" for lid in _lids)
        _label = _parts + (f" | {_bmode}" if _bmode else "") + \
                 (f" | G+{_growth}%" if _growth not in ("0","0%","") else "")

        entry = {
            "Scenario":    row.get("Scenario_ID","?"),
            "Label":       _label,
            "Batch Mode":  _bmode, "Growth": _growth,
            "Target Demand (ton)": round(_tgt_ton, 1),
            "Total Produksi (ton)": round(_tons_fin, 1),
            "Selesai (%)":  round(fr, 1), "Unmet (%)": round(ur, 1),
            "Util Max (%)": round(umx, 1),
            "Bottleneck":   str(row.get("Bottleneck_Area","")).strip(),
            "Status Kapasitas": str(row.get("Capacity_Status","")).strip(),
            "Skor FIS":     round(score, 3),
            "Keputusan":    level, "Severity": severity,
            "Alasan":       "; ".join(_reasons),
            "Unmet Demand (ton)": round(_s(row,"Unmet_Demand"), 1),
            "_util":        _util, "_tons":_tons, "_days":_days, "_hrs":_hrs,
            "_row":         row, "_tons_fin": round(_tons_fin,1), "_tgt": round(_tgt_ton,1),
        }
        # Kolom per lini
        for lid in _lids:
            entry[f"Util {lid} (%)"]   = round(_util[lid], 1)
            entry[f"Tons {lid} (ton)"] = round(_tons[lid], 1)
            entry[f"Hari {lid}"]       = int(_days[lid])
            entry[f"Jam {lid}"]        = int(_hrs[lid])

        results.append(entry)

    rank_df = pd.DataFrame(results)
    rank_df["_sort"] = rank_df.apply(lambda r: _sort_key_dyn(
        r.get("Total Produksi (ton)", 0), r.get("Unmet Demand (ton)", 0),
        r.get("_util", {}), _util_limits, _util_tol, _util_target, r.get("Skor FIS", 3.0)
    ), axis=1)
    rank_df = rank_df.sort_values("_sort").reset_index(drop=True)
    rank_df["Rank"] = rank_df.index + 1
    rank_df["ChartLabel"] = rank_df.apply(
        lambda r: f"#{int(r['Rank'])} " + " ".join(
            f"{lid}:{int(r.get(f'Hari {lid}',7))}D/{int(r.get(f'Jam {lid}',24))}H"
            for lid in _lids), axis=1)

    n_m   = (rank_df["Keputusan"]=="MAINTAIN").sum()
    n_mod = (rank_df["Keputusan"]=="MODIFY").sum()
    best  = rank_df.iloc[0]
    overall = "MAINTAIN" if n_m >= n_mod else "MODIFY"

    def _dec_color(v):
        if v=="MAINTAIN": return "color:#1a7f4b;font-weight:700"
        if v=="MODIFY":   return "color:#d29922;font-weight:700"
        return ""

    def _banner(decision, util_max, bottleneck, alasan, headroom=None):
        clr = "#1a7f4b" if decision=="MAINTAIN" else "#d29922"
        lbl = "KONDISI LAYAK" if decision=="MAINTAIN" else "DIPERLUKAN EVALUASI"
        hr_txt = f" &nbsp;|&nbsp; Headroom <b>{headroom:.1f}%</b>" if headroom is not None else ""
        return (f'<div style="border-left:5px solid {clr};background:#F8FDFB;border-radius:6px;'
                f'padding:14px 20px;margin-bottom:10px;">'
                f'<div style="font-size:.68rem;color:#8b949e;letter-spacing:.1em;font-weight:700;'
                f'margin-bottom:4px;">KEPUTUSAN — {decision}</div>'
                f'<div style="font-size:1.1rem;font-weight:800;color:{clr};margin-bottom:6px;">{lbl}</div>'
                f'<div style="font-size:.8rem;color:#071952;">Util maks <b>{util_max:.1f}%</b>'
                f'{hr_txt} &nbsp;|&nbsp; Bottleneck: <b>{bottleneck}</b></div>'
                f'<div style="font-size:.72rem;color:#8b949e;margin-top:4px;">Dasar keputusan: {alasan}</div>'
                f'</div>')

    def _util_chart(util_by_lid, limits, title="UTILISASI PER LINI"):
        """Bar chart utilisasi — garis batas digambar per-bar sesuai batas lini masing-masing."""
        st.markdown(f"<div class='section-title'>{title}</div>", unsafe_allow_html=True)
        fig = go.Figure()
        _colors = ["#071952","#088395","#37B7C3","#d29922","#3fb950","#8b949e"]
        _lids_order = list(util_by_lid.keys())
        for i, lid in enumerate(_lids_order):
            uv  = float(util_by_lid[lid])
            lim = float(limits.get(lid, 85))
            clr = "#d29922" if uv >= lim else _colors[i % len(_colors)]
            fig.add_trace(go.Bar(x=[f"Line {lid}"], y=[uv], name=f"Line {lid}",
                marker_color=clr, text=[f"{uv:.1f}%"],
                textposition="outside", textfont=dict(size=12)))
        # Garis batas hanya selebar bar lini terkait (koordinat kategori = indeks)
        for i, lid in enumerate(_lids_order):
            lim = float(limits.get(lid, 85))
            fig.add_shape(type="line", xref="x", yref="y",
                x0=i-0.38, x1=i+0.38, y0=lim, y1=lim,
                line=dict(color="#f85149", width=2, dash="dot"))
            fig.add_annotation(x=i, y=lim, text=f"{lim:.0f}%",
                showarrow=False, yshift=8, font=dict(size=9, color="#f85149"))
        fig.update_layout(template="plotly_white", paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
            margin=dict(l=0,r=0,t=20,b=16), showlegend=False, height=240,
            yaxis=dict(range=[0,110], title="Utilisasi (%)", gridcolor="#EBF4F6"),
            xaxis=dict(title=""), bargap=0.4)
        st.plotly_chart(fig, use_container_width=True)

    def _tons_chart(tons_by_lid, title="TONASE PER LINI"):
        st.markdown(f"<div class='section-title'>{title}</div>", unsafe_allow_html=True)
        fig = go.Figure()
        _colors = ["#071952","#088395","#37B7C3","#d29922","#3fb950","#8b949e"]
        for i, (lid, tv) in enumerate(tons_by_lid.items()):
            fig.add_trace(go.Bar(x=[f"Line {lid}"], y=[float(tv)], name=f"Line {lid}",
                marker_color=_colors[i % len(_colors)],
                text=[f"{float(tv):,.0f} ton"], textposition="outside"))
        fig.update_layout(template="plotly_white", paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
            margin=dict(l=0,r=0,t=20,b=16), showlegend=False, height=240,
            yaxis=dict(title="Tonase (ton)", gridcolor="#EBF4F6"), bargap=0.4)
        st.plotly_chart(fig, use_container_width=True)

    # ── TABS ────────────────────────────────────────────────────────────────
    tab_eval, tab_diag = st.tabs(["Evaluasi Skenario", "Diagnosa & Rekomendasi"])

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 1 — EVALUASI SKENARIO
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_eval:
        # KPI
        _kpi = [(str(len(rank_df)),"Total Skenario","#071952")]
        if n_m  > 0: _kpi.append((str(n_m),"MAINTAIN","#3fb950"))
        if n_mod > 0: _kpi.append((str(n_mod),"MODIFY","#d29922"))
        _kpi.append((best.get("Label","—"),"Skenario Terbaik","#071952"))
        for col,(val,lbl,clr) in zip(st.columns(len(_kpi)), _kpi):
            with col:
                st.markdown(
                    f'<div class="kpi-box" style="border-left-color:{clr};">'
                    f'<div class="kpi-label">{lbl}</div>'
                    f'<div class="kpi-value" style="color:{clr};font-size:.85rem;">{val}</div></div>',
                    unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        _ov_badge = "badge-maintain" if overall=="MAINTAIN" else "badge-modify"
        st.markdown(f'<b>Keputusan Keseluruhan:</b> <span class="{_ov_badge}">{overall}</span>',
                    unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        # FIS chart + Top 5
        _top_chart = rank_df.head(min(12, len(rank_df)))
        _fig_r = go.Figure()
        for lvl, clr in [("MAINTAIN","#3fb950"),("MODIFY","#d29922")]:
            _sub = _top_chart[_top_chart["Keputusan"]==lvl]
            if _sub.empty: continue
            _fig_r.add_trace(go.Bar(x=_sub["ChartLabel"], y=_sub["Skor FIS"], name=lvl,
                marker_color=clr, text=[f"#{r}" for r in _sub["Rank"]],
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Rank: %{text}<br>Skor FIS: %{y:.3f}<extra></extra>"))
        _fig_r.update_layout(template="plotly_white", paper_bgcolor="#FFFFFF",
            plot_bgcolor="#FFFFFF", height=260, barmode="group",
            legend=dict(orientation="h",y=-0.25), margin=dict(l=0,r=0,t=8,b=44),
            yaxis=dict(title="Skor FIS",gridcolor="#EBF4F6",range=[0,4.3]),
            xaxis=dict(gridcolor="#EBF4F6"))

        _cc, _ct = st.columns([3,2])
        with _cc: st.plotly_chart(_fig_r, use_container_width=True)
        with _ct:
            st.markdown('<div class="section-title">Top 5 Skenario</div>', unsafe_allow_html=True)
            _T5 = ["Rank","Label","Total Produksi (ton)","Selesai (%)","Unmet (%)",
                   "Util Max (%)","Skor FIS","Keputusan"]
            _t5 = rank_df.head(5)[[c for c in _T5 if c in rank_df.columns]].copy()
            for nc in _t5.select_dtypes(include="number").columns:
                _t5[nc] = _t5[nc].round(2)
            _dc = ["Keputusan"] if "Keputusan" in _t5.columns else []
            st.dataframe(
                _t5.style.map(_dec_color, subset=_dc).format(precision=2) if _dc else _t5,
                use_container_width=True, hide_index=True)

        with st.expander("Semua Skenario"):
            _ALL = ["Rank","Label","Target Demand (ton)","Total Produksi (ton)",
                    "Selesai (%)","Unmet (%)","Util Max (%)"] + \
                   [f"Util {lid} (%)" for lid in _lids] + \
                   [f"Tons {lid} (ton)" for lid in _lids] + \
                   ["Bottleneck","Status Kapasitas","Skor FIS","Keputusan"]
            _all_show = rank_df[[c for c in _ALL if c in rank_df.columns]].copy()
            for nc in _all_show.select_dtypes(include="number").columns:
                _all_show[nc] = _all_show[nc].round(2)
            _dc2 = ["Keputusan"] if "Keputusan" in _all_show.columns else []
            st.dataframe(
                _all_show.style.map(_dec_color, subset=_dc2).format(precision=2) if _dc2 else _all_show,
                use_container_width=True, hide_index=True)
            st.caption("Keputusan berbasis aturan transparan: MODIFY jika unmet demand "
                       "melebihi toleransi atau ada lini yang melewati batas utilisasinya.")

        st.markdown("---")

        # Detail skenario
        _scen_labels = rank_df["Label"].tolist()
        _t1_sel_idx  = st.selectbox(
            "Detail skenario:", range(len(_scen_labels)),
            format_func=lambda i: (
                f"#{i+1} — {_scen_labels[i]}  "
                f"[{rank_df.iloc[i].get('Keputusan','?')}]"),
            index=0, key="cp_t1_sel_idx")
        st.session_state["cp_diag_sel"] = rank_df.iloc[_t1_sel_idx]["Scenario"]

        _sm = rank_df.iloc[_t1_sel_idx]
        _sm_util = {lid: float(_sm.get(f"Util {lid} (%)", 0)) for lid in _lids}
        _sm_tons = {lid: float(_sm.get(f"Tons {lid} (ton)", 0)) for lid in _lids}
        st.markdown(_banner(
            str(_sm.get("Keputusan","MAINTAIN")),
            float(_sm.get("Util Max (%)", 0)),
            str(_sm.get("Bottleneck","—")),
            str(_sm.get("Alasan","—")),
            headroom=round(100 - float(_sm.get("Util Max (%)", 0)), 1),
        ), unsafe_allow_html=True)

        _mc1, _mc2 = st.columns(2)
        with _mc1: _util_chart(_sm_util, _util_limits)
        with _mc2: _tons_chart(_sm_tons)

        if str(_sm.get("Keputusan","MAINTAIN")) == "MODIFY":
            st.info("Skenario ini perlu evaluasi lebih lanjut. "
                    "Buka tab **Diagnosa & Rekomendasi** untuk analisis lengkap.")

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 2 — DIAGNOSA & REKOMENDASI
    # ═══════════════════════════════════════════════════════════════════════════
    with tab_diag:
        # ── Scenario selector (synced dari Tab 1) ──────────────────────────
        _def_id  = st.session_state.get("cp_diag_sel", rank_df.iloc[0]["Scenario"])
        _sid_lst = rank_df["Scenario"].tolist()
        _def_idx = next((i for i,s in enumerate(_sid_lst) if s==_def_id), 0)
        sel_id = st.selectbox(
            "Skenario yang dianalisis:", _sid_lst, index=_def_idx,
            format_func=lambda s: (
                lambda r: f"Rank #{r['Rank'].values[0]}  —  "
                          + str(r.get("Label",r.get("Scenario","")).values[0])
                          + f"  |  {r['Keputusan'].values[0]}"
            )(rank_df[rank_df["Scenario"]==s]),
            key="cp_t2_sel")
        st.caption("Tersinkronisasi dengan tab Evaluasi Skenario.")

        sel_row  = rank_df[rank_df["Scenario"]==sel_id].iloc[0]
        sel_orig = sel_row["_row"]
        _t2_dec  = str(sel_row.get("Keputusan","MAINTAIN"))
        _t2_util = dict(sel_row.get("_util", {lid: float(sel_row.get(f"Util {lid} (%)",0)) for lid in _lids}))
        _t2_tons = dict(sel_row.get("_tons", {lid: float(sel_row.get(f"Tons {lid} (ton)",0)) for lid in _lids}))
        _t2_days = dict(sel_row.get("_days", {lid: 7.0 for lid in _lids}))
        _t2_hrs  = dict(sel_row.get("_hrs",  {lid: 24.0 for lid in _lids}))

        st.markdown(_banner(
            _t2_dec,
            float(sel_row.get("Util Max (%)", 0)),
            str(sel_row.get("Bottleneck","—")),
            str(sel_row.get("Alasan","—")),
        ), unsafe_allow_html=True)

        if _t2_dec == "MAINTAIN":
            st.success(
                f"Skenario ini memenuhi seluruh kriteria operasional — "
                f"tidak ada kebutuhan kapasitas tambahan pada kondisi ini.")
            _c1, _c2 = st.columns(2)
            with _c1: _util_chart(_t2_util, _util_limits)
            with _c2: _tons_chart(_t2_tons)

        else:
            # ── A. DIAGNOSA BOTTLENECK ────────────────────────────────────
            st.markdown('<div class="section-title">Diagnosa Bottleneck</div>',
                        unsafe_allow_html=True)
            diag = diagnose_bottleneck(sel_orig)
            _sev_clr = {"KRITIS":"#f85149","TINGGI":"#d29922","SEDANG":"#071952","RENDAH":"#3fb950"}
            _cl, _cr = st.columns([1,2])
            with _cl:
                st.markdown(
                    f'<div class="dss-card">'
                    f'<div class="kpi-label">Bottleneck Utama</div>'
                    f'<div style="font-size:1.2rem;font-weight:700;color:#d29922;">'
                    f'{diag["primary_bottleneck"]}</div>'
                    f'<div style="font-size:.82rem;color:#8b949e;margin-top:6px;">'
                    f'Utilisasi: <b>{diag["max_util"]}%</b> &nbsp;|&nbsp; '
                    f'<b style="color:{_sev_clr.get(diag["severity"],"#8b949e")};">'
                    f'{diag["severity"]}</b></div>'
                    f'<div style="font-size:.82rem;color:#8b949e;margin-top:4px;">'
                    f'Unmet: <b>{diag["unmet_ratio"]:.1f}%</b></div>'
                    f'</div>', unsafe_allow_html=True)
            with _cr:
                _fu = go.Figure(go.Bar(
                    x=[f"Line {k}" for k in diag["utils"].keys()],
                    y=list(diag["utils"].values()),
                    marker_color=["#d29922" if k==diag.get("primary_bottleneck","") else "#071952"
                                  for k in diag["utils"].keys()],
                    text=[f"{v:.1f}%" for v in diag["utils"].values()],
                    textposition="outside"))
                _dlids = list(diag["utils"].keys())
                for i, lid in enumerate(_dlids):
                    _lim = float(_util_limits.get(lid, 85))
                    _fu.add_shape(type="line", xref="x", yref="y",
                        x0=i-0.38, x1=i+0.38, y0=_lim, y1=_lim,
                        line=dict(color="#f85149", width=2, dash="dot"))
                    _fu.add_annotation(x=i, y=_lim, text=f"{_lim:.0f}%",
                        showarrow=False, yshift=8, font=dict(size=9, color="#f85149"))
                _fu.update_layout(template="plotly_white", paper_bgcolor="#FFFFFF",
                    plot_bgcolor="#FFFFFF", height=200,
                    margin=dict(l=0,r=0,t=8,b=16),
                    yaxis=dict(range=[0,105],gridcolor="#EBF4F6"), showlegend=False)
                st.plotly_chart(_fu, use_container_width=True)

            st.markdown("---")

            # ── B. KONFIGURASI LINI (Tab 2 saja) ─────────────────────────
            st.markdown('<div class="section-title">Analisis Kebutuhan Kapasitas</div>',
                        unsafe_allow_html=True)
            st.caption("Sistem menyajikan dua opsi penanganan beserta rekomendasinya — "
                       "penyesuaian jadwal operasi atau investasi mesin.")

            with st.expander("Konfigurasi Lini", expanded=False):
                st.caption("Tentukan tipe dan format kemasan setiap lini. "
                           "Informasi ini dipakai untuk mencocokkan rekomendasi mesin.")
                _line_cfg = {}
                _fmt_opts  = _cat.get("package_formats", ["SSS","BIB","STICKPACK"])
                _type_opts = _cat.get("line_types", ["Single line","Multiline","Stickpack"])
                _cfg_cols = st.columns(min(len(_lids), 4))
                for i, lid in enumerate(sorted(_lids)):
                    with _cfg_cols[i % len(_cfg_cols)]:
                        st.markdown(f"**Line {lid}**")
                        _dtype = ("Multiline" if lid.upper()=="D" else
                                  ("Stickpack" if lid.upper()=="STICKPACK" else "Single line"))
                        _tidx = _type_opts.index(_dtype) if _dtype in _type_opts else 0
                        _type_sel = st.selectbox(f"Tipe Line {lid}", _type_opts,
                            index=_tidx, key=f"p3_type_{lid}")
                        _dfmt = (["STICKPACK"] if _type_sel=="Stickpack" else
                                 (["SSS"] if lid.upper()=="D" else ["SSS","BIB"]))
                        _fmt_sel = st.multiselect(f"Format Line {lid}",
                            _fmt_opts, default=_dfmt, key=f"p3_fmt_{lid}",
                            label_visibility="collapsed")
                        # Alokasi kapasitas per format (editable, generik untuk
                        # format apa pun). Default: dibagi rata; dinormalisasi.
                        _alloc = {}
                        if len(_fmt_sel) == 1:
                            _alloc[_fmt_sel[0]] = 1.0
                        elif len(_fmt_sel) > 1:
                            st.caption("Porsi kapasitas tiap format (%)")
                            _eq = int(round(100.0 / len(_fmt_sel)))
                            _raw = {}
                            for _f in _fmt_sel:
                                _raw[_f] = st.number_input(
                                    f"% {_f}", 0, 100, _eq, 5,
                                    key=f"p3_alloc_{lid}_{_f}")
                            _tot = sum(_raw.values()) or 1
                            _alloc = {_f: v / _tot for _f, v in _raw.items()}
                        _line_cfg[lid] = {"formats": _fmt_sel, "type": _type_sel,
                                          "limit": float(_util_limits.get(lid, 85)),
                                          "alloc": _alloc}

                st.markdown("---")
                st.caption("Konfigurasi lini baru (dipakai bila rekomendasi berupa "
                           "penambahan lini). Jadwal menentukan kapasitas efektif lini "
                           "baru = kapasitas filler \u00d7 (hari/7) \u00d7 (jam/24).")
                _nlc1, _nlc2 = st.columns(2)
                with _nlc1:
                    _newline_days = st.number_input("Hari kerja/minggu lini baru",
                        1, 7, 7, 1, key="p3_nl_days")
                with _nlc2:
                    _newline_hours = st.number_input("Jam kerja/hari lini baru",
                        1, 24, 24, 1, key="p3_nl_hours")
            # Faktor jadwal lini baru (di luar expander agar selalu terdefinisi)
            _newline_days  = int(st.session_state.get("p3_nl_days", 7))
            _newline_hours = int(st.session_state.get("p3_nl_hours", 24))
            _newline_sched_factor = (_newline_days / 7.0) * (_newline_hours / 24.0)

            # ── C. TAHAP 1 / TAHAP 2 ──────────────────────────────────────
            # Ambil data per lini dari skenario terpilih
            _unmet_ton = float(sel_row.get("Unmet Demand (ton)", 0))
            _tgt_ton   = float(sel_row.get("Target Demand (ton)", 1))
            _unmet_pct = _unmet_ton / max(_tgt_ton, 1) * 100
            _has_unmet = _unmet_pct > float(_unmet_tol)

            # Untuk setiap lini: hitung kapasitas saat ini dan di jadwal penuh
            _line_analysis = {}
            for lid in _lids:
                _u  = float(_t2_util.get(lid, 0))
                _t  = float(_t2_tons.get(lid, 0))
                _d  = float(_t2_days.get(lid, 7))
                _h  = float(_t2_hrs.get(lid, 24))
                _sf = schedule_factor(_d, _h)
                # Kapasitas mesin pada jadwal saat ini (ton/periode)
                _cap_p = _t / (_u/100) if _u > 0 else 0.0
                # Kapasitas di jadwal penuh (7D/24H) — ton/periode
                _cap_fp = _cap_p / _sf if _sf > 0.001 else _cap_p
                # Basis bulanan (kapasitas dikali rendemen bila opsi aktif)
                _cap_m  = _to_month(_cap_p, _horizon_months) * _cap_yield   # ton/bln saat ini
                _cap_fm = _to_month(_cap_fp, _horizon_months) * _cap_yield  # ton/bln jika jadwal penuh
                _dem_m  = _to_month(_t, _horizon_months)        # demand/produksi bulanan
                # Util yang diproyeksikan jika jadwal diperpanjang ke 7D/24H
                _proj_util_full = (_dem_m / _cap_fm * 100) if _cap_fm > 0 else 0
                _lim = float(_util_limits.get(lid, 85))
                _is_full = _sf >= 0.999
                _line_analysis[lid] = {
                    "util": _u, "tons": _t, "days": _d, "hours": _h, "sf": _sf,
                    "cap_m": _cap_m, "cap_fm": _cap_fm, "dem_m": _dem_m,
                    "proj_util_full": _proj_util_full, "lim": _lim, "is_full": _is_full,
                    "over_limit": _u > _lim,
                    "status": _line_status(_u, _lim, _util_tol),
                }

            # ── Pencarian jadwal optimal per lini ─────────────────────────
            # Bukan langsung 7D/24H: enumerasi kandidat jadwal (5/6/7 hari ×
            # 8/16/24 jam) yang >= jadwal saat ini, hitung proyeksi utilisasi
            # tiap kandidat (termasuk menyerap porsi unmet demand), lalu pilih
            # perpanjangan TERKECIL yang masih dalam batas — utilisasi tetap
            # sehat, tidak jatuh ke under-utilisasi.
            _dem_factor = (_tgt_ton / max(_tgt_ton - _unmet_ton, 1e-9)) if _has_unmet else 1.0
            _SCHED_CAND = sorted(
                [(d, h) for d in (5, 6, 7) for h in (8, 16, 24)],
                key=lambda x: x[0] * x[1])
            for lid, la in _line_analysis.items():
                _need_m  = la["dem_m"] * _dem_factor      # beban bulanan + porsi unmet
                _cur_wh  = la["days"] * la["hours"]
                la["need_m"] = _need_m
                _fits = []
                for (_d, _h) in _SCHED_CAND:
                    if _d * _h < _cur_wh - 1e-9:
                        continue                            # hanya pertahankan/perpanjang
                    _sfc  = schedule_factor(_d, _h)
                    _capc = la["cap_fm"] * _sfc
                    _pu   = (_need_m / _capc * 100) if _capc > 0 else 999
                    if _pu <= la["lim"]:
                        _fits.append((_d, _h, _pu))
                if _fits:
                    # perpanjangan terkecil yang cukup = proyeksi util tertinggi <= batas
                    _bd, _bh, _bpu = max(_fits, key=lambda x: x[2])
                    la["rec_days"], la["rec_hours"], la["rec_util"] = _bd, _bh, _bpu
                    la["rec_changed"] = (_bd * _bh) > _cur_wh + 1e-9
                    la["sched_ok"] = True
                else:
                    # bahkan 7D/24H tidak cukup → lini ini butuh investasi
                    _sff = schedule_factor(7, 24)
                    _capf = la["cap_fm"] * _sff
                    la["rec_days"], la["rec_hours"] = 7, 24
                    la["rec_util"] = (_need_m / _capf * 100) if _capf > 0 else 999
                    la["rec_changed"] = (_cur_wh < 7 * 24)
                    la["sched_ok"] = False

            # Problem lines: over limit ATAU bottleneck dari unmet
            # Hanya lini KRITIS (di luar toleransi) yang memicu rekomendasi investasi.
            # Lini WASPADA (dalam toleransi) cukup dipantau.
            _critical_lines = [lid for lid, la in _line_analysis.items() if la["status"]=="KRITIS"]
            _watch_lines    = [lid for lid, la in _line_analysis.items() if la["status"]=="WASPADA"]
            _over_lines     = _critical_lines
            _btl_lid = max(_line_analysis, key=lambda lid: _line_analysis[lid]["util"],
                          default=None) if _has_unmet else None

            # Apakah Tahap 1 (jadwal extension) bisa cukup?
            if _watch_lines:
                _wl_txt = "; ".join(
                    f"Line {lid} {_line_analysis[lid]['util']:.1f}% "
                    f"(batas {_line_analysis[lid]['lim']:.0f}%)" for lid in _watch_lines)
                st.markdown(
                    f'<div style="border-left:4px solid #d29922;background:#FDFAF4;'
                    f'border-radius:4px;padding:10px 16px;margin-bottom:10px;font-size:.82rem;">'
                    f'<b style="color:#d29922;">PEMANTAUAN</b> — {_wl_txt}. '
                    f'Pelampauan masih dalam toleransi; belum memerlukan investasi, '
                    f'namun perlu dipantau pada periode berikutnya.</div>',
                    unsafe_allow_html=True)

            # Penyesuaian jadwal dinilai memadai jika SEMUA lini menemukan
            # jadwal (>= saat ini) yang proyeksinya dalam batas, dan masih ada
            # ruang perpanjangan ketika memang dibutuhkan.
            _sched_solves = all(la["sched_ok"] for la in _line_analysis.values())
            _any_room     = not all(la["is_full"] for la in _line_analysis.values())
            _needs_action = _has_unmet or bool(_critical_lines)

            # ── Rekomendasi sistem + pilihan opsi (keputusan di tangan pengguna) ──
            if _needs_action and _sched_solves and _any_room:
                _rec_opt, _rec_reason = "A", (
                    "penyesuaian jadwal operasi diproyeksikan memenuhi seluruh "
                    "target tanpa pengeluaran investasi")
            else:
                _rec_opt, _rec_reason = "B", (
                    "penyesuaian jadwal tidak memadai — kapasitas terpasang perlu ditambah"
                    if _needs_action else
                    "evaluasi investasi tersedia sebagai tinjauan opsional")
            st.markdown(
                f'<div style="border-left:4px solid #088395;background:#F4FBFC;'
                f'border-radius:4px;padding:10px 16px;margin-bottom:10px;font-size:.82rem;">'
                f'<b style="color:#088395;">REKOMENDASI SISTEM — OPSI {_rec_opt}</b> '
                f'&nbsp;{_rec_reason.capitalize()}. Keputusan akhir tetap pada '
                f'pertimbangan manajemen.</div>', unsafe_allow_html=True)

            _OPSI = ["Opsi A — Penyesuaian Jadwal Operasi (tanpa investasi)",
                     "Opsi B — Investasi Mesin"]
            _opsi_sel = st.radio("Opsi penanganan:", _OPSI,
                                 index=0 if _rec_opt == "A" else 1,
                                 horizontal=True, key="p3_opsi")

            if _opsi_sel.startswith("Opsi A"):
                # ── OPSI A: Penyesuaian jadwal — perpanjangan terkecil yang cukup ──
                if _sched_solves:
                    _a_head = "Demand dapat dipenuhi melalui penyesuaian jadwal"
                    _a_body = (
                        f'Terdapat unmet demand <b>{_unmet_pct:.1f}%</b>. '
                        f'Jadwal yang disarankan adalah perpanjangan terkecil yang '
                        f'memenuhi target — utilisasi tetap dalam rentang sehat, '
                        f'tidak jatuh ke under-utilisasi.'
                        if _has_unmet else
                        'Jadwal yang disarankan menjaga utilisasi setiap lini dalam batas.')
                    _a_clr = "#088395"
                else:
                    _a_head = "Penyesuaian jadwal tidak sepenuhnya memadai"
                    _a_body = ('Pada jadwal maksimum pun terdapat lini yang melewati batas '
                               'utilisasi — pertimbangkan Opsi B untuk lini tersebut.')
                    _a_clr = "#d29922"
                st.markdown(
                    f'<div style="border-left:5px solid {_a_clr};background:#F4FBFC;'
                    f'border-radius:6px;padding:14px 20px;margin-bottom:12px;">'
                    f'<div style="font-size:.72rem;color:{_a_clr};letter-spacing:.08em;'
                    f'font-weight:700;margin-bottom:4px;">OPSI A — PENYESUAIAN JADWAL</div>'
                    f'<div style="font-size:.95rem;font-weight:700;color:#071952;margin-bottom:6px;">'
                    f'{_a_head}</div>'
                    f'<div style="font-size:.82rem;color:#071952;">{_a_body}</div>'
                    f'</div>', unsafe_allow_html=True)
                _proj_rows = []
                for lid, la in _line_analysis.items():
                    _cur_lbl = f"{int(la['days'])}D/{int(la['hours'])}H"
                    _rec_lbl = (f"{int(la['rec_days'])}D/{int(la['rec_hours'])}H"
                                if la["rec_changed"] else "Pertahankan")
                    if not la["sched_ok"]:
                        _st_lbl = "Tidak memadai — perlu investasi"
                    elif la["rec_changed"]:
                        _st_lbl = "Perpanjang jadwal"
                    else:
                        _st_lbl = "Memadai"
                    _proj_rows.append({
                        "Lini": f"Line {lid}",
                        "Jadwal Saat Ini": _cur_lbl,
                        "Util Saat Ini (%)": round(la["util"], 1),
                        "Jadwal Disarankan": _rec_lbl,
                        "Proyeksi Util (%)": round(la["rec_util"], 1),
                        "Batas (%)": la["lim"],
                        "Status": _st_lbl,
                    })
                st.dataframe(pd.DataFrame(_proj_rows).style.format(precision=1),
                             use_container_width=True, hide_index=True)
                st.caption("Jadwal disarankan adalah perpanjangan terkecil yang memenuhi "
                           "kebutuhan. Skenario dengan jadwal berbeda tersedia di tab "
                           "Evaluasi Skenario.")

            else:
                # ── OPSI B: Evaluasi investasi ────────────────────────────
                _alasan_inv = []
                if _has_unmet and all(_line_analysis[lid]["is_full"] for lid in _lids):
                    _alasan_inv.append(
                        f"unmet demand {_unmet_pct:.1f}% pada jadwal yang sudah maksimal")
                elif _has_unmet and not _sched_solves:
                    _alasan_inv.append(
                        f"unmet demand {_unmet_pct:.1f}% (perpanjangan jadwal tidak memadai)")
                for lid in _over_lines:
                    la = _line_analysis[lid]
                    _alasan_inv.append(
                        f"utilisasi Line {lid} {la['util']:.1f}% melewati batas {la['lim']:.0f}%")
                if not _alasan_inv:
                    _alasan_inv.append(
                        "ditinjau sebagai alternatif dari penyesuaian jadwal "
                        "atas pertimbangan manajemen")

                st.markdown(
                    '<div style="border-left:5px solid #d29922;background:#FDFAF4;'
                    'border-radius:6px;padding:14px 20px;margin-bottom:12px;">'
                    '<div style="font-size:.72rem;color:#d29922;letter-spacing:.08em;'
                    'font-weight:700;margin-bottom:4px;">OPSI B — INVESTASI MESIN</div>'
                    '<div style="font-size:.95rem;font-weight:700;color:#071952;margin-bottom:6px;">'
                    'Evaluasi penambahan kapasitas terpasang</div>'
                    f'<div style="font-size:.82rem;color:#071952;">'
                    f'Dasar: {"; ".join(_alasan_inv)}.</div></div>',
                    unsafe_allow_html=True)

                # Lini yang perlu dianalisis: over-limit + bottleneck dari unmet.
                # Jika Opsi B dipilih sukarela (tidak ada lini kritis), analisis
                # diarahkan ke lini dengan utilisasi tertinggi.
                _problem_lids = list(_over_lines)
                if _btl_lid and _btl_lid not in _problem_lids:
                    _problem_lids.append(_btl_lid)
                if not _problem_lids and _line_analysis:
                    _problem_lids = [max(_line_analysis,
                                         key=lambda lid: _line_analysis[lid]["util"])]

                # ── REKOMENDASI INTERVENSI PER LINI ───────────────────────
                st.markdown('<div class="section-title">Rekomendasi Investasi Kapasitas</div>',
                            unsafe_allow_html=True)
                st.caption("Untuk setiap lini bermasalah, sistem mengevaluasi jenis "
                           "investasi dari katalog (penggantian filler, konversi multiline, "
                           "atau penambahan lini baru) dan memilih yang paling sesuai. "
                           "Jenis investasi dapat dikelola di menu Parameter & Katalog Investasi.")
                if _yield_on:
                    st.info(f"Kapasitas dihitung pada basis produk jadi "
                            f"(rendemen proses {_cap_yield*100:.1f}% dari neraca massa "
                            f"lini filling). Dapat dinonaktifkan di Parameter & Katalog "
                            f"Investasi.")

                _iv_pkgs = _cat.get("intervention_packages", {})

                def _pkg_extra_capex(pkg):
                    return sum(float(_cat_machines.get(c["key"], {}).get("capex", 0)) * c.get("qty", 1)
                               for c in pkg.get("components_extra", []))

                def _pkg_overhead_pct(pkg_key):
                    """Overhead % berlaku untuk paket ini (dari master CAPEX)."""
                    items = _cat.get("capex_overhead_items", [])
                    if items:
                        return sum(float(it.get("pct", 0)) for it in items
                                   if "all" in it.get("applies", ["all"])
                                   or pkg_key in it.get("applies", []))
                    return float(_oh_pct) if _oh_pct else 0.18

                def _pkg_fixed_cost(pkg_key):
                    """Biaya tetap (komisioning dll) berlaku untuk paket ini."""
                    return sum(float(it.get("amount", 0))
                               for it in _cat.get("capex_fixed_items", [])
                               if "all" in it.get("applies", ["all"])
                               or pkg_key in it.get("applies", []))

                def _pkg_opex_annual(pkg_key):
                    """OPEX tahunan (maintenance, manpower, dll) berlaku untuk paket ini."""
                    return sum(float(it.get("annual", 0))
                               for it in _cat.get("opex_items", [])
                               if "all" in it.get("applies", ["all"])
                               or pkg_key in it.get("applies", []))

                def _custom_param_effects():
                    """Parameter tambahan dengan peran perhitungan."""
                    _ce = {"opex_add": 0.0, "capex_add": 0.0, "benefit_cut": 0.0}
                    for _cv in _cat.get("custom_params", {}).values():
                        if isinstance(_cv, dict) and _cv.get("usage") in _ce:
                            _ce[_cv["usage"]] += float(_cv.get("value", 0))
                    return _ce
                _cust_fx = _custom_param_effects()

                def _filler_candidates(fmts):
                    """Filler dari katalog yang kompatibel format.
                    Kapasitas dikali rendemen bila opsi kapasitas efektif aktif."""
                    out = []
                    for mk, mm in _cat_machines.items():
                        if mm.get("role") != "Filling": continue
                        cap = float(mm.get("capacity_ton_month", 0) or 0) * _cap_yield
                        if cap <= 0: continue
                        mf = [f.upper() for f in mm.get("format_compat", [])]
                        if not any(f in mf for f in [x.upper() for x in fmts]): continue
                        out.append((mk, mm, cap))
                    return out

                _sel_machines = {}  # lid → dict hasil pilihan untuk analisis finansial

                for lid in _problem_lids:
                    la    = _line_analysis[lid]
                    _cfg  = _line_cfg.get(lid, {})
                    _fmts = _cfg.get("formats", ["SSS","BIB"])
                    _ltype= _cfg.get("type", "Single line")
                    _lim  = la["lim"]
                    _dem_m = la["dem_m"]
                    _unmet_share = _to_month(_unmet_ton, _horizon_months) if (_has_unmet and lid == _btl_lid) else 0.0
                    _need_m  = _dem_m + _unmet_share
                    _cap_fm  = la["cap_fm"]   # kapasitas lini existing (basis bulanan, operasi penuh)

                    st.markdown(
                        f'<div style="font-size:.86rem;color:#071952;margin:12px 0 4px 0;">'
                        f'<b>Line {lid}</b> ({_ltype}) — beban produksi '
                        f'<b>{_need_m:,.0f} ton/bln</b> &nbsp;|&nbsp; '
                        f'kapasitas existing {_cap_fm:,.0f} ton/bln &nbsp;|&nbsp; '
                        f'Format {"/".join(_fmts)}</div>', unsafe_allow_html=True)

                    _fcands = _filler_candidates(_fmts)
                    if not _fcands:
                        st.warning(f"Tidak ada filler pada katalog yang kompatibel dengan "
                                   f"Line {lid} (format {'/'.join(_fmts)}). "
                                   "Tambahkan di menu Parameter & Katalog Investasi.")
                        continue

                    # ── Pool rebalans sadar-format untuk lini ini ─────────────
                    # Pool = seluruh lini yang berbagi minimal satu format dengan
                    # lini didiagnosis. Investasi apa pun mengubah kapasitas pool,
                    # lalu permintaan tiap format dibagi ulang proporsional →
                    # SEMUA lini se-format ikut berubah (bukan hanya yang didiagnosis).
                    _diag_fmt_set = set(f.upper() for f in _fmts)
                    _pool_lids = [
                        _l2 for _l2 in _lids
                        if set(f.upper() for f in
                               _line_cfg.get(_l2, {}).get("formats", [])) & _diag_fmt_set
                    ]
                    if lid not in _pool_lids:
                        _pool_lids.append(lid)
                    _unmet_by_lid = ({_btl_lid: _to_month(_unmet_ton, _horizon_months)}
                                     if _has_unmet else {})
                    _demand_by_fmt = _pool_demand_by_fmt(_pool_lids, _line_analysis,
                                                         _line_cfg, _unmet_by_lid)
                    # Basis pool: kapasitas & alokasi tiap lini (sebelum investasi)
                    _pool_base = [{
                        "id": _l2, "cap": float(_line_analysis[_l2]["cap_fm"]),
                        "alloc": (_line_cfg.get(_l2, {}).get("alloc")
                                  or {f: 1.0/len(_line_cfg.get(_l2, {}).get("formats", ["SSS"]))
                                      for f in (_line_cfg.get(_l2, {}).get("formats") or ["SSS"])}),
                    } for _l2 in _pool_lids]
                    _alloc_diag = next((p["alloc"] for p in _pool_base if p["id"] == lid),
                                       {f: 1.0/len(_fmts) for f in _fmts})

                    def _eval_pool(new_cap, is_new=False, new_alloc=None):
                        """Bangun pool dgn kapasitas lini didiagnosis diset new_cap
                        (atau tambah lini baru), rebalans, kembalikan (max_util, peta)."""
                        _pl = [dict(p) for p in _pool_base]
                        if is_new:
                            _pl.append({"id": "__NEW__", "cap": new_cap,
                                        "alloc": new_alloc or _alloc_diag})
                        else:
                            for p in _pl:
                                if p["id"] == lid:
                                    p["cap"] = new_cap
                        _r = _rebalance_pool(_pl, _demand_by_fmt)
                        _mx = max((_r[p["id"]]["util"] for p in _pl), default=999)
                        return _mx, _r

                    # Basis manfaat yang KONSISTEN untuk semua jenis investasi:
                    # produksi berkelanjutan pool sebelum investasi (pada batas) dan
                    # total permintaan pool. Manfaat tiap opsi = produksi tambahan
                    # neto yang kini dapat dilayani (sama metode untuk replace,
                    # multijalur, maupun lini baru) — hanya CAPEX yang berbeda.
                    _pool_keep = sum((_line_cfg.get(_l2, {}).get("limit", _lim) / 100.0)
                                     * _line_analysis[_l2]["cap_fm"] for _l2 in _pool_lids)
                    _pool_target = sum(_demand_by_fmt.values())
                    _pool_current = sum(_line_analysis[_l2]["dem_m"] for _l2 in _pool_lids)
                    # Apakah ada kekurangan kapasitas KERAS (demand > kapasitas nominal,
                    # benar-benar tak bisa diproduksi)? vs. sekadar di atas batas andal.
                    _hard_unmet = _pool_target - _pool_current   # = unmet dari DES (bila ada)

                    def _net_new_for(sus_after):
                        """Produksi tambahan neto = kenaikan demand yang dapat dilayani
                        secara BERKELANJUTAN (dalam batas utilisasi andal) berkat
                        investasi. Batas utilisasi = kapasitas andal: produksi di atas
                        batas bersifat berisiko/tak berkelanjutan, sehingga demand yang
                        memaksa operasi di atas batas dihitung sebagai 'at-risk' yang
                        diamankan investasi. Bila demand sudah di dalam kapasitas andal,
                        manfaat = 0."""
                        return max(min(_pool_target, sus_after)
                                   - min(_pool_target, _pool_keep), 0.0)

                    # Helper untuk MEMERINGKAT opsi berdasar kelayakan finansial
                    # (forward-looking) → rekomendasi default = opsi terbaik secara
                    # ekonomi, bukan sekadar opsi pertama di katalog.
                    _eff_cf = float(params.get("effective_capacity_factor", 0.91))
                    _g_dem  = float(params.get("demand_growth_annual", 0.08))
                    _N_life = int(params.get("project_lifetime_year", 10))
                    _vpt    = float(params.get("internal_value_per_ton", 2_100_000))
                    def _opex_for(filler, lanes, iv_key):
                        o = float(filler.get("capex", 0)) * float(filler.get("opex_rate", 0.09)) * lanes
                        for _c in _iv_pkgs.get(iv_key, {}).get("components_extra", []):
                            _cm = _cat_machines.get(_c["key"], {})
                            o += float(_cm.get("capex", 0)) * float(_cm.get("opex_rate", 0.06)) * _c.get("qty", 1)
                        return o + _pkg_opex_annual(iv_key) + _cust_fx["opex_add"]
                    def _fwd_npv(capex, opex, sus_after):
                        _cb = (_pool_keep / max(_lim / 100.0, 0.01)) * _eff_cf
                        _ca = (sus_after  / max(_lim / 100.0, 0.01)) * _eff_cf
                        _st = []
                        for _t in range(1, _N_life + 1):
                            _d = _pool_target * ((1 + _g_dem) ** _t)
                            _pv = max(0.0, max(0.0, _d - _cb) - max(0.0, _d - _ca))
                            _st.append(max(_pv * 12 * _vpt - _cust_fx["benefit_cut"], 0.0))
                        try:
                            return compute_financial(int(capex), 0,
                                {**params, "_benefit_stream": _st}, annual_opex_extra=opex).get("npv", 0)
                        except Exception:
                            return 0

                    # Bangun opsi intervensi sesuai jenis yang berlaku untuk tipe lini ini
                    _iv_options = []   # tiap: dict(jenis, label, filler, capex, proj_util, layak_kapasitas, scope)

                    for iv_key, iv in _iv_pkgs.items():
                        if _ltype not in iv.get("applies_to", []): continue
                        _extra = _pkg_extra_capex(iv)
                        _ohp   = 1 + _pkg_overhead_pct(iv_key)
                        _fixc  = _pkg_fixed_cost(iv_key) + _cust_fx["capex_add"]

                        _mode = iv.get("mode", "replace")
                        if _mode == "new_line":
                            # Lini baru = menambah kapasitas baru ke pool se-format.
                            # Seluruh pool (existing + lini baru) direbalans proporsional
                            # sadar-format. Pilih filler TERKECIL yang membuat utilisasi
                            # maksimum pool ≤ batas. Manfaat = produksi tambahan neto.
                            _net_new = max(_pool_target - _pool_keep, 0)
                            if _net_new <= 1.0:
                                _iv_options.append({
                                    "jenis": iv["name"], "key": iv_key, "scope": iv.get("scope",""),
                                    "filler_key": None, "filler": {}, "filler_cap": 0, "lanes": 1,
                                    "eff_cap": 0, "proj_util": 0, "capex": 0, "extra": 0,
                                    "layak": False, "need_for_fin": 0, "transfer": 0,
                                    "keep_load": _need_m, "not_applicable": True,
                                    "exist_after": la["util"], "peer_lids": _pool_lids,
                                    "new_line_tons": 0, "net_new": 0,
                                    "desc": "Tidak relevan untuk kondisi ini — kapasitas lini "
                                            "existing se-format masih sanggup menampung beban "
                                            "pada batas utilisasinya (cukup penyeimbangan beban "
                                            "antar-lini atau penyesuaian jadwal).",
                                })
                            else:
                                # Kapasitas efektif lini baru = kapasitas filler ×
                                # faktor jadwal (hari/jam kerja lini baru). Pilih filler
                                # terkecil yang membuat utilisasi maksimum pool ≤ batas.
                                _best = None  # (mk, mm, effcap, mx, rmap)
                                for mk, mm, cap in sorted(_fcands, key=lambda x: x[2]):
                                    _eff = cap * _newline_sched_factor
                                    _mx, _rmap = _eval_pool(_eff, is_new=True, new_alloc=_alloc_diag)
                                    if _mx <= _lim:
                                        _best = (mk, mm, _eff, _mx, _rmap); break
                                if _best is None:
                                    mk, mm, cap = max(_fcands, key=lambda x: x[2])
                                    _eff = cap * _newline_sched_factor
                                    _mx, _rmap = _eval_pool(_eff, is_new=True, new_alloc=_alloc_diag)
                                    _best = (mk, mm, _eff, _mx, _rmap)
                                mk, mm, cap, _mx, _rmap = _best
                                _capex = (mm.get("capex",0) + _extra) * _ohp + _fixc
                                _new_util = _rmap.get("__NEW__", {}).get("util", 0)
                                _new_tons = _rmap.get("__NEW__", {}).get("load", 0)
                                _peer_u = {l2: _rmap[l2]["util"] for l2 in _pool_lids}
                                _peer_t = {l2: _rmap[l2]["load"] for l2 in _pool_lids}
                                _sus_after_nl = _pool_keep + (_lim / 100.0) * cap
                                _net_new = _net_new_for(_sus_after_nl)
                                _iv_options.append({
                                    "jenis": iv["name"], "key": iv_key, "scope": iv.get("scope",""),
                                    "filler_key": mk, "filler": mm, "filler_cap": cap, "lanes": 1,
                                    "eff_cap": cap, "proj_util": _new_util, "capex": _capex,
                                    "extra": _extra, "layak": _mx <= _lim,
                                    "need_for_fin": _net_new, "transfer": _net_new,
                                    "keep_load": _pool_keep, "not_applicable": False,
                                    "exist_after": _mx, "peer_lids": _pool_lids,
                                    "peer_util_after": _peer_u, "peer_tons_after": _peer_t,
                                    "pool_util_after": _new_util, "new_line_tons": _new_tons,
                                    "net_new": _net_new, "pool_max_util": _mx,
                                    "sus_after": _sus_after_nl,
                                    "pool_target": _pool_target,
                                    "new_days": _newline_days, "new_hours": _newline_hours,
                                    "desc": f"Lini baru ditambahkan ke kelompok format "
                                            f"{'/'.join(_fmts)}. Seluruh lini se-format "
                                            f"direbalans proporsional; lini baru menangani "
                                            f"{_new_tons:,.0f} ton/bln (utilisasi {_new_util:.1f}%). "
                                            f"Produksi tambahan neto {_net_new:,.0f} ton/bln.",
                                })

                        elif _mode == "multilane":
                            # Multijalur: kapasitas efektif = jumlah jalur × kapasitas
                            # per-unit. Kapasitas lini berubah → seluruh pool direbalans.
                            # Pilih kombinasi (unit, jalur) dengan jalur MINIMUM yang
                            # membuat utilisasi maksimum pool ≤ batas.
                            _best = None  # (mk, mm, cap, lanes, effcap, mx, rmap)
                            for mk, mm, cap in sorted(_fcands, key=lambda x: x[2]):
                                _max_lanes = int(mm.get("multiline_lanes", 4))
                                for _n in range(1, _max_lanes + 1):
                                    _effcap = cap * _n
                                    _mx, _rmap = _eval_pool(_effcap)
                                    if _mx <= _lim:
                                        _best = (mk, mm, cap, _n, _effcap, _mx, _rmap)
                                        break
                                if _best is not None:
                                    break
                            if _best is None:
                                _t = max(_fcands, key=lambda x: x[2] * int(x[1].get("multiline_lanes", 4)))
                                _mk, _mm, _cap = _t
                                _n = int(_mm.get("multiline_lanes", 4)); _effcap = _cap * _n
                                _mx, _rmap = _eval_pool(_effcap)
                                _best = (_mk, _mm, _cap, _n, _effcap, _mx, _rmap)
                            mk, mm, cap, _lanes, _effcap, _mx, _rmap = _best
                            _pu = _rmap.get(lid, {}).get("util", 0)
                            _capex = (mm.get("capex",0)*_lanes + _extra) * _ohp + _fixc
                            _peer_u = {l2: _rmap[l2]["util"] for l2 in _pool_lids}
                            _peer_t = {l2: _rmap[l2]["load"] for l2 in _pool_lids}
                            _sus_after = sum((_line_cfg.get(_l2, {}).get("limit", _lim)/100.0)
                                             * (_effcap if _l2 == lid else _line_analysis[_l2]["cap_fm"])
                                             for _l2 in _pool_lids)
                            _nn = _net_new_for(_sus_after)
                            _iv_options.append({
                                "jenis": iv["name"], "key": iv_key, "scope": iv.get("scope",""),
                                "filler_key": mk, "filler": mm, "filler_cap": cap, "lanes": _lanes,
                                "eff_cap": _effcap, "proj_util": _pu, "capex": _capex, "extra": _extra,
                                "layak": _mx <= _lim, "need_for_fin": _nn,
                                "net_new": _nn, "sus_after": _sus_after, "pool_target": _pool_target,
                                "peer_lids": _pool_lids, "peer_util_after": _peer_u,
                                "peer_tons_after": _peer_t, "pool_max_util": _mx,
                                "desc": f"{_lanes} jalur paralel {mm.get('full_name','')} "
                                        f"(kapasitas efektif {_effcap:,.0f} ton/bln). Seluruh "
                                        f"lini se-format direbalans; utilisasi Line {lid} "
                                        f"{_pu:.1f}%, tertinggi di pool {_mx:.1f}%.",
                            })

                        else:  # replace_filler
                            # Penggantian unit tunggal pada lini didiagnosis. Kapasitas
                            # lini berubah → seluruh pool se-format direbalans. Pilih
                            # filler TERKECIL yang membuat utilisasi maksimum pool ≤ batas.
                            _best = None
                            for mk, mm, cap in sorted(_fcands, key=lambda x: x[2]):
                                _mx, _rmap = _eval_pool(cap)
                                if _mx <= _lim:
                                    _best = (mk, mm, cap, _mx, _rmap); break
                            if _best is None:
                                mk, mm, cap = max(_fcands, key=lambda x: x[2])
                                _mx, _rmap = _eval_pool(cap)
                                _best = (mk, mm, cap, _mx, _rmap)
                            mk, mm, cap, _mx, _rmap = _best
                            _pu = _rmap.get(lid, {}).get("util", 0)
                            _capex = (mm.get("capex",0) + _extra) * _ohp + _fixc
                            _peer_u = {l2: _rmap[l2]["util"] for l2 in _pool_lids}
                            _peer_t = {l2: _rmap[l2]["load"] for l2 in _pool_lids}
                            _sus_after = sum((_line_cfg.get(_l2, {}).get("limit", _lim)/100.0)
                                             * (cap if _l2 == lid else _line_analysis[_l2]["cap_fm"])
                                             for _l2 in _pool_lids)
                            _nn = _net_new_for(_sus_after)
                            _iv_options.append({
                                "jenis": iv["name"], "key": iv_key, "scope": iv.get("scope",""),
                                "filler_key": mk, "filler": mm, "filler_cap": cap, "lanes": 1,
                                "eff_cap": cap, "proj_util": _pu, "capex": _capex, "extra": _extra,
                                "layak": _mx <= _lim, "need_for_fin": _nn,
                                "net_new": _nn, "sus_after": _sus_after, "pool_target": _pool_target,
                                "peer_lids": _pool_lids, "peer_util_after": _peer_u,
                                "peer_tons_after": _peer_t, "pool_max_util": _mx,
                                "desc": f"Filler tunggal {mm.get('full_name','')} "
                                        f"({cap:,.0f} ton/bln) menggantikan unit existing. "
                                        f"Seluruh lini se-format {'/'.join(_fmts)} direbalans; "
                                        f"utilisasi Line {lid} {_pu:.1f}%, tertinggi di pool "
                                        f"{_mx:.1f}%.",
                            })

                    if not _iv_options:
                        st.warning(f"Tidak ada jenis investasi yang berlaku untuk tipe "
                                   f"'{_ltype}' pada Line {lid}. Atur di Parameter & Katalog Investasi.")
                        continue

                    # Ranking: yang memenuhi kapasitas dulu, lalu CAPEX termurah;
                    # opsi tidak-relevan selalu di urutan terakhir
                    _iv_options.sort(key=lambda o: (o.get("not_applicable", False),
                                                    not o["layak"], o["capex"]))

                    # Peringkat opsi: opsi yang tidak relevan di akhir; selebihnya
                    # diurut prioritas kelayakan kapasitas, lalu NPV (forward-looking)
                    # tertinggi. Rekomendasi sistem = opsi terbaik secara ekonomi.
                    for o in _iv_options:
                        if o.get("not_applicable"):
                            o["npv"] = float("-inf")
                        else:
                            o["npv"] = _fwd_npv(o["capex"],
                                                _opex_for(o.get("filler", {}), o.get("lanes", 1), o["key"]),
                                                o.get("sus_after", _pool_keep))
                    _iv_options.sort(key=lambda o: (o.get("not_applicable", False),
                                                    not o.get("layak", False),
                                                    -(o["npv"] if o["npv"] != float("-inf") else -1e30)))

                    # Tabel ringkas semua jenis investasi.
                    # Untuk penambahan lini baru, kolom util menampilkan hasil
                    # PEMBAGIAN beban: lini existing turun ke sekian%, lini baru
                    # menanggung sisanya — bukan 0 (lini lama tidak dihapus).
                    _tbl = []
                    for o in _iv_options:
                        _is_nl_row = (_iv_pkgs.get(o["key"], {}).get("mode") == "new_line")
                        if _is_nl_row and not o.get("not_applicable"):
                            # Label lini baru: sebut kapasitas total & format saja.
                            # TIDAK menyebut jumlah jalur — capacity_ton_month sudah
                            # kapasitas TOTAL mesin (bukan per-jalur), sehingga
                            # menyebut "N jalur" justru menyesatkan.
                            _cfg_txt = f"Lini baru terpisah ({'/'.join(_fmts)})"
                            _eff_txt = f'{o["eff_cap"]:,.0f} (lini baru)'
                            _util_txt = (f'existing se-format ~{o.get("exist_after",0):.0f}% + '
                                         f'baru {o["proj_util"]:.0f}%')
                        elif _is_nl_row and o.get("not_applicable"):
                            _cfg_txt = "Lini baru terpisah"
                            _eff_txt = "Tidak diperlukan"
                            _util_txt = f'existing cukup ({la["util"]:.0f}%)'
                        else:
                            # Konversi multijalur → sebut jumlah jalur; penggantian
                            # filler → sebut TIPE lini (bukan "jalur tunggal" yang
                            # membingungkan untuk lini multiline).
                            if o["lanes"] > 1:
                                _cfg_txt = f'{o["lanes"]} jalur paralel'
                            else:
                                _cfg_txt = f'Ganti filler pada lini {_ltype.lower()} ({"/".join(_fmts)})'
                            _eff_txt = f'{o["eff_cap"]:,.0f}'
                            _util_txt = f'{o["proj_util"]:.0f}%'
                        _tbl.append({
                            "Jenis Investasi": o["jenis"],
                            "Unit Filler": o["filler"].get("full_name","") or "—",
                            "Konfigurasi": _cfg_txt,
                            "Kapasitas Efektif (ton/bln)": _eff_txt,
                            "Proyeksi Utilisasi": _util_txt,
                            "Memenuhi Kapasitas": ("Tidak relevan" if o.get("not_applicable")
                                                   else ("Ya" if o["layak"] else "Tidak")),
                            "Estimasi CAPEX": fmt_rp(o["capex"]),
                        })
                    def _iv_style(v):
                        return {"Ya":"color:#1a7f4b;font-weight:700",
                                "Tidak":"color:#f85149;font-weight:700",
                                "Tidak relevan":"color:#8b949e;font-weight:600"}.get(v,"")
                    st.dataframe(
                        pd.DataFrame(_tbl).style.map(_iv_style, subset=["Memenuhi Kapasitas"]),
                        use_container_width=True, hide_index=True)

                    # Rekomendasi sistem = opsi teratas
                    _rec = _iv_options[0]
                    st.markdown(
                        f'<div style="border-left:4px solid #088395;background:#F4FBFC;'
                        f'border-radius:4px;padding:8px 14px;margin:4px 0;font-size:.82rem;">'
                        f'<b style="color:#088395;">Rekomendasi sistem — Line {lid}:</b> '
                        f'{_rec["jenis"]}. {_rec["desc"]}</div>', unsafe_allow_html=True)

                    # Pilihan jenis investasi (perusahaan bebas memilih)
                    _iv_labels = [o["jenis"] for o in _iv_options]
                    _pick = st.selectbox(
                        f"Jenis investasi untuk analisis finansial — Line {lid}:",
                        _iv_labels, index=0, key=f"iv_pick_{lid}")
                    _chosen = next((o for o in _iv_options if o["jenis"] == _pick), _rec)

                    # Komponen tambahan (jika ada) ditampilkan
                    _pkg = _iv_pkgs.get(_chosen["key"], {})
                    if _pkg.get("components_extra"):
                        _is_nl = _iv_pkgs.get(_chosen["key"], {}).get("mode") == "new_line"
                        _comp_ttl = (f"Komponen {_chosen['jenis']} — Lini Baru (pendamping Line {lid})"
                                     if _is_nl else f"Komponen {_chosen['jenis']} — Line {lid}")
                        with st.expander(_comp_ttl):
                            _crows = [{
                                "Komponen": _cat_machines.get(c["key"],{}).get("full_name", c["key"]),
                                "Qty": c.get("qty",1),
                                "CAPEX": fmt_rp(float(_cat_machines.get(c["key"],{}).get("capex",0))*c.get("qty",1)),
                            } for c in _pkg["components_extra"]]
                            _crows.append({"Komponen": _chosen["filler"].get("full_name",""), "Qty": 1,
                                           "CAPEX": fmt_rp(_chosen["filler"].get("capex",0))})
                            st.dataframe(pd.DataFrame(_crows), use_container_width=True, hide_index=True)

                    _sel_machines[lid] = {
                        "data": _chosen["filler"], "key": _chosen["filler_key"],
                        "name": _chosen["filler"].get("full_name",""),
                        "jenis": _chosen["jenis"], "iv_key": _chosen["key"],
                        "mode": _iv_pkgs.get(_chosen["key"], {}).get("mode", "replace"),
                        "need_m": _chosen["need_for_fin"], "proj_util": _chosen["proj_util"],
                        "total_capex_override": int(_chosen["capex"]),
                        "lanes": _chosen.get("lanes", 1), "eff_cap": _chosen.get("eff_cap", 0),
                        "util_before": float(_line_analysis.get(lid, {}).get("util", 0)),
                        "transfer": float(_chosen.get("transfer", 0)),
                        "exist_after": float(_chosen.get("exist_after", 0)),
                        "peer_lids": _chosen.get("peer_lids", [lid]),
                        "pool_util_after": float(_chosen.get("pool_util_after",
                                                _chosen.get("exist_after", 0))),
                        "peer_util_after": _chosen.get("peer_util_after", {}),
                        "peer_tons_after": _chosen.get("peer_tons_after", {}),
                        "new_line_tons": float(_chosen.get("new_line_tons", 0)),
                        "net_new": float(_chosen.get("net_new", _chosen.get("transfer", 0))),
                        "sus_after": float(_chosen.get("sus_after", 0)),
                        "pool_target": float(_chosen.get("pool_target", 0)),
                        "pool_keep": float(_pool_keep),
                        "pool_current": float(_pool_current),
                        "lim": float(_lim),
                        "pool_current": float(_pool_current),
                        "lim": float(_lim),
                        "new_days": int(_chosen.get("new_days", 7)),
                        "new_hours": int(_chosen.get("new_hours", 24)),
                    }

                if not _sel_machines:
                    st.info("Tambahkan mesin yang sesuai ke katalog untuk melanjutkan "
                            "analisis kelayakan finansial.")
                else:
                    st.markdown("---")
                    # ── ANALISIS KELAYAKAN FINANSIAL ─────────────────────
                    st.markdown('<div class="section-title">Kelayakan Finansial</div>',
                                unsafe_allow_html=True)
                    st.markdown('<div style="color:#f85149;font-weight:600;'
                                'font-size:.8rem;margin-bottom:.8rem;">'
                                'Semua angka merupakan estimasi — konfirmasi ke pemasok '
                                'dan manajemen sebelum keputusan final.</div>',
                                unsafe_allow_html=True)

                    for lid, sm in _sel_machines.items():
                        mm = sm["data"]
                        _need_m  = float(sm["need_m"])
                        _pu      = float(sm["proj_util"])
                        _lanes   = int(sm.get("lanes", 1))
                        # Kapasitas efektif (memperhitungkan jumlah jalur untuk multiline)
                        _cap_m   = float(sm.get("eff_cap", mm.get("capacity_ton_month", 0)))
                        # CAPEX sudah dihitung lengkap (filler×jalur + komponen + overhead) saat memilih intervensi
                        _total_capex = int(sm.get("total_capex_override",
                                                   mm.get("capex", 0) * (1 + (_oh_pct or 0.18))))
                        # OPEX: perawatan mesin (filler x jalur + komponen) + item OPEX
                        # yang berlaku untuk paket ini (maintenance/manpower dari master)
                        _opex_yr = float(mm.get("capex", 0)) * float(mm.get("opex_rate", 0.09)) * _lanes
                        _ivp = _cat.get("intervention_packages", {}).get(sm.get("iv_key",""), {})
                        for _c in _ivp.get("components_extra", []):
                            _cm = _cat_machines.get(_c["key"], {})
                            _opex_yr += float(_cm.get("capex", 0)) * float(_cm.get("opex_rate", 0.06)) * _c.get("qty", 1)
                        _opex_yr += _pkg_opex_annual(sm.get("iv_key","")) + _cust_fx["opex_add"]

                        # ── Manfaat — FORWARD-LOOKING (ekspansi kapasitas proaktif) ──
                        # Investasi kapasitas dinilai sebagaimana praktik nyata: atas
                        # produksi tambahan yang dapat DILAYANI sepanjang umur proyek
                        # seiring demand tumbuh. Tiap tahun dibandingkan unmet TANPA vs
                        # DENGAN investasi; selisihnya (demand yang kini terlayani,
                        # sebelumnya hilang) × margin = manfaat tahun itu.
                        # Ini menyelesaikan kasus "titik 34%": demand masih terpenuhi
                        # kini, tetapi investasi mencegah unmet yang datang saat tumbuh
                        # (sesuai prinsip: investasi dilakukan SEBELUM kapasitas habis).
                        # Peka skenario: demand jauh di bawah kapasitas → manfaat ~0
                        # (tak perlu investasi); demand mendekati/menembus kapasitas →
                        # manfaat tumbuh → investasi menjadi layak.
                        _rf       = float(params.get("realization_factor", 0.75))
                        _saving_ton = float(params.get("internal_value_per_ton", 2_100_000))
                        _g        = float(params.get("demand_growth_annual", 0.08))
                        _eff      = float(params.get("effective_capacity_factor", 0.91))
                        _ccfg     = params.get("calc_config", {}) or {}
                        _lim_f    = max(float(sm.get("lim", 85)) / 100.0, 0.01)
                        # Kapasitas EFEKTIF pool (maks produksi sebelum unmet, sudah
                        # memperhitungkan changeover/BLOSS — konsisten dgn DES):
                        #   efektif = (kapasitas nominal) × faktor_kapasitas_efektif
                        _cap_before = (float(sm.get("pool_keep", 0)) / _lim_f) * _eff
                        _cap_after  = (float(sm.get("sus_after", 0)) / _lim_f) * _eff
                        _dem0       = float(sm.get("pool_target", 0))   # demand kini (+unmet)
                        _N        = int(params.get("project_lifetime_year", 10))
                        _benefit_stream, _prevented_list = [], []
                        for _t in range(1, _N + 1):
                            _dem_t = _dem0 * ((1 + _g) ** _t)
                            _unmet_wo = max(0.0, _dem_t - _cap_before)   # tanpa investasi
                            _unmet_wi = max(0.0, _dem_t - _cap_after)    # dengan investasi
                            _prev_t   = max(0.0, _unmet_wo - _unmet_wi)  # demand kini terlayani
                            _prevented_list.append(_prev_t)
                            # Manfaat = kehilangan penjualan yang DICEGAH × margin.
                            # Tidak dikenai haircut realisasi: lost-sales yang dicegah
                            # terealisasi penuh saat demand forecast terjadi (haircut
                            # realisasi diperuntukkan manfaat spekulatif/kapasitas nganggur,
                            # bukan penjualan yang nyata hilang bila tak berinvestasi).
                            # Konservatisme sudah ada pada asumsi pertumbuhan demand
                            # (moderat) dan derating kapasitas efektif.
                            _ben_t = _prev_t * 12 * _saving_ton
                            _ben_t = max(_ben_t - _cust_fx["benefit_cut"], 0.0)
                            _benefit_stream.append(_ben_t)
                        # Rata-rata untuk tampilan ringkas
                        _prev_avg = sum(_prevented_list) / max(_N, 1)
                        _annual_add = _prev_avg * 12
                        _unmet_yr = _prev_avg * 12          # transparansi: demand tambahan/th
                        _headroom_yr = 0.0
                        _new_rev_m = _prev_avg; _secure_m = 0.0

                        _p2 = dict(params); _p2["_benefit_stream"] = _benefit_stream
                        fin = compute_financial(_total_capex, _annual_add, _p2,
                                               annual_opex_extra=_opex_yr)
                        _N = int(params.get("project_lifetime_year", 5))
                        _pb = fin.get("payback_year") or 99
                        _irr_pct = fin.get("irr_pct") or 0
                        _pb_thr = int(params.get("payback_threshold_year", 3))
                        _r_min = float(params.get("minimum_irr", 0.15))
                        _roi_yr = fin.get("roi_pct", 0) / _N

                        _is_nl_fin = sm.get("mode") == "new_line"
                        _hdr_line  = (f'Lini Baru (pendamping Line {lid})' if _is_nl_fin
                                      else f'Line {lid}')
                        st.markdown(
                            f'<div style="font-size:.85rem;font-weight:700;'
                            f'color:#071952;margin:8px 0 2px 0;">'
                            f'{_hdr_line} — {sm.get("jenis","Investasi")}: {sm["name"]} '
                            f'({_cap_m:.0f} ton/bln → proj. util {_pu:.1f}%)</div>',
                            unsafe_allow_html=True)
                        st.markdown(
                            f'<div style="font-size:.78rem;color:#506680;margin:0 0 6px 0;">'
                            f'Nilai investasi (CAPEX, termasuk overhead instalasi): '
                            f'<b>{fmt_rp(_total_capex)}</b> — menghasilkan kelayakan berikut:</div>',
                            unsafe_allow_html=True)

                        _kf_cols = st.columns(3)
                        for col, lbl, val, ok in [
                            (_kf_cols[0], "NPV", fmt_rp(fin["npv"]), fin["npv"]>=0),
                            (_kf_cols[1], "IRR", f"≥200%" if _irr_pct>200 else f"{_irr_pct:.1f}%",
                             _irr_pct/100 >= _r_min),
                            (_kf_cols[2], "Payback", f"{_pb:.2f} thn" if fin["payback_year"] else "N/A",
                             _pb <= _pb_thr),
                        ]:
                            clr = "#3fb950" if ok else "#f85149"
                            with col:
                                st.markdown(
                                    f'<div class="kpi-box" style="border-left-color:{clr};">'
                                    f'<div class="kpi-label">{lbl}</div>'
                                    f'<div class="kpi-value" style="color:{clr};font-size:1rem;">'
                                    f'{val}</div></div>', unsafe_allow_html=True)

                        # Catatan finansial: netral, formal, singkat (bukan alarm).
                        _g_pct = float(params.get("demand_growth_annual", 0.08)) * 100
                        if fin["npv"] >= 0:
                            st.caption(f"Dinilai sebagai ekspansi kapasitas proaktif: pada "
                                       f"asumsi pertumbuhan demand {_g_pct:.0f}%/tahun, investasi "
                                       f"mencegah kehilangan penjualan saat demand menembus "
                                       f"kapasitas — memberi pengembalian positif sepanjang umur lini.")
                        else:
                            st.caption(f"Pada parameter saat ini (pertumbuhan demand {_g_pct:.0f}%/"
                                       f"tahun, nilai manfaat Rp {_saving_ton/1e6:,.1f} jt/ton), opsi ini "
                                       f"belum mencapai pengembalian positif. Asumsi dapat ditinjau "
                                       f"di menu Parameter & Katalog Investasi.")

                        # ── ANALISIS SENSITIVITAS (TORNADO) ─────────────────
                        # Uji ketahanan keputusan: tiap parameter pendorong NPV
                        # digeser ±range (lainnya tetap di nilai dasar), NPV dihitung
                        # ulang dengan metode forward-looking yang SAMA. Batang
                        # diurutkan dari pengaruh terbesar → bentuk tornado. Garis 0
                        # = ambang kelayakan; garis titik-titik = NPV dasar.
                        _sens_pct = float(params.get("sensitivity_range_pct", 0.20))
                        _wacc0    = float(params.get("discount_rate", 0.13))

                        def _npv_sens(margin=None, growth=None, wacc=None,
                                      capex=None, opex=None, eff=None):
                            _m  = _saving_ton if margin is None else margin
                            _gg = _g   if growth is None else growth
                            _ee = _eff if eff    is None else eff
                            _cx = _total_capex if capex is None else capex
                            _ox = _opex_yr if opex is None else opex
                            _cb = (float(sm.get("pool_keep", 0)) / _lim_f) * _ee
                            _ca = (float(sm.get("sus_after", 0)) / _lim_f) * _ee
                            _stream, _prev = [], []
                            for _tt in range(1, _N + 1):
                                _dt = _dem0 * ((1 + _gg) ** _tt)
                                _pv = max(0.0, max(0.0, _dt - _cb) - max(0.0, _dt - _ca))
                                _prev.append(_pv)
                                _stream.append(max(_pv * 12 * _m - _cust_fx["benefit_cut"], 0.0))
                            _pp = dict(params); _pp["_benefit_stream"] = _stream
                            if wacc is not None:
                                _pp["discount_rate"] = wacc
                            _add = sum(_prev) / max(_N, 1) * 12
                            return compute_financial(int(_cx), _add, _pp,
                                                     annual_opex_extra=_ox).get("npv", 0)

                        _drivers = [
                            ("Margin / ton",             "margin", _saving_ton),
                            ("Pertumbuhan demand",       "growth", _g),
                            ("WACC",                     "wacc",   _wacc0),
                            ("CAPEX",                    "capex",  _total_capex),
                            ("OPEX tahunan",             "opex",   _opex_yr),
                        ]
                        # Catatan: "Faktor kapasitas efektif" sengaja TIDAK diuji dalam
                        # tornado. Faktor ini adalah asumsi operasional yang relatif tetap
                        # (≈91%) dan secara fisik tidak dapat bergeser ±20% (mis. >100%
                        # mustahil), sehingga mengujinya dengan rentang sama seperti
                        # parameter pasar (margin/growth/WACC) akan menyesatkan.
                        _srows = []
                        for _dn, _dk, _dbase in _drivers:
                            _lo_v = _dbase * (1 - _sens_pct)
                            _hi_v = _dbase * (1 + _sens_pct)
                            if _dk == "eff":
                                _hi_v = min(_hi_v, 1.0)
                            _npv_lo = _npv_sens(**{_dk: _lo_v})
                            _npv_hi = _npv_sens(**{_dk: _hi_v})
                            _srows.append({"name": _dn, "key": _dk, "base": _dbase,
                                           "lo": _npv_lo, "hi": _npv_hi,
                                           "lo_v": _lo_v, "hi_v": _hi_v,
                                           "swing": abs(_npv_hi - _npv_lo)})
                        _srows.sort(key=lambda x: x["swing"], reverse=True)

                        st.markdown(
                            '<div style="font-size:.85rem;font-weight:700;color:#071952;'
                            'margin:14px 0 2px 0;">Analisis Sensitivitas (Tornado)</div>',
                            unsafe_allow_html=True)
                        st.caption(f"Tiap parameter digeser \u00b1{_sens_pct*100:.0f}% dari nilai "
                                   f"dasarnya (lainnya tetap), lalu NPV dihitung ulang. Makin "
                                   f"panjang batang, makin menentukan parameter itu terhadap "
                                   f"kelayakan. Garis merah putus-putus = ambang layak (NPV 0); "
                                   f"garis biru titik-titik = NPV dasar.")

                        _fig_t = go.Figure()
                        for _row in _srows[::-1]:        # bottom-up → terbesar di atas
                            _x0 = min(_row["lo"], _row["hi"]); _x1 = max(_row["lo"], _row["hi"])
                            _cross = _x0 < 0 < _x1
                            _clr = "#d29922" if _cross else ("#1a7f4b" if _x0 >= 0 else "#e05c4b")
                            _fig_t.add_trace(go.Bar(
                                y=[_row["name"]], x=[_x1 - _x0], base=[_x0],
                                orientation="h", marker_color=_clr, showlegend=False,
                                hovertemplate=(f"{_row['name']}<br>"
                                               f"\u2212{_sens_pct*100:.0f}%: {fmt_rp(_row['lo'])}<br>"
                                               f"+{_sens_pct*100:.0f}%: {fmt_rp(_row['hi'])}"
                                               f"<extra></extra>")))
                        _fig_t.add_vline(x=fin["npv"], line_dash="dot",
                                         line_color="#071952", line_width=1)
                        _fig_t.add_vline(x=0, line_dash="dash",
                                         line_color="#e05c4b", line_width=1.2)
                        _fig_t.update_layout(
                            height=56 * len(_srows) + 60,
                            margin=dict(l=10, r=10, t=8, b=10),
                            paper_bgcolor="white", plot_bgcolor="white", bargap=0.35,
                            xaxis=dict(title="NPV (Rp)", gridcolor="#EBF4F6", zeroline=False),
                            yaxis=dict(autorange="reversed"))
                        st.plotly_chart(_fig_t, use_container_width=True)

                        # Tabel ringkas + titik impas parameter kunci (margin)
                        _stbl = pd.DataFrame([{
                            "Parameter": _r["name"],
                            f"NPV \u2212{_sens_pct*100:.0f}%": fmt_rp(_r["lo"]),
                            f"NPV +{_sens_pct*100:.0f}%": fmt_rp(_r["hi"]),
                            "Rentang Ayun NPV": fmt_rp(_r["swing"]),
                        } for _r in _srows])
                        st.dataframe(_stbl, hide_index=True, use_container_width=True)

                        def _breakeven(key, lo, hi):
                            """Cari nilai parameter di [lo,hi] saat NPV menyeberang 0."""
                            _flo = _npv_sens(**{key: lo}); _fhi = _npv_sens(**{key: hi})
                            if (_flo >= 0) == (_fhi >= 0):
                                return None
                            for _ in range(48):
                                _mid = (lo + hi) / 2.0
                                if (_npv_sens(**{key: _mid}) >= 0) == (_fhi >= 0):
                                    hi = _mid
                                else:
                                    lo = _mid
                            return (lo + hi) / 2.0

                        if fin["npv"] >= 0:
                            _be_m = _breakeven("margin", _saving_ton * 0.05, _saving_ton)
                            _be_g = _breakeven("growth", -0.05, _g)
                            _be_w = _breakeven("wacc", _wacc0, 0.60)
                            _bx = []
                            if _be_m is not None:
                                _bx.append(f"margin turun ke Rp {_be_m/1e6:.1f} jt/ton "
                                           f"(\u2212{(1-_be_m/_saving_ton)*100:.0f}%)")
                            if _be_g is not None:
                                _bx.append(f"pertumbuhan demand turun ke {_be_g*100:.1f}%/th")
                            if _be_w is not None:
                                _bx.append(f"WACC naik ke {_be_w*100:.0f}%")
                            if _bx:
                                st.caption("Titik impas (keputusan berubah jadi tidak layak bila): "
                                           + "; ".join(_bx) + ". Di luar itu, rekomendasi tetap layak.")
                            else:
                                st.caption("Keputusan tahan banting: tetap layak di seluruh rentang "
                                           f"\u00b1{_sens_pct*100:.0f}% untuk semua parameter di atas.")

                        # ── Dampak Kapasitas: seluruh lini (sebelum vs sesudah) ──
                        # Untuk SEMUA jenis investasi, lini se-format direbalans:
                        # nilai "sesudah" diambil dari peta hasil rebalans proporsional.
                        # Lini di luar pool tidak terdampak.
                        _imp_lbls, _imp_before, _imp_after = [], [], []
                        _peer_u = sm.get("peer_util_after", {}) or {}
                        for _l2 in _lids:
                            _la2 = _line_analysis.get(_l2, {})
                            _ub2 = float(_la2.get("util", 0))
                            _imp_lbls.append(f"Line {_l2}")
                            _imp_before.append(_ub2)
                            if _l2 in _peer_u:
                                _imp_after.append(_peer_u[_l2])     # hasil rebalans pool
                            else:
                                _imp_after.append(_ub2)             # di luar pool, tetap
                        if _is_nl_fin and sm.get("net_new", sm.get("transfer", 0)) > 0:
                            _imp_lbls.append("Lini Baru")
                            _imp_before.append(0)
                            _nlu = float(sm.get("pool_util_after", 0)) or float(sm.get("proj_util", 0))
                            _imp_after.append(_nlu)
                        _fig_imp = go.Figure()
                        _fig_imp.add_trace(go.Bar(name="Sebelum", x=_imp_lbls,
                            y=_imp_before, marker_color="#8b949e",
                            text=[f"{v:.0f}%" if v > 0 else "" for v in _imp_before],
                            textposition="outside", textfont=dict(size=9)))
                        _fig_imp.add_trace(go.Bar(name="Sesudah", x=_imp_lbls,
                            y=_imp_after, marker_color="#37B7C3",
                            text=[f"{v:.0f}%" for v in _imp_after],
                            textposition="outside", textfont=dict(size=9)))
                        # Garis batas per lini (selebar grup batang masing-masing)
                        _nbar = len(_imp_lbls)
                        for _bi, _bl in enumerate(_imp_lbls):
                            _l2k = _bl.replace("Line ", "")
                            _lv  = float(_line_analysis.get(_l2k, {}).get("lim",
                                         _line_analysis.get(lid, {}).get("lim", 84)))
                            _fig_imp.add_shape(type="line",
                                x0=_bi-0.42, x1=_bi+0.42, y0=_lv, y1=_lv,
                                line=dict(color="#c0392b", width=1.5, dash="dash"))
                        _fig_imp.update_layout(template="plotly_white", barmode="group",
                            title=dict(text="Dampak Kapasitas — Utilisasi per Lini (%)",
                                       font=dict(size=12, color="#071952")),
                            paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                            height=230, margin=dict(l=0, r=0, t=34, b=8),
                            yaxis=dict(range=[0, max(_imp_after + _imp_before + [90]) * 1.22],
                                       gridcolor="#EBF4F6"),
                            legend=dict(orientation="h", font=dict(size=10),
                                        yanchor="bottom", y=1.0, xanchor="right", x=1.0))
                        st.plotly_chart(_fig_imp, use_container_width=True,
                                        key=f"imp_{lid}")

                        # Tonase per lini: existing vs alokasi setelah investasi
                        _ton_before, _ton_after = [], []
                        _peer_t = sm.get("peer_tons_after", {}) or {}
                        for _l2 in _lids:
                            _la2t = _line_analysis.get(_l2, {})
                            _dm2 = float(_la2t.get("dem_m", 0))
                            _ton_before.append(_dm2)
                            if _l2 in _peer_t:
                                _ton_after.append(_peer_t[_l2])     # hasil rebalans pool
                            else:
                                _ton_after.append(_dm2)             # di luar pool, tetap
                        _ton_lbls = [f"Line {_l2}" for _l2 in _lids]
                        if _is_nl_fin and sm.get("net_new", sm.get("transfer", 0)) > 0:
                            _ton_lbls.append("Lini Baru")
                            _ton_before.append(0)
                            _ton_after.append(float(sm.get("new_line_tons",
                                                            sm.get("transfer", 0))))
                        _fig_ton = go.Figure()
                        _fig_ton.add_trace(go.Bar(name="Sebelum", x=_ton_lbls,
                            y=_ton_before, marker_color="#8b949e"))
                        _fig_ton.add_trace(go.Bar(name="Sesudah", x=_ton_lbls,
                            y=_ton_after, marker_color="#088395"))
                        _fig_ton.update_layout(template="plotly_white", barmode="group",
                            title=dict(text="Dampak Kapasitas — Tonase per Lini (ton/bln)",
                                       font=dict(size=12, color="#071952")),
                            paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                            height=210, margin=dict(l=0, r=0, t=34, b=8),
                            yaxis=dict(gridcolor="#EBF4F6"),
                            legend=dict(orientation="h", font=dict(size=10),
                                        yanchor="bottom", y=1.0, xanchor="right", x=1.0))
                        st.plotly_chart(_fig_ton, use_container_width=True,
                                        key=f"impton_{lid}")

                        with st.expander(f"Rincian analisis — {_hdr_line}", expanded=False):
                            _det_rows = [
                                ("CAPEX total (termasuk overhead instalasi)", fmt_rp(_total_capex)),
                                ("OPEX tahunan", fmt_rp(_opex_yr)),
                                ("Asumsi pertumbuhan demand / tahun", f"{float(params.get('demand_growth_annual',0.08))*100:,.1f}%"),
                                ("Demand tambahan terlayani / tahun (rata-rata)", f"{_unmet_yr:,.0f} ton"),
                                ("Estimasi manfaat / tahun (rata-rata)", fmt_rp(fin.get("annual_benefit", 0))),
                                ("Arus kas bersih / tahun", fmt_rp(fin.get("annual_fcf", 0))),
                            ]
                            st.dataframe(pd.DataFrame(_det_rows, columns=["Item","Nilai"]),
                                         use_container_width=True, hide_index=True)
                            st.caption("Manfaat dihitung dari nilai manfaat per ton "
                                       "kapasitas tambahan yang terserap. Seluruh parameter "
                                       "mengikuti menu Parameter & Katalog Investasi.")

                        with st.expander(f"Transparansi Perhitungan — {_hdr_line}", expanded=False):
                            st.caption("Langkah perhitungan dengan nilai aktual skenario ini. "
                                       "Komponen yang disertakan dapat diatur di menu "
                                       "Parameter & Katalog Investasi (Konfigurasi Perhitungan).")
                            _steps = fin.get("calc_steps", [])
                            _step_rows = []
                            for _nm, _frm, _vl in _steps:
                                if _vl is None:
                                    _vs = "-"
                                elif _nm in ("IRR", "ROI/tahun"):
                                    _vs = f"{_vl*100:.1f}%"
                                elif _nm == "Payback":
                                    _vs = (f"{_vl:.2f} thn" if _vl is not None else "Tidak tercapai")
                                else:
                                    _vs = fmt_rp(_vl)
                                _step_rows.append({"Komponen": _nm, "Cara Hitung": _frm, "Nilai": _vs})
                            st.dataframe(pd.DataFrame(_step_rows),
                                         use_container_width=True, hide_index=True)

                        cfs = fin["cash_flows"]
                        yrs = list(range(len(cfs))); cum = np.cumsum(cfs).tolist()
                        _fig_cf = go.Figure()
                        _fig_cf.add_trace(go.Bar(x=yrs, y=[v/1e6 for v in cfs],
                            marker_color=["#f85149"]+["#3fb950"]*(len(cfs)-1),
                            showlegend=False))
                        _fig_cf.add_trace(go.Scatter(x=yrs, y=[v/1e6 for v in cum],
                            mode="lines+markers", line=dict(color="#071952",width=2),
                            name="Kumulatif"))
                        _fig_cf.add_hline(y=0, line_color="#8b949e", line_width=1)
                        _fig_cf.update_layout(template="plotly_white",
                            paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                            height=200, margin=dict(l=0,r=0,t=8,b=16),
                            xaxis=dict(title="Tahun", tickvals=yrs, gridcolor="#EBF4F6"),
                            yaxis=dict(title="Rp Juta", gridcolor="#EBF4F6"),
                            legend=dict(font=dict(size=10)))
                        st.plotly_chart(_fig_cf, use_container_width=True)
                        st.markdown("<br>", unsafe_allow_html=True)
                        sm["fin"] = fin
                        sm["total_capex"] = _total_capex
                        sm["feasible"] = bool(fin.get("npv", 0) >= 0 and
                                              (_irr_pct/100 >= _r_min))

                    # ── KONKLUSI REKOMENDASI ──────────────────────────────
                    # Struktur baris per lini, selaras alur sistem saat ini:
                    # Lini → Tindakan → Konfigurasi → Proyeksi Util → Kelayakan.
                    _n_layak   = sum(1 for sm in _sel_machines.values() if sm.get("feasible"))
                    _n_total   = len(_sel_machines)
                    _all_capex = sum(sm.get("total_capex", 0) for sm in _sel_machines.values())
                    _concl_ok  = _n_layak == _n_total and _n_total > 0
                    _concl_clr = "#1a7f4b" if _concl_ok else ("#d29922" if _n_layak>0 else "#c0392b")
                    _concl_lbl = ("LAYAK" if _concl_ok else
                                  ("LAYAK SEBAGIAN" if _n_layak>0 else "TIDAK LAYAK"))

                    _rows_html = ""
                    for lid2, sm in _sel_machines.items():
                        _nl2   = sm.get("mode") == "new_line"
                        _lini2 = (f"Lini Baru<br><span style='font-size:.68rem;color:#8b949e;'>"
                                  f"pendamping Line {lid2}</span>") if _nl2 else f"Line {lid2}"
                        _nl_type2 = _line_cfg.get(lid2, {}).get("type", "Single line")
                        _cfg2  = (f"{sm.get('lanes',1)} jalur paralel" if sm.get("lanes",1) > 1
                                  else ("Lini baru terpisah" if _nl2
                                        else _nl_type2))
                        _ok2   = sm.get("feasible")
                        _st2_c = "#1a7f4b" if _ok2 else "#c0392b"
                        _st2_t = "Layak" if _ok2 else "Tidak layak"
                        _rows_html += (
                            f'<div style="display:flex;align-items:center;gap:10px;'
                            f'padding:8px 12px;border-top:1px solid #E3EEF1;font-size:.8rem;'
                            f'color:#071952;">'
                            f'<div style="flex:0 0 16%;font-weight:700;">{_lini2}</div>'
                            f'<div style="flex:0 0 24%;">{sm.get("jenis","")}</div>'
                            f'<div style="flex:1 1 auto;">{sm["name"]} &middot; {_cfg2} &middot; '
                            f'{sm.get("eff_cap",0):,.0f} ton/bln</div>'
                            f'<div style="flex:0 0 13%;">Util {sm.get("proj_util",0):.1f}%</div>'
                            f'<div style="flex:0 0 12%;text-align:right;font-weight:700;'
                            f'color:{_st2_c};">{_st2_t}</div></div>')

                    _sched_chips = "".join(
                        f'<span style="display:inline-block;background:#EBF4F6;'
                        f'border:1px solid #37B7C3;border-radius:4px;padding:3px 12px;'
                        f'margin:3px 2px;font-size:.76rem;color:#071952;">'
                        f'Line {lid2}: {int(_t2_days.get(lid2,7))}D/{int(_t2_hrs.get(lid2,24))}H</span>'
                        for lid2 in _lids)
                    # Sertakan lini baru bila ada rekomendasi penambahan lini
                    for lid2, sm in _sel_machines.items():
                        if sm.get("mode") == "new_line" and sm.get("net_new", 0) > 0:
                            _nd = int(sm.get("new_days", 7)); _nh = int(sm.get("new_hours", 24))
                            _sched_chips += (
                                f'<span style="display:inline-block;background:#F4FBFC;'
                                f'border:1px dashed #088395;border-radius:4px;padding:3px 12px;'
                                f'margin:3px 2px;font-size:.76rem;color:#088395;">'
                                f'Lini Baru (pendamping Line {lid2}): {_nd}D/{_nh}H</span>')

                    st.markdown(f"""
                    <div class="dss-card" style="border:1px solid {_concl_clr};border-radius:12px;
                         padding:0;margin:1rem 0;overflow:hidden;">
                      <div style="display:flex;align-items:center;justify-content:space-between;
                           gap:12px;padding:14px 18px;background:#F8FDFB;">
                        <div>
                          <div class="kpi-label" style="letter-spacing:.12em;">KONKLUSI REKOMENDASI</div>
                          <div style="font-size:1.05rem;font-weight:800;color:#071952;margin-top:2px;">
                            {sel_row.get("Label", sel_id)}</div>
                        </div>
                        <span style="background:{_concl_clr};color:#fff;font-weight:700;
                              padding:6px 20px;border-radius:8px;font-size:.86rem;
                              white-space:nowrap;">{_concl_lbl}</span>
                      </div>
                      {_rows_html}
                      <div style="display:flex;align-items:center;justify-content:space-between;
                           gap:12px;padding:10px 18px;border-top:1px solid #E3EEF1;
                           background:#FBFEFE;flex-wrap:wrap;">
                        <div style="font-size:.8rem;color:#071952;">
                          Total estimasi investasi: <b>{fmt_rp(_all_capex)}</b>
                          &nbsp;|&nbsp; {_n_layak} dari {_n_total} rekomendasi memenuhi kriteria</div>
                        <div>{_sched_chips}</div>
                      </div>
                    </div>""", unsafe_allow_html=True)
                    st.caption("Rincian langkah perhitungan tiap rekomendasi tersedia pada "
                               "Transparansi Perhitungan di atas. Seluruh parameter mengikuti "
                               "menu Parameter & Katalog Investasi.")

                    # ── Unduh konfigurasi skenario terpilih (untuk Alokasi Produksi
                    #    atau dokumentasi). Berisi jadwal + hasil rekomendasi per lini.
                    import io as _io_csv
                    _exp_rows = []
                    for _l2 in _lids:
                        _la2 = _line_analysis.get(_l2, {})
                        _sm2 = _sel_machines.get(_l2, {})
                        _exp_rows.append({
                            "Lini": f"Line {_l2}",
                            "Jadwal": f"{int(_t2_days.get(_l2,7))}D/{int(_t2_hrs.get(_l2,24))}H",
                            "Util Sebelum (%)": round(_la2.get("util", 0), 1),
                            "Tonase (ton/bln)": round(_la2.get("dem_m", 0), 1),
                            "Kapasitas (ton/bln)": round(_la2.get("cap_fm", 0), 1),
                            "Keputusan": _t2_dec,
                            "Rekomendasi": _sm2.get("jenis", "-") if _sm2 else "-",
                            "Unit Filler": _sm2.get("name", "-") if _sm2 else "-",
                            "Proj Util Investasi (%)": round(_sm2.get("proj_util", 0), 1) if _sm2 else "",
                            "Estimasi CAPEX": _sm2.get("total_capex", "") if _sm2 else "",
                            "Layak Finansial": ("Ya" if _sm2.get("feasible") else "Tidak") if _sm2 else "",
                        })
                    _exp_df = pd.DataFrame(_exp_rows)
                    _buf = _io_csv.StringIO(); _exp_df.to_csv(_buf, index=False)
                    st.download_button(
                        "Unduh Konfigurasi Skenario Terpilih (CSV)",
                        data=_buf.getvalue(),
                        file_name=f"konfigurasi_skenario_{sel_id}.csv".replace(" ", "_"),
                        mime="text/csv", key="dl_scenario_cfg",
                        help="Berisi jadwal operasi, utilisasi, dan hasil rekomendasi "
                             "per lini untuk skenario terpilih. Dapat dipakai sebagai "
                             "dokumentasi atau referensi di menu Alokasi Produksi.")
