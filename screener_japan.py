"""
screener_japan.py
─────────────────────────────────────────────────────────────
日本株スクリーナー（連続出現追跡機能付き）

【データソース】
  - 日経電子版ランキングページ（__NEXT_DATA__ JSON）
  - yfinance（財務補完：時価総額・PBR・PER・配当利回り・52週高値比）

【スクリーニング軸とスコア】
  値上がり率上位     +3pt
  出来高急増率上位   +3pt
  低PBR上位          +2pt
  低PER上位          +2pt
  年初来高値更新     +2pt
  売買代金上位       +1pt

【連続出現追跡】
  screener_history.json に日次データを自動保存・追記
  直近 HISTORY_WINDOW 日間の出現回数をHTMLに表示

【フィルター】
  時価総額 500億円以上

【出力】
  screener_japan.html（同ディレクトリに生成）
  screener_history.json（同ディレクトリに自動生成・追記）

【実行方法】
  pip install yfinance
  python3 screener_japan.py

【推奨タイミング】
  東証引け後（15:30以降）に実行すると当日データが反映される
─────────────────────────────────────────────────────────────
"""

import urllib.request, json, re, yfinance as yf, os
from collections import defaultdict
from datetime import datetime, date, timedelta

# ══════════════════════════════════════════════
# 設定（ここを変更してカスタマイズ）
# ══════════════════════════════════════════════

MARKET_CAP_MIN  = 500          # 時価総額フィルター（億円）
HISTORY_WINDOW  = 5            # 連続出現を見る日数
HISTORY_FILE    = "screener_history.json"
OUTPUT_FILE     = "screener_japan.html"

TAG_MAP = {
    "price-rise":             ("値上がり",     3),
    "spike-in-trading-value": ("出来高急増",   3),
    "pbr-low":                ("低PBR",        2),
    "per-low":                ("低PER",        2),
    "year-high":              ("年初来高値",    2),
    "trading-value":          ("売買代金上位",  1),
}

# ══════════════════════════════════════════════
# 1. 日経ランキング取得
# ══════════════════════════════════════════════

def fetch_nikkei_ranking():
    url = "https://www.nikkei.com/marketdata/ranking-jp/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r:
        html = r.read().decode("utf-8")
    m = re.search(r'__NEXT_DATA__" type="application/json">({.*?})</script>', html, re.DOTALL)
    if not m:
        raise RuntimeError("日経ランキングのJSONが取得できませんでした")
    data = json.loads(m.group(1))
    return data['props']['pageProps']['rankingDataMap']

# ══════════════════════════════════════════════
# 2. スコアリング
# ══════════════════════════════════════════════

def build_scores(ranking):
    scores = defaultdict(lambda: {"name": "", "score": 0, "tags": [], "values": {}})
    for cat, (tag_label, weight) in TAG_MAP.items():
        if cat not in ranking:
            continue
        for item in ranking[cat]["data"]:
            code = item["stockCode"]
            scores[code]["name"]   = item["stockName"]
            scores[code]["score"] += weight
            scores[code]["tags"].append(tag_label)
            scores[code]["values"][cat] = item["stockValues"]
    return sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)

# ══════════════════════════════════════════════
# 3. 履歴の読み書き
# ══════════════════════════════════════════════

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_history(history, today_str, codes):
    history[today_str] = codes
    # HISTORY_WINDOW * 3 日分を超えたら古いものを削除
    cutoff = (date.today() - timedelta(days=HISTORY_WINDOW * 3)).isoformat()
    history = {k: v for k, v in history.items() if k >= cutoff}
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return history

def count_appearances(history, today_str, code):
    """直近 HISTORY_WINDOW 営業日（今日を除く）での出現回数を返す"""
    past_dates = sorted([d for d in history.keys() if d < today_str], reverse=True)
    window = past_dates[:HISTORY_WINDOW]
    return sum(1 for d in window if code in history.get(d, []))

# ══════════════════════════════════════════════
# 4. yfinance 財務補完
# ══════════════════════════════════════════════

