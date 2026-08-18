# Second independent prospective replication. Strategy and capacity are unchanged from the first $500 OOS.
src=open('maker_inventory_fresh_ws_oos.py',encoding='utf-8').read()
src=src.replace("SYMBOLS=['GALAUSDT','IMXUSDT'];CAPTURE=180;STEP=250;LAT=10;ORDER_USD=100.;CAP_USD=100.;MIN_SPREAD=4.25;MAKER=.0002;TAKER=.0005","SYMBOLS=['GALAUSDT'];CAPTURE=300;STEP=250;LAT=10;ORDER_USD=500.;CAP_USD=500.;MIN_SPREAD=4.25;MAKER=.0002;TAKER=.0005")
src=src.replace("OUT='maker_inventory_fresh_ws_oos_output'","OUT='maker_gala_500_oos2_output'")
src=src.replace("# Fresh WebSocket locked inventory-maker OOS","# Second fresh GALA $500 locked inventory-maker replication")
src=src.replace("GALAUSDT + IMXUSDT","GALAUSDT only")
src=src.replace("$100/order, $100 inventory cap","$500/order, $500 inventory cap")
exec(compile(src,'maker_gala_500_oos2_exec.py','exec'))
