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
# [3-Kingdom] 專案代號：草船借箭 (Operation: STRAW BOATS v3.9.7)
# 核心優化：[流動性防禦系統] 針對 $6,666U 大額注碼進行深度安全加固。
# 功能：三艦隊同時並行測試，具備滑價監控與自動成交量過濾。
# 總預算：20,000 USDT (每艦隊 6,666.6 USDT)
# =================================================================

class FleetInstance:
    """單個艦隊實例：定義性格、戰術與專屬流動性紀錄"""

    def __init__(self, name, order_size, target_profit, atr_mult):
        self.name = name
        self.order_size = order_size
        self.target_profit = target_profit
        self.atr_mult = atr_mult

        self.active_trades = []
        self.total_profit = 0.0
        self.success_count = 0
        self.timeout_count = 0
        self.early_exit_count = 0
        self.total_duration = 0.0

        # 🚀 建立獨立日誌路徑
        root = Path(__file__).resolve().parent
        self.csv_path = root / 'result' / f'multiverse_{self.name.lower()}.csv'
        self._init_csv()

    def _init_csv(self):
        """初始化 CSV，新增 slippage_pct 以便進行流動性審計"""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            'timestamp', 'symbol', 'side', 'status', 'duration',
            'entry', 'target', 'slippage_pct', 'atr', 'profit_u'
        ]
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            pd.DataFrame(columns=headers).to_csv(self.csv_path, index=False)
            logger.info(f"📁 艦隊【{self.name}】日誌已掛載：{self.csv_path.name}")

    def log_trade(self, trade, status, duration, profit=0.0):
        """將成交數據寫入該艦隊專屬的 CSV"""
        data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': trade['symbol'],
            'side': trade['side'],
            'status': status,
            'duration': round(duration, 2),
            'entry': round(trade['entry_p'], 6),
            'target': round(trade['target_p'], 6),
            'slippage_pct': round(trade.get('slippage', 0), 5),
            'atr': round(trade.get('atr', 0), 6),
            'profit_u': round(profit, 2)
        }
        pd.DataFrame([data]).to_csv(self.csv_path, mode='a', header=False, index=False)

        # 更新內部績效統計
        if status == "SUCCESS":
            self.success_count += 1
            self.total_profit += profit
            self.total_duration += duration
        elif status == "EARLY_EXIT":
            self.early_exit_count += 1
            self.total_profit += profit
        elif status == "TIMEOUT":
            self.timeout_count += 1
            self.total_profit -= 2.5  # 模擬大額注碼下止損的手續費與價差損耗


