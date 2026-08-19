import io, os, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

# Strategy #9 — High-Turnover Cost-Aware Spot/Perp Basis Convergence
# Signal-directed one-sided passive SPOT execution + immediate aggressive USD-M perp hedge.
# This is NOT market making: no continuous/two-sided quotes, and no order exists without a signal.
#
# #8's signal family is held fixed. #9 tests whether execution-cost compression can make the
# same economic mechanism executable on basis-unseen blocks. No threshold/side/holding-period
# retuning is allowed after outcomes.
BLOCKS = {
    'BLOCK_A_2020_JAN_MAY': ('2020-01-01', '2020-06-01'),
    'BLOCK_B_2023_JUN_NOV': ('2023-06-01', '2023-12-01'),
}
SYMS = [
    'BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','LTCUSDT','BCHUSDT',
    'LINKUSDT','TRXUSDT','ETCUSDT','XLMUSDT','EOSUSDT','ATOMUSDT'
]
OUT = 'basis_passive_exec_output'
os.makedirs(OUT, exist_ok=True)

# Frozen #8 alpha rule.
DEV_BP = 15.0
Z_MIN = 3.0
EXPAND15_BP = 5.0
VOL_MULT = 0.5
HOLD_MS = 30 * 60 * 1000
DEOVERLAP_BARS = 6

# Frozen #9 execution rule.
ENTRY_OFFSET_BP = 1.0       # passive spot BUY 1bp below signal close
EXIT_OFFSET_BP = 1.0        # passive spot SELL 1bp above first observable spot trade at target
PENETRATION_BP = 0.1        # require actual trade-through; avoids queue-at-touch optimism
ENTRY_TIMEOUT_MS = 5000
EXIT_TIMEOUT_MS = 5000
HEDGE_MAX_DELAY_MS = 1000
LATENCIES_MS = [100, 250]

# Fees / execution stress. Perp taker is user's known 5bp/side => 10bp round trip.
PERP_TAKER_RT_BP = 10.0
SPOT_MAKER_STRESS_BP_PER_SIDE = 2.5   # unknown actual maker fee; conservative parameter
SPOT_TAKER_BP_PER_SIDE = 5.0
AGGRESSIVE_SLIPPAGE_STRESS_BP_PER_LEG = 1.0

# Frozen PASS gate, applied independently to BOTH blocks.
MIN_DATA_COVERAGE = 0.90
MIN_COMPLETED = 200
MIN_SYMBOLS = 6
MIN_COMPLETED_PER_DAY = 1.5
MIN_ENTRY_FILL_RATE = 0.15
MIN_CONSERVATIVE_MEAN_BP = 2.0
MAX_FORCED_EXIT_RATE = 0.25
MIN_POSITIVE_MONTH_FRAC = 0.60
MAX_TOP_SYMBOL_SHARE = 0.30

UA = {'User-Agent': 'Mozilla/5.0'}


def months(a, b):
    x = pd.Timestamp(a).to_period('M')
    z = (pd.Timestamp(b) - pd.Timedelta(days=1)).to_period('M')
    out = []
    while x <= z:
        out.append(str(x)); x += 1
    return out


def getzip(url):
    for k in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            if r.status_code == 200:
                return r.content
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(0.35 * (k + 1))
    return None


def read_kline(blob):
    if blob is None:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            d = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if d.shape[1] < 8:
            return None
        ts = pd.to_numeric(d.iloc[:, 0], errors='coerce')
        ts = np.where(ts > 1e14, ts / 1000.0, ts)  # normalize spot microseconds if ever encountered
        out = pd.DataFrame({
            'ts': pd.Series(ts).round().astype('Int64'),
            'close': pd.to_numeric(d.iloc[:, 4], errors='coerce'),
            'qv': pd.to_numeric(d.iloc[:, 7], errors='coerce'),
        }).dropna()
        out['ts'] = out.ts.astype('int64')
        return out.drop_duplicates('ts').sort_values('ts')
    except Exception:
        return None


