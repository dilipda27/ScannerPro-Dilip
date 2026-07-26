import os
import json
import datetime
import pandas as pd
from kiteconnect import KiteConnect
from core import config
from strategies import kite_scanner

session = json.load(open('.kite_session.json'))
kite = KiteConnect(api_key=config.KITE_API_KEY)
kite.set_access_token(session['access_token'])

token = kite_scanner.get_kite_instruments(kite, ['JUBLFOOD'])['JUBLFOOD']
today = datetime.datetime.now()
from_t = today.replace(hour=9, minute=15, second=0, microsecond=0)

df_5m = kite_scanner.fetch_kite_data(kite, token, from_t, today, '5minute')
df_1m = kite_scanner.fetch_kite_data(kite, token, from_t, today, 'minute')

if df_1m.index.tz is not None:
    df_1m.index = df_1m.index.tz_localize(None)
if df_5m.index.tz is not None:
    df_5m.index = df_5m.index.tz_localize(None)

print("=========================================================================")
print("           EXACT RAW JUBLFOOD CANDLE DATA FROM KITE API TODAY            ")
print("=========================================================================\n")

print("--- MORNING RANGE (09:15 to 09:44:59 1-MIN CANDLES) ---")
df_morning = df_1m[(df_1m.index >= from_t.replace(hour=9, minute=15)) & (df_1m.index < from_t.replace(hour=9, minute=45))]
print(f"Candle Count: {len(df_morning)}")
print(f"High (09:15-09:45): {df_morning['high'].max()}")
print(f"Low  (09:15-09:45): {df_morning['low'].min()}")
print(f"Close (at 09:44): {df_morning['close'].iloc[-1]}")

print("\n--- 1-MINUTE CANDLES AROUND ENTRY (09:45 to 09:55) ---")
df_entry = df_1m[(df_1m.index >= from_t.replace(hour=9, minute=45)) & (df_1m.index <= from_t.replace(hour=9, minute=55))]
print(df_entry[['open', 'high', 'low', 'close', 'volume']])

# Calculate morning range numbers
open_915 = df_morning['open'].iloc[0]
high_945 = df_morning['high'].max()
low_945 = df_morning['low'].min()
close_945 = df_morning['close'].iloc[-1]
range_w = high_945 - low_945

print("\n--- STRATEGY CALCULATIONS ---")
print(f"Open 9:15: {open_915}")
print(f"High 9:45: {high_945}")
print(f"Low 9:45:  {low_945}")
print(f"Close 9:45:{close_945}")
print(f"Classification: WEAK (since close {close_945} < open {open_915} and near low)")

breakdown_level = low_945 * 0.9985
print(f"Breakdown Trigger Level (low_945 * 0.9985): {breakdown_level:.2f}")

print("\n--- CHECKING ENTRY CANDLE AT 09:49 AM ---")
row_949 = df_1m[df_1m.index == from_t.replace(hour=9, minute=49)]
if not row_949.empty:
    c_close = row_949.iloc[0]['close']
    c_low = row_949.iloc[0]['low']
    c_vol = row_949.iloc[0]['volume']
    print(f"09:49 Candle Close: {c_close}")
    print(f"09:49 Candle Low:   {c_low}")
    print(f"Is Close ({c_close}) <= Breakdown Level ({breakdown_level:.2f})? {c_close <= breakdown_level}")
