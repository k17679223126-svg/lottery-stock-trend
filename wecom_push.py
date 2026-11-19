#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""趋势双引擎 · 企业微信群推送
用法：
  python wecom_push.py --slot morning     # 早盘（09:26 推送）
  python wecom_push.py --slot afternoon   # 下午（13:05 推送）
  python wecom_push.py --slot overnight   # 隔夜（14:50 推送）
  python wecom_push.py --slot holdings    # 持仓提醒（可随时）
加 --dry-run 只打印不发送。Webhook 从环境变量 WECOM_WEBHOOK 读取，不写死。
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
HERE = os.path.dirname(os.path.abspath(__file__))

WATCH = [
    ("sh600519", "白酒"), ("sz000858", "白酒"), ("sh600036", "银行"), ("sh601318", "保险"),
    ("sh600030", "券商"), ("sz300059", "互联网金融"), ("sh601899", "有色金属"), ("sh600900", "电力"),
    ("sh601088", "煤炭"), ("sh600028", "石油石化"), ("sh600276", "医药"), ("sh603259", "医药外包"),
    ("sz300760", "医疗器械"), ("sh600436", "中药"), ("sz000333", "家电"), ("sz000651", "家电"),
    ("sz002594", "新能源车"), ("sh601127", "新能源车"), ("sh600031", "工程机械"), ("sz300124", "工业自动化"),
    ("sz002475", "消费电子"), ("sz002415", "安防"), ("sz000725", "面板"), ("sh603501", "半导体"),
    ("sz002371", "半导体设备"), ("sh688981", "晶圆代工"), ("sh688041", "国产CPU"), ("sh688256", "AI芯片"),
    ("sz300308", "光模块"), ("sz300502", "光模块"), ("sh601138", "AI服务器"), ("sh600050", "通信运营"),
    ("sh600941", "通信运营"), ("sz300750", "动力电池"), ("sz300014", "锂电池"), ("sh601012", "光伏"),
    ("sz300274", "光伏逆变器"), ("sz002460", "锂资源"), ("sh600309", "化工"), ("sh601668", "建筑"),
    ("sh600048", "地产"), ("sh601816", "铁路运输"), ("sh601919", "航运"), ("sz002352", "物流"),
    ("sh600887", "乳制品"), ("sh603288", "调味品"), ("sz000002", "地产"), ("sz000651", "家电"),
]