def read_agg(blob):
    if blob is None:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            d = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if d.shape[1] < 7:
            return None
        price = pd.to_numeric(d.iloc[:, 1], errors='coerce')
        ts = pd.to_numeric(d.iloc[:, 5], errors='coerce')
        ts = np.where(ts > 1e14, ts / 1000.0, ts)
        maker = d.iloc[:, 6].astype(str).str.lower().isin(['true', '1'])
        out = pd.DataFrame({
            'ts': pd.Series(ts).round().astype('Int64'),
            'price': price,
            'buyer_maker': maker,
        }).dropna()
        out['ts'] = out.ts.astype('int64')
        return out.drop_duplicates(['ts','price','buyer_maker']).sort_values('ts').reset_index(drop=True)
    except Exception:
        return None


def monthly_urls(sym, ym):
    return (
        f'https://data.binance.vision/data/spot/monthly/klines/{sym}/5m/{sym}-5m-{ym}.zip',
        f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{ym}.zip',
    )


def daily_agg_url(sym, day, market):
    if market == 'spot':
        return f'https://data.binance.vision/data/spot/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'
    return f'https://data.binance.vision/data/futures/um/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip'


def load_klines(sym, a, b):
    ss, ff, missing = [], [], []
    for ym in months(a, b):
        su, fu = monthly_urls(sym, ym)
        sb, fb = getzip(su), getzip(fu)
        if sb is None or fb is None:
            missing.append(ym); continue
        s, f = read_kline(sb), read_kline(fb)
        if s is not None and f is not None:
            ss.append(s); ff.append(f)
    if not ss or not ff:
        return None, missing
    s = pd.concat(ss).drop_duplicates('ts').sort_values('ts').rename(columns={'close':'spot','qv':'spot_qv'})
    f = pd.concat(ff).drop_duplicates('ts').sort_values('ts').rename(columns={'close':'perp','qv':'perp_qv'})
    d = s.merge(f, on='ts', how='inner')
    lo = int(pd.Timestamp(a, tz='UTC').timestamp() * 1000)
    hi = int(pd.Timestamp(b, tz='UTC').timestamp() * 1000)
    d = d[(d.ts >= lo) & (d.ts < hi)].sort_values('ts').reset_index(drop=True)
    return d, missing


def make_signals(d):
    if d is None or len(d) < 300:
        return pd.DataFrame()
    x = d.copy()
    x['basis_bp'] = (x.perp / x.spot - 1.0) * 1e4
    med = x.basis_bp.rolling(288, min_periods=144).median().shift(1)
    sd = x.basis_bp.rolling(288, min_periods=144).std(ddof=0).shift(1)
    x['dev_bp'] = x.basis_bp - med
    x['z'] = x.dev_bp / (sd + 1e-9)
    x['expand15_bp'] = x.basis_bp - x.basis_bp.shift(3)
    sm = x.spot_qv.rolling(288, min_periods=144).median().shift(1)
    fm = x.perp_qv.rolling(288, min_periods=144).median().shift(1)
    sig = (
        (x.dev_bp >= DEV_BP) & (x.z >= Z_MIN) & (x.expand15_bp >= EXPAND15_BP) &
        (x.spot_qv >= VOL_MULT * sm) & (x.perp_qv >= VOL_MULT * fm)
    )
    idx = np.where(sig)[0]
    keep, last = [], -999999
    for i in idx:
        if i - last >= DEOVERLAP_BARS:
            keep.append(i); last = i
    if not keep:
        return pd.DataFrame()
    e = x.loc[keep, ['ts','spot','perp','basis_bp','dev_bp','z']].copy()
    e['signal_close_ts'] = e.ts.astype('int64') + 5 * 60 * 1000
    e['signal_day'] = pd.to_datetime(e.signal_close_ts, unit='ms', utc=True).dt.strftime('%Y-%m-%d')
    return e.reset_index(drop=True)


def load_day_pair(sym, day):
    day2 = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    jobs = [('spot', day), ('spot', day2), ('fut', day), ('fut', day2)]
    got = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs = {ex.submit(getzip, daily_agg_url(sym, dd, m)): (m, dd) for m, dd in jobs}
        for f in as_completed(fs):
            m, dd = fs[f]
            got[(m, dd)] = read_agg(f.result())
    # Current day is mandatory for both markets. Next day may be absent if not needed.
    if got.get(('spot', day)) is None or got.get(('fut', day)) is None:
        return None, None, False
    sp = [got[('spot', day)]]
    fu = [got[('fut', day)]]
    if got.get(('spot', day2)) is not None: sp.append(got[('spot', day2)])
    if got.get(('fut', day2)) is not None: fu.append(got[('fut', day2)])
    spot = pd.concat(sp, ignore_index=True).sort_values('ts').drop_duplicates(['ts','price','buyer_maker']).reset_index(drop=True)
    fut = pd.concat(fu, ignore_index=True).sort_values('ts').drop_duplicates(['ts','price','buyer_maker']).reset_index(drop=True)
    return spot, fut, True


