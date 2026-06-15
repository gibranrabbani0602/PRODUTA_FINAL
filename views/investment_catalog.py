"""
Parameter & Katalog Investasi — sumber kebenaran tunggal seluruh sistem.
Semua master data (kategori, format, tipe lini, alur, paket, CAPEX, OPEX,
parameter) dikelola di sini dan dirujuk oleh menu lain. Tidak ada hardcode.
"""
import json
import streamlit as st
import pandas as pd
from pathlib import Path
from modules.financial_calc import DEFAULT_PARAMS, fmt_rp

CATALOG_PATH = Path("data/machine_catalog.json")
ASSETS_DIR   = Path("assets/machines")


def _load_catalog():
    if CATALOG_PATH.exists():
        try:
            with open(CATALOG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_catalog(cat):
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(cat, f, indent=2, ensure_ascii=True)


def _render_bpd(line_type, flow, by_role, yields, bfd, size="Sedang"):
    """Block Flow Diagram vertikal dengan neraca massa per batch.
    Blok konteks hulu/hilir (di luar lingkup) + tahap filling terperinci.
    Tiap tahap menerapkan rendemen; susut ditampilkan ke kanan, kuantitas
    yang diteruskan tampil di tiap blok. Ukuran dapat disesuaikan."""
    import html as _html

    basis = float(bfd.get("basis_kg", 1000))
    up_lab = bfd.get("upstream_label", "Blending")
    up_note = bfd.get("upstream_note", "")
    dn_lab = bfd.get("downstream_label", "Packing")
    dn_note = bfd.get("downstream_note", "")

    NAVY, TEAL, CYAN, MIST = "#071952", "#088395", "#37B7C3", "#EBF4F6"
    GREY = "#9aa6b2"

    # Lebar tampilan akhir (max-width) mengikuti pilihan ukuran
    MAXW = {"Kecil": 380, "Sedang": 520, "Besar": 680}.get(size, 520)

    # Geometri internal (viewBox) — ramping, garis tipis, aksen halus
    BW, BH = 260, 44
    CH = 40
    VGAP = 26
    LEFT, TOPP = 8, 10
    LOSS_X = LEFT + BW + 16
    width = LOSS_X + 130
    n_proc = len(flow)
    height = TOPP + (CH + VGAP) + n_proc * (BH + VGAP) + CH + 12

    svg = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
           f'style="width:100%;height:auto;font-family:inherit;">']
    svg.append(f'<defs><marker id="av" markerWidth="8" markerHeight="8" refX="3" '
               f'refY="5" orient="auto"><path d="M0,0 L6,0 L3,5 Z" fill="{TEAL}"/>'
               f'</marker><marker id="al" markerWidth="8" markerHeight="8" refX="5" '
               f'refY="3" orient="auto"><path d="M0,0 L5,3 L0,6 Z" fill="{GREY}"/>'
               f'</marker></defs>')

    cx_mid = LEFT + BW / 2
    y = TOPP

    def varrow(y0, y1, label=None):
        svg.append(f'<line x1="{cx_mid}" y1="{y0}" x2="{cx_mid}" y2="{y1-1}" '
                   f'stroke="{TEAL}" stroke-width="1.3" marker-end="url(#av)"/>')
        if label:
            svg.append(f'<text x="{cx_mid+7}" y="{(y0+y1)/2+3}" font-size="8.5" '
                       f'fill="{NAVY}">{label}</text>')

    # ── Blok konteks hulu (di luar lingkup) ──────────────────────────────
    svg.append(f'<rect x="{LEFT}" y="{y}" width="{BW}" height="{CH}" rx="6" '
               f'fill="{MIST}" stroke="{CYAN}" stroke-width="1" '
               f'stroke-dasharray="4 3"/>')
    svg.append(f'<text x="{cx_mid}" y="{y+17}" text-anchor="middle" font-size="11" '
               f'font-weight="700" fill="{NAVY}">{_html.escape(up_lab)}</text>')
    svg.append(f'<text x="{cx_mid}" y="{y+30}" text-anchor="middle" font-size="7.5" '
               f'fill="{GREY}">di luar lingkup &middot; {_html.escape(up_note)}</text>')
    y += CH
    varrow(y, y + VGAP, f"masuk {basis:,.0f} kg/batch")
    y += VGAP

    # ── Tahap filling (dalam lingkup) — neraca massa berjalan ────────────
    flow_kg = basis
    total_loss = 0.0
    for i, stg in enumerate(flow):
        yld = float(yields.get(stg, 99.5)) / 100.0
        out_kg = flow_kg * yld
        loss = flow_kg - out_kg
        total_loss += loss
        svg.append(f'<rect x="{LEFT}" y="{y}" width="{BW}" height="{BH}" rx="6" '
                   f'fill="#FFFFFF" stroke="{TEAL}" stroke-width="1.2"/>')
        # nomor tahap — lingkaran kecil, garis tipis (tidak mencolok)
        svg.append(f'<circle cx="{LEFT+14}" cy="{y+BH/2}" r="8" fill="none" '
                   f'stroke="{TEAL}" stroke-width="1.2"/>'
                   f'<text x="{LEFT+14}" y="{y+BH/2+3}" text-anchor="middle" '
                   f'font-size="9" fill="{TEAL}" font-weight="700">{i+1}</text>')
        svg.append(f'<text x="{LEFT+30}" y="{y+BH/2-2}" font-size="11" '
                   f'font-weight="700" fill="{NAVY}">{_html.escape(stg)}</text>')
        names = by_role.get(stg, [])
        if names:
            t = _html.escape(names[0])
            if len(names) > 1: t += f" +{len(names)-1}"
            if len(t) > 32: t = t[:30] + "…"
            mtxt = t
        else:
            mtxt = "belum ada mesin"
        svg.append(f'<text x="{LEFT+30}" y="{y+BH/2+11}" font-size="7.5" '
                   f'fill="{GREY}">{mtxt}</text>')
        svg.append(f'<text x="{LEFT+BW-8}" y="{y+BH/2+3}" text-anchor="end" '
                   f'font-size="9" font-weight="700" fill="{TEAL}">'
                   f'{out_kg:,.1f} kg</text>')
        if loss > 0.05:
            ly = y + BH / 2
            svg.append(f'<line x1="{LEFT+BW}" y1="{ly}" x2="{LOSS_X-1}" y2="{ly}" '
                       f'stroke="{GREY}" stroke-width="1" stroke-dasharray="2 2" '
                       f'marker-end="url(#al)"/>')
            svg.append(f'<text x="{LOSS_X+3}" y="{ly-1}" font-size="8" fill="{GREY}">'
                       f'susut {loss:,.1f} kg</text>')
            svg.append(f'<text x="{LOSS_X+3}" y="{ly+8}" font-size="6.5" '
                       f'fill="#bcc5cf">rendemen {yld*100:.1f}%</text>')
        flow_kg = out_kg
        y += BH
        varrow(y, y + VGAP)
        y += VGAP

    # ── Blok konteks hilir (di luar lingkup) ─────────────────────────────
    svg.append(f'<rect x="{LEFT}" y="{y}" width="{BW}" height="{CH}" rx="6" '
               f'fill="{MIST}" stroke="{CYAN}" stroke-width="1" '
               f'stroke-dasharray="4 3"/>')
    svg.append(f'<text x="{cx_mid}" y="{y+17}" text-anchor="middle" font-size="11" '
               f'font-weight="700" fill="{NAVY}">{_html.escape(dn_lab)}</text>')
    svg.append(f'<text x="{cx_mid}" y="{y+30}" text-anchor="middle" font-size="7.5" '
               f'fill="{GREY}">di luar lingkup &middot; {_html.escape(dn_note)}</text>')
    svg.append('</svg>')

    # Bungkus dengan max-width agar ukuran terkendali dan menyatu dengan menu
    st.markdown(f'<div style="max-width:{MAXW}px;margin:4px 0;">'
                f'{"".join(svg)}</div>', unsafe_allow_html=True)

    overall = (flow_kg / basis * 100) if basis else 0
    st.markdown(
        f'<div style="max-width:{MAXW}px;background:#F4FBFC;border:1px solid #d5e8ec;'
        f'border-radius:6px;padding:7px 11px;font-size:.8rem;color:#071952;">'
        f'<b>Neraca massa lini {_html.escape(line_type)}</b> (per batch): '
        f'masuk {basis:,.0f} kg &rarr; keluar <b>{flow_kg:,.1f} kg</b> '
        f'&middot; susut {total_loss:,.1f} kg &middot; '
        f'rendemen total <b>{overall:.1f}%</b></div>', unsafe_allow_html=True)
    st.caption("Lingkup analisis: lini filling. Tahap hulu dan hilir adalah "
               "konteks. Rendemen tiap tahap dapat diatur di atas.")