def enrich_with_yfinance(candidates, history, today_str):
    results = []
    for code, info in candidates:
        ticker_sym = f"{code}.T" if code.isdigit() else None
        row = {
            "code":         code,
            "name":         info["name"],
            "score":        info["score"],
            "tags":         info["tags"],
            "price":        None,
            "market_cap_b": None,
            "pbr":          None,
            "per":          None,
            "div_yield":    None,
            "change_pct":   None,
            "week52_ratio": None,
            "appearances":  count_appearances(history, today_str, code),
        }

        # 日経から直接取れる値
        if "price-rise" in info["values"]:
            v = info["values"]["price-rise"]
            if len(v) > 1:
                try: row["change_pct"] = float(v[1])
                except: pass
        if "pbr-low" in info["values"]:
            try: row["pbr"] = float(info["values"]["pbr-low"][0])
            except: pass
        if "per-low" in info["values"]:
            try: row["per"] = float(info["values"]["per-low"][0])
            except: pass

        # yfinance補完
        if ticker_sym:
            try:
                inf = yf.Ticker(ticker_sym).info
                price = inf.get("currentPrice") or inf.get("regularMarketPrice")
                mc    = inf.get("marketCap")
                h52   = inf.get("fiftyTwoWeekHigh")
                if price: row["price"]        = price
                if mc:    row["market_cap_b"] = mc / 1e8
                if inf.get("priceToBook") and row["pbr"] is None:
                    row["pbr"] = round(inf["priceToBook"], 2)
                if inf.get("trailingPE") and row["per"] is None:
                    row["per"] = round(inf["trailingPE"], 1)
                if inf.get("dividendYield"):
                    row["div_yield"] = round(inf["dividendYield"] * 100, 2)
                if price and h52:
                    row["week52_ratio"] = round((price / h52 - 1) * 100, 1)
                if inf.get("regularMarketChangePercent") and row["change_pct"] is None:
                    row["change_pct"] = round(inf["regularMarketChangePercent"], 2)
            except Exception:
                pass

        results.append(row)

    # 時価総額フィルター（データなしは通過させる）
    return [r for r in results if r["market_cap_b"] is None or r["market_cap_b"] >= MARKET_CAP_MIN]

# ══════════════════════════════════════════════
# 5. HTML 生成
# ══════════════════════════════════════════════

TAG_COLORS = {
    "値上がり":     ("#d4480a", "#fff3ee"),
    "出来高急増":   ("#b8360a", "#fff0ea"),
    "低PBR":        ("#1a6fd4", "#eef4ff"),
    "低PER":        ("#1a55a8", "#e8f0ff"),
    "年初来高値":   ("#1a9e60", "#edfaf3"),
    "売買代金上位": ("#7a5c1a", "#fdf6e3"),
}

def tag_html(tags):
    parts = []
    for t in tags:
        c, bg = TAG_COLORS.get(t, ("#555", "#eee"))
        parts.append(f'<span class="tag" style="color:{c};background:{bg};border-color:{c}30">{t}</span>')
    return "".join(parts)

def score_bars(s, max_s=6):
    filled = min(s, max_s)
    return (f'<span class="score-bars">{"▮"*filled}{"▯"*(max_s-filled)}</span>'
            f' <span class="score-num">{s}pt</span>')

def appearance_badge(n):
    """連続出現バッジ。0回=初登場、1〜2回=注目、3回以上=継続"""
    if n == 0:
        return '<span class="ap ap-new">NEW</span>'
    elif n <= 2:
        return f'<span class="ap ap-watch">{n}日</span>'
    else:
        return f'<span class="ap ap-hot">🔴 {n}日</span>'

def fmt(val, template, fallback='<span class="na">—</span>'):
    return fallback if val is None else template.format(val)

def fmt_signed(val, decimals=2, suffix="%"):
    if val is None: return '<span class="na">—</span>'
    cls  = "pos" if val >= 0 else "neg"
    sign = "+" if val >= 0 else ""
    return f'<span class="{cls}">{sign}{val:.{decimals}f}{suffix}</span>'

def fmt_mc(v):
    if v is None: return '<span class="na">—</span>'
    return f"{v/10000:.1f}兆" if v >= 10000 else f"{v:,.0f}億"

def build_rows(filtered):
    rows = ""
    for i, r in enumerate(filtered):
        rows += f"""
    <tr class="stock-row">
      <td class="rank-cell">#{i+1}</td>
      <td class="code-cell">
        <div class="stock-code">{r['code']}</div>
        <div class="stock-name">{r['name']}</div>
      </td>
      <td class="score-cell">{score_bars(r['score'])}</td>
      <td class="ap-cell">{appearance_badge(r['appearances'])}</td>
      <td class="tags-cell">{tag_html(r['tags'])}</td>
      <td class="num-cell">{fmt(r['price'], '¥{:,.0f}')}</td>
      <td class="num-cell">{fmt_signed(r['change_pct'])}</td>
      <td class="num-cell">{fmt_mc(r['market_cap_b'])}</td>
      <td class="num-cell">{fmt(r['pbr'],  '{:.2f}x')}</td>
      <td class="num-cell">{fmt(r['per'],  '{:.1f}x')}</td>
      <td class="num-cell">{fmt(r['div_yield'], '{:.2f}%')}</td>
      <td class="num-cell">{fmt_signed(r['week52_ratio'], decimals=1)}</td>
    </tr>"""
    return rows