def first_index_at_or_after(ts_arr, t):
    i = int(np.searchsorted(ts_arr, t, side='left'))
    return i if i < len(ts_arr) else None


def execute_one(ev, spot, fut, latency_ms):
    sts = spot.ts.to_numpy(dtype=np.int64); spr = spot.price.to_numpy(float); smk = spot.buyer_maker.to_numpy(bool)
    fts = fut.ts.to_numpy(dtype=np.int64); fpr = fut.price.to_numpy(float)
    post = int(ev.signal_close_ts) + latency_ms
    entry_limit = float(ev.spot) * (1.0 - ENTRY_OFFSET_BP / 1e4)
    entry_pen = entry_limit * (1.0 - PENETRATION_BP / 1e4)
    lo = first_index_at_or_after(sts, post)
    if lo is None:
        return {'entry_fill': False}
    hi = int(np.searchsorted(sts, post + ENTRY_TIMEOUT_MS, side='right'))
    cand = np.where(smk[lo:hi] & (spr[lo:hi] <= entry_pen))[0]
    if not len(cand):
        return {'entry_fill': False}
    si = lo + int(cand[0])
    spot_entry_ts = int(sts[si]); spot_entry = entry_limit

    fi = first_index_at_or_after(fts, spot_entry_ts)
    if fi is None or int(fts[fi]) > spot_entry_ts + HEDGE_MAX_DELAY_MS:
        return {'entry_fill': True, 'completed': False}
    perp_entry_ts = int(fts[fi]); perp_entry = float(fpr[fi])

    target = perp_entry_ts + HOLD_MS
    ai = first_index_at_or_after(sts, target)
    if ai is None or int(sts[ai]) > target + HEDGE_MAX_DELAY_MS:
        return {'entry_fill': True, 'completed': False}
    anchor_ts, anchor_px = int(sts[ai]), float(spr[ai])
    exit_post = anchor_ts + 1
    exit_limit = anchor_px * (1.0 + EXIT_OFFSET_BP / 1e4)
    exit_pen = exit_limit * (1.0 + PENETRATION_BP / 1e4)
    elo = first_index_at_or_after(sts, exit_post)
    if elo is None:
        return {'entry_fill': True, 'completed': False}
    ehi = int(np.searchsorted(sts, exit_post + EXIT_TIMEOUT_MS, side='right'))
    # Passive SELL is hit by aggressive BUY => buyer_maker == False.
    ecand = np.where((~smk[elo:ehi]) & (spr[elo:ehi] >= exit_pen))[0]
    forced = False
    if len(ecand):
        ei = elo + int(ecand[0]); spot_exit_ts = int(sts[ei]); spot_exit = exit_limit
    else:
        forced = True
        force_t = exit_post + EXIT_TIMEOUT_MS
        ei = first_index_at_or_after(sts, force_t)
        if ei is None or int(sts[ei]) > force_t + HEDGE_MAX_DELAY_MS:
            return {'entry_fill': True, 'completed': False}
        spot_exit_ts = int(sts[ei]); spot_exit = float(spr[ei])

    fj = first_index_at_or_after(fts, spot_exit_ts)
    if fj is None or int(fts[fj]) > spot_exit_ts + HEDGE_MAX_DELAY_MS:
        return {'entry_fill': True, 'completed': False}
    perp_exit = float(fpr[fj]); perp_exit_ts = int(fts[fj])

    gross = (np.log(spot_exit / spot_entry) - np.log(perp_exit / perp_entry)) * 1e4
    aggressive_legs = 2 + (1 if forced else 0)
    spot_fee_opt = SPOT_TAKER_BP_PER_SIDE if forced else 0.0
    spot_fee_cons = SPOT_MAKER_STRESS_BP_PER_SIDE + (SPOT_TAKER_BP_PER_SIDE if forced else SPOT_MAKER_STRESS_BP_PER_SIDE)
    net_opt = gross - PERP_TAKER_RT_BP - spot_fee_opt
    net_cons = gross - PERP_TAKER_RT_BP - spot_fee_cons - aggressive_legs * AGGRESSIVE_SLIPPAGE_STRESS_BP_PER_LEG
    # Harder stress: spot maker charged 5bp/side and 2bp/each aggressive leg.
    spot_fee_hard = SPOT_TAKER_BP_PER_SIDE + SPOT_TAKER_BP_PER_SIDE
    net_hard = gross - PERP_TAKER_RT_BP - spot_fee_hard - aggressive_legs * 2.0
    return {
        'entry_fill': True, 'completed': True, 'forced_exit': forced,
        'spot_entry_ts': spot_entry_ts, 'perp_entry_ts': perp_entry_ts,
        'spot_exit_ts': spot_exit_ts, 'perp_exit_ts': perp_exit_ts,
        'spot_entry': spot_entry, 'perp_entry': perp_entry, 'spot_exit': spot_exit, 'perp_exit': perp_exit,
        'gross_bp': gross, 'net_opt_bp': net_opt, 'net_cons_bp': net_cons, 'net_hard_bp': net_hard,
    }


