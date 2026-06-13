"""
modules/scenario_ranking.py

SATU metode ranking skenario — dipakai oleh DES (Simulasi Kapasitas)
dan Evaluasi Kapasitas, agar urutan selalu konsisten apapun sumber input.

Logika ranking (berurutan):
  1. Total produksi selesai   (desc)  — pemenuhan demand adalah tujuan utama
  2. Unmet demand             (asc)   — sisa demand tak terpenuhi sekecil mungkin
  3. Skor efisiensi           (asc)   — di antara skenario yang sama-sama memenuhi
                                        demand, pilih yang utilisasinya paling dekat
                                        dengan zona operasional sehat. Utilisasi yang
                                        terlalu rendah berarti jadwal boros (jam
                                        operasi panjang untuk output yang sama);
                                        utilisasi melewati batas berarti berisiko.
  4. Skor FIS                 (asc)   — tie-breaker risiko.

Keputusan MAINTAIN/MODIFY (transparan, dapat dijelaskan baris-per-baris):
  MODIFY jika unmet demand > toleransi  ATAU  ada lini yang melewati batas
  utilisasinya (B/G dan D punya batas berbeda karena konfigurasi berbeda).
  Selain itu MAINTAIN.
"""

# ── Konstanta operasional (default; dapat diubah dari sidebar Evaluasi) ──────
UTIL_LIMIT_BG   = 84.0   # batas utilisasi Line B & G (single line SSS+BIB)
UTIL_LIMIT_D    = 88.0   # batas utilisasi Line D (multiline SSS)
UTIL_TARGET     = 80.0   # titik tengah zona operasional sehat (≈75–85%)
UNMET_TOL_PCT   = 0.5    # toleransi unmet demand (% dari target) sebelum MODIFY

PERIOD_DAYS     = 349    # hari kerja efektif per periode simulasi
PERIOD_MONTHS   = PERIOD_DAYS / 30.42   # ≈ 11.47 bulan per periode


def efficiency_score(util_max: float, over_limit: bool,
                     target: float = UTIL_TARGET) -> float:
    """
    Skor efisiensi (lebih kecil = lebih baik).
    Jarak absolut utilisasi maksimum ke zona target,
    dengan penalti besar jika ada lini yang melewati batasnya.
    """
    penalty = 100.0 if over_limit else 0.0
    return abs(float(util_max) - float(target)) + penalty


def is_over_limit(util_b: float, util_g: float, util_d: float,
                  limit_bg: float = UTIL_LIMIT_BG,
                  limit_d: float = UTIL_LIMIT_D) -> bool:
    """Cek apakah ada lini yang melewati batas utilisasinya."""
    return (float(util_b) > limit_bg or
            float(util_g) > limit_bg or
            float(util_d) > limit_d)


def scenario_sort_tuple(tons_finished: float, unmet_ton: float,
                        util_b: float, util_g: float, util_d: float,
                        fis_score: float = 0.0,
                        limit_bg: float = UTIL_LIMIT_BG,
                        limit_d: float = UTIL_LIMIT_D,
                        target: float = UTIL_TARGET) -> tuple:
    """
    Sort key tunggal. Ascending sort pada tuple ini menghasilkan
    ranking yang benar (rank 1 = terbaik).
    """
    util_max = max(float(util_b), float(util_g), float(util_d))
    over     = is_over_limit(util_b, util_g, util_d, limit_bg, limit_d)
    eff      = efficiency_score(util_max, over, target)
    return (
        -round(float(tons_finished), 2),   # produksi terbesar dulu
        round(float(unmet_ton), 2),        # unmet terkecil dulu
        round(eff, 2),                     # paling efisien dulu
        round(float(fis_score), 4),        # risiko terendah dulu
    )


def decide(util_b: float, util_g: float, util_d: float,
           unmet_ratio_pct: float,
           limit_bg: float = UTIL_LIMIT_BG,
           limit_d: float = UTIL_LIMIT_D,
           unmet_tol: float = UNMET_TOL_PCT) -> tuple:
    """
    Keputusan MAINTAIN/MODIFY berbasis aturan transparan.
    Mengembalikan (keputusan, daftar alasan) — alasan dapat
    ditampilkan langsung ke pengguna sebagai justifikasi.
    """
    reasons = []
    if float(unmet_ratio_pct) > unmet_tol:
        reasons.append(f"Unmet demand {float(unmet_ratio_pct):.1f}% melebihi toleransi {unmet_tol:.1f}%")
    if float(util_b) > limit_bg:
        reasons.append(f"Utilisasi Line B {float(util_b):.1f}% melewati batas {limit_bg:.0f}%")
    if float(util_g) > limit_bg:
        reasons.append(f"Utilisasi Line G {float(util_g):.1f}% melewati batas {limit_bg:.0f}%")
    if float(util_d) > limit_d:
        reasons.append(f"Utilisasi Line D {float(util_d):.1f}% melewati batas {limit_d:.0f}%")
    if reasons:
        return "MODIFY", reasons
    return "MAINTAIN", ["Demand terpenuhi dan seluruh lini dalam batas utilisasi"]


def schedule_factor(days: float, hours: float) -> float:
    """Faktor jadwal relatif terhadap operasi penuh 7 hari / 24 jam."""
    try:
        return max(0.0, min(1.0, (float(days) / 7.0) * (float(hours) / 24.0)))
    except Exception:
        return 1.0


def tons_period_to_month(tons_period: float) -> float:
    """Konversi tonase per periode simulasi menjadi rata-rata per bulan."""
    return float(tons_period) / PERIOD_MONTHS if PERIOD_MONTHS > 0 else 0.0
