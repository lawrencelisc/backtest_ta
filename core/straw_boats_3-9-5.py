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
# [3-Kingdom] 專案代號：草船借箭 (Operation: STRAW BOATS v3.9.5)
# 核心優化：[多重宇宙並行測試]
# 功能：同時運行三種不同性格的艦隊，並分別存儲至 3 個獨立 CSV 檔案。
# 預算分配：6,666U * 3 = 19,998U (~20,000U)
# =================================================================

class FleetInstance:
    """單個艦隊實例：定義性格、戰術與專屬日誌檔案"""

    def __init__(self, name, order_size, target_profit, atr_mult):
        self.name = name
        self.order_size = order_size
        self.target_profit = target_profit
        self.atr_mult = atr_mult

        self.active_trades = []
        self.total_profit = 0.0
        self.success_count = 0
        self.timeout_count = 0

        # 🚀 [核心功能]：獨立日誌路徑
        root = Path(__file__).resolve().parent
        self.csv_path = root / 'result' / f'00_multiverse_{self.name.lower()}.csv'
        self._init_csv()

    def _init_csv(self):
        """確保檔案與表頭存在，避免 0 byte 問題"""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            'timestamp', 'symbol', 'side', 'status',
            'duration', 'entry', 'target', 'move', 'atr', 'profit_u'
        ]
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            pd.DataFrame(columns=headers).to_csv(self.csv_path, index=False)
            logger.info(f"📁 艦隊【{self.name}】日誌已建立：{self.csv_path.name}")

    def log_trade(self, trade, status, duration, profit=0.0):
        """將成交數據寫入該艦隊專屬的 CSV"""
        data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': trade['symbol'],
            'side': trade['side'],
            'status': status,
            'duration': round(duration, 2),
            'entry': trade['entry_p'],
            'target': trade['target_p'],
            'move': round(trade['move'], 4),
            'atr': round(trade['atr'], 4),
            'profit_u': round(profit, 2)
        }
        pd.DataFrame([data]).to_csv(self.csv_path, mode='a', header=False, index=False)

        # 強制刷新磁碟
        os.sync() if hasattr(os, 'sync') else None

        # 內部統計更新
        if status == "SUCCESS":
            self.success_count += 1
            self.total_profit += profit
        elif status == "TIMEOUT":
            self.timeout_count += 1
            self.total_profit -= 2.0  # 模擬手續費與價差損耗


