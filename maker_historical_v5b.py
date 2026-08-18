import maker_historical_v5 as m

m.DAYS=['2024-03-27','2024-03-28','2024-03-29']
m.TRAIN_DAY='2024-03-27'
m.OOS_DAYS=['2024-03-28','2024-03-29']

if __name__=='__main__':
    m.main()
