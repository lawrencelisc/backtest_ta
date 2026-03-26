import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.9 穩定雷達版)
# 更新：1. 強制指定現貨類別 (Spot) 2. 增加路徑探測寬度 3. 修復 -999% 顯示錯誤
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

        # 🧪 戰術參數
        self.fee_taker = 0.0006
        self.fee_maker = 0.0002
        self.jitter_range = (0.0002, 0.0008)

        self.paths = []
        self.all_time_best_roi = -999.0

        logger.info(f"🚀 3-Kingdom v1.9 [穩定雷達] 啟動 | 演技修正: {self.jitter_range}")

    def build_matrix(self, top_n=50):
        """構建三角矩陣 (優化 Spot 類別抓取)"""
        logger.info(f"🔍 正在搜尋全市場成交量前 {top_n} 名的獵物...")
        try:
            # 1. 強制指定類別為 spot，確保抓到現貨數據
            tickers = self.exchange.fetch_tickers(params={'category': 'spot'})
            spot_volumes = []
            for symbol, data in tickers.items():
                # 排除槓桿代幣與穩定幣對
                if '/USDT' in symbol and 'quoteVolume' in data:
                    base = symbol.split('/')[0]
                    if base in ['USDC', 'DAI', 'FDUSD', 'BUSD', 'RLUSD'] or 'DOWN' in base or 'UP' in base:
                        continue
                    spot_volumes.append({'symbol': symbol, 'volume': float(data['quoteVolume'])})

            if not spot_volumes:
                logger.error("❌ 無法獲取現貨成交量數據，請檢查網絡或 API 權限")
                return

            df_vol = pd.DataFrame(spot_volumes).sort_values(by='volume', ascending=False)
            top_base_symbols = df_vol.head(top_n)['symbol'].str.replace('/USDT', '').tolist()

            logger.info(f"🎯 已識別前 10 名活躍幣種: {', '.join(top_base_symbols[:10])}")

            # 2. 構建路徑節點
            markets = self.exchange.load_markets()
            nodes = {}
            for s, m in markets.items():
                if m.get('spot') and m.get('active'):
                    base, quote = m['base'], m['quote']
                    # 只要涉及 USDT、BTC、ETH 或 Top N 幣種的都納入節點
                    if quote in ['USDT', 'BTC', 'ETH'] or base in top_base_symbols:
                        if base not in nodes: nodes[base] = {}
                        nodes[base][quote] = ('SELL', s)
                        if quote not in nodes: nodes[quote] = {}
                        nodes[quote][base] = ('BUY', s)

            # 3. 搜尋路徑環路
            self.paths = []
            start = 'USDT'
            for a in nodes.get(start, {}):
                for b in nodes.get(a, {}):
                    if b in nodes and start in nodes[b]:
                        if a != b and a != start and b != start:
                            # 強化路徑：只要包含任何活躍幣種就納入
                            if a in top_base_symbols or b in top_base_symbols:
                                self.paths.append(
                                    [(start, a, nodes[start][a]), (a, b, nodes[a][b]), (b, start, nodes[b][start])])

            if not self.paths:
                logger.warning("⚠️ 獵場目前太過冷清，嘗試放寬門檻搜尋全場路徑...")
                # 若找不到活躍路徑，則不限制活躍幣種，抓取全場路徑作為備案
                for a in nodes.get(start, {}):
                    for b in nodes.get(a, {}):
                        if b in nodes and start in nodes[b]:
                            if a != b and a != start and b != start:
                                self.paths.append(
                                    [(start, a, nodes[start][a]), (a, b, nodes[a][b]), (b, start, nodes[b][start])])

            logger.success(f"✅ 矩陣重構完成，已鎖定 {len(self.paths)} 條搜尋路徑。")
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
        temp_taker, temp_maker = current_u, current_u
        prices, steps = [], []

        for i, step in enumerate(path):
            side, symbol = step[2]
            vwap = self.get_vwap_price(symbol, side, max(temp_taker, temp_maker))
            if not vwap: return -999, None, 0, [], [], -999

            eff_p = vwap * (1 + jitter) if side == 'BUY' else vwap * (1 - jitter)
            prices.append(eff_p)
            steps.append(f"{symbol}({side})")

            # Taker 費率
            fee_t = self.fee_taker
            if side == 'BUY':
                temp_taker = (temp_taker / eff_p) * (1 - fee_t)
            else:
                temp_taker = (temp_taker * eff_p) * (1 - fee_t)

            # 第一腿 Maker 費率
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

            if not self.paths:
                logger.warning("❌ 無可用路徑，正在重新初始化雷達...")
                self.build_matrix(top_n=50)
                time.sleep(5)
                continue

            sample = random.sample(self.paths, min(15, len(self.paths)))
            best_t, best_m = -99.0, -99.0  # 修復初始值顯示

            for path in sample:
                acc = random.choice(['Yamato', 'Dreadnought', 'Yukikaze'])
                roi_t, path_s, jitter, raw_p, steps, roi_m = self.simulate_strike(path, acc)

                if roi_t > best_t: best_t = roi_t
                if roi_m > best_m: best_m = roi_m

                if roi_m > -0.15:
                    self.save_log({
                        'timestamp': now, 'it': it, 'path': path_s, 'roi_taker': round(roi_t, 4),
                        'roi_maker': round(roi_m, 4), 'jitter': round(jitter, 6),
                        'p1': raw_p[0], 'p2': raw_p[1], 'p3': raw_p[2]
                    })

                if roi_t > 0:
                    self.virtual_wallet[acc] += (roi_t / 100) * (self.virtual_wallet[acc] / 4)
                    logger.success(f"🔥 成交! {path_s} | ROI: {roi_t:.4f}%")

            # 只有當真的有掃描到路徑時才更新紀錄
            if best_t > -90 and best_t > self.all_time_best_roi:
                self.all_time_best_roi = best_t
                logger.info(f"🏆 新紀錄 (Taker): {best_t:+.4f}% | (若首腿 Maker): {best_m:+.4f}%")

            if it % 10 == 0:
                logger.info(
                    f"📊 [輪次 {it}] Taker最高: {self.all_time_best_roi if self.all_time_best_roi > -90 else 0:+.4f}% | 資產: {sum(self.virtual_wallet.values()):.2f}U")
                if it % 300 == 0: self.build_matrix(top_n=50)
            else:
                print(f"📡 搜獵中... 輪次: {it} | 掃描路徑: {len(self.paths)}", end='\r')
            time.sleep(1.2)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()