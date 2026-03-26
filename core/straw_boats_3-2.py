import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：草船借箭 (Operation: STRAW BOATS v3.2.1)
# 核心功能：極速反應 + 戰場能見度 + 自動任務完結 (Mission Completion)
# 修正：修復 RSI 為 None 時導致的 TypeError 崩潰問題
# =================================================================

class StrawBoatsMissionSim:
    def __init__(self, initial_capital=20000.0, max_samples=100):
        # 1. 任務設定
        self.max_samples = max_samples  # 收集多少筆數據後完結
        self.samples_collected = 0

        # 2. 目錄配置
        self.root_dir = Path(__file__).resolve().parent
        self.result_dir = self.root_dir / 'result'
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.result_dir / 'straw_boats_v32_final_report.csv'

        # 3. 交易所與參數
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.fee_taker, self.fee_maker = 0.0006, 0.0002
        self.order_size_u = 5000.0

        self.top_20_pairs = []
        self.active_trades = []
        self.max_exposure_sec = 600
        self.best_signal_info = "Waiting..."

        logger.info(f"🏹 Operation: STRAW BOATS v3.2.1 [修復穩定版] 啟動")
        logger.info(f"📋 目標樣本數：{self.max_samples} 筆紀錄 | 滿額後自動停機。")

    def fetch_top_20(self):
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            spot_vols = []
            for s, data in tickers.items():
                if '/USDT' in s and 'quoteVolume' in data:
                    base = s.split('/')[0]
                    if base in ['USDC', 'DAI', 'FDUSD', 'BUSD', 'RLUSD'] or 'DOWN' in base or 'UP' in base:
                        continue
                    spot_vols.append({'symbol': s, 'vol': float(data['quoteVolume'])})

            df = pd.DataFrame(spot_vols).sort_values(by='vol', ascending=False)
            self.top_20_pairs = df.head(20)['symbol'].tolist()
            logger.success("🎯 獵場更新完畢。")
        except Exception as e:
            logger.error(f"❌ 偵察失敗: {e}")

    def get_signal_with_metrics(self, symbol):
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

    def calculate_net_target(self, entry_p, target_net_u, side):
        fees = self.order_size_u * (self.fee_taker + self.fee_maker)
        gross_needed = target_net_u + fees
        qty = (self.order_size_u * (1 - self.fee_taker)) / entry_p
        price_step = gross_needed / qty
        return entry_p + price_step if side == 'LONG' else entry_p - price_step

    def scan_and_deploy(self):
        if not self.top_20_pairs: return "Initializing"

        sample = random.sample(self.top_20_pairs, 10)
        temp_best_rsi = 50.0
        temp_best_coin = ""

        for symbol in sample:
            if any(t['symbol'] == symbol for t in self.active_trades): continue

            side, entry_p, rsi = self.get_signal_with_metrics(symbol)

            # 🛡️ 安全檢查：確保 RSI 不是 None 才進行比較
            if rsi is not None:
                if abs(rsi - 50) > abs(temp_best_rsi - 50):
                    temp_best_rsi = rsi
                    temp_best_coin = symbol.replace('/USDT', '')

            if side:
                net_targets = [5.0, 10.0, 15.0, 20.0, 30.0]
                target_map = {t: self.calculate_net_target(entry_p, t, side) for t in net_targets}

                trade = {
                    'symbol': symbol, 'side': side, 'entry_time': time.time(),
                    'entry_p': entry_p, 'targets': target_map,
                    'achieved': {t: None for t in net_targets}
                }
                self.active_trades.append(trade)
                logger.success(f"🔥 {side} 觸發! {symbol} (RSI: {rsi:.1f})")

        if temp_best_coin:
            self.best_signal_info = f"{temp_best_coin} RSI:{temp_best_rsi:.1f}"
        else:
            self.best_signal_info = "Scanning..."

        return ", ".join([s.replace('/USDT', '') for s in sample])

    def update_fleet(self):
        if not self.active_trades: return
        try:
            all_tk = self.exchange.fetch_tickers(params={'category': 'spot'})
            for trade in self.active_trades[:]:
                symbol = trade['symbol']
                if symbol not in all_tk: continue

                curr_p = all_tk[symbol]['bid'] if trade['side'] == 'LONG' else all_tk[symbol]['ask']
                elapsed = time.time() - trade['entry_time']

                all_done = True
                for t_val, t_price in trade['targets'].items():
                    if trade['achieved'][t_val] is None:
                        is_hit = (curr_p >= t_price) if trade['side'] == 'LONG' else (curr_p <= t_price)
                        if is_hit:
                            trade['achieved'][t_val] = round(elapsed, 2)
                        else:
                            all_done = False

                if all_done or elapsed > self.max_exposure_sec:
                    self.save_csv(trade, elapsed > self.max_exposure_sec)
                    self.active_trades.remove(trade)
                    self.samples_collected += 1
                    logger.info(f"📊 樣本收集進度: {self.samples_collected}/{self.max_samples}")
        except Exception as e:
            logger.error(f"❌ 更新出錯: {e}")

    def save_csv(self, trade, timeout):
        data = {
            'time': datetime.now().strftime('%H:%M:%S'), 'symbol': trade['symbol'],
            'side': trade['side'], 'entry': trade['entry_p'],
            'net_5u': trade['achieved'][5.0], 'net_10u': trade['achieved'][10.0],
            'net_15u': trade['achieved'][15.0], 'net_20u': trade['achieved'][20.0],
            'net_30u': trade['achieved'][30.0], 'is_timeout': timeout
        }
        pd.DataFrame([data]).to_csv(self.csv_path, mode='a', header=not self.csv_path.exists(), index=False)

    def run(self):
        self.fetch_top_20()
        it = 0
        while self.samples_collected < self.max_samples:
            it += 1
            scanning_msg = self.scan_and_deploy()
            self.update_fleet()

            status = f"📡 [輪次 {it}] 掃描中... | 進度: {self.samples_collected}/{self.max_samples} | 最接近: {self.best_signal_info}"
            print(f"\r{status}", end="", flush=True)

            time.sleep(1.2)

        logger.success(f"\n🏁 任務完成！已收集滿 {self.max_samples} 筆數據。")
        logger.info(f"📂 請查閱最終報告：{self.csv_path}")


if __name__ == "__main__":
    sim = StrawBoatsMissionSim(max_samples=100)  # 設定為 100 筆紀錄
    sim.run()