def build_legend_items():
    items = ""
    weights = {
        "値上がり":     ("当日値上がり率ランキング上位",  "+3pt"),
        "出来高急増":   ("売買代金急増率ランキング上位",  "+3pt"),
        "低PBR":        ("PBR低位（解散価値割れ圏）",     "+2pt"),
        "低PER":        ("PER低位（割安益回り）",          "+2pt"),
        "年初来高値":   ("年初来高値更新銘柄",             "+2pt"),
        "売買代金上位": ("当日売買代金ランキング上位",     "+1pt"),
    }
    for tag, (desc, pt) in weights.items():
        c, bg = TAG_COLORS[tag]
        items += f"""
    <div class="legend-item">
      <span class="tag" style="color:{c};background:{bg};border-color:{c}30">{tag}</span>
      <span>{pt}：{desc}</span>
    </div>"""
    return items

def build_history_summary(history, today_str):
    """過去HISTORY_WINDOW日間で複数回出現した銘柄サマリー"""
    counter = defaultdict(lambda: {"name": "", "count": 0})
    past_dates = sorted([d for d in history.keys() if d <= today_str], reverse=True)[:HISTORY_WINDOW]
    for d in past_dates:
        for entry in history.get(d, []):
            code = entry if isinstance(entry, str) else entry
            counter[code]["count"] += 1

    # 2回以上出現した銘柄を出現回数順に並べる
    repeats = [(c, v) for c, v in counter.items() if v["count"] >= 2]
    repeats.sort(key=lambda x: x[1]["count"], reverse=True)

    if not repeats:
        return '<p class="no-repeat">直近5日間で複数回出現した銘柄はありません</p>'

    items = ""
    for code, v in repeats:
        dot_color = "#c93030" if v["count"] >= 3 else "#b8922a"
        items += f'<span class="repeat-chip" style="border-color:{dot_color}30;"><span class="repeat-dot" style="background:{dot_color}"></span>{code} <span class="repeat-count">{v["count"]}回</span></span>'
    return items

def generate_html(filtered, history, today_str, source_dt):
    dt_str  = datetime.now().strftime("%Y-%m-%d %H:%M")
    top     = filtered[0] if filtered else {}
    hot     = sum(1 for r in filtered if r["appearances"] >= 3)
    rows    = build_rows(filtered)
    legend  = build_legend_items()
    hist_summary = build_history_summary(history, today_str)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日本株スクリーナー｜{dt_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;600&family=Bebas+Neue&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#ffffff; --surface:#f8f9fb; --surface2:#f0f2f6;
  --border:#e2e6ef; --text:#3a4255; --text-dim:#9aa3b8; --text-mid:#6b7592; --text-bright:#111827;
  --accent:#b8922a; --accent2:#1a6fd4; --pos:#1a9e60; --neg:#c93030;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Noto Sans JP',sans-serif; font-weight:300; min-height:100vh; }}
body::before {{
  content:''; position:fixed; inset:0;
  background-image:linear-gradient(rgba(26,111,212,0.025) 1px,transparent 1px),linear-gradient(90deg,rgba(26,111,212,0.025) 1px,transparent 1px);
  background-size:48px 48px; pointer-events:none; z-index:0;
}}
.page {{ max-width:1280px; margin:0 auto; padding:52px 24px 80px; position:relative; z-index:1; }}
header {{ margin-bottom:36px; border-left:4px solid var(--accent); padding-left:20px; animation:fadeSlide 0.6s ease both; }}
.h-meta {{ display:flex; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }}
.h-badge {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.2em; padding:3px 10px; border-radius:2px; }}
.b-live {{ background:rgba(26,158,96,0.1); color:var(--pos); border:1px solid rgba(26,158,96,0.25); }}
.b-src  {{ background:rgba(184,146,42,0.1); color:var(--accent); border:1px solid rgba(184,146,42,0.25); }}
.b-dt   {{ background:var(--surface2); color:var(--text-dim); border:1px solid var(--border); }}
h1 {{ font-family:'Bebas Neue',sans-serif; font-size:clamp(36px,6vw,64px); letter-spacing:0.04em; color:var(--text-bright); line-height:0.95; margin-bottom:10px; }}
h1 span {{ color:var(--accent); }}
.h-sub {{ font-size:12px; color:var(--text-mid); line-height:1.8; }}
.summary {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-bottom:28px; }}
.s-card {{ background:var(--surface); border:1px solid var(--border); border-radius:3px; padding:16px; position:relative; overflow:hidden; }}
.s-card::before {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; border-radius:3px 3px 0 0; }}
.s-card.c1::before {{ background:var(--neg); }}
.s-card.c2::before {{ background:var(--accent); }}
.s-card.c3::before {{ background:var(--accent2); }}
.s-card.c4::before {{ background:var(--pos); }}
.s-card.c5::before {{ background:#7a5c1a; }}
.s-label {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.18em; color:var(--text-dim); margin-bottom:8px; }}
.s-val   {{ font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:600; color:var(--text-bright); }}
.s-desc  {{ font-size:11px; color:var(--text-mid); margin-top:5px; line-height:1.5; }}

/* 連続出現サマリー */
.history-box {{ background:var(--surface); border:1px solid var(--border); border-radius:3px; padding:16px 20px; margin-bottom:28px; }}
.history-title {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.2em; color:var(--text-dim); margin-bottom:12px; }}
.repeat-chip {{
  display:inline-flex; align-items:center; gap:6px;
  font-family:'IBM Plex Mono',monospace; font-size:12px;
  padding:4px 10px; border-radius:2px; border:1px solid;
  margin:3px 5px 3px 0; background:var(--bg);
  color:var(--text-bright);
}}
.repeat-dot {{ width:7px; height:7px; border-radius:50%; flex-shrink:0; }}
.repeat-count {{ font-size:10px; color:var(--text-dim); }}
.no-repeat {{ font-size:12px; color:var(--text-dim); }}

