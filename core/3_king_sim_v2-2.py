import ccxt
import time
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger


# =================================================================
# [3-Kingdom] 專案代號：三國演義 - 實戰模擬探測器 (v1.5 菁英搜獵版)
# 模式：24/7 零足跡偵察 (Public API)
# 更新：自動篩選全市場成交量前 20 (BEST 20) 的活躍路徑，避開過於成熟的 BTC/ETH 陷阱
# =================================================================

class ThreeKingdomsSim:
    def __init__(self, initial_capital=20000.0):
        # 1. 初始化路徑與目錄
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

        logger.info(f"🚀 3-Kingdom v1.5 [菁英搜獵] 啟動 | 數據存儲: {self.csv_path}")

    def build_matrix(self, top_n=20):
        """構建三角矩陣 (自動篩選成交量 Top N 的幣種)"""
        logger.info(f"🔍 正在偵察全市場成交量，篩選前 {top_n} 名精銳標的...")
        try:
            # 1. 獲取所有交易對的 Ticker
            tickers = self.exchange.fetch_tickers()

            # 2. 篩選 USDT 現貨並按成交量排序
            spot_volumes = []
            for symbol, data in tickers.items():
                if '/USDT' in symbol and 'quoteVolume' in data:
                    # 排除穩定幣對 (如 USDC/USDT) 以免浪費掃描資源
                    base = symbol.split('/')[0]
                    if base in ['USDC', 'DAI', 'FDUSD', 'BUSD']: continue

                    spot_volumes.append({
                        'symbol': symbol,
                        'volume': float(data['quoteVolume'])
                    })

            df_vol = pd.DataFrame(spot_volumes).sort_values(by='volume', ascending=False)
            top_coins = df_vol.head(top_n)['symbol'].tolist()
            top_base_symbols = [s.split('/')[0] for s in top_coins]

            logger.info(f"🏆 已鎖定 BEST {top_n} 幣種: {', '.join(top_base_symbols)}")

            # 3. 構建連接點
            markets = self.exchange.load_markets()
            nodes = {}
            for s, m in markets.items():
                # 只有涉及到 Top N 幣種或核心轉換幣 (BTC/ETH) 的路徑才被納入
                if m.get('spot') and m.get('active'):
                    base, quote = m['base'], m['quote']
                    if quote in ['USDT', 'BTC', 'ETH'] or base in top_base_symbols:
                        if base not in nodes: nodes[base] = {}
                        nodes[base][quote] = ('SELL', s)
                        if quote not in nodes: nodes[quote] = {}
                        nodes[quote][base] = ('BUY', s)

            # 4. 尋找環路 (USDT -> A -> B -> USDT)
            start = 'USDT'
            self.paths = []
            for a in nodes.get(start, {}):
                for b in nodes.get(a, {}):
                    if b in nodes and start in nodes[b]:
                        if a != b and a != start and b != start:
                            # 確保路徑中至少包含一個 Top N 幣種，增加波動利潤
                            if a in top_base_symbols or b in top_base_symbols:
                                self.paths.append([
                                    (start, a, nodes[start][a]),
                                    (a, b, nodes[a][b]),
                                    (b, start, nodes[b][start])
                                ])

            logger.success(f"✅ 精銳矩陣構建完成，發現 {len(self.paths)} 條高質量路徑。")
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
        # 默認篩選前 20 名幣種
        if not self.paths: self.build_matrix(top_n=20)
        iteration = 0

        while True:
            iteration += 1
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 隨機抽樣進行測試
            sample_size = min(15, len(self.paths))
            sample_paths = random.sample(self.paths, sample_size)

            best_roi_this_round = -999.0
            best_path_this_round = ""

            for path in sample_paths:
                account = random.choice(['Yamato', 'Dreadnought', 'Yukikaze'])
                roi, path_str, jitter = self.simulate_strike(path, account)

                if roi > best_roi_this_round:
                    best_roi_this_round = roi
                    best_path_this_round = path_str

                # 紀錄高品質路徑 (ROI > -0.2%)
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

                # 每 500 輪重新刷新一次 BEST 20 矩陣，適應市場熱點切換
                if iteration % 500 == 0:
                    self.build_matrix(top_n=20)
            else:
                print(f"📡 24/7 搜獵中... 輪次: {iteration} | 歷史最高: {self.all_time_best_roi:+.4f}%", end='\r')

            time.sleep(1.2)


if __name__ == "__main__":
    sim = ThreeKingdomsSim(initial_capital=20000.0)
    sim.run()