def trimmed_mean(x):
    x = np.sort(np.asarray(x, float))
    if len(x) < 20:
        return np.nan
    cut = max(1, int(np.ceil(len(x) * .05)))
    return float(np.mean(x[:-cut])) if len(x) > cut else np.nan


def process_symbol(block, sym, a, b):
    d, miss_months = load_klines(sym, a, b)
    sig = make_signals(d)
    base = {'symbol': sym, 'signals': len(sig), 'kline_missing_months': len(miss_months)}
    if sig.empty:
        return base, [], [], []
    out100, out250, missing_days = [], [], []
    for day, g in sig.groupby('signal_day', sort=True):
        spot, fut, ok = load_day_pair(sym, day)
        if not ok:
            missing_days.append(day)
            continue
        for _, ev in g.iterrows():
            for latency, bucket in [(100, out100), (250, out250)]:
                r = execute_one(ev, spot, fut, latency)
                rec = {
                    'block': block, 'symbol': sym, 'signal_ts': int(ev.ts),
                    'signal_close_ts': int(ev.signal_close_ts), 'signal_day': day,
                    'basis_bp': float(ev.basis_bp), 'dev_bp': float(ev.dev_bp), 'z': float(ev.z),
                    'latency_ms': latency,
                }
                rec.update(r); bucket.append(rec)
        print(block, sym, day, 'signals', len(g), 'loaded', flush=True)
    return base, out100, out250, missing_days


