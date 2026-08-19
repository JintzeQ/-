# Strategy #20 runner.
# One structural change versus frozen #19: replace fixed 60s signal de-overlap
# with no-overlap lifecycle re-entry plus a fixed 5s post-exit cooldown.
# Signal thresholds, universe, execution, exits and costs remain unchanged.

from pathlib import Path

src = Path('persistent_multivenue_repricing.py').read_text()

def replace_once(old, new):
    global src
    if old not in src:
        raise RuntimeError('Frozen source fragment not found: ' + old[:100])
    src = src.replace(old, new, 1)

replace_once("OUT='persistent_multivenue_output'; os.makedirs(OUT,exist_ok=True)",
             "OUT='persistent_multivenue_throughput_output'; os.makedirs(OUT,exist_ok=True)")
replace_once("TMP='/tmp/s18_multivenue'; os.makedirs(TMP,exist_ok=True)",
             "TMP='/tmp/s20_multivenue'; os.makedirs(TMP,exist_ok=True)")
replace_once("SYMS=['BTCUSDT','ETHUSDT','DOGEUSDT']",
             "SYMS=['BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','SOLUSDT','LTCUSDT','LINKUSDT','TRXUSDT']")
replace_once("BLOCKS={\n    'BLOCK_A_2024_DEC': pd.date_range('2024-12-01','2024-12-14',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),\n    'BLOCK_B_2026_JUN': pd.date_range('2026-06-01','2026-06-14',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),\n}",
             "BLOCKS={\n    'BLOCK_A_2023_FEB': pd.date_range('2023-02-01','2023-02-10',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),\n    'BLOCK_B_2025_FEB': pd.date_range('2025-02-01','2025-02-10',freq='D',tz='UTC').strftime('%Y-%m-%d').tolist(),\n}")

# Generate every fresh qualifying second. Exposure overlap is controlled later by
# actual executable exit time, not by a fixed signal-time de-overlap window.
replace_once("DEOVERLAP_MS=60000", "DEOVERLAP_MS=0")

# Count all frozen calendar evaluation days in the positive-day gate.
replace_once("dm=e.groupby('day').net_fee_bp.mean()",
             "dm=e.groupby('day').net_fee_bp.mean().reindex(days)")

old_gate = """primary=(coverage>=.95 and n>=70 and r['symbols']>=3 and r['exec_venues']>=2 and r['completed_per_day']>=5 and
                     r['gross_mean_bp']>=15 and r['net_fee_mean_bp']>5 and r['net_fee_median_bp']>0 and
                     r['net_fee_remove_best5_bp']>0 and r['net_stress_mean_bp']>3 and r['positive_day_frac']>=.60 and
                     r['top_symbol_share']<=.60 and r['top_venue_share']<=.70)
            stress=(coverage>=.95 and n>=70 and r['symbols']>=3 and r['exec_venues']>=2 and r['completed_per_day']>=5 and
                    r['net_fee_mean_bp']>0 and r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0)"""
new_gate = """primary=(coverage>=.95 and n>=50 and r['symbols']>=8 and r['exec_venues']>=3 and r['completed_per_day']>=5 and
                     r['gross_mean_bp']>=15 and r['net_fee_mean_bp']>5 and r['net_fee_median_bp']>0 and
                     r['net_fee_remove_best5_bp']>0 and r['net_stress_mean_bp']>3 and r['positive_day_frac']>=.60 and
                     r['top_symbol_share']<=.25 and r['top_venue_share']<=.70)
            stress=(coverage>=.95 and n>=50 and r['symbols']>=8 and r['exec_venues']>=3 and r['completed_per_day']>=5 and
                    r['net_fee_mean_bp']>0 and r['net_fee_median_bp']>0 and r['net_fee_remove_best5_bp']>0)"""
replace_once(old_gate, new_gate)

# Enforce lifecycle-based re-entry independently for each latency row.
old_exec = """signals=generate_signals(s,day,frame)
            for ev in signals:
                for lat in LATENCIES:
                    z=execute_signal(ev,dl[s],frame,lat)
                    if z is not None:ev_by_lat[lat].append(z); all_events.append(z)"""
new_exec = """signals=generate_signals(s,day,frame)
            for lat in LATENCIES:
                next_allowed=-10**18
                for ev in signals:
                    if ev['signal_ms'] < next_allowed:continue
                    z=execute_signal(ev,dl[s],frame,lat)
                    if z is not None:
                        ev_by_lat[lat].append(z); all_events.append(z)
                        next_allowed=z['exit_ts']+MIN_HOLD_MS"""
replace_once(old_exec, new_exec)

replace_once("# Strategy #18 Persistent Multi-Venue Repricing",
             "# Strategy #20 Lifecycle-Reentry Persistent Multi-Venue Repricing")
