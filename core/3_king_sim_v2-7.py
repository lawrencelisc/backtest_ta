import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.7 數據收割版)
# 模式：24/7 零足跡偵察 (Public API)
# 更新：增加深層係數收割 (Raw vs Effective Price)，用於離線獲利歸因分析
# =================================================================

class ThreeKingdomsSim:
    def __init__(self, initial_capital=20000.0):
        # 1. 初始化路徑與目錄 (Ensure result folder exists)
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
        self.fee_rate = 0.0006  # 0.06% Taker Fee
        self.jitter_range = (0.0005, 0.0022)
        self.paths = []

        # 搜獵紀錄儀
        self.all_time_best_roi = -999.0
        self.all_time_best_path = ""

        logger.info(f"🚀 3-Kingdom v1.7 [數據收割] 啟動 | 數據存儲: {self.csv_path}")

    def build_matrix(self, top_n=20):
        """構建三角矩陣 (自動篩選成交量 Top N 的幣種)"""
        logger.info(f"🔍 偵察全市場成交量，篩選前 {top_n} 名標的...")
        try:
            tickers = self.exchange.fetch_tickers()
            spot_volumes = []
            for symbol, data in tickers.items():
                if '/USDT' in symbol and 'quoteVolume' in data:
                    base = symbol.split('/')[0]
                    if base in ['USDC', 'DAI', 'FDUSD', 'BUSD']: continue
                    spot_volumes.append({'symbol': symbol, 'volume': float(data['quoteVolume'])})

            df_vol = pd.DataFrame(spot_volumes).sort_values(by='volume', ascending=False)
            top_coins = df_vol.head(top_n)['symbol'].tolist()
            top_base_symbols = [s.split('/')[0] for s in top_coins]

            logger.info(f"🏆 已鎖定 BEST 20 幣種: {', '.join(top_base_symbols)}")

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

            start = 'USDT'
            self.paths = []
            for a in nodes.get(start, {}):
                for b in nodes.get(a, {}):
                    if b in nodes and start in nodes[b]:
                        if a != b and a != start and b != start:
                            if a in top_base_symbols or b in top_base_symbols:
                                self.paths.append([
                                    (start, a, nodes[start][a]),
                                    (a, b, nodes[a][b]),
                                    (b, start, nodes[b][start])
                                ])
            logger.success(f"✅ 矩陣構建完成，發現 {len(self.paths)} 條高質量路徑。")
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
        """
        模擬執行並返回完整分析係數
        """
        current_u = self.virtual_wallet[account_name] / 4
        jitter = random.uniform(*self.jitter_range)
        temp_u = current_u

        raw_prices = []  # 市場原始 VWAP
        eff_prices = []  # 微震後的執行價
        step_names = []

        for step in path:
            side, symbol = step[2]
            vwap_price = self.get_vwap_price(symbol, side, temp_u)
            if not vwap_price: return -999, None, 0.0, [], []

            raw_prices.append(vwap_price)
            # 實施微震演技 (買高賣低)
            eff_price = vwap_price * (1 + jitter) if side == 'BUY' else vwap_price * (1 - jitter)
            eff_prices.append(eff_price)
            step_names.append(f"{symbol}({side})")

            if side == 'BUY':
                temp_u = (temp_u / eff_price) * (1 - self.fee_rate)
            else:
                temp_u = (temp_u * eff_price) * (1 - self.fee_rate)

        profit = temp_u - current_u
        roi = (profit / current_u) * 100
        return roi, " -> ".join(step_names), jitter, raw_prices, eff_prices

    def save_log_to_csv(self, data):
        """將詳細紀錄存儲為 CSV 以供後續分析"""
        df = pd.DataFrame([data])
        file_exists = self.csv_path.exists()
        df.to_csv(self.csv_path, mode='a', header=not file_exists, index=False)

    def run(self):
        if not self.paths: self.build_matrix(top_n=20)
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
                roi, path_str, jitter, raw_p, eff_p = self.simulate_strike(path, account)

                if roi > best_roi_this_round:
                    best_roi_this_round = roi
                    best_path_this_round = path_str

                # 紀錄門檻放寬至 -0.5% 以收集分析樣本
                if roi > -0.5 and path_str is not None:
                    log_entry = {
                        'timestamp': now,
                        'it': iteration,
                        'account': account,
                        'path': path_str,
                        'roi_pct': round(roi, 6),
                        'jitter': round(jitter, 6),
                        'raw_p1': raw_p[0], 'raw_p2': raw_p[1], 'raw_p3': raw_p[2],
                        'eff_p1': eff_p[0], 'eff_p2': eff_p[1], 'eff_p3': eff_p[2],
                        'wallet_total': round(sum(self.virtual_wallet.values()), 2)
                    }
                    self.save_log_to_csv(log_entry)

                if roi > 0:
                    profit = (roi / 100) * (self.virtual_wallet[account] / 4)
                    self.virtual_wallet[account] += profit
                    logger.success(f"🔥 成交! {path_str} | ROI: {roi:.4f}% | 錢包: {self.virtual_wallet[account]:.2f}")

            if best_roi_this_round > self.all_time_best_roi:
                self.all_time_best_roi = best_roi_this_round
                self.all_time_best_path = best_path_this_round
                logger.info(f"🏆 新紀錄: {self.all_time_best_path} ({self.all_time_best_roi:+.4f}%)")

            if iteration % 10 == 0:
                logger.info(
                    f"📊 [輪次 {iteration}] 最高: {self.all_time_best_roi:+.4f}% | 資產: {sum(self.virtual_wallet.values()):.2f}U")
                if iteration % 500 == 0: self.build_matrix(top_n=20)
            else:
                print(f"📡 搜獵中... 輪次: {iteration} | 全場最高: {self.all_time_best_roi:+.4f}%", end='\r')

            time.sleep(1.2)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()