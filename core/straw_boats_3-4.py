import ccxt
import time
import random
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：草船借箭 (Operation: STRAW BOATS v3.4)
# 核心功能：48 小時極速動量偵察 + 精確淨利成本核算 + TG 通報
# 目的：收集 5U-30U 淨利達成率與時效，為實盤參數提供數據支持
# =================================================================

class StrawBoatsDataCollector:
    def __init__(self, max_hours=48, max_samples=5000):
        # --- 1. 任務生命週期 ---
        self.start_time = datetime.now(timezone.utc)
        self.end_time = self.start_time + timedelta(hours=max_hours)
        self.max_samples = max_samples
        self.samples_collected = 0

        # --- 2. Telegram 配置 ---
        self.tg_token = "8730258275:AAFAispAsUEIAwy6fAHXGV_3nERRSpbOp4Q"
        self.tg_chat_id = "8394621040"

        # --- 3. 目錄與路徑 ---
        self.root_dir = Path(__file__).resolve().parent
        self.result_dir = self.root_dir / 'result'
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.result_dir / f'sb_48h_data_{self.start_time.strftime("%m%d_%H")}.csv'

        # --- 4. 交易參數 (Bybit Spot) ---
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.fee_taker = 0.0006  # 0.06%
        self.fee_maker = 0.0002  # 0.02%
        self.order_size_u = 5000.0

        self.top_20_pairs = []
        self.active_trades = []
        self.max_exposure_sec = 600  # 單筆 10 分鐘上限
        self.best_signal_info = "Initializing..."

        logger.info(f"🏹 Operation: STRAW BOATS v3.4 [48H 採集版] 啟動")
        logger.info(f"🕒 預計結束時間: {self.end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        self.send_tg(
            f"🚀 **[Straw Boats v3.4] 48H 採集啟動**\n目標：收集 {max_samples} 筆數據\n結束時間：{self.end_time.strftime('%H:%M')} UTC")

    def send_tg(self, message):
        """異步發送 TG 訊息"""
        try:
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": message, "parse_mode": "Markdown"}
            requests.post(url, json=payload, timeout=5)
        except:
            pass

    def fetch_top_20(self):
        """掃描獵場"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            spot_vols = []
            for s, data in tickers.items():
                if '/USDT' in s and 'quoteVolume' in data:
                    base = s.split('/')[0]
                    if base in ['USDC', 'DAI', 'FDUSD', 'BUSD', 'RLUSD'] or 'DOWN' in base or 'UP' in base: continue
                    spot_vols.append({'symbol': s, 'vol': float(data['quoteVolume'])})
            df = pd.DataFrame(spot_vols).sort_values(by='vol', ascending=False)
            self.top_20_pairs = df.head(20)['symbol'].tolist()
            logger.success(f"🎯 獵場已更新，鎖定前 20 名精英幣種。")
        except Exception as e:
            logger.error(f"❌ 偵察失敗: {e}")

    def calculate_net_target_price(self, entry_p, target_net_u, side):
        """
        🚀 核心算法：精確計算扣除成本後的目標價
        公式推導：Qty = (Capital * (1-TakerFee)) / EntryP
                  (Qty * TargetP * (1-MakerFee)) - Capital = TargetNetU
        """
        capital = self.order_size_u
        qty = (capital * (1 - self.fee_taker)) / entry_p

        # TargetP = (TargetNetU + Capital) / (Qty * (1 - MakerFee))
        if side == 'LONG':
            return (target_net_u + capital) / (qty * (1 - self.fee_maker))
        else:
            # 做空邏輯反轉：(Capital - Qty * CoverP * (1+MakerFee)) = TargetNetU
            # 這裡簡化為等效價差計算
            return entry_p * (1 - (target_net_u + (capital * (self.fee_taker + self.fee_maker))) / capital)

    def get_signal(self, symbol):
        """1m 動量偵測"""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=30)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df['ema'] = df['close'].ewm(span=9, adjust=False).mean()
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=7).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            c, ema, rsi = df['close'].iloc[-1], df['ema'].iloc[-1], df['rsi'].iloc[-1]
            if rsi > 55 and c > ema: return 'LONG', c, rsi
            if rsi < 45 and c < ema: return 'SHORT', c, rsi
            return None, c, rsi
        except:
            return None, None, None

    def update_fleet(self):
        """追蹤時效"""
        if not self.active_trades: return
        try:
            all_tk = self.exchange.fetch_tickers(params={'category': 'spot'})
            for trade in self.active_trades[:]:
                symbol = trade['symbol']
                if symbol not in all_tk: continue

                curr_p = all_tk[symbol]['bid'] if trade['side'] == 'LONG' else all_tk[symbol]['ask']
                elapsed = time.time() - trade['entry_time']

                all_hit = True
                for t_val, t_price in trade['targets'].items():
                    if trade['achieved'][t_val] is None:
                        is_hit = (curr_p >= t_price) if trade['side'] == 'LONG' else (curr_p <= t_price)
                        if is_hit:
                            trade['achieved'][t_val] = round(elapsed, 2)
                            # 只有在達成 5U 和 15U 時才發 TG，避免洗版
                            if t_val in [5.0, 15.0]:
                                self.send_tg(f"✨ `{symbol}` 達成利潤階梯 `+{t_val}U`！\n耗時: `{elapsed:.1f}s`")
                        else:
                            all_hit = False

                if all_hit or elapsed > self.max_exposure_sec:
                    self.save_csv(trade, elapsed > self.max_exposure_sec)
                    self.active_trades.remove(trade)
                    self.samples_collected += 1
        except Exception as e:
            logger.error(f"❌ 數據追蹤更新失敗: {e}")

    def save_csv(self, trade, timeout):
        data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': trade['symbol'], 'side': trade['side'], 'entry': trade['entry_p'],
            'net_5u': trade['achieved'][5.0], 'net_10u': trade['achieved'][10.0],
            'net_15u': trade['achieved'][15.0], 'net_20u': trade['achieved'][20.0],
            'net_30u': trade['achieved'][30.0], 'is_timeout': timeout
        }
        pd.DataFrame([data]).to_csv(self.csv_path, mode='a', header=not self.csv_path.exists(), index=False)

    def run(self):
        self.fetch_top_20()
        it = 0
        while datetime.now(timezone.utc) < self.end_time and self.samples_collected < self.max_samples:
            it += 1
            # 掃描並部署
            sample = random.sample(self.top_20_pairs, 10)
            for symbol in sample:
                if any(t['symbol'] == symbol for t in self.active_trades): continue
                side, entry_p, rsi = self.get_signal(symbol)
                if side:
                    net_targets = [5.0, 10.0, 15.0, 20.0, 30.0]
                    target_map = {t: self.calculate_net_target_price(entry_p, t, side) for t in net_targets}
                    self.active_trades.append({
                        'symbol': symbol, 'side': side, 'entry_time': time.time(),
                        'entry_p': entry_p, 'targets': target_map,
                        'achieved': {t: None for t in net_targets}
                    })
                    logger.success(f"🔥 {side} 觸發: {symbol} (RSI: {rsi:.1f})")

            self.update_fleet()

            # 控制台心跳
            time_left = self.end_time - datetime.now(timezone.utc)
            print(
                f"\r📡 [搜獵中] 剩餘時間: {str(time_left).split('.')[0]} | 已收割樣本: {self.samples_collected}/{self.max_samples} | 監控中: {len(self.active_trades)} 艘",
                end="")

            if it % 1000 == 0: self.fetch_top_20()
            time.sleep(1.2)

        summary = f"🏁 **48H 搜獵任務完成**\n總共採集樣本：`{self.samples_collected}`\n數據文件：`{self.csv_path.name}`"
        self.send_tg(summary)
        logger.success(summary)


if __name__ == "__main__":
    # 執行 48 小時，或收集滿 5,000 個樣本
    collector = StrawBoatsDataCollector(max_hours=48, max_samples=5000)
    collector.run()