import ccxt
import time
import os
import pandas as pd
import numpy as np
import threading
from datetime import datetime
from pathlib import Path
from loguru import logger
from concurrent.futures import ThreadPoolExecutor


# =================================================================
# [3-Kingdom] 專案代號：草船借箭 (Operation: STRAW BOATS v3.9.5 Pro)
# 核心優化：[高頻暴力 + 三大防禦外掛]
# 1. 毒藥黑名單 (Blacklist)
# 2. 15分鐘微利撤退 (Half-Time Rescue)
# 3. BTC 海嘯警報 (Tsunami Alarm)
# 模組重構：參數集中化，於最底部統一管理。
# =================================================================

class FleetInstance:
    """單個艦隊實例：負責獨立日誌與績效統計"""

    def __init__(self, name, order_size, target_profit, atr_mult):
        self.name = name
        self.order_size = order_size
        self.target_profit = target_profit
        self.atr_mult = atr_mult

        self.active_trades = []
        self.total_profit = 0.0
        self.success_count = 0
        self.early_exit_count = 0
        self.timeout_count = 0

        root = Path(__file__).resolve().parent
        self.csv_path = root / 'result' / f'multiverse_{self.name.lower()}_pro.csv'
        self._init_csv()

    def _init_csv(self):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        headers = ['timestamp', 'symbol', 'side', 'status', 'duration', 'entry', 'target', 'atr', 'profit_u']
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            pd.DataFrame(columns=headers).to_csv(self.csv_path, index=False)

    def log_trade(self, trade, status, duration, profit=0.0):
        data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': trade['symbol'], 'side': trade['side'], 'status': status,
            'duration': round(duration, 2), 'entry': round(trade['entry_p'], 6),
            'target': round(trade['target_p'], 6), 'atr': round(trade.get('atr', 0), 6),
            'profit_u': round(profit, 2)
        }
        pd.DataFrame([data]).to_csv(self.csv_path, mode='a', header=False, index=False)

        if status == "SUCCESS":
            self.success_count += 1
            self.total_profit += profit
        elif status == "EARLY_EXIT":
            self.early_exit_count += 1
            self.total_profit += profit
        elif status == "TIMEOUT":
            self.timeout_count += 1
            self.total_profit -= 2.5  # 模擬超時虧損


