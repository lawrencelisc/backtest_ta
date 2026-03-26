import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.8 擴張與策略對比版)
# 更新：1. 擴大掃描至 Top 50 2. 計算 Maker 策略潛在收益 3. 縮減演技損耗
# =================================================================

class ThreeKingdomsSim:
    def __init__(self, initial_capital=20000.0):
        self.root_dir = Path(__file__).resolve().parent
        self.result_dir = self.root_dir / 'result'
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.result_dir / 'sim_battle_report.csv'

        self.exchange = ccxt.bybit({'enableRateLimit': True})
        self.virtual_wallet = {'Yamato': initial_capital / 3, 'Dreadnought': initial_capital / 3,
                               'Yukikaze': initial_capital / 3}

        # 🧪 戰術調整
        self.fee_taker = 0.0006  # 0.06%
        self.fee_maker = 0.0002  # 模擬 Maker 費率 (0.02%)
        self.jitter_range = (0.0002, 0.0008)  # 縮減演技損耗至 0.02% - 0.08%

        self.paths = []
        self.all_time_best_roi = -999.0

        logger.info(f"🚀 3-Kingdom v1.8 [戰術擴張] 啟動 | 演技修正: {self.jitter_range}")

    def build_matrix(self, top_n=50):  # 擴張至 Top 50
        logger.info(f"🔍 正在搜尋全市場成交量前 {top_n} 名的獵物...")
        try:
            tickers = self.exchange.fetch_tickers()
            spot_volumes = []
            for symbol, data in tickers.items():
                if '/USDT' in symbol and 'quoteVolume' in data:
                    base = symbol.split('/')[0]
                    if base in ['USDC', 'DAI', 'FDUSD', 'BUSD', 'RLUSD']: continue
                    spot_volumes.append({'symbol': symbol, 'volume': float(data['quoteVolume'])})

            top_base_symbols = pd.DataFrame(spot_volumes).sort_values(by='volume', ascending=False).head(top_n)[
                'symbol'].str.replace('/USDT', '').tolist()

            markets = self.exchange.load_markets()
            nodes = {}
            for s, m in markets.items():
                if m.get('spot') and m.get('active'):
                    base, quote = m['base'], m['quote']
                    if quote in ['USDT', 'BTC', 'ETH'] or base in top_base_symbols:
                        if base not in nodes: nodes[base] = {}
                        nodes[base][quote] = ('SELL', s)
                        if quote not in nodes: nodes[quote] = {}
                        nodes[quote][base] = ('BUY', s)

            self.paths = []
            start = 'USDT'
            for a in nodes.get(start, {}):
                for b in nodes.get(a, {}):
                    if b in nodes and start in nodes[b]:
                        if a != b and a != start and b != start:
                            if a in top_base_symbols or b in top_base_symbols:
                                self.paths.append(
                                    [(start, a, nodes[start][a]), (a, b, nodes[a][b]), (b, start, nodes[b][start])])
            logger.success(f"✅ 矩陣重構完成，已鎖定 {len(self.paths)} 條路徑 (含 Top 50 幣種)。")
        except Exception as e:
            logger.error(f"❌ 矩陣構建失敗: {e}")

    def get_vwap_price(self, symbol, side, amount_u):
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=5)
            book_side = 'asks' if side == 'BUY' else 'bids'
            levels = ob[book_side][:3]
            acc_u, acc_qty = 0, 0
            for p, v in levels:
                lev_u = p * v
                needed_u = amount_u - acc_u
                if needed_u <= 0: break
                take_u = min(lev_u, needed_u)
                acc_u += take_u
                acc_qty += (take_u / p)
            return acc_u / acc_qty if acc_u >= amount_u else None
        except:
            return None

    def simulate_strike(self, path, account_name):
        current_u = self.virtual_wallet[account_name] / 4
        jitter = random.uniform(*self.jitter_range)

        # 1. 計算純 Taker ROI (現有邏輯)
        temp_taker = current_u
        # 2. 計算 Leg 1 Maker ROI (優化邏輯)
        temp_maker = current_u

        prices, steps = [], []

        for i, step in enumerate(path):
            side, symbol = step[2]
            vwap = self.get_vwap_price(symbol, side, max(temp_taker, temp_maker))
            if not vwap: return -999, None, 0, [], [], -999

            eff_p = vwap * (1 + jitter) if side == 'BUY' else vwap * (1 - jitter)
            prices.append(eff_p)
            steps.append(f"{symbol}({side})")

            # Taker 計算
            fee_t = self.fee_taker
            if side == 'BUY':
                temp_taker = (temp_taker / eff_p) * (1 - fee_t)
            else:
                temp_taker = (temp_taker * eff_p) * (1 - fee_t)

            # Maker 計算 (僅針對第一腿 i==0)
            fee_m = self.fee_maker if i == 0 else self.fee_taker
            if side == 'BUY':
                temp_maker = (temp_maker / eff_p) * (1 - fee_m)
            else:
                temp_maker = (temp_maker * eff_p) * (1 - fee_m)

        roi_t = ((temp_taker - current_u) / current_u) * 100
        roi_m = ((temp_maker - current_u) / current_u) * 100
        return roi_t, " -> ".join(steps), jitter, prices, steps, roi_m

    def save_log(self, data):
        df = pd.DataFrame([data])
        df.to_csv(self.csv_path, mode='a', header=not self.csv_path.exists(), index=False)

    def run(self):
        if not self.paths: self.build_matrix(top_n=50)
        it = 0
        while True:
            it += 1
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            sample = random.sample(self.paths, min(15, len(self.paths)))

            best_t, best_m = -999.0, -999.0

            for path in sample:
                acc = random.choice(['Yamato', 'Dreadnought', 'Yukikaze'])
                roi_t, path_s, jitter, raw_p, steps, roi_m = self.simulate_strike(path, acc)

                if roi_t > best_t: best_t = roi_t
                if roi_m > best_m: best_m = roi_m

                if roi_m > -0.15:  # 只要 Maker 策略接近回本就紀錄
                    self.save_log({
                        'timestamp': now, 'it': it, 'path': path_s, 'roi_taker': round(roi_t, 4),
                        'roi_maker': round(roi_m, 4), 'jitter': round(jitter, 6),
                        'p1': raw_p[0], 'p2': raw_p[1], 'p3': raw_p[2]
                    })

                if roi_t > 0:
                    self.virtual_wallet[acc] += (roi_t / 100) * (self.virtual_wallet[acc] / 4)
                    logger.success(f"🔥 成交! {path_s} | ROI: {roi_t:.4f}%")

            if best_t > self.all_time_best_roi:
                self.all_time_best_roi = best_t
                logger.info(f"🏆 新紀錄 (Taker): {best_t:+.4f}% | (若首腿 Maker): {best_m:+.4f}%")

            if it % 10 == 0:
                logger.info(
                    f"📊 [輪次 {it}] Taker最高: {self.all_time_best_roi:+.4f}% | 資產: {sum(self.virtual_wallet.values()):.2f}U")
                if it % 300 == 0: self.build_matrix(top_n=50)
            else:
                print(f"📡 搜獵中 (Top 50)... 輪次: {it} | Taker最高: {self.all_time_best_roi:+.4f}%", end='\r')
            time.sleep(1.2)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()