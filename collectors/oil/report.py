"""Генерира docs/index.html — терминален светофар с двата часовника."""
from __future__ import annotations
import json
import datetime as dt

C = {"BULL": "#36D67E", "BEAR": "#FF5252", "NEUTRAL": "#7E8AA0", "NODATA": "#4A5568"}
BG_STATE = {"BULL": "ДОКАЗАТЕЛСТВО", "BEAR": "ОПРОВЕРЖЕНИЕ",
            "NEUTRAL": "ИЗЧАКВАНЕ", "NODATA": "НЯМА ДАННИ"}
VERDICT_C = {"PROVEN": "#36D67E", "DEAD": "#FF5252", "WAIT": "#F6A21B"}


def _spark(points: list, color: str, w: int = 260, h: int = 44) -> str:
    vals = [p[1] for p in points][-60:]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    step = w / (len(vals) - 1)
    pts = " ".join(f"{i*step:.1f},{h - 4 - (v - lo) / rng * (h - 8):.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"/></svg>')


def _gauge(title: str, sub: str, t: float, left_label: str, right_label: str,
           reading: str) -> str:
    """Полукръгъл циферблат. t∈[0,1]; дясно = в полза на тезата."""
    t = max(0.0, min(1.0, t))
    ang = -90 + 180 * t
    ticks = "".join(
        f'<line x1="100" y1="22" x2="100" y2="30" stroke="#2A3447" stroke-width="2" '
        f'transform="rotate({a} 100 100)"/>' for a in range(-90, 91, 15))
    return f'''<div class="clock">
  <div class="clock-title">{title}</div>
  <svg viewBox="0 0 200 116" class="dial" role="img" aria-label="{title}: {reading}">
    <path d="M 16 100 A 84 84 0 0 1 184 100" fill="none" stroke="#1B2330" stroke-width="10"/>
    <path d="M 100 16 A 84 84 0 0 1 184 100" fill="none" stroke="#3D2F14" stroke-width="10"/>
    {ticks}
    <line x1="100" y1="100" x2="100" y2="30" stroke="#F6A21B" stroke-width="3"
          stroke-linecap="round" transform="rotate({ang:.1f} 100 100)"/>
    <circle cx="100" cy="100" r="5" fill="#F6A21B"/>
  </svg>
  <div class="clock-scale"><span>{left_label}</span><span>{right_label}</span></div>
  <div class="clock-reading">{reading}</div>
  <div class="clock-sub">{sub}</div>
</div>'''


def _card(sid: str, name: str, score: dict, spark_svg: str) -> str:
    st = score["state"]
    return f'''<article class="card" data-state="{st}">
  <header><span class="lamp" style="background:{C[st]}"></span>
    <span class="sid">{sid}</span><h2>{name}</h2>
    <span class="state" style="color:{C[st]}">{BG_STATE[st]}</span></header>
  <div class="value">{score.get("value", "—")}</div>
  {spark_svg}
  <div class="detail">{score.get("detail", "")}</div>
</article>'''


def build_html(state: dict) -> str:
    s, raw = state["scores"], state["raw"]
    comp, fals = state["composite"], state["falsifier"]
    now = state["generated_at"]

    # Часовник 1: ЗАПАСЪТ — средно отклонение от нормата, +2 (пълни се) .. −6 (изпразва се)
    dev = 0.0
    if raw["eia"].get("ok"):
        d = [v for _, v in raw["eia"]["deviations_mbbl"]][-4:]
        dev = sum(d) / len(d) if d else 0.0
    t_stock = (2.0 - dev) / 8.0
    g1 = _gauge("ЗАПАСЪТ", "EIA: отклонение от 5-год. сезонна норма, 4-седм. средно",
                t_stock, "пълни се", "изпразва се", f"{dev:+.1f} млн. б/седм.")

    # Часовник 2: ПОТОКЪТ — % от предвоенните транзити; 100% (ляво) .. 0% (дясно)
    pct = None
    if raw["hormuz"].get("ok"):
        pct = raw["hormuz"]["last_7d_pct"]
    t_flow = 1.0 - (pct / 100.0) if pct is not None else 0.5
    g2 = _gauge("ПОТОКЪТ", "PortWatch: танкери през Ормуз, 7-дн. средно срещу базата",
                t_flow, "излекуван", "блокиран",
                f"{pct:.0f}% от базата" if pct is not None else "няма данни")

    cards = "".join([
        _card("С1", "Brent M1−M2 (бекуордейшън)", s["S1"],
              _spark(raw["prices"].get("spread_series", []), C[s["S1"]["state"]]) if raw["prices"].get("ok") else ""),
        _card("С2", "Транзити през Ормуз", s["S2"],
              _spark(raw["hormuz"].get("weekly_pct", []), C[s["S2"]["state"]]) if raw["hormuz"].get("ok") else ""),
        _card("С3", "Запаси — EIA срещу нормата", s["S3"],
              _spark(raw["eia"].get("deviations_mbbl", []), C[s["S3"]["state"]]) if raw["eia"].get("ok") else ""),
        _card("С5", "COT managed money (персентил)", s["S5"],
              _spark(raw["cot"].get("pctile_series", []), C[s["S5"]["state"]]) if raw["cot"].get("ok") else ""),
        _card("С6", "Канарчето — Brent−WTI спред", s["S6"],
              _spark(raw["prices"].get("bw_spread_series", []), C[s["S6"]["state"]]) if raw["prices"].get("ok") else ""),
    ])

    wti = raw["prices"].get("wti_last", "—")
    spr = raw["prices"].get("spread_last", "—")
    vcol = VERDICT_C[comp["verdict"]]

    return f'''<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Два часовника · петролен монитор</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;600;700&display=swap&subset=cyrillic" rel="stylesheet">
<style>
:root {{
  --bg:#0B0E14; --panel:#121826; --line:#1E2735; --ink:#D7DEE8; --dim:#8A93A6;
  --amber:#F6A21B; --bull:#36D67E; --bear:#FF5252;
}}
* {{ box-sizing:border-box; margin:0 }}
body {{ background:var(--bg); color:var(--ink);
  font:15px/1.5 "IBM Plex Sans", system-ui, sans-serif; padding-bottom:48px }}
.mono, .tape, .value, .clock-reading, .detail {{ font-family:"IBM Plex Mono", monospace }}
.wrap {{ max-width:980px; margin:0 auto; padding:0 16px }}
.tape {{ display:flex; gap:24px; flex-wrap:wrap; padding:10px 16px; font-size:12.5px;
  color:var(--amber); border-bottom:1px solid var(--line); background:#0D1119;
  text-transform:uppercase; letter-spacing:.06em }}
.tape b {{ color:var(--ink); font-weight:500 }}
h1 {{ font-size:26px; letter-spacing:.02em; margin:28px 0 4px }}
.subtitle {{ color:var(--dim); margin-bottom:20px }}
.verdict {{ border:1px solid {vcol}; border-left:6px solid {vcol}; background:var(--panel);
  padding:16px 20px; margin:0 0 24px; display:flex; align-items:baseline; gap:14px; flex-wrap:wrap }}
.verdict .big {{ font-size:19px; font-weight:700; color:{vcol} }}
.verdict .tally {{ color:var(--dim); font-size:13px }}
.clocks {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px }}
.clock {{ background:var(--panel); border:1px solid var(--line); padding:18px; text-align:center }}
.clock-title {{ font-weight:700; letter-spacing:.14em; color:var(--amber); font-size:13px }}
.dial {{ width:100%; max-width:240px; margin:6px auto 0; display:block }}
.clock-scale {{ display:flex; justify-content:space-between; max-width:240px; margin:0 auto;
  color:var(--dim); font-size:11.5px; text-transform:uppercase; letter-spacing:.05em }}
.clock-reading {{ font-size:17px; margin-top:8px }}
.clock-sub {{ color:var(--dim); font-size:12px; margin-top:4px }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px }}
.card {{ background:var(--panel); border:1px solid var(--line); padding:16px 18px }}
.card header {{ display:flex; align-items:center; gap:10px; margin-bottom:10px }}
.lamp {{ width:11px; height:11px; border-radius:50%; flex:none }}
.sid {{ color:var(--dim); font-size:12px }}
.card h2 {{ font-size:14.5px; font-weight:600; flex:1 }}
.state {{ font-size:11.5px; font-weight:700; letter-spacing:.08em }}
.value {{ font-size:13.5px; margin-bottom:8px }}
.spark {{ width:100%; height:44px; display:block; background:#0D1119;
  border:1px solid var(--line); margin-bottom:8px }}
.detail {{ color:var(--dim); font-size:11.5px }}
.fals {{ margin-top:24px; border:1px dashed var(--line); padding:12px 18px;
  color:var(--dim); font-size:13px }}
.fals b {{ color:var(--ink); font-weight:500 }}
footer {{ margin-top:28px; color:var(--dim); font-size:12px; line-height:1.7 }}
@media (max-width:720px) {{ .clocks, .grid {{ grid-template-columns:1fr }} }}
@media (prefers-reduced-motion:no-preference) {{
  .verdict .big {{ animation:settle .5s ease-out }}
  @keyframes settle {{ from {{ opacity:0; transform:translateY(3px) }} }}
}}
</style>
</head>
<body>
<div class="tape"><span>ДВА ЧАСОВНИКА</span><span>WTI <b>{wti}</b></span>
<span>BRENT M1−M2 <b>{spr}</b></span><span>ОБНОВЕНО <b>{now}</b></span></div>
<div class="wrap">
<h1>Два часовника</h1>
<p class="subtitle">Изпразва ли се запасът по-бързо, отколкото потокът се лекува. Седмичен монитор на петролното неравновесие.</p>
<div class="verdict"><span class="big">{comp["label"]}</span>
<span class="tally">✅ {comp["bulls"]} · ❌ {comp["bears"]} от {comp["active"]} активни серии{" · без данни: " + str(comp["nodata"]) if comp["nodata"] else ""}</span></div>
<div class="clocks">{g1}{g2}</div>
<div class="grid">{cards}</div>
<div class="fals"><b>Фалсификатори:</b> {fals["text"]}. Под 80 за 15 затваряния → тезата мъртва. Над 95 за 5 затваряния → потвърждение в ход.</div>
<footer>Източници: yfinance (ICE/NYMEX сетълменти) · IMF PortWatch (сателитен AIS) · EIA API v2 · CFTC Disaggregated COT.
Серия 4 (физически диференциали, фрахт, военна застраховка) не е автоматизирана — следи се ръчно. С6 е канарчето на вносния регион: цената на нуждата от море.
Композитно правило: 3 на ✅ от 4-те гласуващи серии (С5 = контекст, НЕ гласува — managed money слаб предиктор в енергия), задължително включващи С1 или С2 → тезата доказана; 3 на ❌ → опровергана. Не е инвестиционен съвет.</footer>
</div>
</body>
</html>'''


def write_outputs(state: dict) -> None:
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(state))
    with open("data/state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, default=str)
