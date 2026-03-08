"""
screener_japan.py
─────────────────────────────────────────────────────────────
日本株スクリーナー

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

【フィルター】
  時価総額 500億円以上（上位500社相当）

【出力】
  screener_japan.html（同ディレクトリに生成）

【実行方法】
  pip install yfinance
  python3 screener_japan.py

【推奨タイミング】
  東証引け後（15:30以降）に実行すると当日データが反映される
─────────────────────────────────────────────────────────────
"""

import urllib.request, json, re, yfinance as yf
from collections import defaultdict
from datetime import datetime

# ══════════════════════════════════════════════
# 設定（ここを変更してカスタマイズ）
# ══════════════════════════════════════════════

# 時価総額フィルター（億円）
MARKET_CAP_MIN = 500

# スコアリング軸と重み
TAG_MAP = {
    "price-rise":             ("値上がり",     3),
    "spike-in-trading-value": ("出来高急増",   3),
    "pbr-low":                ("低PBR",        2),
    "per-low":                ("低PER",        2),
    "year-high":              ("年初来高値",    2),
    "trading-value":          ("売買代金上位",  1),
}

OUTPUT_FILE = "screener_japan.html"

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
            scores[code]["name"]  = item["stockName"]
            scores[code]["score"] += weight
            scores[code]["tags"].append(tag_label)
            scores[code]["values"][cat] = item["stockValues"]
    return sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)

# ══════════════════════════════════════════════
# 3. yfinance 財務補完
# ══════════════════════════════════════════════

