import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.2)
# 優化：增加 DEBUG 模式，顯示「最接近獲利」的路徑，防止視覺 Hang 機
# =================================================================

class ThreeKingdomsSim:
    def __init__(self, initial_capital=20000.0):
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.virtual_wallet = {
            'Yamato': initial_capital / 3,
            'Dreadnought': initial_capital / 3,
            'Yukikaze': initial_capital / 3
        }
        self.fee_rate = 0.0006  # 0.06% Taker
        self.jitter_range = (0.0005, 0.0022)
        self.paths = []

        logger.info(f"🚀 3-Kingdom 虛擬演兵啟動 | 初始本金: {initial_capital}U")

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

        for step in path:
            side, symbol = step[2]
            vwap_price = self.get_vwap_price(symbol, side, temp_u)
            if not vwap_price: return -999, None

            eff_price = vwap_price * (1 + jitter) if side == 'BUY' else vwap_price * (1 - jitter)
            if side == 'BUY':
                temp_u = (temp_u / eff_price) * (1 - self.fee_rate)
            else:
                temp_u = (temp_u * eff_price) * (1 - self.fee_rate)

        profit = temp_u - current_u
        roi = (profit / current_u) * 100
        return roi, profit

    def run(self):
        if not self.paths: self.build_matrix()
        iteration = 0

        while True:
            iteration += 1
            now = datetime.now().strftime('%H:%M:%S')

            # 抽樣路徑進行測試
            sample_size = min(15, len(self.paths))
            sample_paths = random.sample(self.paths, sample_size)

            best_roi_this_round = -999

            for path in sample_paths:
                account = random.choice(['Yamato', 'Dreadnought', 'Yukikaze'])
                roi, profit = self.simulate_strike(path, account)

                if roi > best_roi_this_round:
                    best_roi_this_round = roi

                if roi > 0:
                    self.virtual_wallet[account] += profit
                    logger.success(
                        f"🔥 成交! {path[0][1]}->{path[1][1]} | ROI: {roi:.4f}% | 錢包: {self.virtual_wallet[account]:.2f}")

            # 每 5 次輸出一次「最接近成功」的數據
            if iteration % 5 == 0:
                logger.info(f"📊 [第 {iteration} 輪] 最優路徑利潤: {best_roi_this_round:+.4f}% (目標: >0)")
            else:
                print(f"📡 掃描中... 輪次: {iteration} | 最佳嘗試: {best_roi_this_round:+.4f}%", end='\r')

            time.sleep(1.2)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()