class MultiverseCommander:
    def __init__(self, fleets_config, safety_timeout=1800, blacklist=None, tsunami_drop_pct=-0.005,
                 tsunami_pause_duration=900):
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.lock = threading.Lock()

        # 🚀 艦隊部署從參數載入
        self.fleets = [
            FleetInstance(cfg["name"], cfg["order_size"], cfg["target_profit"], cfg["atr_mult"])
            for cfg in fleets_config
        ]

        self.top_20 = []
        self.safety_timeout = safety_timeout

        # 🛡️ 防禦外掛 1：毒藥黑名單
        self.blacklist = blacklist if blacklist else []

        # 🛡️ 防禦外掛 3：BTC 海嘯警報狀態與參數
        self.tsunami_pause_until = 0
        self.tsunami_drop_pct = tsunami_drop_pct
        self.tsunami_pause_duration = tsunami_pause_duration

        logger.info("🌌 v3.9.5 Pro [聖杯測試版] 啟動 | 三大防禦外掛已掛載 | 參數已載入")

    def check_btc_tsunami(self):
        """🛡️ 檢查 BTC 是否發生 5 分鐘級別的暴跌"""
        try:
            ohlcv = self.exchange.fetch_ohlcv('BTC/USDT', timeframe='5m', limit=2)
            if not ohlcv or len(ohlcv) < 2: return

            # 獲取當前 K 線的開盤價與現價
            open_p = ohlcv[-1][1]
            curr_p = ohlcv[-1][4]
            drop_pct = (curr_p - open_p) / open_p

            if drop_pct <= self.tsunami_drop_pct:
                pause_time = time.time() + self.tsunami_pause_duration
                if self.tsunami_pause_until < pause_time:
                    self.tsunami_pause_until = pause_time
                    logger.error(
                        f"🚨 海嘯警報！BTC 急跌 {drop_pct * 100:.2f}%！全軍暫停出擊 {self.tsunami_pause_duration // 60} 分鐘回港避風！")
        except:
            pass

    def fetch_market_data(self):
        """獲取 Top 20 並過濾黑名單"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            vols = []
            for s, d in tickers.items():
                if '/USDT' in s and 'quoteVolume' in d:
                    if s in self.blacklist: continue  # 🛡️ 觸發黑名單過濾
                    vols.append({'s': s, 'v': float(d['quoteVolume'])})
            self.top_20 = pd.DataFrame(vols).sort_values(by='v', ascending=False).head(20)['s'].tolist()
        except:
            pass

    def calculate_atr(self, df):
        high, low, close = df['h'], df['l'], df['c'].shift(1)
        tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
        return tr.rolling(window=5).mean().iloc[-1]

    def scan_single_coin(self, symbol):
        """高速掃描引擎"""
        # 🛡️ 如果處於海嘯暫停期，拒絕所有新進場
        if time.time() < self.tsunami_pause_until:
            return

        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=10)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            atr = self.calculate_atr(df)
            if not atr: return

            last_c, curr_p = df['c'].iloc[-2], df['c'].iloc[-1]
            move = curr_p - last_c
            move_ratio = abs(move / atr)

            for fleet in self.fleets:
                if not any(t['symbol'] == symbol for t in fleet.active_trades):
                    if move_ratio >= fleet.atr_mult:
                        self.execute_virtual_entry(fleet, symbol, curr_p, move, atr)
        except:
            pass

    def execute_virtual_entry(self, fleet, symbol, curr_p, move, atr):
        side = 'LONG' if move < 0 else 'SHORT'
        profit_factor = (fleet.target_profit / fleet.order_size) + 0.0012  # 含手續費預估
        target_p = curr_p * (1 + profit_factor) if side == 'LONG' else curr_p * (1 - profit_factor)

        trade = {
            'symbol': symbol, 'side': side, 'entry_p': curr_p,
            'target_p': target_p, 'atr': atr, 'entry_time': time.time()
        }
        with self.lock:
            fleet.active_trades.append(trade)
            fleet.log_trade(trade, "OPEN", 0.0)
            logger.warning(f"⚔️ 【{fleet.name}】出擊 {symbol} | 偏離: {abs(move / atr):.1f}x ATR")

    def monitor_all_fleets(self):
        """監控持倉與智能撤退"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            for fleet in self.fleets:
                for trade in fleet.active_trades[:]:
                    sym = trade['symbol']
                    if sym not in tickers: continue

                    curr_p = tickers[sym]['bid'] if trade['side'] == 'LONG' else tickers[sym]['ask']
                    elapsed = time.time() - trade['entry_time']

                    # 1. 成功達成目標
                    is_hit = (curr_p >= trade['target_p']) if trade['side'] == 'LONG' else (curr_p <= trade['target_p'])
                    if is_hit:
                        with self.lock:
                            fleet.log_trade(trade, "SUCCESS", elapsed, profit=fleet.target_profit)
                            fleet.active_trades.remove(trade)
                            logger.success(f"✅ 【{fleet.name}】{sym} 完美收割 +{fleet.target_profit}U!")
                        continue

                    # 🛡️ 防禦外掛 2：15 分鐘微利撤退 (Half-Time Rescue)
                    if elapsed > 900:  # 超過 15 分鐘
                        pnl_u = ((curr_p - trade['entry_p']) * (fleet.order_size / trade['entry_p'])) if trade[
                                                                                                             'side'] == 'LONG' else (
                                    (trade['entry_p'] - curr_p) * (fleet.order_size / trade['entry_p']))
                        if pnl_u >= 1.5:  # 只要有利潤大於 1.5U
                            with self.lock:
                                fleet.log_trade(trade, "EARLY_EXIT", elapsed, profit=pnl_u)
                                fleet.active_trades.remove(trade)
                                logger.info(f"🏃 【{fleet.name}】{sym} 啟動微利撤退 (+{pnl_u:.2f}U)")
                            continue

                    # 3. 超時止損
                    if elapsed > self.safety_timeout:
                        with self.lock:
                            fleet.log_trade(trade, "TIMEOUT", elapsed)
                            fleet.active_trades.remove(trade)
                            logger.error(f"🛑 【{fleet.name}】{sym} 超時撤退")
        except:
            pass

    def run(self):
        self.fetch_market_data()
        it = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            while True:
                it += 1

                # 🛡️ 每 10 輪檢查一次 BTC 海嘯警報
                if it % 10 == 0: self.check_btc_tsunami()

                # 掃描與監控
                executor.map(self.scan_single_coin, self.top_20)
                self.monitor_all_fleets()

                if it % 500 == 0: self.fetch_market_data()

                # Dashboard
                tsunami_str = "🚨 海嘯避風中" if time.time() < self.tsunami_pause_until else "🌊 海面平靜"
                print("\n" + "=" * 75)
                print(f"🛰️ 3-Kingdom v3.9.5 Pro | 輪次: {it} | 大盤狀態: {tsunami_str}")
                print(f"{'艦隊':12} | {'總利潤':10} | {'勝/微利/敗':12} | {'勝率':8} | {'持倉'}")
                print("-" * 75)
                for f in self.fleets:
                    total_closed = f.success_count + f.early_exit_count + f.timeout_count
                    wr = ((f.success_count + f.early_exit_count) / total_closed * 100) if total_closed > 0 else 0
                    print(
                        f"{f.name:12} | {f.total_profit:7.1f} U | {f.success_count:3}/{f.early_exit_count:3}/{f.timeout_count:3} | {wr:5.1f}% | {len(f.active_trades)}")
                print("=" * 75 + "\n", end='\r')

                time.sleep(1.0)