/* テーブル */
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
thead tr {{ background:var(--surface2); }}
th {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.15em; color:var(--text-dim); padding:10px 12px; text-align:left; white-space:nowrap; border-bottom:2px solid var(--border); }}
th.r {{ text-align:right; }}
.stock-row {{ border-bottom:1px solid var(--border); transition:background 0.15s; }}
.stock-row:hover {{ background:var(--surface); }}
td {{ padding:12px 12px; vertical-align:middle; }}
.rank-cell {{ font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); white-space:nowrap; }}
.code-cell {{ white-space:nowrap; min-width:90px; }}
.stock-code {{ font-family:'IBM Plex Mono',monospace; font-size:15px; font-weight:600; color:var(--text-bright); }}
.stock-name {{ font-size:11px; color:var(--text-mid); margin-top:2px; }}
.score-cell {{ white-space:nowrap; min-width:120px; }}
.score-bars {{ font-family:'IBM Plex Mono',monospace; font-size:13px; color:var(--accent); letter-spacing:1px; }}
.score-num  {{ font-family:'IBM Plex Mono',monospace; font-size:10px; color:var(--text-dim); margin-left:4px; }}
.ap-cell {{ white-space:nowrap; }}
.ap {{
  font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.1em;
  padding:3px 8px; border-radius:2px; white-space:nowrap;
}}
.ap-new   {{ background:rgba(26,111,212,0.08); color:var(--accent2); border:1px solid rgba(26,111,212,0.2); }}
.ap-watch {{ background:rgba(184,146,42,0.08); color:var(--accent); border:1px solid rgba(184,146,42,0.25); }}
.ap-hot   {{ background:rgba(201,48,48,0.08); color:var(--neg); border:1px solid rgba(201,48,48,0.2); font-weight:500; }}
.tags-cell {{ min-width:150px; }}
.tag {{ display:inline-block; font-size:10px; font-weight:500; padding:2px 7px; border-radius:2px; border:1px solid; margin:2px 3px 2px 0; white-space:nowrap; }}
.num-cell {{ text-align:right; white-space:nowrap; font-family:'IBM Plex Mono',monospace; font-size:13px; }}
.na {{ color:var(--text-dim); }}
.pos {{ color:var(--pos); }}
.neg {{ color:var(--neg); }}
.legend {{ margin-top:24px; background:var(--surface); border:1px solid var(--border); border-radius:3px; padding:16px 20px; }}
.legend-title {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.2em; color:var(--text-dim); margin-bottom:12px; }}
.legend-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
.legend-item {{ font-size:11px; color:var(--text-mid); display:flex; gap:8px; align-items:flex-start; }}
footer {{ margin-top:32px; padding-top:14px; border-top:1px solid var(--border); font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.12em; color:var(--text-dim); display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px; }}
@keyframes fadeSlide {{ from{{opacity:0;transform:translateY(10px)}} to{{opacity:1;transform:translateY(0)}} }}
@media(max-width:768px) {{ .summary{{grid-template-columns:1fr 1fr;}} .legend-grid{{grid-template-columns:1fr 1fr;}} }}
</style>
</head>
<body>
<div class="page">
<header>
  <div class="h-meta">
    <span class="h-badge b-live">● LIVE DATA</span>
    <span class="h-badge b-src">SOURCE: 日経電子版 + yfinance</span>
    <span class="h-badge b-dt">生成: {dt_str}</span>
  </div>
  <h1>日本株<span>スクリーナー</span></h1>
  <p class="h-sub">
    日経ランキング（値上がり率 × 出来高急増 × 低PBR/PER × 年初来高値）を複合スコアリング。時価総額{MARKET_CAP_MIN}億円以上フィルター。<br>
    データソース更新: {source_dt} ／ 連続出現ウィンドウ: 直近{HISTORY_WINDOW}営業日
  </p>