class MultiverseCommander:
    def __init__(self):
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.top_20 = []
        self.lock = threading.Lock()  # 確保多線程存取安全

        # 🚀 [艦隊部署]：三路並進，注碼對齊 20,000U 總預算
        self.fleets = [
            # 先鋒艦隊：勤力執雞，小利多銷
            FleetInstance("Vanguard", 6666.0, 5.0, 0.8),
            # 主力艦隊：標準配置，穩健收割
            FleetInstance("Strike", 6666.0, 10.0, 1.1),
            # 獵人艦隊：專獵巨震，執大雞
            FleetInstance("Hunter", 6666.0, 25.0, 1.5)
        ]

        self.safety_timeout = 1800  # 30 分鐘強制撤退
        logger.info("🌌 3-Kingdom Multiverse 總指揮塔啟動 | 3 個 CSV 檔案已掛載")

    def fetch_market_data(self):
        """偵察全場 Top 20 成交量幣種"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            vols = []
            for s, d in tickers.items():
                if '/USDT' in s and 'quoteVolume' in d:
                    # 排除穩定幣
                    if s.split('/')[0] in ['USDC', 'DAI', 'FDUSD', 'BUSD', 'RLUSD']: continue
                    vols.append({'s': s, 'v': float(d['quoteVolume'])})
            self.top_20 = pd.DataFrame(vols).sort_values(by='v', ascending=False).head(20)['s'].tolist()
            logger.success(f"🎯 獵場更新成功：{', '.join(self.top_20[:5])}")
        except Exception as e:
            logger.error(f"數據獲取失敗: {e}")

    def calculate_atr(self, df):
        """計算 ATR(5) 作為波動基準"""
        high, low, close = df['h'], df['l'], df['c'].shift(1)
        tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
        return tr.rolling(window=5).mean().iloc[-1]

    def scan_single_coin(self, symbol):
        """
        一次數據請求，供給 3 個平行宇宙的艦隊判斷
        """
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=10)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            atr = self.calculate_atr(df)
            if not atr: return

            last_c, curr_p = df['c'].iloc[-2], df['c'].iloc[-1]
            move = curr_p - last_c
            move_ratio = abs(move / atr)

            for fleet in self.fleets:
                # 檢查該艦隊是否已持有該幣，以及是否滿足其專屬 ATR 門檻
                if not any(t['symbol'] == symbol for t in fleet.active_trades):
                    if move_ratio >= fleet.atr_mult:
                        self.execute_virtual_entry(fleet, symbol, curr_p, move, atr)
        except:
            pass

    def execute_virtual_entry(self, fleet, symbol, curr_p, move, atr):
        """模擬進場執行"""
        side = 'LONG' if move < 0 else 'SHORT'
        # 計算達成淨利所需的目標價 (假設 ROI 0.2% - 0.5%)
        profit_factor = (fleet.target_profit / fleet.order_size) + 0.0012  # 含手續費預算

        target_p = curr_p * (1 + profit_factor) if side == 'LONG' else curr_p * (1 - profit_factor)

        trade = {
            'symbol': symbol, 'side': side, 'entry_p': curr_p,
            'target_p': target_p, 'move': move, 'atr': atr, 'entry_time': time.time()
        }
        with self.lock:
            fleet.active_trades.append(trade)
            fleet.log_trade(trade, "OPEN", 0.0)
            logger.warning(f"⚔️ 【{fleet.name}】出擊: {symbol} | 目標 +{fleet.target_profit}U")

    def monitor_all_fleets(self):
        """監控所有艦隊的所有持倉，執行獲利或超時撤退"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            for fleet in self.fleets:
                for trade in fleet.active_trades[:]:
                    sym = trade['symbol']
                    if sym not in tickers: continue

                    curr_p = tickers[sym]['bid'] if trade['side'] == 'LONG' else tickers[sym]['ask']
                    elapsed = time.time() - trade['entry_time']

                    # 判斷成交條件
                    is_hit = (curr_p >= trade['target_p']) if trade['side'] == 'LONG' else (curr_p <= trade['target_p'])

                    if is_hit:
                        with self.lock:
                            fleet.log_trade(trade, "SUCCESS", elapsed, profit=fleet.target_profit)
                            fleet.active_trades.remove(trade)
                            logger.success(f"✅ 【{fleet.name}】收割成功: {sym} (+{fleet.target_profit}U)")
                    elif elapsed > self.safety_timeout:
                        with self.lock:
                            fleet.log_trade(trade, "TIMEOUT", elapsed)
                            fleet.active_trades.remove(trade)
                            logger.error(f"🛑 【{fleet.name}】{sym} 超時強制撤退")
        except:
            pass

    def run(self):
        """指揮塔主循環"""
        self.fetch_market_data()
        it = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            while True:
                it += 1
                # 1. 多線程全場掃描
                executor.map(self.scan_single_coin, self.top_20)

                # 2. 集中監控
                self.monitor_all_fleets()

                # 3. 定期刷新獵場
                if it % 500 == 0: self.fetch_market_data()

                # 4. 實時比較 Dashboard
                print("\n" + "=" * 65)
                print(f"🛰️ 3-Kingdom Multiverse Dashboard | 輪次: {it} | {datetime.now().strftime('%H:%M:%S')}")
                print("-" * 65)
                for f in self.fleets:
                    win_rate = (f.success_count / (f.success_count + f.timeout_count) * 100) if (
                                                                                                            f.success_count + f.timeout_count) > 0 else 0
                    print(
                        f"🛳️ {f.name:10} | 獲利: {f.total_profit:8.1f}U | 勝率: {win_rate:5.1f}% | 持倉: {len(f.active_trades)}")
                print("=" * 65 + "\n", end='\r')

                time.sleep(1.0)


if __name__ == "__main__":
    commander = MultiverseCommander()
    commander.run()