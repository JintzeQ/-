# Reuse the exact v7 prospective methodology on a new untouched current-market universe.
src=open('maker_live_v7.py',encoding='utf-8').read()
src=src.replace("SYMBOLS=['BBUSDT','ROBOUSDT','RAREUSDT']","SYMBOLS=['FETUSDT','OPUSDT','WIFUSDT']")
src=src.replace("OUT='maker_live_v7_output'","OUT='maker_live_v9_output'")
src=src.replace("# BB/ROBO/RARE maker v7 — prospective queue-turnover probe","# FET/OP/WIF maker v9 — corrected-stream prospective queue-turnover probe")
exec(compile(src,'maker_live_v9_exec.py','exec'))