</header>

<div class="summary">
  <div class="s-card c1">
    <div class="s-label">TOTAL HITS</div>
    <div class="s-val">{len(filtered)}</div>
    <div class="s-desc">スクリーニング通過銘柄数</div>
  </div>
  <div class="s-card c2">
    <div class="s-label">TOP SCORE</div>
    <div class="s-val">{top.get('score', 0)}pt</div>
    <div class="s-desc">{top.get('name','—')} ({top.get('code','—')})</div>
  </div>
  <div class="s-card c3">
    <div class="s-label">SCREENING AXES</div>
    <div class="s-val">{len(TAG_MAP)}</div>
    <div class="s-desc">値上がり・出来高・PBR・PER・高値・売買代金</div>
  </div>
  <div class="s-card c4">
    <div class="s-label">🔴 HOT（3日以上）</div>
    <div class="s-val">{hot}</div>
    <div class="s-desc">直近{HISTORY_WINDOW}日間で3回以上出現</div>
  </div>
  <div class="s-card c5">
    <div class="s-label">HISTORY DAYS</div>
    <div class="s-val">{len([d for d in history.keys() if d <= today_str])}</div>
    <div class="s-desc">蓄積済み営業日数</div>
  </div>
</div>

<div class="history-box">
  <div class="history-title">REPEAT WATCH — 直近{HISTORY_WINDOW}日間で複数回出現した銘柄</div>
  {hist_summary}
</div>

<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th></th><th>コード / 銘柄</th><th>スコア</th><th>連続出現</th><th>該当タグ</th>
      <th class="r">株価</th><th class="r">騰落率</th><th class="r">時価総額</th>
      <th class="r">PBR</th><th class="r">PER</th><th class="r">配当利回り</th><th class="r">52週高値比</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</div>

<div class="legend">
  <div class="legend-title">SCORING LOGIC ／ 連続出現バッジ凡例</div>
  <div class="legend-grid">
    {legend}
    <div class="legend-item"><span class="ap ap-new">NEW</span> 初登場（過去{HISTORY_WINDOW}日に出現なし）</div>
    <div class="legend-item"><span class="ap ap-watch">N日</span> 直近{HISTORY_WINDOW}日間で1〜2回出現（注目）</div>
    <div class="legend-item"><span class="ap ap-hot">🔴 N日</span> 直近{HISTORY_WINDOW}日間で3回以上出現（継続シグナル）</div>
  </div>
</div>

<footer>
  <span>PERSONAL RESEARCH TOOL — NOT INVESTMENT ADVICE</span>
  <span>日経電子版 + yfinance | {dt_str}</span>
</footer>
</div>
</body>
</html>"""

# ══════════════════════════════════════════════
# main
# ══════════════════════════════════════════════

if __name__ == "__main__":
    today_str = date.today().isoformat()
    print(f"日付: {today_str}")

    print("日経ランキング取得中...")
    ranking   = fetch_nikkei_ranking()
    source_dt = ranking.get("price-rise", {}).get("lastupdate", "—").replace("T", " ")

    print("スコアリング中...")
    candidates = build_scores(ranking)

    print("履歴を読み込み中...")
    history = load_history()

    print(f"yfinance補完中（{len(candidates)}銘柄）...")
    filtered = enrich_with_yfinance(candidates, history, today_str)

    print("履歴を保存中...")
    all_codes = [code for code, _ in candidates]
    history   = save_history(history, today_str, all_codes)

    print(f"HTML生成中（{len(filtered)}銘柄がフィルター通過）...")
    html = generate_html(filtered, history, today_str, source_dt)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"完了 → {OUTPUT_FILE}")
    print(f"履歴蓄積日数: {len(history)}日")