def render():
    st.markdown('<div class="page-title">PARAMETER & KATALOG INVESTASI</div>',
                unsafe_allow_html=True)
    st.caption("Pusat konfigurasi: mesin, paket intervensi, biaya, dan parameter "
               "finansial. Seluruh menu lain merujuk pada pengaturan di sini.")

    cat = _load_catalog()
    if not cat:
        st.error("Katalog tidak ditemukan."); return

    machines   = cat.setdefault("machines", {})
    categories = cat.setdefault("machine_categories",
        ["Discharging","Feeding","Dosing","Filling","Transfer","Folding","Inspeksi","Packing"])
    formats    = cat.setdefault("package_formats", ["SSS","BIB","STICKPACK"])
    line_types = cat.setdefault("line_types", ["Single line","Multiline","Stickpack"])
    iv_pkgs    = cat.setdefault("intervention_packages", {})
    _pkg_opts  = ["all"] + list(iv_pkgs.keys())
    _pkg_lbl   = {"all": "Semua paket", **{k: v.get("name", k) for k, v in iv_pkgs.items()}}

    tab_mesin, tab_paket, tab_capex, tab_opex, tab_param = st.tabs(
        ["Katalog Mesin", "Paket Investasi", "CAPEX", "OPEX", "Parameter Finansial"])

    # ════════════════════════════════════════════════════════════════════════
    # TAB 1 — KATALOG MESIN
    # ════════════════════════════════════════════════════════════════════════
    with tab_mesin:
        st.markdown("**KATALOG MESIN**")
        st.caption("Spesifikasi dan harga referensi komponen mesin.")

        with st.expander("Kelola Kategori & Format"):
            mc1, mc2 = st.columns(2)
            st.caption("Dua daftar ini berdiri sendiri: kategori mengelompokkan "
                       "fungsi mesin, format mendaftarkan jenis kemasan yang dikenal sistem.")
            with mc1:
                st.markdown("**Kategori Mesin**")
                st.caption(", ".join(categories))
                _nc1, _nc2 = st.columns([3,1])
                with _nc1:
                    _new_cat = st.text_input("Kategori baru", key="new_cat",
                                             label_visibility="collapsed",
                                             placeholder="Nama kategori baru")
                with _nc2:
                    if st.button("Tambah", key="add_cat") and _new_cat.strip():
                        if _new_cat.strip() not in categories:
                            categories.append(_new_cat.strip())
                            save_catalog(cat); st.rerun()
            with mc2:
                st.markdown("**Format Kemasan**")
                st.caption(", ".join(formats))
                _nf1, _nf2 = st.columns([3,1])
                with _nf1:
                    _new_fmt = st.text_input("Format baru", key="new_fmt",
                                             label_visibility="collapsed",
                                             placeholder="Nama format baru")
                with _nf2:
                    if st.button("Tambah", key="add_fmt") and _new_fmt.strip():
                        _f = _new_fmt.strip().upper()
                        if _f not in formats:
                            formats.append(_f)
                            save_catalog(cat); st.rerun()

        fc1, fc2 = st.columns([3, 2])
        with fc1:
            _q = st.text_input("Cari mesin", "", key="mesin_search",
                               placeholder="Ketik nama mesin...")
        with fc2:
            sel_role = st.selectbox("Kategori", ["Semua"] + categories, key="mesin_role")

        _filtered = []
        for key, m in machines.items():
            if sel_role != "Semua" and m.get("role") != sel_role: continue
            if _q and _q.lower() not in m.get("full_name","").lower(): continue
            _filtered.append((key, m))
        _filtered.sort(key=lambda x: (x[1].get("role",""), x[1].get("full_name","")))
        st.caption(f"{len(_filtered)} dari {len(machines)} mesin ditampilkan.")

        _to_delete = None
        for i in range(0, len(_filtered), 2):
            cols = st.columns(2)
            for col, (key, m) in zip(cols, _filtered[i:i+2]):
                with col:
                    with st.container(border=True):
                        _img_p = m.get("image","")
                        if _img_p and Path(_img_p).exists():
                            _ic1, _ic2 = st.columns([1, 3])
                            with _ic1: st.image(_img_p, width=72)
                            _info_col = _ic2
                        else:
                            _info_col = st.container()
                        _cap_t = float(m.get("capacity_ton_month",0) or 0)
                        _cap_disp = f"{_cap_t:.0f} ton/bln" if _cap_t > 0 else "&mdash;"
                        with _info_col:
                            st.markdown(
                                f'<div style="font-weight:700;color:#071952;font-size:.92rem;">'
                                f'{m.get("full_name", key)}</div>'
                                f'<div style="font-size:.76rem;color:#8b949e;margin:2px 0 6px 0;">'
                                f'{m.get("role","")} &nbsp;|&nbsp; {_cap_disp} &nbsp;|&nbsp; '
                                f'{"/".join(m.get("format_compat",[])) or "-"}</div>'
                                f'<div style="font-size:.9rem;font-weight:600;color:#088395;">'
                                f'{fmt_rp(m.get("capex",0))}</div>',
                                unsafe_allow_html=True)
                        with st.expander("Edit"):
                            e1, e2 = st.columns(2)
                            with e1:
                                m["full_name"] = st.text_input("Nama", m.get("full_name",""), key=f"mfn_{key}")
                                _role_idx = categories.index(m["role"]) if m.get("role") in categories else 0
                                m["role"] = st.selectbox("Kategori", categories,
                                    index=_role_idx, key=f"mr_{key}")
                                m["format_compat"] = st.multiselect("Format", formats,
                                    default=[f for f in m.get("format_compat",[]) if f in formats],
                                    key=f"mfmt_{key}")
                            with e2:
                                m["capex"] = st.number_input("CAPEX (Rp)", 0, 50_000_000_000,
                                    int(m.get("capex",0)), 10_000_000, format="%d", key=f"mc_{key}")
                                m["opex_per_ton"] = st.number_input("OPEX/Ton (Rp)", 0, 1_000_000,
                                    int(m.get("opex_per_ton",0)), 5_000, format="%d", key=f"mot_{key}")
                                _dft = float(m.get("capacity_ton_month",0)) or round(
                                    float(m.get("capacity_kg_hr",0)) * 730 / 1000, 1)
                                m["capacity_ton_month"] = st.number_input(
                                    "Kapasitas (ton/bln)", 0.0, 2000.0, float(_dft), 5.0,
                                    key=f"mtonm_{key}",
                                    help="Kapasitas produksi per bulan pada operasi penuh.")
                                if m.get("role") == "Filling":
                                    m["multiline_lanes"] = st.number_input(
                                        "Jalur Maks (multijalur)", 1, 12,
                                        int(m.get("multiline_lanes", 4)), 1, key=f"mlanes_{key}")
                            _up = st.file_uploader("Gambar mesin (opsional)",
                                type=["png","jpg","jpeg"], key=f"mimg_{key}")
                            if _up is not None:
                                ASSETS_DIR.mkdir(parents=True, exist_ok=True)
                                _ext  = _up.name.rsplit(".",1)[-1].lower()
                                _path = ASSETS_DIR / f"{key}.{_ext}"
                                _path.write_bytes(_up.read())
                                m["image"] = str(_path)
                                save_catalog(cat); st.rerun()
                            if m.get("image") and Path(m["image"]).exists():
                                if st.button("Hapus Gambar", key=f"delimg_{key}"):
                                    Path(m["image"]).unlink(missing_ok=True)
                                    m["image"] = ""; save_catalog(cat); st.rerun()
                            if st.button("Hapus Mesin", key=f"del_{key}", type="secondary"):
                                _to_delete = key

        if _to_delete:
            del machines[_to_delete]
            save_catalog(cat); st.rerun()

        st.markdown("---")
        with st.expander("Tambah Mesin Baru"):
            na1, na2 = st.columns(2)
            with na1:
                new_full = st.text_input("Nama Mesin", key="nfn")
                new_role = st.selectbox("Kategori", categories, key="nr")
                new_fmt  = st.multiselect("Format", formats, key="nfmt")
            with na2:
                new_capex = st.number_input("CAPEX (Rp)", 0, 50_000_000_000, 500_000_000,
                                            10_000_000, format="%d", key="ncpx")
                new_opex  = st.number_input("OPEX/Ton (Rp)", 0, 1_000_000, 100_000,
                                            5_000, format="%d", key="nopt")
                new_tonm  = st.number_input("Kapasitas (ton/bln)", 0.0, 2000.0,
                                            75.0, 5.0, key="ntonm",
                                            help="Kapasitas produksi per bulan pada operasi penuh.")
            if st.button("Tambah Mesin", type="primary", key="add_machine"):
                if new_full.strip():
                    # ID dibuat otomatis dari nama mesin
                    _slug = "".join(ch if ch.isalnum() else "_"
                                    for ch in new_full.strip().lower()).strip("_")
                    while _slug in machines:
                        _slug += "_1"
                    machines[_slug] = {
                        "name": new_role.upper(), "full_name": new_full.strip(),
                        "role": new_role, "capex": new_capex,
                        "opex_per_ton": new_opex, "opex_rate": 0.06,
                        "capacity_kg_hr": 0.0, "capacity_ton_month": new_tonm,
                        "format_compat": new_fmt, "multiline_lanes": 4,
                        "image": "", "note": "",
                    }
                    save_catalog(cat); st.success("Mesin ditambahkan."); st.rerun()

        if st.button("Simpan Katalog Mesin", type="primary", key="save_mesin"):
            save_catalog(cat); st.success("Katalog mesin disimpan.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB 2 — PAKET INTERVENSI (satu-satunya konsep paket)
    # ════════════════════════════════════════════════════════════════════════
    with tab_paket:
        st.markdown("**PAKET INVESTASI KAPASITAS**")
        st.caption("Jenis investasi yang direkomendasikan sistem pada menu Evaluasi "
                   "Kapasitas. Mode perhitungan menentukan cara sistem menghitung "
                   "kapasitas dan kebutuhan filler.")

        _MODE_OPTS = {
            "replace":  "Penggantian unit (1 filler menanggung seluruh beban lini)",
            "multilane":"Multijalur (kapasitas = jumlah jalur x kapasitas unit)",
            "new_line": "Lini baru (menanggung selisih beban; lini existing tetap)",
        }

        def _pkg_capex_preview(iv):
            _mch = sum(float(machines.get(c["key"],{}).get("capex",0))*c.get("qty",1)
                       for c in iv.get("components_extra",[]))
            _ohp = sum(o["pct"] for o in cat.get("capex_overhead_items",[])
                       if "all" in o.get("applies",["all"]) or _ivk in o.get("applies",[]))
            _fix = sum(o["amount"] for o in cat.get("capex_fixed_items",[])
                       if "all" in o.get("applies",["all"]) or _ivk in o.get("applies",[]))
            return _mch, _ohp, _fix

        _iv_del = None
        _iv_items = list(iv_pkgs.items())
        for i in range(0, len(_iv_items), 2):
            cols = st.columns(2)
            for col, (_ivk, iv) in zip(cols, _iv_items[i:i+2]):
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{iv.get('name', _ivk)}**")
                        iv["name"] = st.text_input("Nama Paket", iv.get("name", _ivk),
                                                   key=f"ivn_{_ivk}")
                        _mode_keys = list(_MODE_OPTS.keys())
                        _mi = _mode_keys.index(iv.get("mode","replace")) if iv.get("mode") in _mode_keys else 0
                        iv["mode"] = st.selectbox("Mode Perhitungan", _mode_keys,
                            index=_mi, key=f"ivm_{_ivk}",
                            format_func=lambda k: _MODE_OPTS[k])
                        iv["applies_to"] = st.multiselect("Berlaku untuk tipe lini",
                            line_types,
                            default=[t for t in iv.get("applies_to", line_types) if t in line_types],
                            key=f"iva_{_ivk}")
                        st.markdown("**Komponen tambahan (di luar filler):**")
                        _comps = iv.get("components_extra", [])
                        _new_comps, _sub = [], 0
                        for ci, comp in enumerate(_comps):
                            c1, c2 = st.columns([3, 1])
                            with c1:
                                _mk_list = list(machines.keys())
                                _cidx = _mk_list.index(comp["key"]) if comp["key"] in _mk_list else 0
                                ck = st.selectbox("Mesin", _mk_list, index=_cidx,
                                    key=f"ivck_{_ivk}_{ci}", label_visibility="collapsed",
                                    format_func=lambda k: machines.get(k,{}).get("full_name",k))
                            with c2:
                                cq = st.number_input("Qty", 0, 20, int(comp.get("qty",1)),
                                    key=f"ivcq_{_ivk}_{ci}", label_visibility="collapsed")
                            if cq > 0:
                                _new_comps.append({"key": ck, "qty": cq})
                                _sub += machines.get(ck,{}).get("capex",0) * cq
                        iv["components_extra"] = _new_comps
                        a1, a2 = st.columns([3, 1])
                        with a1:
                            _am = st.selectbox("Tambah komponen", list(machines.keys()),
                                key=f"ivadd_{_ivk}",
                                format_func=lambda k: machines.get(k,{}).get("full_name",k))
                        with a2:
                            if st.button("Tambah", key=f"ivaddbtn_{_ivk}"):
                                iv.setdefault("components_extra",[]).append({"key": _am, "qty": 1})
                                save_catalog(cat); st.rerun()
                        # Estimasi linked: mesin + overhead + biaya tetap dari master CAPEX
                        _mch, _ohp, _fix = _pkg_capex_preview(iv)
                        st.markdown(
                            f'<div style="font-size:.78rem;color:#071952;background:#F4FBFC;'
                            f'border-radius:6px;padding:8px 12px;margin-top:6px;">'
                            f'Komponen: <b>{fmt_rp(_sub)}</b> &nbsp;|&nbsp; '
                            f'Overhead: <b>{_ohp*100:.0f}%</b> &nbsp;|&nbsp; '
                            f'Biaya tetap: <b>{fmt_rp(_fix)}</b><br>'
                            f'Estimasi paket (tanpa filler): '
                            f'<b>{fmt_rp(_sub*(1+_ohp)+_fix)}</b></div>',
                            unsafe_allow_html=True)
                        if st.button("Hapus Paket", key=f"ivdel_{_ivk}", type="secondary"):
                            _iv_del = _ivk
        if _iv_del:
            del iv_pkgs[_iv_del]; save_catalog(cat); st.rerun()

        with st.expander("Tambah Paket Investasi Baru"):
            np1, np2 = st.columns(2)
            with np1:
                _np_key  = st.text_input("ID paket (unik)", key="np_key")
                _np_name = st.text_input("Nama paket", key="np_name")
            with np2:
                _np_mode = st.selectbox("Mode Perhitungan", list(_MODE_OPTS.keys()),
                    key="np_mode", format_func=lambda k: _MODE_OPTS[k])
                _np_appl = st.multiselect("Berlaku untuk tipe lini", line_types,
                    default=line_types, key="np_appl")
            if st.button("Tambah Paket", type="primary", key="np_add"):
                if _np_key.strip() and _np_name.strip() and _np_key.strip() not in iv_pkgs:
                    iv_pkgs[_np_key.strip()] = {
                        "name": _np_name.strip(), "mode": _np_mode,
                        "applies_to": _np_appl, "components_extra": [],
                    }
                    save_catalog(cat); st.rerun()

        st.markdown("---")
        st.markdown("**ALUR KOMPONEN LINI**")
        st.caption("Urutan kategori komponen penyusun tiap tipe lini — referensi "
                   "kelengkapan paket dan dasar penambahan tipe lini baru.")
        bp = cat.setdefault("line_blueprints", {})
        _bp_cols = st.columns(min(len(line_types), 3))
        for i, lt in enumerate(line_types):
            with _bp_cols[i % len(_bp_cols)]:
                st.markdown(f"**{lt}**")
                bp[lt] = st.multiselect(f"Alur {lt}", categories,
                    default=[c for c in bp.get(lt, categories) if c in categories],
                    key=f"bp_{lt}", label_visibility="collapsed")
        _nl1, _nl2 = st.columns([3, 1])
        with _nl1:
            _new_lt = st.text_input("Tipe lini baru", key="new_lt",
                                    placeholder="Nama tipe lini baru")
        with _nl2:
            if st.button("Tambah Tipe", key="add_lt") and _new_lt.strip():
                if _new_lt.strip() not in line_types:
                    line_types.append(_new_lt.strip())
                    bp[_new_lt.strip()] = list(categories)
                    save_catalog(cat); st.rerun()

        # ── BLOCK FLOW DIAGRAM (NERACA MASSA) ────────────────────────────
        st.markdown("---")
        st.markdown("**DIAGRAM ALIR PROSES (NERACA MASSA)**")
        st.caption("Alur proses lini filling beserta neraca massa per batch. "
                   "Lingkup analisis adalah lini filling; tahap hulu (blending) "
                   "dan hilir (packing) ditampilkan sebagai konteks. Urutan tahap "
                   "mengikuti Alur Komponen di atas.")

        _bfd = cat.setdefault("bfd_config", {})
        _bfd.setdefault("basis_kg", 1000)
        _bfd.setdefault("upstream_label", "Blending")
        _bfd.setdefault("upstream_note", "Bubuk siap isi, per bin maks 1 ton")
        _bfd.setdefault("downstream_label", "Packing")
        _bfd.setdefault("downstream_note", "Pengemasan sekunder")
        _yields = cat.setdefault("stage_yields", {})
        _DEF_Y = {"Discharging":99.7,"Feeding":99.8,"Dosing":99.5,"Filling":99.5,
                  "Transfer":99.9,"Folding":99.8,"Inspeksi":99.5,"Packing":99.7}
        for _c in categories:
            _yields.setdefault(_c, _DEF_Y.get(_c, 99.5))

        _bcol1, _bcol2, _bcol3 = st.columns([1, 1, 1])
        with _bcol1:
            _bpd_lt = st.selectbox("Tipe lini", line_types, key="bpd_lt")
        with _bcol2:
            _bfd["basis_kg"] = st.number_input("Basis input per batch (kg)",
                100, 5000, int(_bfd.get("basis_kg", 1000)), 50, key="bfd_basis",
                help="Kapasitas satu bin / basis satu batch produksi. Default 1 ton.")
        with _bcol3:
            _bpd_size = st.select_slider("Ukuran diagram",
                options=["Kecil", "Sedang", "Besar"], value="Sedang", key="bpd_size")

        with st.expander("Rendemen per tahap & label konteks (editable)"):
            st.caption("Rendemen = persen material yang diteruskan ke tahap "
                       "berikutnya; sisanya adalah susut (debu, tumpahan, reject). "
                       "Nilai awal merupakan estimasi dan dapat dikalibrasi.")
            _yc = st.columns(3)
            for _i, _c in enumerate([c for c in categories]):
                with _yc[_i % 3]:
                    _yields[_c] = st.number_input(f"{_c} (%)", 80.0, 100.0,
                        float(_yields.get(_c, 99.5)), 0.1, key=f"yld_{_c}")
            st.markdown("---")
            _ctx1, _ctx2 = st.columns(2)
            with _ctx1:
                _bfd["upstream_label"] = st.text_input("Label hulu (konteks)",
                    _bfd.get("upstream_label","Blending"), key="bfd_up")
                _bfd["upstream_note"] = st.text_input("Keterangan hulu",
                    _bfd.get("upstream_note",""), key="bfd_upn")
            with _ctx2:
                _bfd["downstream_label"] = st.text_input("Label hilir (konteks)",
                    _bfd.get("downstream_label","Packing"), key="bfd_dn")
                _bfd["downstream_note"] = st.text_input("Keterangan hilir",
                    _bfd.get("downstream_note",""), key="bfd_dnn")

        _flow = [c for c in bp.get(_bpd_lt, []) if c in categories]
        if not _flow:
            st.info("Atur alur komponen untuk tipe lini ini terlebih dahulu.")
        else:
            _by_role = {}
            for _mk, _mm in machines.items():
                _by_role.setdefault(_mm.get("role", ""), []).append(
                    _mm.get("full_name", _mk))
            _render_bpd(_bpd_lt, _flow, _by_role, _yields, _bfd, _bpd_size)


        if st.button("Simpan Paket & Alur", type="primary", key="save_iv"):
            save_catalog(cat); st.success("Paket investasi dan alur lini disimpan.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB 3 — CAPEX
    # ════════════════════════════════════════════════════════════════════════
    with tab_capex:
        st.markdown("**CAPEX**")
        st.caption("Komponen biaya investasi di luar harga mesin. Setiap item dapat "
                   "diterapkan ke semua paket atau paket tertentu.")

        cx1, cx2 = st.columns(2)
        with cx1:
            st.markdown("**Overhead (% dari harga mesin)**")
            _oh_items = cat.setdefault("capex_overhead_items", [])
            _oh_del = None
            for i, it in enumerate(_oh_items):
                with st.container(border=True):
                    st.markdown(f"**{it.get('label','Item')}**")
                    it["label"] = st.text_input("Nama", it.get("label",""), key=f"ohl_{i}")
                    it["pct"] = st.number_input("Persen (%)", 0.0, 50.0,
                        float(it.get("pct",0))*100, 0.5, key=f"ohp_{i}") / 100
                    it["applies"] = st.multiselect("Diterapkan pada", _pkg_opts,
                        default=[a for a in it.get("applies",["all"]) if a in _pkg_opts],
                        key=f"oha_{i}", format_func=lambda k: _pkg_lbl.get(k,k))
                    if st.button("Hapus", key=f"ohd_{i}"): _oh_del = i
            if _oh_del is not None:
                _oh_items.pop(_oh_del); save_catalog(cat); st.rerun()
            if st.button("Tambah Item Overhead", key="oh_add"):
                _oh_items.append({"label":"Item Baru","pct":0.01,"applies":["all"]})
                save_catalog(cat); st.rerun()

        with cx2:
            st.markdown("**Biaya Tetap (Rp per paket)**")
            _fx_items = cat.setdefault("capex_fixed_items", [])
            _fx_del = None
            for i, it in enumerate(_fx_items):
                with st.container(border=True):
                    st.markdown(f"**{it.get('label','Item')}**")
                    it["label"] = st.text_input("Nama", it.get("label",""), key=f"fxl_{i}")
                    it["amount"] = st.number_input("Nilai (Rp)", 0, 5_000_000_000,
                        int(it.get("amount",0)), 5_000_000, format="%d", key=f"fxa_{i}")
                    it["applies"] = st.multiselect("Diterapkan pada", _pkg_opts,
                        default=[a for a in it.get("applies",["all"]) if a in _pkg_opts],
                        key=f"fxap_{i}", format_func=lambda k: _pkg_lbl.get(k,k))
                    if st.button("Hapus", key=f"fxd_{i}"): _fx_del = i
            if _fx_del is not None:
                _fx_items.pop(_fx_del); save_catalog(cat); st.rerun()
            if st.button("Tambah Biaya Tetap", key="fx_add"):
                _fx_items.append({"label":"Item Baru","amount":10_000_000,"applies":["all"]})
                save_catalog(cat); st.rerun()

        _total_oh = sum(it["pct"] for it in cat.get("capex_overhead_items",[])) * 100
        st.info(f"Total overhead (semua item): **{_total_oh:.1f}%** dari harga mesin")
        if st.button("Simpan CAPEX", type="primary", key="save_cx"):
            save_catalog(cat); st.success("CAPEX disimpan.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB 4 — OPEX
    # ════════════════════════════════════════════════════════════════════════
    with tab_opex:
        st.markdown("**OPEX**")
        st.caption("Biaya operasional tahunan. Setiap item dapat diterapkan ke semua "
                   "paket atau paket tertentu.")

        with st.expander("Kalkulator Biaya Tenaga Kerja (UMK)"):
            kc1, kc2, kc3, kc4 = st.columns(4)
            with kc1:
                _umr = st.number_input("UMK/bulan (Rp)", 3_000_000, 15_000_000,
                    int(cat.get("opex_umr_monthly", 5_558_515)), 100_000, format="%d",
                    key="om_umr", help="Referensi: UMK Kabupaten Bekasi 2025 sekitar Rp 5,56 juta")
            with kc2:
                _thr = st.number_input("THR (bulan gaji)", 1, 3, 1, key="om_thr")
            with kc3:
                _bpjs = st.number_input("BPJS (%)", 10, 25, 15, 1, key="om_bpjs")
            with kc4:
                _annual = int(_umr * (12 + _thr) * (1 + _bpjs/100))
                st.metric("Biaya/orang/tahun", fmt_rp(_annual))
            cat["opex_umr_monthly"] = _umr
            st.caption("Gunakan nilai ini saat mengisi item biaya tenaga kerja di bawah.")

        _ox_items = cat.setdefault("opex_items", [])
        _ox_del = None
        for i in range(0, len(_ox_items), 2):
            cols = st.columns(2)
            for col, idx in zip(cols, range(i, min(i+2, len(_ox_items)))):
                it = _ox_items[idx]
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{it.get('label','Item')}**")
                        it["label"] = st.text_input("Nama", it.get("label",""), key=f"oxl_{idx}")
                        it["annual"] = st.number_input("Biaya/Tahun (Rp)", 0, 10_000_000_000,
                            int(it.get("annual",0)), 5_000_000, format="%d", key=f"oxa_{idx}")
                        it["applies"] = st.multiselect("Diterapkan pada", _pkg_opts,
                            default=[a for a in it.get("applies",["all"]) if a in _pkg_opts],
                            key=f"oxap_{idx}", format_func=lambda k: _pkg_lbl.get(k,k))
                        st.caption(f"= {fmt_rp(it['annual']/12)}/bulan")
                        if st.button("Hapus", key=f"oxd_{idx}"): _ox_del = idx
        if _ox_del is not None:
            _ox_items.pop(_ox_del); save_catalog(cat); st.rerun()
        if st.button("Tambah Item OPEX", key="ox_add"):
            _ox_items.append({"label":"Item Baru","annual":50_000_000,"applies":["all"]})
            save_catalog(cat); st.rerun()

        if st.button("Simpan OPEX", type="primary", key="save_ox"):
            save_catalog(cat); st.success("OPEX disimpan.")

    # ════════════════════════════════════════════════════════════════════════
    # TAB 5 — PARAMETER FINANSIAL
    # ════════════════════════════════════════════════════════════════════════
    with tab_param:
        st.markdown("**PARAMETER FINANSIAL**")
        st.caption("Parameter evaluasi kelayakan investasi yang berlaku di seluruh menu.")

        gp = cat.setdefault("global_params", dict(DEFAULT_PARAMS))

        pc1, pc2 = st.columns(2)
        with pc1:
            with st.container(border=True):
                st.markdown("**Asumsi Dasar**")
                gp["discount_rate"] = st.number_input("Discount Rate / WACC (%)", 5.0, 30.0,
                    float(gp.get("discount_rate",0.13))*100, 0.5, key="gp_dr") / 100
                gp["project_lifetime_year"] = st.number_input("Umur Proyek (tahun)", 3, 20,
                    int(gp.get("project_lifetime_year",5)), 1, key="gp_pl")
                gp["useful_life_year"] = st.number_input("Umur Ekonomis Aset (tahun)", 3, 20,
                    int(gp.get("useful_life_year",5)), 1, key="gp_ul",
                    help="Dasar depresiasi straight-line")
                gp["tax_rate"] = st.number_input("Tarif Pajak (%)", 0.0, 40.0,
                    float(gp.get("tax_rate",0.25))*100, 1.0, key="gp_tax") / 100
                gp["inflation_rate"] = st.number_input("Inflasi Tahunan (%)", 0.0, 15.0,
                    float(gp.get("inflation_rate",0.03))*100, 0.5, key="gp_inf") / 100
                gp["maintenance_capex_pct"] = st.number_input(
                    "Maintenance CAPEX (%/thn dari investasi)", 0.0, 5.0,
                    float(gp.get("maintenance_capex_pct",0.008))*100, 0.1, key="gp_mcx") / 100
            with st.container(border=True):
                st.markdown("**Threshold Kelayakan**")
                gp["minimum_npv"] = st.number_input("NPV Minimum (Rp)",
                    -1_000_000_000, 5_000_000_000, int(gp.get("minimum_npv",0)),
                    100_000_000, format="%d", key="gp_npv")
                gp["minimum_irr"] = st.number_input("IRR Minimum (%)", 5.0, 50.0,
                    float(gp.get("minimum_irr",0.15))*100, 0.5, key="gp_irr") / 100
                gp["minimum_roi"] = st.number_input("ROI Minimum (%)", 5.0, 100.0,
                    float(gp.get("minimum_roi",0.25))*100, 1.0, key="gp_roi") / 100
                gp["payback_threshold_year"] = st.number_input("Payback Maksimal (tahun)",
                    1, 10, int(gp.get("payback_threshold_year",3)), 1, key="gp_pb")

        with pc2:
            with st.container(border=True):
                st.markdown("**Harga Pokok Produksi (HPP) per Ton**")
                st.caption("Metode full costing: bahan baku langsung + tenaga kerja "
                           "langsung + overhead pabrik. Nilai awal mengikuti acuan "
                           "harga komoditas dan upah publik, dan dapat dikalibrasi.")
                _hpp = gp.setdefault("hpp", {})
                _hpp["bahan_baku"] = st.number_input(
                    "Bahan Baku Langsung per Ton (Rp)",
                    0, 200_000_000, int(_hpp.get("bahan_baku", 58_000_000)),
                    1_000_000, format="%d", key="hpp_bb",
                    help="Acuan: harga rata-rata susu bubuk pasar global "
                         "(Whole Milk Powder USD 3.400–4.675/ton, GlobalDairyTrade/USDA 2025) "
                         "dikalikan kurs. Sesuaikan dengan data internal bila tersedia.")
                _hpp["tkl"] = st.number_input(
                    "Tenaga Kerja Langsung per Ton (Rp)",
                    0, 50_000_000, int(_hpp.get("tkl", 8_500_000)),
                    500_000, format="%d", key="hpp_tkl",
                    help="Acuan: jumlah operator dikalikan upah minimum regional "
                         "Bekasi per satuan output.")
                _hpp["overhead"] = st.number_input(
                    "Overhead Pabrik per Ton (Rp)",
                    0, 80_000_000, int(_hpp.get("overhead", 18_800_000)),
                    500_000, format="%d", key="hpp_ovh",
                    help="Energi, uap, perawatan, depresiasi, dan pengendalian mutu.")
                _hpp_total = (_hpp["bahan_baku"] + _hpp["tkl"] + _hpp["overhead"])
                _hpp["total"] = _hpp_total
                _bb_pct = (_hpp["bahan_baku"] / _hpp_total * 100) if _hpp_total else 0
                st.markdown(
                    f'<div style="background:#EBF4F6;border-radius:6px;padding:8px 12px;'
                    f'margin-top:4px;font-size:.84rem;color:#071952;">'
                    f'<b>HPP Total: {fmt_rp(_hpp_total)}/ton</b><br>'
                    f'<span style="font-size:.76rem;color:#088395;">Komposisi: '
                    f'bahan baku {_bb_pct:.0f}% &middot; '
                    f'tenaga kerja {(_hpp["tkl"]/_hpp_total*100) if _hpp_total else 0:.0f}% '
                    f'&middot; overhead {(_hpp["overhead"]/_hpp_total*100) if _hpp_total else 0:.0f}%'
                    f'</span></div>', unsafe_allow_html=True)
                st.caption("Nilai awal merupakan estimasi berbasis acuan publik dan "
                           "dapat disesuaikan ke data perusahaan.")

            with st.container(border=True):
                st.markdown("**Nilai Manfaat**")
                st.caption("Margin kontribusi per ton kapasitas tambahan — selisih "
                           "antara nilai jual dan biaya produksi (HPP). Menjadi dasar "
                           "penilaian manfaat seluruh jenis investasi.")
                gp["internal_value_per_ton"] = st.number_input(
                    "Nilai Manfaat per Ton (Rp)",
                    500_000, 10_000_000, int(gp.get("internal_value_per_ton",2_100_000)),
                    100_000, format="%d", key="gp_ivt",
                    help="Margin kontribusi per ton kapasitas tambahan yang terserap.")
                if _hpp_total:
                    _mc_pct = gp["internal_value_per_ton"] / _hpp_total * 100
                    st.caption(f"Setara {_mc_pct:.1f}% dari HPP — margin konservatif, "
                               f"mencerminkan kontribusi per ton secara hati-hati.")
                gp["realization_factor"] = st.number_input("Faktor Realisasi", 0.50, 1.00,
                    float(gp.get("realization_factor",0.75)), 0.05, key="gp_rf",
                    help="Porsi kapasitas tambahan yang diasumsikan terserap demand.")

            with st.container(border=True):
                st.markdown("**Konfigurasi Perhitungan Kelayakan**")
                st.caption("Tentukan komponen yang disertakan dalam perhitungan "
                           "NPV/IRR/ROI/Payback. Rincian langkah perhitungan dapat "
                           "dilihat pada menu Evaluasi Kapasitas.")
                _cc = cat.setdefault("calc_config", {})
                _cc["include_tax"] = st.toggle("Sertakan pajak & depresiasi (tax shield)",
                    _cc.get("include_tax", True), key="cc_tax")
                _cc["include_inflation"] = st.toggle("Sertakan eskalasi inflasi biaya",
                    _cc.get("include_inflation", True), key="cc_inf")
                _cc["include_maint_capex"] = st.toggle("Sertakan maintenance CAPEX tahunan",
                    _cc.get("include_maint_capex", True), key="cc_mcx")
                _cc["payback_discounted"] = st.toggle("Payback terdiskonto (discounted payback)",
                    _cc.get("payback_discounted", False), key="cc_pbd",
                    help="Nonaktif = payback sederhana (standar model referensi internal)")
                st.markdown("<div style='margin-top:6px;font-size:.78rem;font-weight:700;"
                            "color:#071952;'>Komposisi Volume Bernilai (dasar manfaat):</div>",
                            unsafe_allow_html=True)
                _cc["benefit_include_unmet"] = st.toggle(
                    "Sertakan Unmet Demand Tahunan",
                    _cc.get("benefit_include_unmet", True), key="cc_bun")
                _cc["benefit_include_headroom"] = st.toggle(
                    "Sertakan Headroom Kapasitas",
                    _cc.get("benefit_include_headroom", True), key="cc_bhd")
                _cc["benefit_apply_realization"] = st.toggle(
                    "Terapkan Faktor Realisasi pada Headroom",
                    _cc.get("benefit_apply_realization", True), key="cc_brf")
                st.markdown("<div style='margin-top:6px;font-size:.78rem;font-weight:700;"
                            "color:#071952;'>Rendemen proses pada kapasitas:</div>",
                            unsafe_allow_html=True)
                # Rendemen efektif diturunkan dari neraca massa BFD (rata-rata
                # rendemen tahap pada alur lini referensi)
                _ref_lt = line_types[0] if line_types else None
                _ref_flow = [c for c in cat.get("line_blueprints", {}).get(_ref_lt, [])
                             if c in categories] if _ref_lt else []
                _calc_y = 100.0
                for _s in _ref_flow:
                    _calc_y *= float(cat.get("stage_yields", {}).get(_s, 99.5)) / 100.0
                gp["effective_yield_pct"] = st.number_input(
                    "Rendemen Proses Efektif (%)", 80.0, 100.0,
                    float(gp.get("effective_yield_pct", round(_calc_y, 1))), 0.1,
                    key="gp_eyld",
                    help="Persen produk jadi terhadap material yang diproses, sesuai "
                         "neraca massa lini filling. Dipakai bila opsi di bawah aktif.")
                _cc["apply_yield_to_capacity"] = st.toggle(
                    "Perhitungkan rendemen pada kapasitas efektif",
                    _cc.get("apply_yield_to_capacity", False), key="cc_ayc",
                    help="Aktif: kapasitas dihitung sebagai kapasitas nominal \u00d7 "
                         "rendemen (basis produk jadi), pada menu Evaluasi Kapasitas "
                         "dan Alokasi Produksi. Nonaktif: kapasitas nominal (default).")
                if _ref_flow:
                    st.caption(f"Rendemen acuan dari neraca massa lini {_ref_lt}: "
                               f"{_calc_y:.1f}%. Dapat ditimpa manual di atas.")

            with st.container(border=True):
                st.markdown("**Parameter Tambahan**")
                st.caption("Parameter baru dengan peran yang jelas dalam perhitungan.")
                _USAGE = {
                    "doc":        "Dokumentasi / sensitivitas (tidak masuk perhitungan)",
                    "opex_add":   "Penambah OPEX tahunan (Rp/tahun)",
                    "capex_add":  "Penambah CAPEX (Rp, satu kali)",
                    "benefit_cut":"Pengurang benefit tahunan (Rp/tahun)",
                }
                _UNIT = {"rp": "Rupiah (Rp)", "pct": "Persentase (%)",
                         "year": "Tahun", "factor": "Faktor/Rasio"}
                _cust = cat.setdefault("custom_params", {})
                _cdel = None
                for ck, cv in list(_cust.items()):
                    if not isinstance(cv, dict): cv = {"value": float(cv), "usage": "doc", "unit": "factor"}
                    with st.container(border=True):
                        st.markdown(f"**{ck}**")
                        _un = list(_UNIT.keys())
                        cv["unit"] = st.selectbox("Satuan", _un,
                            index=_un.index(cv.get("unit","factor")) if cv.get("unit") in _un else 3,
                            key=f"cpun_{ck}", format_func=lambda k: _UNIT[k])
                        if cv["unit"] == "rp":
                            cv["value"] = float(st.number_input("Nilai (Rp)", 0, 100_000_000_000,
                                int(cv.get("value",0)), 1_000_000, format="%d", key=f"cpv_{ck}"))
                        elif cv["unit"] == "pct":
                            cv["value"] = st.number_input("Nilai (%)", 0.0, 100.0,
                                float(cv.get("value",0)), 0.5, key=f"cpv_{ck}")
                        elif cv["unit"] == "year":
                            cv["value"] = float(st.number_input("Nilai (tahun)", 0, 50,
                                int(cv.get("value",0)), 1, key=f"cpv_{ck}"))
                        else:
                            cv["value"] = st.number_input("Nilai", value=float(cv.get("value",0)),
                                key=f"cpv_{ck}", format="%.4f")
                        _uk = list(_USAGE.keys())
                        cv["usage"] = st.selectbox("Peran dalam perhitungan", _uk,
                            index=_uk.index(cv.get("usage","doc")) if cv.get("usage") in _uk else 0,
                            key=f"cpu_{ck}", format_func=lambda k: _USAGE[k])
                        _cust[ck] = cv
                        if st.button("Hapus", key=f"cpd_{ck}"): _cdel = ck
                if _cdel: del _cust[_cdel]; save_catalog(cat); st.rerun()
                _ncn, _ncb = st.columns([3, 1])
                with _ncn:
                    _ncp = st.text_input("Nama parameter baru", key="ncp_name",
                                         label_visibility="collapsed",
                                         placeholder="Nama parameter baru")
                with _ncb:
                    if st.button("Tambah", key="ncp_add") and _ncp.strip():
                        _cust[_ncp.strip()] = {"value": 0.0, "usage": "doc"}
                        save_catalog(cat); st.rerun()

        # ── TRANSPARANSI FORMULA ─────────────────────────────────────────
        # Formula ditulis dengan NAMA parameter pada halaman ini — setiap
        # parameter dapat diubah pada kartu di atas; komponen yang disertakan
        # diatur pada Konfigurasi Perhitungan Kelayakan.
        st.markdown("---")
        st.markdown("**TRANSPARANSI FORMULA PERHITUNGAN**")
        st.caption("Cara sistem menghitung setiap metrik kelayakan, ditulis dengan "
                   "parameter pada halaman ini. Mengubah parameter di atas otomatis "
                   "mengubah hasil perhitungan di seluruh sistem.")
        _ccv = cat.get("calc_config", {})
        _on  = lambda f: "" if _ccv.get(f, True) else "  — [nonaktif]"
        _wacc_s = f"{float(gp.get('discount_rate',0.13))*100:.0f}%"
        _tax_s  = f"{float(gp.get('tax_rate',0.25))*100:.0f}%"
        _ul_s   = f"{int(gp.get('useful_life_year',5))} thn"
        _n_s    = f"{int(gp.get('project_lifetime_year',5))} thn"
        _mcx_s  = f"{float(gp.get('maintenance_capex_pct',0.008))*100:.1f}%"
        _pbm    = ("akumulasi FCF terdiskonto mencapai Total CAPEX"
                   if _ccv.get("payback_discounted", False)
                   else "akumulasi FCF mencapai Total CAPEX")
        _vb_terms = []
        if _ccv.get("benefit_include_unmet", True):
            _vb_terms.append("Unmet Demand Tahunan")
        if _ccv.get("benefit_include_headroom", True):
            _vb_terms.append("Headroom Kapasitas × Faktor Realisasi"
                             if _ccv.get("benefit_apply_realization", True)
                             else "Headroom Kapasitas")
        _vb_formula = " + ".join(_vb_terms) if _vb_terms else "— [seluruh komponen nonaktif]"
        _frm_rows = [
            ("Volume Bernilai", _vb_formula),
            ("HPP per Ton", "Bahan Baku Langsung + Tenaga Kerja Langsung + Overhead Pabrik"),
            ("Nilai Manfaat per Ton", "Margin kontribusi di atas HPP (nilai jual − biaya produksi)"),
            ("Manfaat Tahunan", "Volume Bernilai × Nilai Manfaat per Ton"),
            ("OPEX Tahunan", "Jumlah seluruh item OPEX yang berlaku pada paket investasi"),
            ("Depresiasi", f"Total CAPEX ÷ Umur Ekonomis Aset ({_ul_s})" + _on("include_tax")),
            ("Pajak Kas", f"Laba Operasional (bila positif) × Tarif Pajak ({_tax_s})" + _on("include_tax")),
            ("Maintenance CAPEX", f"Maintenance CAPEX ({_mcx_s}) × Total CAPEX" + _on("include_maint_capex")),
            ("Eskalasi Biaya", "Biaya meningkat sebesar Inflasi Tahunan tiap tahun" + _on("include_inflation")),
            ("Arus Kas Bersih (FCF)", "Manfaat Tahunan − OPEX Tahunan − Pajak Kas − Maintenance CAPEX"),
            ("NPV", f"Σ FCF tahun-t ÷ (1 + WACC {_wacc_s})^t − Total CAPEX, selama Umur Proyek ({_n_s})"),
            ("IRR", "Tingkat diskonto yang menjadikan NPV = 0 — kriteria: ≥ IRR Minimum"),
            ("ROI per Tahun", "Arus Kas Bersih Tahunan ÷ Total CAPEX — kriteria: ≥ ROI Minimum"),
            ("Payback Period", f"Tahun saat {_pbm} — kriteria: ≤ Payback Maksimal"),
        ]
        st.dataframe(pd.DataFrame(_frm_rows, columns=["Metrik", "Formula"]),
                     use_container_width=True, hide_index=True)
        st.caption("Rincian nilai aktual tiap langkah untuk skenario tertentu dapat "
                   "dilihat pada Transparansi Perhitungan di menu Evaluasi Kapasitas.")

        if st.button("Simpan Parameter", type="primary", key="save_gp"):
            cat["global_params"] = gp
            save_catalog(cat); st.success("Parameter finansial disimpan.")