replace_once("Frozen: Binance/Bybit/Gate symmetric candidates; 10s formation with both leader venues aligned and persistent over the latest 5s; median leader 10s move >=25bp; target trails leader consensus by >=18bp; route to largest lagger. Enter selected venue after 100/250ms. Exit when relative gap <=5bp after >=5s, otherwise max hold 60s. Fee-only=10bp RT; stress=12bp. No market making, no fee optimization, no post-outcome retuning.",
             "Frozen #20: same 10-symbol broad universe and unchanged #18/#19 signal economics; fresh 2023-02 and 2025-02 blocks. Fixed 60s signal de-overlap is replaced only by one-position-per-symbol lifecycle control plus a fixed 5s post-exit cooldown. Enter after 100/250ms. Exit on residual <=5bp after >=5s, otherwise max 60s. Fee-only=10bp RT; stress=12bp. No post-outcome retuning.")

# Vectorized candidate scan; economics are identical to the authoritative signal.
start = src.index('def generate_signals(')
end = src.index('\ndef choose_exit', start)
fast = r'''def generate_signals(sym,day,frame):
    r10=np.log(frame/frame.shift(FORMATION_S))*1e4
    r5=np.log(frame/frame.shift(PERSIST_S))*1e4
    parts=[]
    for target_ord,target in enumerate(VENUES):
        others=[v for v in VENUES if v!=target]
        a10=r10[others[0]].to_numpy(float); b10=r10[others[1]].to_numpy(float); own10=r10[target].to_numpy(float)
        a5=r5[others[0]].to_numpy(float); b5=r5[others[1]].to_numpy(float); own5=r5[target].to_numpy(float)
        side=np.where(a10>0,1.0,-1.0)
        sa10=side*a10; sb10=side*b10; so10=side*own10
        sa5=side*a5; sb5=side*b5; so5=side*own5
        leader10=(sa10+sb10)/2.0
        leader5=(sa5+sb5)/2.0
        gap=leader10-so10
        valid=np.isfinite(a10)&np.isfinite(b10)&np.isfinite(own10)&np.isfinite(a5)&np.isfinite(b5)&np.isfinite(own5)
        valid &= (np.abs(a10)<=MAX_ABS_10S_BP)&(np.abs(b10)<=MAX_ABS_10S_BP)&(np.abs(own10)<=MAX_ABS_10S_BP)
        valid &= (a10!=0)&(b10!=0)&(np.sign(a10)==np.sign(b10))
        valid &= leader10>=LEADER10_MIN_BP
        valid &= (sa5>0)&(sb5>0)
        valid &= (sa5>=LEADER5_FRACTION*sa10)&(sb5>=LEADER5_FRACTION*sb10)
        valid &= so10<np.minimum(sa10,sb10)
        valid &= gap>=MIN_LAG_GAP_BP
        valid &= so5<leader5
        pos=np.flatnonzero(valid)
        if len(pos)==0:continue
        p=pd.DataFrame({
            'formation_ts':frame.index.to_numpy(np.int64)[pos],
            'target_venue':target,'target_ord':target_ord,'side':side[pos].astype(int),'initial_gap_bp':gap[pos],
            'leader10_bp':leader10[pos],'leader5_bp':leader5[pos],'target10_signed_bp':so10[pos],
            'target5_signed_bp':so5[pos],'leader1':others[0],'leader2':others[1],
            'leader1_10_bp':a10[pos],'leader2_10_bp':b10[pos],'leader1_5_bp':a5[pos],'leader2_5_bp':b5[pos],
            'target_base_px':frame[target].to_numpy(float)[pos],
            'leader1_base_px':frame[others[0]].to_numpy(float)[pos],
            'leader2_base_px':frame[others[1]].to_numpy(float)[pos],
        })
        parts.append(p)
    if not parts:return []
    c=pd.concat(parts,ignore_index=True)
    c=c.sort_values(['formation_ts','initial_gap_bp','target_ord'],ascending=[True,False,True],kind='mergesort')
    c=c.drop_duplicates('formation_ts',keep='first').sort_values('formation_ts',kind='mergesort')
    signals=[]; last=-10**18
    for row in c.itertuples(index=False):
        its=int(row.formation_ts)
        if its-last<DEOVERLAP_MS:continue
        signal=its+1000
        signals.append({'symbol':sym,'day':day,'signal_ms':signal,'formation_ts':its,'target_venue':row.target_venue,'side':int(row.side),
                        'initial_gap_bp':float(row.initial_gap_bp),'leader10_bp':float(row.leader10_bp),'leader5_bp':float(row.leader5_bp),
                        'target10_signed_bp':float(row.target10_signed_bp),'target5_signed_bp':float(row.target5_signed_bp),
                        'leader1':row.leader1,'leader2':row.leader2,'leader1_10_bp':float(row.leader1_10_bp),
                        'leader2_10_bp':float(row.leader2_10_bp),'leader1_5_bp':float(row.leader1_5_bp),'leader2_5_bp':float(row.leader2_5_bp),
                        'target_base_px':float(row.target_base_px),'leader1_base_px':float(row.leader1_base_px),'leader2_base_px':float(row.leader2_base_px)})
        last=signal
    return signals
'''

patched = src[:start] + fast + src[end:]
ns = {'__name__':'__main__','__file__':'persistent_multivenue_repricing.py'}
exec(compile(patched, 'persistent_multivenue_repricing.py', 'exec'), ns, ns)