class MultiverseCommander:
    def __init__(self):
        # 1. 交易所配置 (僅使用 Public 接口)
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.top_20 = []
        self.lock = threading.Lock()

        # 2. 艦隊部署：三路並進，總計 $20,000 預算
        self.fleets = [
            # 先鋒：魏 (Yamato) - 勤力刷單
            FleetInstance("Vanguard", 6666.0, 5.0, 0.8),
            # 主力：蜀 (Dreadnought) - 穩健獲利
            FleetInstance("Strike", 6666.0, 10.0, 1.1),
            # 獵人：吳 (Yukikaze) - 專獵巨震
            FleetInstance("Hunter", 6666.0, 25.0, 1.5)
        ]

        # 3. 戰術控制參數
        self.safety_timeout = 1800  # 30 分鐘
        self.max_slippage_allowed = 0.0008  # 🚀 核心：不允許超過 0.08% 的進場滑價 (8 bps)
        self.vol_threshold_24h = 30000000  # 🚀 成交量防火牆：低於 $30M 不碰

        logger.info("🌌 3-Kingdom Multiverse v3.9.7 [Liquidity Shield] 總指揮塔啟動")
        logger.info(f"🛡️ 滑價防線：{self.max_slippage_allowed * 100}% | 活力門檻：${self.vol_threshold_24h / 1e6}M")

    def fetch_market_data(self):
        """偵察全場 Top 20 並執行流動性初步過濾"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            vols = []
            for s, d in tickers.items():
                if '/USDT' in s and 'quoteVolume' in d:
                    # 排除穩定幣與低流動性幣種
                    if s.split('/')[0] in ['USDC', 'DAI', 'FDUSD', 'BUSD', 'RLUSD']: continue
                    vol_24h = float(d['quoteVolume'])
                    if vol_24h < self.vol_threshold_24h: continue

                    vols.append({'s': s, 'v': vol_24h})

            self.top_20 = pd.DataFrame(vols).sort_values(by='v', ascending=False).head(20)['s'].tolist()
            logger.success(f"🎯 獵場更新成功：{', '.join(self.top_20[:5])} ...")
        except Exception as e:
            logger.error(f"數據獲取失敗: {e}")

    def get_vwap_and_slippage(self, symbol, side, amount_u):
        """
        🚀 工業級深度計價：計算 $6,666U 入場的真實 VWAP 與滑價
        """
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            # 中間價基準 (用於計算滑價)
            mid_p = (ob['bids'][0][0] + ob['asks'][0][0]) / 2

            book_side = 'asks' if side == 'BUY' else 'bids'
            levels = ob[book_side][:3]  # 嚴格計算前三層

            acc_u, acc_qty = 0, 0
            for p, v in levels:
                lev_u = p * v
                needed_u = amount_u - acc_u
                if needed_u <= 0: break
                take_u = min(lev_u, needed_u)
                acc_u += take_u
                acc_qty += (take_u / p)

            if acc_u < amount_u:
                return None, 1.0  # 深度不足以承載 $6,666U

            vwap = acc_u / acc_qty
            slippage = abs(vwap - mid_p) / mid_p
            return vwap, slippage
        except:
            return None, 1.0

    def scan_single_coin(self, symbol):
        """一次 API 請求，三艦隊共享判斷"""
        try:
            # 獲取最近 1m 線計算 ATR
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1m', limit=10)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            tr = (df['h'] - df['l']).rolling(window=5).mean()
            atr = tr.iloc[-1]
            if not atr: return

            last_c, curr_p = df['c'].iloc[-2], df['c'].iloc[-1]
            move = curr_p - last_c
            move_ratio = abs(move / atr)

            for fleet in self.fleets:
                # 檢查是否已持有
                if any(t['symbol'] == symbol for t in fleet.active_trades): continue

                # 檢查性格門檻
                if move_ratio >= fleet.atr_mult:
                    # 🚀 進入流動性審查階段
                    side_action = 'BUY' if (move < 0) else 'SELL'
                    vwap, slippage = self.get_vwap_and_slippage(symbol, side_action, fleet.order_size)

                    if vwap and slippage <= self.max_slippage_allowed:
                        self.execute_virtual_entry(fleet, symbol, vwap, slippage, side_action, atr, move)
                    else:
                        if vwap:
                            # logger.debug(f"🛡️ 攔截 {symbol}: 滑價 {slippage*100:.3f}% 超標")
                            pass
        except:
            pass

    def execute_virtual_entry(self, fleet, symbol, vwap, slippage, side, atr, move):
        """模擬進場執行並計算目標價"""
        direction = 'LONG' if side == 'BUY' else 'SHORT'
        # 計算達成淨利所需目標價 (包含手續費損耗)
        profit_factor = (fleet.target_profit / fleet.order_size) + 0.0012
        target_p = vwap * (1 + profit_factor) if direction == 'LONG' else vwap * (1 - profit_factor)

        trade = {
            'symbol': symbol, 'side': direction, 'entry_p': vwap,
            'target_p': target_p, 'slippage': slippage, 'atr': atr,
            'move': move, 'entry_time': time.time()
        }
        with self.lock:
            fleet.active_trades.append(trade)
            fleet.log_trade(trade, "OPEN", 0.0)
            logger.warning(
                f"⚔️ 【{fleet.name}】出擊: {symbol} | 滑價: {slippage * 100:.3f}% | 目標: +{fleet.target_profit}U")

    def monitor_all_fleets(self):
        """監控獲利、微利撤退與超時"""
        try:
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            for fleet in self.fleets:
                for trade in fleet.active_trades[:]:
                    sym = trade['symbol']
                    if sym not in tickers: continue

                    # 平倉價觀察
                    curr_p = tickers[sym]['bid'] if trade['side'] == 'LONG' else tickers[sym]['ask']
                    elapsed = time.time() - trade['entry_time']

                    # 1. 成功達成目標 (SUCCESS)
                    is_hit = (curr_p >= trade['target_p']) if trade['side'] == 'LONG' else (curr_p <= trade['target_p'])

                    if is_hit:
                        with self.lock:
                            fleet.log_trade(trade, "SUCCESS", elapsed, profit=fleet.target_profit)
                            fleet.active_trades.remove(trade)
                            logger.success(f"✅ 【{fleet.name}】{sym} 達成 {fleet.target_profit}U 目標!")

                    # 2. 智能微利撤退 (EARLY_EXIT) - 過了 15 分鐘只要賺錢就走
                    elif elapsed > (self.safety_timeout / 2):
                        pnl_u = ((curr_p - trade['entry_p']) * (fleet.order_size / trade['entry_p'])) if trade[
                                                                                                             'side'] == 'LONG' else (
                                    (trade['entry_p'] - curr_p) * (fleet.order_size / trade['entry_p']))
                        if pnl_u > 1.5:
                            with self.lock:
                                fleet.log_trade(trade, "EARLY_EXIT", elapsed, profit=pnl_u)
                                fleet.active_trades.remove(trade)
                                logger.info(f"🏃 【{fleet.name}】{sym} 微利撤退 (+{pnl_u:.2f}U)")

                    # 3. 超時止損 (TIMEOUT)
                    elif elapsed > self.safety_timeout:
                        with self.lock:
                            fleet.log_trade(trade, "TIMEOUT", elapsed)
                            fleet.active_trades.remove(trade)
                            logger.error(f"🛑 【{fleet.name}】{sym} 超時強制平倉")
        except:
            pass

    def run(self):
        """啟動全自動多重宇宙循環"""
        self.fetch_market_data()
        it = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            while True:
                it += 1
                # 1. 全場同步掃描
                executor.map(self.scan_single_coin, self.top_20)

                # 2. 集中持倉監控
                self.monitor_all_fleets()

                # 3. 定期更新獵場與流動性數據
                if it % 500 == 0: self.fetch_market_data()

                # 4. 戰效 Dashboard
                print("\n" + "=" * 80)
                print(f"🛰️ 3-Kingdom Multiverse v3.9.7 | 輪次: {it} | {datetime.now().strftime('%H:%M:%S')}")
                print(
                    f"{'艦隊 (Fleet)':15} | {'淨利 (Profit)':12} | {'勝率 (Win%)':10} | {'均耗時 (Avg)':10} | {'持倉'}")
                print("-" * 80)
                for f in self.fleets:
                    total_done = f.success_count + f.timeout_count + f.early_exit_count
                    wr = (f.success_count / (f.success_count + f.timeout_count) * 100) if (
                                                                                                      f.success_count + f.timeout_count) > 0 else 0
                    avg_d = (f.total_duration / f.success_count) if f.success_count > 0 else 0
                    print(
                        f"{f.name:15} | {f.total_profit:9.1f} U | {wr:8.1f}% | {avg_d:7.0f}s | {len(f.active_trades)}")
                print("=" * 80 + "\n", end='\r')

                time.sleep(1.0)


if __name__ == "__main__":
    try:
        commander = MultiverseCommander()
        commander.run()
    except KeyboardInterrupt:
        logger.info("👋 艦長手動終止，艦隊進入靜默狀態。")