if __name__ == "__main__":
    # =================================================================
    # ⚙️ [全局參數管理區]
    # 艦長，日後要調整任何策略，只需在這裡修改，不需動用上方核心邏輯！
    # =================================================================

    # 1. 艦隊配署與戰術 (預算, 目標利潤, ATR敏銳度)
    FLEETS_CONFIG = [
        {"name": "Vanguard", "order_size": 6666.0, "target_profit": 5.0, "atr_mult": 0.8},  # 勤力執雞
        {"name": "Strike", "order_size": 6666.0, "target_profit": 10.0, "atr_mult": 1.1},  # 穩健主力
        {"name": "Hunter", "order_size": 6666.0, "target_profit": 25.0, "atr_mult": 1.5}  # 專獵巨震
    ]

    # 2. 防禦超時設定 (秒)
    SAFETY_TIMEOUT = 1800  # 30分鐘超時硬撤退

    # 3. 毒藥黑名單 (剔除無波動穩定幣或死水幣)
    BLACKLIST = [
        'USDE/USDT', 'TRX/USDT', 'MNT/USDT',
        'USDC/USDT', 'DAI/USDT', 'FDUSD/USDT'
    ]

    # 4. BTC 海嘯警報參數
    TSUNAMI_DROP_PCT = -0.005  # 觸發條件：5分鐘內跌幅超過 0.5% (-0.005)
    TSUNAMI_PAUSE_DURATION = 900  # 懲罰/保護：暫停所有進場 15分鐘 (900秒)

    # 🚀 啟動總指揮塔並注入參數
    commander = MultiverseCommander(
        fleets_config=FLEETS_CONFIG,
        safety_timeout=SAFETY_TIMEOUT,
        blacklist=BLACKLIST,
        tsunami_drop_pct=TSUNAMI_DROP_PCT,
        tsunami_pause_duration=TSUNAMI_PAUSE_DURATION
    )
    commander.run()