def summarize(block, raw, bases, a, b):
    total_signals = int(sum(x['signals'] for x in bases))
    calendar_days = max(1, (pd.Timestamp(b) - pd.Timestamp(a)).days)
    rows = []
    for latency in LATENCIES_MS:
        df = pd.DataFrame([x for x in raw if x.get('latency_ms') == latency])
        if df.empty:
            rows.append({'block':block,'latency_ms':latency,'signals':total_signals,'data_valid':0,'data_coverage':0,'entry_fills':0,'entry_fill_rate':np.nan,'completed':0,'symbols':0,'completed_per_day':0,'pass':False})
            continue
        data_valid = len(df)
        fills = int(df.entry_fill.fillna(False).sum())
        comp = df[df.completed.fillna(False)].copy()
        coverage = data_valid / total_signals if total_signals else 0.0
        fill_rate = fills / data_valid if data_valid else np.nan
        if comp.empty:
            rows.append({'block':block,'latency_ms':latency,'signals':total_signals,'data_valid':data_valid,'data_coverage':coverage,'entry_fills':fills,'entry_fill_rate':fill_rate,'completed':0,'symbols':0,'completed_per_day':0,'pass':False})
            continue
        comp['month'] = pd.to_datetime(comp.signal_close_ts, unit='ms', utc=True).dt.strftime('%Y-%m')
        x = comp.net_cons_bp.astype(float)
        positive_month_frac = float((comp.groupby('month').net_cons_bp.mean() >= 0).mean())
        top_share = float(comp.symbol.value_counts(normalize=True).max())
        forced_rate = float(comp.forced_exit.fillna(False).mean())
        r = {
            'block': block, 'latency_ms': latency, 'signals': total_signals,
            'data_valid': data_valid, 'data_coverage': coverage,
            'entry_fills': fills, 'entry_fill_rate': fill_rate,
            'completed': len(comp), 'symbols': comp.symbol.nunique(),
            'completed_per_day': len(comp)/calendar_days,
            'gross_mean_bp': comp.gross_bp.mean(), 'gross_median_bp': comp.gross_bp.median(),
            'net_opt_mean_bp': comp.net_opt_bp.mean(),
            'net_cons_mean_bp': x.mean(), 'net_cons_median_bp': x.median(),
            'net_cons_remove_best5_bp': trimmed_mean(x),
            'net_hard_mean_bp': comp.net_hard_bp.mean(),
            'win_cons': float((x > 0).mean()), 'forced_exit_rate': forced_rate,
            'positive_month_frac': positive_month_frac, 'top_symbol_share': top_share,
        }
        if latency == 100:
            r['pass'] = bool(
                coverage >= MIN_DATA_COVERAGE and len(comp) >= MIN_COMPLETED and comp.symbol.nunique() >= MIN_SYMBOLS and
                len(comp)/calendar_days >= MIN_COMPLETED_PER_DAY and fill_rate >= MIN_ENTRY_FILL_RATE and
                x.mean() > MIN_CONSERVATIVE_MEAN_BP and x.median() > 0 and trimmed_mean(x) > 0 and
                forced_rate <= MAX_FORCED_EXIT_RATE and positive_month_frac >= MIN_POSITIVE_MONTH_FRAC and
                top_share <= MAX_TOP_SYMBOL_SHARE
            )
        else:
            # 250ms is a latency robustness requirement; other structural gates are assessed at primary 100ms.
            r['pass'] = bool(x.mean() > 0 and x.median() > 0 and trimmed_mean(x) > 0)
        rows.append(r)
        comp.to_csv(f'{OUT}/{block}_{latency}ms_completed.csv', index=False)
    return rows


all_summary = []
for block, (a, b) in BLOCKS.items():
    bases, raw100, raw250, miss_rows = [], [], [], []
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs = {ex.submit(process_symbol, block, s, a, b): s for s in SYMS}
        for n, f in enumerate(as_completed(fs), 1):
            s = fs[f]
            base, r100, r250, md = f.result()
            bases.append(base); raw100.extend(r100); raw250.extend(r250)
            miss_rows.extend([{'symbol':s,'day':x} for x in md])
            print(block, n, '/', len(SYMS), s, 'signals', base['signals'], 'missing_days', len(md), flush=True)
    pd.DataFrame(bases).to_csv(f'{OUT}/{block}_signal_coverage.csv', index=False)
    pd.DataFrame(miss_rows).to_csv(f'{OUT}/{block}_missing_agg_days.csv', index=False)
    raw = raw100 + raw250
    if raw:
        pd.DataFrame(raw).to_csv(f'{OUT}/{block}_all_attempts.csv', index=False)
    rows = summarize(block, raw, bases, a, b)
    all_summary.extend(rows)

summary = pd.DataFrame(all_summary)
summary.to_csv(f'{OUT}/summary.csv', index=False)
print('\n# Strategy #9 Cost-Aware Basis Convergence Execution\n')
print(summary.to_markdown(index=False, floatfmt='.3f'))

# Overall requires primary 100ms structural/economic PASS and 250ms robustness PASS in BOTH blocks.
verdict = True
for block in BLOCKS:
    q = summary[summary.block == block]
    if len(q) != 2 or not bool(q['pass'].all()): verdict = False
print('\nExecution model: passive spot requires 0.1bp trade-through, not mere touch; perp hedge is first aggTrade <=1s after spot fill. Spot maker fee is unknown, so primary conservative net charges 2.5bp/side maker fee plus 1bp per aggressive leg. Funding is excluded.\n')
print('OVERALL:', 'PASS_TO_DEEPER_EXECUTION' if verdict else 'REJECT_OR_REDESIGN')
open(f'{OUT}/verdict.txt','w').write(('PASS_TO_DEEPER_EXECUTION' if verdict else 'REJECT_OR_REDESIGN')+'\n')
