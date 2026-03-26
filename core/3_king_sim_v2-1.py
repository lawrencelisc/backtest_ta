import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.4 雲端巡航版)
# 模式：24/7 零足跡偵察 (Public API)
# 功能：自動建立 result 目錄並將模擬戰報存為 CSV
# =================================================================

class ThreeKingdomsSim:
    def __init__(self, initial_capital=20000.0):
        # 1. 初始化路徑與目錄 (mkdir result)
        self.root_dir = Path(__file__).resolve().parent
        self.result_dir = self.root_dir / 'result'
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.result_dir / 'sim_battle_report.csv'

        # 2. 初始化交易所
        self.exchange = ccxt.bybit({'enableRateLimit': True})

        # 3. 初始化錢包與參數
        self.virtual_wallet = {
            'Yamato': initial_capital / 3,
            'Dreadnought': initial_capital / 3,
            'Yukikaze': initial_capital / 3
        }
        self.fee_rate = 0.0006  # 0.06% Taker
        self.jitter_range = (0.0005, 0.0022)
        self.paths = []

        # 搜獵紀錄儀
        self.all_time_best_roi = -999.0
        self.all_time_best_path = ""

        logger.info(f"🚀 3-Kingdom v1.4 [雲端巡航] 啟動 | 數據存儲: {self.csv_path}")

    def build_matrix(self):
        """構建三角矩陣 (USDT -> A -> B -> USDT)"""
        logger.info("📡 正在掃描全市場路徑...")
        try:
            markets = self.exchange.load_markets()
            nodes = {}
            for s, m in markets.items():
                if m.get('spot') and m.get('active') and m['quote'] in ['USDT', 'BTC', 'ETH']:
                    b, q = m['base'], m['quote']
                    if b not in nodes: nodes[b] = {}
                    nodes[b][q] = ('SELL', s)
                    if q not in nodes: nodes[q] = {}
                    nodes[q][b] = ('BUY', s)

            start = 'USDT'
            self.paths = []
            for a in nodes.get(start, {}):
                for b in nodes.get(a, {}):
                    if b in nodes and start in nodes[b]:
                        if a != b and a != start and b != start:
                            self.paths.append([
                                (start, a, nodes[start][a]),
                                (a, b, nodes[a][b]),
                                (b, start, nodes[b][start])
                            ])
            logger.success(f"✅ 矩陣構建完成，發現 {len(self.paths)} 條潛在路徑。")
        except Exception as e:
            logger.error(f"❌ 矩陣構建失敗: {e}")

    def get_vwap_price(self, symbol, side, amount_u):
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            book_side = 'asks' if side == 'BUY' else 'bids'
            levels = ob[book_side][:3]
            accum_u, accum_qty = 0, 0
            for p, v in levels:
                lev_u = p * v
                needed_u = amount_u - accum_u
                if needed_u <= 0: break
                take_u = min(lev_u, needed_u)
                accum_u += take_u
                accum_qty += (take_u / p)
            return accum_u / accum_qty if accum_u >= amount_u else None
        except:
            return None

    def simulate_strike(self, path, account_name):
        current_u = self.virtual_wallet[account_name] / 4
        jitter = random.uniform(*self.jitter_range)
        temp_u = current_u

        step_names = []
        for step in path:
            side, symbol = step[2]
            vwap_price = self.get_vwap_price(symbol, side, temp_u)
            # 🚀 [FIX] 修正此處回傳值，必須回傳 3 個值以符合 run() 中的解包需求
            if not vwap_price: return -999, None, 0.0

            step_names.append(symbol.replace('/USDT', ''))
            eff_price = vwap_price * (1 + jitter) if side == 'BUY' else vwap_price * (1 - jitter)
            if side == 'BUY':
                temp_u = (temp_u / eff_price) * (1 - self.fee_rate)
            else:
                temp_u = (temp_u * eff_price) * (1 - self.fee_rate)

        profit = temp_u - current_u
        roi = (profit / current_u) * 100
        return roi, " -> ".join(step_names), jitter

    def save_log_to_csv(self, data):
        """將紀錄持久化到 CSV 文件"""
        df = pd.DataFrame([data])
        file_exists = self.csv_path.exists()
        df.to_csv(self.csv_path, mode='a', header=not file_exists, index=False)

    def run(self):
        if not self.paths: self.build_matrix()
        iteration = 0

        while True:
            iteration += 1
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            sample_size = min(15, len(self.paths))
            sample_paths = random.sample(self.paths, sample_size)

            best_roi_this_round = -999.0
            best_path_this_round = ""

            for path in sample_paths:
                account = random.choice(['Yamato', 'Dreadnought', 'Yukikaze'])
                # 這裡解包需要 3 個值 (roi, path_str, jitter)
                roi, path_str, jitter = self.simulate_strike(path, account)

                if roi > best_roi_this_round:
                    best_roi_this_round = roi
                    best_path_this_round = path_str

                # 只要發現 ROI 高於 -0.2% 的不錯機會 (即使未正數) 也紀錄下來供分析
                if roi > -0.2 and path_str is not None:
                    log_entry = {
                        'timestamp': now,
                        'iteration': iteration,
                        'account': account,
                        'path': path_str,
                        'roi_pct': round(roi, 4),
                        'jitter': round(jitter, 6),
                        'wallet_balance': round(sum(self.virtual_wallet.values()), 2),
                        'is_hit': roi > 0
                    }
                    self.save_log_to_csv(log_entry)

                if roi > 0:
                    profit = (roi / 100) * (self.virtual_wallet[account] / 4)
                    self.virtual_wallet[account] += profit
                    logger.success(f"🔥 成交! {path_str} | ROI: {roi:.4f}% | 錢包: {self.virtual_wallet[account]:.2f}")

            # 更新歷史紀錄
            if best_roi_this_round > self.all_time_best_roi:
                self.all_time_best_roi = best_roi_this_round
                self.all_time_best_path = best_path_this_round
                logger.info(f"🏆 新紀錄: {self.all_time_best_path} ({self.all_time_best_roi:+.4f}%)")

            # 雲端巡航輸出
            if iteration % 10 == 0:
                total_val = sum(self.virtual_wallet.values())
                logger.info(f"📊 [輪次 {iteration}] 最高: {self.all_time_best_roi:+.4f}% | 資產: {total_val:.2f}U")
            else:
                print(f"📡 24/7 搜獵中... 輪次: {iteration} | 歷史最高: {self.all_time_best_roi:+.4f}%", end='\r')

            time.sleep(1.2)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()