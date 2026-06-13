"""
ALOKASI PRODUKSI — proyeksi penambahan produk ke kapasitas lini internal.

Alur: konfigurasi lini existing (fleksibel, tanpa hardcode) -> daftar produk
yang akan ditambahkan -> alokasi otomatis ke lini yang kompatibel -> keputusan
(layak / waspada / perlu investasi) -> opsi investasi + kelayakan finansial.
Seluruh master data (tipe lini, format, mesin, paket, biaya, parameter)
mengikuti menu Parameter & Katalog Investasi.
"""
import json
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from pathlib import Path

from modules.session import get_state, set_state
from modules.financial_calc import compute_financial, fmt_rp, DEFAULT_PARAMS

_CAT_PATH = Path("data/machine_catalog.json")


def _load_cat():
    try:
        if _CAT_PATH.exists():
            with open(_CAT_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _default_limit(line_type):
    return 88.0 if str(line_type).lower().startswith("multi") else 84.0


def render():
    st.markdown('<div class="page-title">ALOKASI PRODUKSI</div>', unsafe_allow_html=True)
    st.caption("Proyeksi penambahan produk ke kapasitas lini internal — alokasi "
               "otomatis, evaluasi dampak, dan kelayakan investasi bila diperlukan.")

    cat        = _load_cat()
    machines   = cat.get("machines", {})
    formats    = cat.get("package_formats", ["SSS", "BIB", "STICKPACK"])
    line_types = cat.get("line_types", ["Single line", "Multiline", "Stickpack"])
    iv_pkgs    = cat.get("intervention_packages", {})
    params     = dict(DEFAULT_PARAMS)
    params.update(cat.get("global_params", {}))
    params["calc_config"] = cat.get("calc_config", {})
    _ccfg      = params["calc_config"]

    # ════════════════════════════════════════════════════════════════════
    # LANGKAH 1 — KONFIGURASI LINI EXISTING
    # ════════════════════════════════════════════════════════════════════
    st.markdown('<div class="section-title">1. Konfigurasi Lini Existing</div>',
                unsafe_allow_html=True)
    st.caption("Kondisi awal kapasitas internal. Dapat diambil dari hasil evaluasi "
               "skenario atau diatur manual.")

    _sim = get_state("simulation_result")
    _has_sim = isinstance(_sim, pd.DataFrame) and not _sim.empty

    c1, c2 = st.columns([2, 1])
    with c1:
        _src_opts = (["Manual", "Dari hasil Evaluasi Kapasitas (skenario terbaik)"]
                     if _has_sim else ["Manual"])
        _src = st.radio("Sumber kondisi awal:", _src_opts, horizontal=True, key="pa_src")
    with c2:
        _horizon = st.number_input("Horizon Basis Data (bulan)", 1, 36, 12, 1,
            key="pa_horizon",
            help="Tonase dan utilisasi awal merepresentasikan beban produksi "
                 "selama rentang waktu ini.")

    # Deteksi lini dari data simulasi bila dipilih
    _detected = []
    if _src.startswith("Dari") and _has_sim:
        _row = _sim.iloc[0]
        for _col in _sim.columns:
            if _col.startswith("Util_Filling_"):
                _lid = _col.replace("Util_Filling_", "")
                _ton_col = f"Tons_{_lid}"
                _util = float(pd.to_numeric(pd.Series([_row.get(_col, 0)]),
                                            errors="coerce").fillna(0).iloc[0])
                _tons = float(pd.to_numeric(pd.Series([_row.get(_ton_col, 0)]),
                                            errors="coerce").fillna(0).iloc[0])
                if _util > 0:
                    _detected.append({"id": _lid, "util": round(_util, 1),
                                      "tons_m": round(_tons / _horizon, 1)})
        if _detected:
            st.caption(f"Terdeteksi {len(_detected)} lini dari skenario peringkat "
                       f"teratas: {', '.join('Line ' + d['id'] for d in _detected)}. "
                       f"Tonase dikonversi ke basis bulanan (horizon {_horizon} bulan).")

    _n_default = len(_detected) if _detected else 3
    _n_lines = st.number_input("Jumlah lini existing", 1, 12, _n_default, 1, key="pa_nl")

    lines = []
    for i in range(0, int(_n_lines), 3):
        cols = st.columns(min(3, int(_n_lines) - i))
        for j, col in enumerate(cols):
            idx = i + j
            _det = _detected[idx] if idx < len(_detected) else {}
            with col:
                with st.container(border=True):
                    _lid = st.text_input("ID Lini", _det.get("id", chr(65 + idx)),
                                         key=f"pa_id_{idx}")
                    _lt = st.selectbox("Tipe Lini", line_types, key=f"pa_lt_{idx}")
                    _fm = st.multiselect("Format kemasan", formats,
                        default=[formats[0]] if formats else [], key=f"pa_fm_{idx}")
                    _ut = st.number_input("Utilisasi saat ini (%)", 0.0, 120.0,
                        float(_det.get("util", 75.0)), 0.5, key=f"pa_ut_{idx}")
                    _tn = st.number_input("Tonase saat ini (ton/bln)", 0.0, 5000.0,
                        float(_det.get("tons_m", 200.0)), 5.0, key=f"pa_tn_{idx}")
                    _lim = st.number_input("Batas utilisasi (%)", 50.0, 100.0,
                        _default_limit(_lt), 0.5, key=f"pa_lim_{idx}")
                    _cap = (_tn / (_ut / 100.0)) if _ut > 0 else 0.0
                    st.caption(f"Kapasitas tersirat: **{_cap:,.0f} ton/bln** "
                               f"(tonase \u00f7 utilisasi)")
                    lines.append({"id": _lid.strip() or chr(65 + idx), "type": _lt,
                                  "formats": _fm, "util": _ut, "tons": _tn,
                                  "lim": _lim, "cap": _cap})

    _util_tol = st.number_input("Toleransi pelampauan utilisasi (poin)", 0.0, 10.0,
        3.0, 0.5, key="pa_tol",
        help="Pelampauan batas dalam toleransi berstatus WASPADA (pemantauan), "
             "di luar toleransi berstatus KRITIS (evaluasi investasi).")

    # ════════════════════════════════════════════════════════════════════
    # LANGKAH 2 — PRODUK YANG DITAMBAHKAN
    # ════════════════════════════════════════════════════════════════════
    st.markdown('<div class="section-title">2. Produk yang Ditambahkan</div>',
                unsafe_allow_html=True)
    st.caption("Daftar produk (eksternal maupun produk baru) yang akan dialokasikan "
               "ke kapasitas internal.")

    _src = st.radio("Sumber data produk:", ["Isi manual", "Unggah file"],
                    horizontal=True, key="pa_prod_src")

    # Pembacaan kolom yang fleksibel — mengenali berbagai gaya penamaan judul
    def _match_col(cols, *aliases):
        _norm = {str(c).strip().lower().replace("_", " ").replace("-", " "): c for c in cols}
        for a in aliases:
            if a in _norm:
                return _norm[a]
        # pencocokan longgar: judul mengandung salah satu alias
        for key, orig in _norm.items():
            for a in aliases:
                if a in key:
                    return orig
        return None

    _df_init = pd.DataFrame([
        {"Kode SKU": "SKU-001", "Nama Produk": "", "Format": formats[0] if formats else "",
         "Tonase (ton/bln)": 50.0},
    ])

    if _src == "Unggah file":
        st.caption("Unggah file CSV atau Excel berisi kolom: kode produk, jenis "
                   "kemasan, dan tonase per bulan. Nama produk bersifat opsional. "
                   "Judul kolom dikenali secara fleksibel.")
        _up = st.file_uploader("Berkas produk", type=["csv", "xlsx", "xls"],
                               key="pa_prod_file")
        if _up is None:
            st.info("Unggah berkas, atau beralih ke pengisian manual.")
            return
        try:
            if _up.name.lower().endswith(("xlsx", "xls")):
                _raw = pd.read_excel(_up)
            else:
                _raw = pd.read_csv(_up)
        except Exception as e:
            st.error(f"Berkas tidak dapat dibaca: {e}")
            return

        _c_sku = _match_col(_raw.columns, "kode sku", "sku id", "kode produk",
                            "product code", "sku", "kode", "id produk", "material")
        _c_nm  = _match_col(_raw.columns, "nama produk", "nama", "product name",
                            "deskripsi", "description", "nama sku")
        _c_fmt = _match_col(_raw.columns, "format", "jenis kemasan", "kemasan",
                            "format kemasan", "packaging", "tipe kemasan", "port")
        _c_ton = _match_col(_raw.columns, "tonase per bulan", "tonase", "ton/bln",
                            "ton per bulan", "volume", "tonnage", "demand", "tonase (ton/bln)")

        if _c_sku is None or _c_ton is None:
            st.error("Kolom kode produk dan tonase wajib ada. "
                     f"Kolom terbaca: {', '.join(str(c) for c in _raw.columns)}")
            return

        _norm_rows = []
        for _, r in _raw.iterrows():
            _fmt_raw = str(r.get(_c_fmt, "")).strip().upper() if _c_fmt else ""
            # Cocokkan format ke master; jika tak cocok, biarkan apa adanya
            _fmt_val = next((f for f in formats if f.upper() == _fmt_raw),
                            (_fmt_raw or (formats[0] if formats else "")))
            _norm_rows.append({
                "Kode SKU": str(r.get(_c_sku, "")).strip(),
                "Nama Produk": str(r.get(_c_nm, "")).strip() if _c_nm else "",
                "Format": _fmt_val,
                "Tonase (ton/bln)": pd.to_numeric(r.get(_c_ton), errors="coerce"),
            })
        _df_loaded = pd.DataFrame(_norm_rows)
        st.success(f"{len(_df_loaded)} baris terbaca. Periksa dan sesuaikan bila perlu.")
        sku_df = st.data_editor(
            _df_loaded, num_rows="dynamic", use_container_width=True,
            key="pa_sku_editor_up",
            column_config={
                "Kode SKU": st.column_config.TextColumn(required=True),
                "Nama Produk": st.column_config.TextColumn(help="Opsional"),
                "Format": st.column_config.SelectboxColumn(options=formats, required=True),
                "Tonase (ton/bln)": st.column_config.NumberColumn(
                    min_value=0.0, format="%.1f", required=True),
            })
    else:
        st.caption("Tambah baris sesuai kebutuhan.")
        sku_df = st.data_editor(
            _df_init, num_rows="dynamic", use_container_width=True, key="pa_sku_editor",
            column_config={
                "Kode SKU": st.column_config.TextColumn(required=True),
                "Nama Produk": st.column_config.TextColumn(help="Opsional"),
                "Format": st.column_config.SelectboxColumn(options=formats, required=True),
                "Tonase (ton/bln)": st.column_config.NumberColumn(
                    min_value=0.0, format="%.1f", required=True),
            })

    sku_df = sku_df.dropna(subset=["Kode SKU"])
    sku_df = sku_df[sku_df["Kode SKU"].astype(str).str.strip() != ""]
    sku_df = sku_df[pd.to_numeric(sku_df["Tonase (ton/bln)"], errors="coerce").fillna(0) > 0]

    if sku_df.empty or not lines:
        st.info("Lengkapi konfigurasi lini dan minimal satu produk untuk "
                "menjalankan proyeksi alokasi.")
        return

    if not st.button("Jalankan Proyeksi Alokasi", type="primary", key="pa_run"):
        st.stop()

    # ════════════════════════════════════════════════════════════════════
    # LANGKAH 3 — PROYEKSI ALOKASI (load balancing pada lini kompatibel)
    # ════════════════════════════════════════════════════════════════════
    st.markdown('<div class="section-title">3. Hasil Alokasi</div>',
                unsafe_allow_html=True)

    _load = {l["id"]: l["tons"] for l in lines}          # beban berjalan (ton/bln)
    _alloc_rows, _unalloc = [], []
    for _, r in sku_df.iterrows():
        _fmt, _ton = str(r["Format"]), float(r["Tonase (ton/bln)"])
        _cands = [l for l in lines if _fmt in l["formats"] and l["cap"] > 0]
        if not _cands:
            _unalloc.append({"sku": r["Kode SKU"], "fmt": _fmt, "ton": _ton,
                             "alasan": f"Tidak ada lini yang menangani format {_fmt}"})
            continue
        # pilih lini dengan utilisasi pasca-alokasi terendah (penyeimbangan beban)
        _best = min(_cands, key=lambda l: (_load[l["id"]] + _ton) / l["cap"])
        _load[_best["id"]] += _ton
        _alloc_rows.append({"Kode SKU": r["Kode SKU"], "Format": _fmt,
                            "Tonase (ton/bln)": _ton, "Dialokasikan ke": f"Line {_best['id']}"})

    if _alloc_rows:
        st.dataframe(pd.DataFrame(_alloc_rows).style.format(precision=1),
                     use_container_width=True, hide_index=True)
    if _unalloc:
        for u in _unalloc:
            st.warning(f"{u['sku']} ({u['ton']:.0f} ton/bln, format {u['fmt']}) "
                       f"tidak dapat dialokasikan — {u['alasan']}.")

    # Status per lini setelah alokasi
    _status_rows, _crit_lines, _warn_lines = [], [], []
    for l in lines:
        _after_t = _load[l["id"]]
        _after_u = (_after_t / l["cap"] * 100) if l["cap"] > 0 else 0
        l["after_t"], l["after_u"] = _after_t, _after_u
        if _after_u > l["lim"] + _util_tol:
            _stt = "KRITIS"; _crit_lines.append(l)
        elif _after_u > l["lim"]:
            _stt = "WASPADA"; _warn_lines.append(l)
        else:
            _stt = "AMAN"
        l["status"] = _stt
        _status_rows.append({
            "Lini": f"Line {l['id']}", "Tipe": l["type"],
            "Format": "/".join(l["formats"]),
            "Util Sebelum (%)": round(l["util"], 1),
            "Util Sesudah (%)": round(_after_u, 1),
            "Tonase Sebelum": round(l["tons"], 1),
            "Tonase Sesudah": round(_after_t, 1),
            "Batas (%)": l["lim"], "Status": _stt,
        })
    def _stt_style(v):
        return {"AMAN": "color:#1a7f4b;font-weight:700",
                "WASPADA": "color:#d29922;font-weight:700",
                "KRITIS": "color:#c0392b;font-weight:700"}.get(v, "")
    st.dataframe(pd.DataFrame(_status_rows).style.map(_stt_style, subset=["Status"])
                 .format(precision=1), use_container_width=True, hide_index=True)

    # ── Visual dampak (gaya sama dengan Evaluasi Kapasitas) ──────────────
    _lbls   = [f"Line {l['id']}" for l in lines]
    _ub     = [l["util"] for l in lines]
    _ua     = [l["after_u"] for l in lines]
    _fig_u  = go.Figure()
    _fig_u.add_trace(go.Bar(name="Sebelum", x=_lbls, y=_ub, marker_color="#8b949e",
        text=[f"{v:.0f}%" for v in _ub], textposition="outside", textfont=dict(size=9)))
    _fig_u.add_trace(go.Bar(name="Sesudah", x=_lbls, y=_ua, marker_color="#37B7C3",
        text=[f"{v:.0f}%" for v in _ua], textposition="outside", textfont=dict(size=9)))
    for i, l in enumerate(lines):
        _fig_u.add_shape(type="line", x0=i-0.42, x1=i+0.42, y0=l["lim"], y1=l["lim"],
                         line=dict(color="#c0392b", width=1.5, dash="dash"))
    _fig_u.update_layout(template="plotly_white", barmode="group",
        title=dict(text="Dampak Alokasi — Utilisasi per Lini (%)",
                   font=dict(size=12, color="#071952")),
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF", height=240,
        margin=dict(l=0, r=0, t=34, b=8),
        yaxis=dict(range=[0, max(_ua + _ub + [90]) * 1.22], gridcolor="#EBF4F6"),
        legend=dict(orientation="h", font=dict(size=10), yanchor="bottom", y=1.0,
                    xanchor="right", x=1.0))
    _fig_t = go.Figure()
    _fig_t.add_trace(go.Bar(name="Existing", x=_lbls, y=[l["tons"] for l in lines],
                            marker_color="#8b949e"))
    _fig_t.add_trace(go.Bar(name="Tambahan", x=_lbls,
                            y=[l["after_t"] - l["tons"] for l in lines],
                            marker_color="#088395"))
    _fig_t.update_layout(template="plotly_white", barmode="stack",
        title=dict(text="Dampak Alokasi — Tonase per Lini (ton/bln)",
                   font=dict(size=12, color="#071952")),
        paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF", height=220,
        margin=dict(l=0, r=0, t=34, b=8), yaxis=dict(gridcolor="#EBF4F6"),
        legend=dict(orientation="h", font=dict(size=10), yanchor="bottom", y=1.0,
                    xanchor="right", x=1.0))
    vc1, vc2 = st.columns(2)
    with vc1: st.plotly_chart(_fig_u, use_container_width=True, key="pa_fig_u")
    with vc2: st.plotly_chart(_fig_t, use_container_width=True, key="pa_fig_t")

    # ════════════════════════════════════════════════════════════════════
    # LANGKAH 4 — KEPUTUSAN
    # ════════════════════════════════════════════════════════════════════
    st.markdown('<div class="section-title">4. Keputusan</div>', unsafe_allow_html=True)

    _need_invest = bool(_crit_lines or _unalloc)
    if not _need_invest and not _warn_lines:
        _dec, _dclr = "LAYAK DIALOKASIKAN", "#1a7f4b"
        _dmsg = ("Seluruh produk terserap kapasitas existing dan utilisasi semua "
                 "lini tetap dalam batas.")
    elif not _need_invest:
        _dec, _dclr = "LAYAK DENGAN PEMANTAUAN", "#d29922"
        _wl = ", ".join(f"Line {l['id']} ({l['after_u']:.1f}%)" for l in _warn_lines)
        _dmsg = (f"Produk terserap, namun utilisasi {_wl} tipis melewati batas "
                 f"(masih dalam toleransi {_util_tol:.0f} poin) — pantau ketat.")
    else:
        _dec, _dclr = "PERLU INVESTASI", "#c0392b"
        _reasons = []
        if _unalloc:
            _reasons.append(f"{len(_unalloc)} produk tidak terserap "
                            f"({', '.join(u['sku'] for u in _unalloc)})")
        for l in _crit_lines:
            _reasons.append(f"utilisasi Line {l['id']} {l['after_u']:.1f}% melewati "
                            f"batas+toleransi ({l['lim']:.0f}+{_util_tol:.0f})")
        _dmsg = "Dasar: " + "; ".join(_reasons) + "."
    st.markdown(
        f'<div style="border-left:5px solid {_dclr};background:#F8FDFB;'
        f'border-radius:6px;padding:14px 20px;margin-bottom:12px;">'
        f'<div style="font-size:1.05rem;font-weight:800;color:{_dclr};">{_dec}</div>'
        f'<div style="font-size:.82rem;color:#071952;margin-top:4px;">{_dmsg}</div>'
        f'</div>', unsafe_allow_html=True)

    if not _need_invest:
        return

    # ════════════════════════════════════════════════════════════════════
    # LANGKAH 5 — OPSI INVESTASI & KELAYAKAN FINANSIAL
    # ════════════════════════════════════════════════════════════════════
    st.markdown('<div class="section-title">5. Opsi Investasi</div>',
                unsafe_allow_html=True)
    st.caption("Opsi dibangun dari paket dan katalog mesin pada menu Parameter & "
               "Katalog Investasi.")

    # Beban yang harus ditanggung kapasitas baru:
    #  - seluruh produk tak teralokasi (format tak cocok / tanpa lini kompatibel)
    #  - kelebihan beban lini KRITIS di atas target utilisasi
    _util_target = st.number_input("Target utilisasi lini baru / hasil investasi (%)",
        50.0, 95.0, 80.0, 1.0, key="pa_tgt")
    _need_new = sum(u["ton"] for u in _unalloc)
    _fmt_need = sorted({u["fmt"] for u in _unalloc})
    _offload  = 0.0
    for l in _crit_lines:
        _keep = (_util_target / 100.0) * l["cap"]
        _ovl  = max(l["after_t"] - _keep, 0)
        _offload += _ovl
        _fmt_need = sorted(set(_fmt_need) | set(l["formats"]))
    _total_new_load = _need_new + _offload

    st.markdown(
        f'<div style="font-size:.82rem;color:#071952;background:#F4FBFC;'
        f'border-radius:6px;padding:10px 14px;margin-bottom:8px;">'
        f'Beban untuk kapasitas baru: <b>{_total_new_load:,.0f} ton/bln</b> '
        f'(produk tak terserap {_need_new:,.0f} + pemindahan beban lini kritis '
        f'{_offload:,.0f}) &nbsp;|&nbsp; Format dibutuhkan: '
        f'<b>{"/".join(_fmt_need) if _fmt_need else "-"}</b></div>',
        unsafe_allow_html=True)

    if _total_new_load <= 0:
        st.info("Tidak ada beban tersisa untuk kapasitas baru."); return

    # Kandidat filler kompatibel format
    _fcands = []
    for mk, mm in machines.items():
        if mm.get("role") != "Filling": continue
        _mf = set(mm.get("format_compat", []))
        if _fmt_need and not set(_fmt_need).issubset(_mf): continue
        _cap = float(mm.get("capacity_ton_month", 0) or 0)
        if _cap > 0: _fcands.append((mk, mm, _cap))
    if not _fcands:
        st.warning(f"Tidak ada filler pada katalog yang mendukung format "
                   f"{'/'.join(_fmt_need)}. Tambahkan di Parameter & Katalog Investasi.")
        return

    def _pkg_overhead(ivk):
        return sum(o.get("pct", 0) for o in cat.get("capex_overhead_items", [])
                   if "all" in o.get("applies", ["all"]) or ivk in o.get("applies", []))

    def _pkg_fixed(ivk):
        return sum(o.get("amount", 0) for o in cat.get("capex_fixed_items", [])
                   if "all" in o.get("applies", ["all"]) or ivk in o.get("applies", []))

    def _pkg_opex(ivk):
        return sum(o.get("annual", 0) for o in cat.get("opex_items", [])
                   if "all" in o.get("applies", ["all"]) or ivk in o.get("applies", []))

    # Bangun opsi dari paket bermode new_line / multilane (yang relevan menambah kapasitas)
    _opts = []
    for ivk, iv in iv_pkgs.items():
        _mode = iv.get("mode", "replace")
        if _mode == "replace":   # mengganti unit tidak menambah lini utk produk baru
            continue
        _extra = sum(float(machines.get(c["key"], {}).get("capex", 0)) * c.get("qty", 1)
                     for c in iv.get("components_extra", []))
        _ohp, _fix = 1 + _pkg_overhead(ivk), _pkg_fixed(ivk)
        if _mode == "new_line":
            _best = None
            for mk, mm, cap in sorted(_fcands, key=lambda x: x[2]):
                _pu = _total_new_load / cap * 100
                if _pu <= _util_target + 5:
                    _best = (mk, mm, cap, 1, cap, _pu); break
            if _best is None:
                mk, mm, cap = max(_fcands, key=lambda x: x[2])
                _best = (mk, mm, cap, 1, cap, _total_new_load / cap * 100)
        else:  # multilane
            _best = None
            for mk, mm, cap in sorted(_fcands, key=lambda x: x[2]):
                for n in range(1, int(mm.get("multiline_lanes", 4)) + 1):
                    _eff = cap * n
                    _pu = _total_new_load / _eff * 100
                    if _pu <= _util_target + 5:
                        _c = (mk, mm, cap, n, _eff, _pu)
                        if _best is None or abs(_pu - _util_target) < abs(_best[5] - _util_target):
                            _best = _c
                        break
            if _best is None: continue
        mk, mm, cap, _n, _eff, _pu = _best
        _capex = (mm.get("capex", 0) * _n + _extra) * _ohp + _fix
        _opts.append({"jenis": iv.get("name", ivk), "key": ivk, "mode": _mode,
                      "mk": mk, "mm": mm, "lanes": _n, "eff": _eff, "pu": _pu,
                      "capex": _capex, "layak_cap": _pu <= _util_target + 5})
    if not _opts:
        st.warning("Tidak ada paket investasi bermode lini baru / multijalur pada "
                   "katalog. Atur di Parameter & Katalog Investasi.")
        return
    _opts.sort(key=lambda o: (not o["layak_cap"], o["capex"]))

    st.dataframe(pd.DataFrame([{
        "Jenis Investasi": o["jenis"],
        "Unit Filler": o["mm"].get("full_name", ""),
        "Konfigurasi": f'{o["lanes"]} jalur' if o["lanes"] > 1 else "Jalur tunggal",
        "Kapasitas Efektif (ton/bln)": round(o["eff"], 0),
        "Proyeksi Util (%)": round(o["pu"], 1),
        "Memenuhi Kapasitas": "Ya" if o["layak_cap"] else "Tidak",
        "Estimasi CAPEX": fmt_rp(o["capex"]),
    } for o in _opts]).style.map(
        lambda v: {"Ya": "color:#1a7f4b;font-weight:700",
                   "Tidak": "color:#f85149;font-weight:700"}.get(v, ""),
        subset=["Memenuhi Kapasitas"]).format(precision=1),
        use_container_width=True, hide_index=True)

    _pick = st.selectbox("Jenis investasi untuk analisis finansial:",
                         [o["jenis"] for o in _opts], index=0, key="pa_pick")
    _ch = next(o for o in _opts if o["jenis"] == _pick)

    # ── Kelayakan finansial — parameter penuh dari katalog ───────────────
    _opex_yr = (_pkg_opex(_ch["key"])
                + float(_ch["mm"].get("capex", 0)) * float(_ch["mm"].get("opex_rate", 0.06))
                * _ch["lanes"])
    _rf = float(params.get("realization_factor", 0.75))
    _vol_yr = _total_new_load * 12
    if _ccfg.get("benefit_apply_realization", True):
        _vol_yr *= _rf
    _benefit = _vol_yr * float(params.get("internal_value_per_ton", 2_100_000))
    _p2 = dict(params); _p2["_benefit_override"] = _benefit
    fin = compute_financial(_ch["capex"], _vol_yr, _p2, annual_opex_extra=_opex_yr)

    _N      = int(params.get("project_lifetime_year", 5))
    _irr    = fin.get("irr_pct") or 0
    _roi_yr = fin.get("roi_pct", 0) / _N
    _pb     = fin.get("payback_year")
    _ok_npv = fin["npv"] >= 0
    _ok_irr = _irr / 100 >= float(params.get("minimum_irr", 0.15))
    _ok_pb  = (_pb or 99) <= int(params.get("payback_threshold_year", 3))
    _feas   = _ok_npv and _ok_irr

    kc = st.columns(5)
    for col, lbl, val, ok in [
        (kc[0], "Total CAPEX", fmt_rp(_ch["capex"]), True),
        (kc[1], "NPV", fmt_rp(fin["npv"]), _ok_npv),
        (kc[2], "IRR", f"\u2265200%" if _irr > 200 else f"{_irr:.1f}%", _ok_irr),
        (kc[3], "ROI/Tahun", f"{_roi_yr:.1f}%", _roi_yr >= 10),
        (kc[4], "Payback", f"{_pb:.2f} thn" if _pb else "N/A", _ok_pb),
    ]:
        clr = "#3fb950" if ok else "#f85149"
        with col:
            st.markdown(f'<div class="kpi-box" style="border-left-color:{clr};">'
                        f'<div class="kpi-label">{lbl}</div>'
                        f'<div class="kpi-value" style="color:{clr};font-size:1rem;">'
                        f'{val}</div></div>', unsafe_allow_html=True)

    with st.expander("Transparansi Perhitungan", expanded=False):
        st.caption("Langkah perhitungan dengan nilai aktual. Komposisi formula "
                   "diatur pada Parameter & Katalog Investasi.")
        _rows = []
        for nm, frm, vl in fin.get("calc_steps", []):
            if vl is None: vs = "-"
            elif nm in ("IRR", "ROI per Tahun"): vs = f"{vl*100:.1f}%"
            elif nm == "Payback Period": vs = f"{vl:.2f} thn" if vl else "Tidak tercapai"
            else: vs = fmt_rp(vl)
            _rows.append({"Komponen": nm, "Cara Hitung": frm, "Nilai": vs})
        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

    # ── Konklusi (selaras gaya Evaluasi Kapasitas) ───────────────────────
    _cclr = "#1a7f4b" if _feas else "#c0392b"
    _clbl = "LAYAK" if _feas else "TIDAK LAYAK"
    _cfg_s = f'{_ch["lanes"]} jalur' if _ch["lanes"] > 1 else "Jalur tunggal"
    st.markdown(f"""
    <div class="dss-card" style="border:1px solid {_cclr};border-radius:12px;
         padding:0;margin:1rem 0;overflow:hidden;">
      <div style="display:flex;align-items:center;justify-content:space-between;
           gap:12px;padding:14px 18px;background:#F8FDFB;">
        <div>
          <div class="kpi-label" style="letter-spacing:.12em;">KONKLUSI ALOKASI PRODUKSI</div>
          <div style="font-size:1.0rem;font-weight:800;color:#071952;margin-top:2px;">
            {len(sku_df)} produk \u00b7 {float(sku_df["Tonase (ton/bln)"].sum()):,.0f} ton/bln tambahan</div>
        </div>
        <span style="background:{_cclr};color:#fff;font-weight:700;
              padding:6px 20px;border-radius:8px;font-size:.86rem;">{_clbl}</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 18px;
           border-top:1px solid #E3EEF1;font-size:.8rem;color:#071952;">
        <div style="flex:0 0 22%;font-weight:700;">Lini Baru</div>
        <div style="flex:0 0 26%;">{_ch["jenis"]}</div>
        <div style="flex:1 1 auto;">{_ch["mm"].get("full_name","")} \u00b7 {_cfg_s} \u00b7
          {_ch["eff"]:,.0f} ton/bln</div>
        <div style="flex:0 0 14%;">Util {_ch["pu"]:.1f}%</div>
        <div style="flex:0 0 14%;text-align:right;font-weight:700;color:{_cclr};">
          {fmt_rp(_ch["capex"])}</div>
      </div>
      <div style="padding:8px 18px;border-top:1px solid #E3EEF1;background:#FBFEFE;
           font-size:.78rem;color:#071952;">
        Menanggung {_total_new_load:,.0f} ton/bln (produk baru {_need_new:,.0f} +
        pemindahan beban {_offload:,.0f}) \u00b7 Seluruh harga dan parameter mengikuti
        menu Parameter & Katalog Investasi.</div>
    </div>""", unsafe_allow_html=True)
