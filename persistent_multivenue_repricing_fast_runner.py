# Semantics-preserving runtime wrapper for Strategy #18.
# It replaces only generate_signals() with a vectorized implementation, then
# executes the frozen authoritative script unchanged for data, execution, exits,
# costs, dates, thresholds, and PASS gates.

from pathlib import Path

src = Path('persistent_multivenue_repricing.py').read_text()
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
    # Original max(candidates,key=gap) breaks exact gap ties by VENUES order.
    c=c.sort_values(['formation_ts','initial_gap_bp','target_ord'],ascending=[True,False,True],kind='mergesort')
    c=c.drop_duplicates('formation_ts',keep='first').sort_values('formation_ts',kind='mergesort')
    signals=[]; last=-10**18
    for row in c.itertuples(index=False):
        its=int(row.formation_ts)
        # Match frozen code exactly: it compares formation ts to the previous signal timestamp,
        # while previous `last` is set to formation_ts + 1000.
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
