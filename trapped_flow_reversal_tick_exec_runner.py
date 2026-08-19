import numpy as np
import trapped_flow_reversal_tick_exec as m

# Parser-only hotfix for #4B. Strategy dates, universe, signal, deterministic
# sample, execution semantics, fees, latencies, slippage stresses and gate are
# all inherited unchanged from trapped_flow_reversal_tick_exec.py.
def writable_norm_ms(a):
    a = np.array(a, dtype='float64', copy=True)
    mask = np.isfinite(a) & (a > 1e14)
    a[mask] = np.floor(a[mask] / 1000.0)
    return a

m.norm_ms = writable_norm_ms

if __name__ == '__main__':
    missk = m.prefetch_klines()
    if missk:
        raise RuntimeError('monthly kline coverage incomplete')
    E = m.build_frozen_signals()
    S = m.deterministic_sample(E)
    book_req, agg_req, reqdays = m.build_requests(S)
    print('required sampled symbol-days', len(reqdays), flush=True)
    missing = m.prefetch_tick(reqdays)
    quotes, trades = m.scan_all(book_req, agg_req)
    R = m.evaluate(S, quotes, trades)
    m.summarize(R, S, missing)