def enrich_with_yfinance(candidates):
    results = []
    for code, info in candidates:
        ticker_sym = f"{code}.T" if code.isdigit() else None
        row = {
            "code":          code,
            "name":          info["name"],
            "score":         info["score"],
            "tags":          info["tags"],
            "price":         None,
            "market_cap_b":  None,   # 億円
            "pbr":           None,
            "per":           None,
            "div_yield":     None,   # %
            "change_pct":    None,   # %
            "week52_ratio":  None,   # %（現在値 / 52週高値）
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
# 4. HTML 生成
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
        parts.append(
            f'<span class="tag" style="color:{c};background:{bg};border-color:{c}30">{t}</span>'
        )
    return "".join(parts)

def score_bars(s, max_s=6):
    filled = min(s, max_s)
    return (f'<span class="score-bars">{"▮"*filled}{"▯"*(max_s-filled)}</span>'
            f' <span class="score-num">{s}pt</span>')

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

def generate_html(filtered, source_dt):
    dt_str    = datetime.now().strftime("%Y-%m-%d %H:%M")
    top       = filtered[0] if filtered else {}
    rows      = build_rows(filtered)
    legend    = build_legend_items()

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
  --border:#e2e6ef; --border2:#d0d6e4;
  --text:#3a4255; --text-dim:#9aa3b8; --text-mid:#6b7592; --text-bright:#111827;
  --accent:#b8922a; --accent2:#1a6fd4; --pos:#1a9e60; --neg:#c93030;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'Noto Sans JP',sans-serif; font-weight:300; min-height:100vh; }}
body::before {{
  content:''; position:fixed; inset:0;
  background-image: linear-gradient(rgba(26,111,212,0.025) 1px,transparent 1px), linear-gradient(90deg,rgba(26,111,212,0.025) 1px,transparent 1px);
  background-size:48px 48px; pointer-events:none; z-index:0;
}}
.page {{ max-width:1200px; margin:0 auto; padding:52px 24px 80px; position:relative; z-index:1; }}
header {{ margin-bottom:40px; border-left:4px solid var(--accent); padding-left:20px; animation:fadeSlide 0.6s ease both; }}
.h-meta {{ display:flex; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }}
.h-badge {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.2em; padding:3px 10px; border-radius:2px; }}
.b-live {{ background:rgba(26,158,96,0.1); color:var(--pos); border:1px solid rgba(26,158,96,0.25); }}
.b-src  {{ background:rgba(184,146,42,0.1); color:var(--accent); border:1px solid rgba(184,146,42,0.25); }}
.b-dt   {{ background:var(--surface2); color:var(--text-dim); border:1px solid var(--border); }}
h1 {{ font-family:'Bebas Neue',sans-serif; font-size:clamp(36px,6vw,64px); letter-spacing:0.04em; color:var(--text-bright); line-height:0.95; margin-bottom:10px; }}
h1 span {{ color:var(--accent); }}
.h-sub {{ font-size:12px; color:var(--text-mid); letter-spacing:0.05em; line-height:1.8; }}
.summary {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:32px; }}
.s-card {{ background:var(--surface); border:1px solid var(--border); border-radius:3px; padding:16px; position:relative; overflow:hidden; }}
.s-card::before {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; border-radius:3px 3px 0 0; }}
.s-card.c1::before {{ background:var(--neg); }}
.s-card.c2::before {{ background:var(--accent); }}
.s-card.c3::before {{ background:var(--accent2); }}
.s-card.c4::before {{ background:var(--pos); }}
.s-label {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.18em; color:var(--text-dim); margin-bottom:8px; }}
.s-val   {{ font-family:'IBM Plex Mono',monospace; font-size:22px; font-weight:600; color:var(--text-bright); }}
.s-desc  {{ font-size:11px; color:var(--text-mid); margin-top:5px; line-height:1.5; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
thead tr {{ background:var(--surface2); }}
th {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.15em; color:var(--text-dim); padding:10px 14px; text-align:left; white-space:nowrap; border-bottom:2px solid var(--border); }}
th.r {{ text-align:right; }}
.stock-row {{ border-bottom:1px solid var(--border); transition:background 0.15s; }}
.stock-row:hover {{ background:var(--surface); }}
td {{ padding:13px 14px; vertical-align:middle; }}
.rank-cell {{ font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); white-space:nowrap; }}
.code-cell {{ white-space:nowrap; min-width:100px; }}
.stock-code {{ font-family:'IBM Plex Mono',monospace; font-size:15px; font-weight:600; color:var(--text-bright); }}
.stock-name {{ font-size:11px; color:var(--text-mid); margin-top:2px; }}
.score-cell {{ white-space:nowrap; min-width:130px; }}
.score-bars {{ font-family:'IBM Plex Mono',monospace; font-size:13px; color:var(--accent); letter-spacing:1px; }}
.score-num  {{ font-family:'IBM Plex Mono',monospace; font-size:10px; color:var(--text-dim); margin-left:4px; }}
.tags-cell {{ min-width:160px; }}
.tag {{ display:inline-block; font-size:10px; font-weight:500; padding:2px 7px; border-radius:2px; border:1px solid; margin:2px 3px 2px 0; white-space:nowrap; }}
.num-cell {{ text-align:right; white-space:nowrap; font-family:'IBM Plex Mono',monospace; font-size:13px; }}
.na {{ color:var(--text-dim); }}
.pos {{ color:var(--pos); }}
.neg {{ color:var(--neg); }}
.legend {{ margin-top:28px; background:var(--surface); border:1px solid var(--border); border-radius:3px; padding:18px 20px; }}
.legend-title {{ font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.2em; color:var(--text-dim); margin-bottom:12px; }}
.legend-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
.legend-item {{ font-size:11px; color:var(--text-mid); display:flex; gap:8px; align-items:flex-start; }}
footer {{ margin-top:36px; padding-top:16px; border-top:1px solid var(--border); font-family:'IBM Plex Mono',monospace; font-size:9px; letter-spacing:0.12em; color:var(--text-dim); display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px; }}
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
    データソース更新: {source_dt}
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
    <div class="s-label">MKT CAP FILTER</div>
    <div class="s-val">{MARKET_CAP_MIN}億+</div>
    <div class="s-desc">時価総額フィルター（上位500社相当）</div>
  </div>
</div>
<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th></th><th>コード / 銘柄</th><th>スコア</th><th>該当タグ</th>
      <th class="r">株価</th><th class="r">騰落率</th><th class="r">時価総額</th>
      <th class="r">PBR</th><th class="r">PER</th><th class="r">配当利回り</th><th class="r">52週高値比</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</div>
<div class="legend">
  <div class="legend-title">SCORING LOGIC — 各タグの重み付け</div>
  <div class="legend-grid">{legend}</div>
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
    print("日経ランキング取得中...")
    ranking    = fetch_nikkei_ranking()
    source_dt  = ranking.get("price-rise", {}).get("lastupdate", "—").replace("T", " ")

    print("スコアリング中...")
    candidates = build_scores(ranking)

    print(f"yfinance補完中（{len(candidates)}銘柄）...")
    filtered   = enrich_with_yfinance(candidates)

    print(f"HTML生成中（{len(filtered)}銘柄がフィルター通過）...")
    html = generate_html(filtered, source_dt)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"完了 → {OUTPUT_FILE}")
