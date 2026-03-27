import ccxt
import pandas as pd
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from loguru import logger
from datetime import datetime
import os

# ==========================================
# 1. 全局配置與安全鎖
# ==========================================
TRADE_LOCK = threading.Lock()
CSV_LOCK = threading.Lock()
CSV_FILE = 'multiverse_trades_v4_1_2.csv'

exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})


class Fleet:
    def __init__(self, name, order_size, target_profit, atr_mult):
        self.name = name
        self.order_size = order_size
        self.target_profit = target_profit
        self.atr_mult = atr_mult
        self.active_trades = []
        self.fee_rate = 0.002
        self.stop_loss_pct = 0.015
        # 統計數據
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0.0

    def log_trade(self, trade, status, elapsed, pnl_u):
        self.total_pnl += pnl_u
        if status == "SUCCESS":
            self.win_count += 1
        elif status in ["STOP_LOSS", "TIMEOUT"]:
            self.loss_count += 1

        with CSV_LOCK:
            with open(CSV_FILE, 'a') as f:
                f.write(f"{datetime.now()},{self.name},{trade['symbol']},{status},{elapsed:.1f},{pnl_u:.4f}\n")


class MasterOrchestrator:
    def __init__(self):
        self.fleets = [
            Fleet("Vanguard", 6666, 5, 0.8),  # 魏：快狠準
            Fleet("Strike", 6666, 10, 1.2),  # 蜀：穩中求勝
            Fleet("Hunter", 6666, 25, 1.8)  # 吳：專獵極端
        ]
        self.top_20 = []
        self.active_symbols = set()
        self.tsunami_pause_until = 0
        self.blacklist = ['USDC/USDT', 'BUSD/USDT', 'DAI/USDT', 'FDUSD/USDT', 'WBTC/USDT']
        self.scan_count = 0  # 紀錄掃描次數

    def calculate_atr(self, symbol):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, '1m', limit=30)
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            df['tr'] = df.apply(lambda r: max(r['h'] - r['l'], 0), axis=1)  # 簡化TR
            return df['tr'].rolling(window=14).mean().iloc[-1]
        except:
            return None

    def check_btc_tsunami(self):
        try:
            ohlcv = exchange.fetch_ohlcv('BTC/USDT', '5m', limit=2)
            drop = (ohlcv[-1][4] - ohlcv[0][1]) / ohlcv[0][1]
            if drop <= -0.015:
                self.tsunami_pause_until = time.time() + 1800
                return True, drop
        except:
            pass
        return False, 0

    def check_liquidity(self, symbol, amount_u):
        try:
            ob = exchange.fetch_order_book(symbol, limit=5)
            bids_u = sum([p * v for p, v in ob['bids'][:3]])
            asks_u = sum([p * v for p, v in ob['asks'][:3]])
            return (bids_u >= amount_u * 1.5 and asks_u >= amount_u * 1.5)
        except:
            return False

    def scan_single_coin(self, symbol):
        if symbol in self.blacklist: return
        if time.time() < self.tsunami_pause_until: return

        try:
            # 獲取價格與 ATR
            ticker = exchange.fetch_ticker(symbol)
            curr_p = ticker['last']
            atr = self.calculate_atr(symbol)
            if not atr: return

            # 策略：均值回歸 (過往1分鐘收盤 vs 現在)
            ohlcv_1m = exchange.fetch_ohlcv(symbol, '1m', limit=2)
            last_c = ohlcv_1m[0][4]
            move_ratio = abs(curr_p - last_c) / atr

            # 進場條件：超跌且波動比率達標
            if (curr_p < last_c) and (move_ratio >= 0.5):
                with TRADE_LOCK:
                    if symbol in self.active_symbols: return

                    # 再次確認 BTC 與 流動性
                    is_tsunami, _ = self.check_btc_tsunami()
                    if is_tsunami: return
                    if not self.check_liquidity(symbol, 6666): return

                    for fleet in self.fleets:
                        if len(fleet.active_trades) < 5 and move_ratio >= fleet.atr_mult:
                            trade = {
                                'symbol': symbol, 'entry_p': ticker['ask'],
                                'target_p': ticker['ask'] * (1 + (fleet.target_profit / fleet.order_size) + 0.002),
                                'entry_time': time.time()
                            }
                            fleet.active_trades.append(trade)
                            self.active_symbols.add(symbol)
                            logger.success(f"⚔️ {fleet.name} 進場 {symbol} | Ratio: {move_ratio:.2f}")
                            break
        except Exception as e:
            pass

    def monitor_all_fleets(self):
        active_list = [t['symbol'] for f in self.fleets for t in f.active_trades]
        if not active_list: return
        try:
            tickers = exchange.fetch_tickers(active_list)
            with TRADE_LOCK:
                for fleet in self.fleets:
                    for trade in fleet.active_trades[:]:
                        sym = trade['symbol']
                        if sym not in tickers: continue
                        curr_bid = tickers[sym]['bid']
                        elapsed = time.time() - trade['entry_time']
                        net_pnl = ((curr_bid - trade['entry_p']) * (fleet.order_size / trade['entry_p'])) - (
                                    fleet.order_size * fleet.fee_rate)

                        if curr_bid >= trade['target_p']:
                            fleet.log_trade(trade, "SUCCESS", elapsed, net_pnl)
                            fleet.active_trades.remove(trade)
                            self.active_symbols.discard(sym)
                        elif net_pnl <= -(fleet.order_size * fleet.stop_loss_pct):
                            fleet.log_trade(trade, "STOP_LOSS", elapsed, net_pnl)
                            fleet.active_trades.remove(trade)
                            self.active_symbols.discard(sym)
                        elif elapsed > 1800:
                            fleet.log_trade(trade, "TIMEOUT", elapsed, net_pnl)
                            fleet.active_trades.remove(trade)
                            self.active_symbols.discard(sym)
        except:
            pass

    def print_dashboard(self):
        """每輪刷新介面，讓你看到它真的在動"""
        os.system('cls' if os.name == 'nt' else 'clear')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        is_tsunami, btc_drop = self.check_btc_tsunami()
        tsunami_status = f"🔴 警告 (跌 {btc_drop * 100:.2f}%)" if is_tsunami else "🟢 正常"

        print(f"=== [3-Kingdoms] 草船借箭 v4.1.2 Dashboard ===")
        print(f"目前時間: {now} | 總掃描次數: {self.scan_count}")
        print(f"BTC 狀態: {tsunami_status} | 獵場幣種數: {len(self.top_20)}")
        print("-" * 50)
        for f in self.fleets:
            wr = (f.win_count / (f.win_count + f.loss_count) * 100) if (f.win_count + f.loss_count) > 0 else 0
            print(f"艦隊: {f.name:10} | 淨利: {f.total_pnl:8.2f} U | 勝率: {wr:5.1f}% | 持倉: {len(f.active_trades)}")
        print("-" * 50)
        if self.active_symbols:
            print(f"當前持倉: {', '.join(self.active_symbols)}")
        else:
            print("當前持倉: (等待進場訊號...)")
        print("==============================================")

    def run(self):
        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, 'w') as f: f.write("Time,Fleet,Symbol,Status,Elapsed,PnL\n")

        with ThreadPoolExecutor(max_workers=5) as executor:
            while True:
                try:
                    self.scan_count += 1
                    # 更新 Top 20
                    tk = exchange.fetch_tickers()
                    self.top_20 = sorted([s for s in tk if '/USDT' in s],
                                         key=lambda x: tk[x].get('quoteVolume', 0), reverse=True)[:20]

                    # 打印儀表板
                    self.print_dashboard()

                    # 執行掃描與監控
                    list(executor.map(self.scan_single_coin, self.top_20))
                    self.monitor_all_fleets()

                    time.sleep(2)  # 每兩秒一輪，確保 API 安全
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    time.sleep(5)


if __name__ == "__main__":
    MasterOrchestrator().run()