def http_get(url, gbk=False, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode("gbk", "replace") if gbk else raw.decode("utf-8", "replace")


def fetch_quotes(codes):
    out = {}
    for i in range(0, len(codes), 30):
        chunk = codes[i:i + 30]
        txt = http_get("https://qt.gtimg.cn/q=" + ",".join(chunk), gbk=True)
        for line in txt.split("\n"):
            if "=" not in line:
                continue
            parts = line.split("=", 1)[1].strip().strip(";").strip('"').split("~")
            if len(parts) < 50:
                continue
            f = lambda i: float(parts[i]) if parts[i] not in ("", "-") else 0.0
            out["%s%s" % ("sh" if parts[0] == "1" else "sz", parts[2])] = {
                "name": parts[1], "price": f(3), "prev": f(4), "open": f(5),
                "high": f(33), "low": f(34), "pct": f(32), "turnover": f(38),
                "vr": f(49), "amt": f(37), "time": parts[30],
            }
    return out


def fetch_kline(code, days=90):
    url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=%s,day,,,%d,qfq" % (code, days))
    d = json.loads(http_get(url))
    node = (d.get("data") or {}).get(code) or {}
    rows = node.get("qfqday") or node.get("day") or []
    out = []
    for r in rows:
        try:
            out.append([r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
        except Exception:
            continue
    return out


def metrics(rows):
    if len(rows) < 65:
        return None
    c = [r[2] for r in rows]
    v = [r[5] for r in rows]
    n = len(c)
    ma = lambda p: sum(c[-p:]) / p
    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    ma20_prev = sum(c[-25:-5]) / 20
    ret5 = (c[-1] / c[-6] - 1) * 100
    ret20 = (c[-1] / c[-21] - 1) * 100
    v5, v20 = sum(v[-5:]) / 5, sum(v[-20:]) / 20
    peak, dd = c[-1], 0.0
    for x in c[-20:]:
        peak = max(peak, x)
        dd = min(dd, (x / peak - 1) * 100)
    return {
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "ma20_up": ma20 > ma20_prev, "ret5": ret5, "ret20": ret20,
        "vol_ratio": (v5 / v20) if v20 else None, "dd20": dd,
        "trend": int(max(0, min(100, (
            (18 if c[-1] > ma20 else 0) + (14 if c[-1] > ma60 else 0) +
            (10 if ma5 > ma10 > ma20 else 0) + (8 if ma20 > ma60 else 0) +
            (8 if ma20 > ma20_prev else 0) + max(-10, min(18, ret20 * 0.8)) +
            ((8 if 1 <= (v5 / v20 if v20 else 0) <= 2.2 else 3)) +
            (6 if dd > -12 else 0)
        )))),
    }


def norm(v, lo, hi):
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def load_holdings():
    path = os.path.join(HERE, "holdings.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return [h for h in json.load(f) if h.get("code") and float(h.get("cost", 0)) > 0]
    except Exception:
        return []


def uniq_watch():
    seen, out = set(), []
    for c, ind in WATCH:
        if c in seen:
            continue
        seen.add(c)
        out.append((c, ind))
    return out


def build_dataset():
    watch = uniq_watch()
    codes = []
    for c, _ in watch:
        if c not in codes:
            codes.append(c)
    for h in load_holdings():
        if h["code"] not in codes:
            codes.append(h["code"])
    quotes = fetch_quotes(codes)
    rows = []
    for code, ind in watch:
        q = quotes.get(code)
        if not q or not q["price"]:
            continue
        mid = (q["high"] + q["low"] + q["price"]) / 3 if q["high"] > q["low"] else q["price"]
        pos = (q["price"] - q["low"]) / (q["high"] - q["low"]) if q["high"] > q["low"] else 0.5
        rows.append({"code": code, "name": q["name"], "ind": ind, "q": q,
                     "mid": mid, "pos": pos, "gap": (q["open"] / q["prev"] - 1) if q["prev"] else 0})
    # 只给涨幅靠前的补日线，控制请求量
    top = sorted(rows, key=lambda r: r["q"]["pct"], reverse=True)[:14]
    for r in top:
        try:
            r["m"] = metrics(fetch_kline(r["code"]))
        except Exception:
            r["m"] = None
    return rows, quotes


def fmt_pct(v):
    return ("%+.2f%%" % v) if v is not None else "-"


def pick_morning(rows):
    out = []
    for r in rows:
        m, q = r.get("m"), r["q"]
        if not m or r["gap"] <= 0.001 or q["price"] < q["open"] * 0.998:
            continue
        score = 100 * (0.28 * norm(r["gap"] * 100, 0, 4) + 0.22 * norm(q["vr"], 0.6, 3)
                       + 0.22 * norm((q["price"] / q["open"] - 1) * 100, -0.6, 1.6) + 0.18 * m["trend"] / 100)
        out.append((score, r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:3]]


def pick_afternoon(rows):
    out = []
    for r in rows:
        m, q = r.get("m"), r["q"]
        if not m or q["pct"] <= 0 or q["price"] <= r["mid"]:
            continue
        score = 100 * (0.30 * r["pos"] + 0.25 * norm(q["pct"], 0, 7) + 0.22 * norm(q["vr"], 0.6, 3) + 0.23 * m["trend"] / 100)
        out.append((score, r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:3]]


def pick_overnight(rows):
    out = []
    for r in rows:
        m, q = r.get("m"), r["q"]
        if not m:
            continue
        if not (0.5 <= q["pct"] <= 5 and r["pos"] >= 0.7 and 1 <= q["vr"] <= 3 and 1 <= q["turnover"] <= 15):
            continue
        if not (q["price"] > m["ma5"] and q["price"] > m["ma20"]):
            continue
        score = 100 * (0.30 * norm(q["pct"], 0.3, 5) + 0.25 * r["pos"] + 0.20 * norm(q["vr"], 1, 3) + 0.25 * m["trend"] / 100)
        out.append((score, r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:3]]


def holdings_section(quotes):
    hs = load_holdings()
    if not hs:
        return []
    lines = ["**持仓提醒**"]
    for h in hs:
        q = quotes.get(h["code"])
        if not q or not q["price"]:
            lines.append("> %s 行情缺失" % h["code"])
            continue
        try:
            m = metrics(fetch_kline(h["code"]))
        except Exception:
            m = None
        cost = float(h["cost"])
        pnl = (q["price"] / cost - 1) * 100
        stop = m["ma10"] * 0.995 if m else None
        if stop and q["price"] <= stop:
            lines.append("> <font color=\"warning\">%s 跌破止损 %.2f（现价 %.2f，%s）</font>"
                         % (q["name"], stop, q["price"], fmt_pct(pnl)))
        elif pnl >= 15:
            lines.append("> <font color=\"info\">%s 浮盈 %s，先卖一半</font>" % (q["name"], fmt_pct(pnl)))
        elif m and q["price"] < m["ma20"]:
            lines.append("> <font color=\"comment\">%s 跌破 MA20 %.2f，趋势转弱</font>" % (q["name"], m["ma20"]))
        else:
            lines.append("> %s 现价 %.2f，浮动 %s，持有观察" % (q["name"], q["price"], fmt_pct(pnl)))
    return lines


def build_message(slot, rows, quotes):
    today = datetime.now(BJ).strftime("%m-%d")
    if slot == "morning":
        picks = pick_morning(rows)
        head = "**早盘推荐 %s**\n> 竞价缺口 + 开盘不破 + 量比，仅供参考" % today
        body = []
        for r in picks:
            q = r["q"]
            body.append("**%s**（%s）竞价 %s，现价 %.2f，量比 %.2f，趋势 %d"
                        % (r["name"], r["ind"], fmt_pct(r["gap"] * 100), q["price"], q["vr"], r["m"]["trend"]))
            body.append("> 参考：开盘 %.2f / 止损 %.2f 附近" % (q["open"], q["open"] * 0.985))
        if not picks:
            body.append("今日没有「高开且不破开盘价」的竞价结构，早盘不建议追高。")
    elif slot == "afternoon":
        picks = pick_afternoon(rows)
        head = "**下午推荐 %s**\n> 午后仍在均价上方、趋势向上的标的" % today
        body = []
        for r in picks:
            q = r["q"]
            body.append("**%s**（%s）%s，现价 %.2f，日内位置 %d%%，量比 %.2f"
                        % (r["name"], r["ind"], fmt_pct(q["pct"]), q["price"], round(r["pos"] * 100), q["vr"]))
            body.append("> 均价 %.2f；跌破 %.2f 视为走弱" % (r["mid"], r["mid"] * 0.995))
        if not picks:
            body.append("午后没有承接良好的标的，明天再看。")
    else:
        picks = pick_overnight(rows)
        head = "**隔夜推荐 " + today + "**"
        head += chr(10) + "> 尾盘买入、次日 09:30-09:45 了结（+2% 止盈 / -1.5% 止损）"
        body = []
        for r in picks:
            q = r["q"]
            body.append("**%s**（%s）%s，现价 %.2f，日内位置 %d%%，量比 %.2f"
                        % (r["name"], r["ind"], fmt_pct(q["pct"]), q["price"], round(r["pos"] * 100), q["vr"]))
            body.append("> 买入参考 %.2f / 目标 %.2f / 止损 %.2f"
                        % (q["price"], q["price"] * 1.02, q["price"] * 0.985))
        if not picks:
            body.append("今天没有满足隔夜条件的标的，不做隔夜。")
    parts = [head] + body + holdings_section(quotes)
    parts.append("> 量化观察，不构成投资建议；数据来自腾讯行情，%s"
                 % datetime.now(BJ).strftime("%Y-%m-%d %H:%M"))
    return "\n".join(parts)


def push(webhook, content):
    payload = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        body = json.loads(resp.read().decode("utf-8"))
        if body.get("errcode") == 0:
            return True, "ok"
        return False, str(body)
    except Exception as exc:
        return False, repr(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="morning", choices=["morning", "afternoon", "overnight", "holdings"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows, quotes = build_dataset()
    if args.slot == "holdings":
        content = "\n".join(["**持仓提醒**"] + holdings_section(quotes)[1:])
    else:
        content = build_message(args.slot, rows, quotes)

    if args.dry_run or not os.environ.get("WECOM_WEBHOOK"):
        print("[dry-run] 未配置 WECOM_WEBHOOK，以下为待发送内容：\n")
        print(content)
        return 0 if args.dry_run else 2

    ok, info = push(os.environ["WECOM_WEBHOOK"].strip(), content)
    print("sent" if ok else "failed: " + info)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())