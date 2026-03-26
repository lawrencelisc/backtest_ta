import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.1)
# 模式：零足跡偵察 (Public API Only)
# 優化：增加 PyCharm 環境下的心跳可見度
# =================================================================

class ThreeKingdomsSim:
    def __init__(self, initial_capital=20000.0):
        # 初始化 Bybit 公共接口
        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.capital = initial_capital
        self.virtual_wallet = {
            'Yamato': initial_capital / 3,
            'Dreadnought': initial_capital / 3,
            'Yukikaze': initial_capital / 3
        }

        # 戰術參數
        self.fee_rate = 0.0006  # 假設 Taker 0.06% (老散等級)
        self.jitter_range = (0.0005, 0.0022)  # 艦長要求的 0.05% - 0.22%
        self.paths = []

        logger.info(f"🚀 3-Kingdom 虛擬演兵啟動 | 初始本金: {initial_capital}U")
        logger.info(f"分遣艦隊預算：Yamato/Dreadnought/Yukikaze 各 {initial_capital / 3:.2f}U")

    def build_matrix(self):
        """構建三角矩陣 (USDT -> A -> B -> USDT)"""
        logger.info("📡 正在掃描全市場路徑...")
        try:
            markets = self.exchange.load_markets()
            nodes = {}
            for s, m in markets.items():
                # 確保是現貨且處於交易狀態
                if m['spot'] and m['active'] and m['quote'] in ['USDT', 'BTC', 'ETH']:
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
        """計算三層深度平均成交價 (VWAP)"""
        try:
            # 抓取 5 層深度，取前 3 層運算
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
        """模擬一次具備「微震」與「滑價」的真實交易"""
        current_u = self.virtual_wallet[account_name] / 4  # 隨機分拆後的單份預算
        jitter = random.uniform(*self.jitter_range)
        temp_u = current_u

        step_details = []
        for step in path:
            from_c, to_c, info = step
            side, symbol = info

            # 獲取三層深度 VWAP
            vwap_price = self.get_vwap_price(symbol, side, temp_u)
            if not vwap_price: return None  # 深度不足直接放棄

            # 加上「微震」影響
            effective_price = vwap_price * (1 + jitter) if side == 'BUY' else vwap_price * (1 - jitter)

            # 計算該步後剩餘資金 (扣除 Taker 費)
            if side == 'BUY':
                temp_u = (temp_u / effective_price) * (1 - self.fee_rate)
            else:
                temp_u = (temp_u * effective_price) * (1 - self.fee_rate)

            step_details.append(f"{symbol}({side})")

        profit = temp_u - current_u
        roi = (profit / current_u) * 100

        # 門檻：扣除成本後必須大於 0
        if roi > 0:
            self.virtual_wallet[account_name] += profit
            return {
                'Account': account_name,
                'Path': " -> ".join(step_details),
                'ROI %': round(roi, 4),
                'Profit U': round(profit, 4),
                'Jitter': f"{jitter:.4%}",
                'Balance': round(self.virtual_wallet[account_name], 2)
            }
        return None

    def run(self):
        if not self.paths: self.build_matrix()
        iteration = 0

        while True:
            iteration += 1
            now = datetime.now().strftime('%H:%M:%S')

            # --- 🚀 [優化點] 心跳監控 ---
            # 每 10 次循環強制印出一行 INFO，防止 PyCharm 顯示 Hang 機
            if iteration % 10 == 0:
                total_val = sum(self.virtual_wallet.values())
                logger.info(f"💓 艦隊巡航中 | 輪次: {iteration} | 總資產: {total_val:.2f} U | 狀態: 正常")
            else:
                print(f"📡 3-Kingdom 掃描中... 輪次: {iteration} | 總資產: {sum(self.virtual_wallet.values()):.2f} U",
                      end='\r')

            # 模擬偵察邏輯
            # 由於 Bybit Public API 有速率限制，我們每輪隨機抽樣進行精算
            sample_size = min(30, len(self.paths))
            sample_paths = random.sample(self.paths, sample_size)

            for path in sample_paths:
                # 語意化分配帳戶
                account = random.choice(['Yamato', 'Dreadnought', 'Yukikaze'])
                result = self.simulate_strike(path, account)

                if result:
                    print(f"\n" + "🔥" * 5 + " 模擬成交 (實時深度) " + "🔥" * 5)
                    print(pd.DataFrame([result]).to_string(index=False))
                    print("-" * 60)

            # 稍微加長等待，確保 API 連接穩定
            time.sleep(1.5)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()