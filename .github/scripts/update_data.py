#!/usr/bin/env python3
"""
每周数据自动更新脚本
- 从腾讯行情接口获取 A 股指数周涨跌幅
- 更新 data.json 中的 idx 基准数据
- 自动更新日期字段

运行在 GitHub Actions 环境中，工作目录为仓库根目录。
"""

import json
import urllib.request
import datetime
import os
import re

# ============================================================
# 一、指数映射：我们的名称 → 腾讯行情代码
# ============================================================
INDEX_MAP = {
    "上证指数":   "sh000001",
    "深证成指":   "sz399001",
    "沪深300":    "sh000300",
    "中证500":    "sh000905",
    "创业板指":   "sz399006",
    "科创50":     "sh000688",
}

def fetch_gtimg(codes):
    """批量获取腾讯行情实时数据"""
    codes_str = ",".join(codes)
    url = f"http://qt.gtimg.cn/q={codes_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    text = resp.read().decode("gbk")
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        # 解析格式: v_sh000001="1~上证指数~3010.66~..."
        match = re.match(r'v_(\w+)="(.+)"', line)
        if not match:
            continue
        code = match.group(1)
        fields = match.group(2).split("~")
        if len(fields) < 5:
            continue
        result[code] = {
            "name": fields[1],
            "price": float(fields[3]) if fields[3] else 0,
            "change_pct": float(fields[4]) if fields[4] else 0,
        }
    return result

def fetch_kline_range(code, start_date, end_date):
    """获取日 K 线数据（用于计算周涨跌幅）"""
    url = (f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={code},day,{start_date},{end_date},10,qfq")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    try:
        kline = data["data"][code]["day"]
    except (KeyError, TypeError):
        return []
    return kline  # [[date, open, close, high, low, volume], ...]

def calc_weekly_change(code):
    """
    计算本周涨跌幅（%）
    策略：取最近一周的日 K 线，本周最后一个收盘价相对上周最后一个收盘价
    """
    today = datetime.date.today()
    # 取过去 2 周的数据
    start = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    kline = fetch_kline_range(code, start, end)
    if len(kline) < 2:
        return None

    # 提取收盘价和日期
    closes = []
    dates = []
    for k in kline:
        dates.append(k[0])
        closes.append(float(k[2]))

    # 找出本交易周的第一天和最后一天
    # 简化：用最近交易日的日期推算本周
    last_date = dates[-1]
    last_close = closes[-1]

    # 找上一个不同周的收盘价
    # 按日期从后往前，找到不同周的最近一天
    try:
        last_dt = datetime.datetime.strptime(last_date, "%Y-%m-%d").date()
        last_week = last_dt.isocalendar()[1]
        last_year = last_dt.isocalendar()[0]
    except (ValueError, IndexError):
        return None

    prev_close = None
    for i in range(len(dates) - 2, -1, -1):
        try:
            dt = datetime.datetime.strptime(dates[i], "%Y-%m-%d").date()
            week = dt.isocalendar()[1]
            year = dt.isocalendar()[0]
            if week != last_week or year != last_year:
                prev_close = closes[i]
                break
        except (ValueError, IndexError):
            continue

    if prev_close and prev_close > 0:
        return round((last_close - prev_close) / prev_close * 100, 2)
    return None


def main():
    # 读取当前 data.json
    data_path = "data.json"
    if not os.path.exists(data_path):
        print(f"❌ data.json not found at {os.path.abspath('.')}")
        ls = os.listdir(".")
        print(f"   Files: {ls}")
        return 1

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    changed = 0

    # 获取实时行情（用于名称验证和备用）
    codes = list(INDEX_MAP.values())
    quotes = fetch_gtimg(codes)
    print(f"📊 获取到 {len(quotes)} 个指数实时数据")

    # 更新 idx 类基准
    for bench in data.get("bench", []):
        if bench.get("category") != "idx":
            continue
        name = bench["name"]
        code = INDEX_MAP.get(name)
        if not code:
            print(f"  ⚠️ 跳过未知指数: {name}")
            continue

        # 计算周涨跌幅
        change = calc_weekly_change(code)
        if change is not None:
            old = bench.get("value")
            bench["value"] = change
            bench["updatedAt"] = today_str
            bench["period"] = f"本周"
            print(f"  ✅ {name}: {old}% → {change}%")
            changed += 1
        else:
            # 兜底：用当日涨跌幅
            if code in quotes:
                daily = quotes[code]["change_pct"]
                old = bench.get("value")
                bench["value"] = round(daily, 2)
                bench["updatedAt"] = today_str
                print(f"  ⚠️ {name}: 用当日数据 {old}% → {daily}%")
                changed += 1
            else:
                print(f"  ❌ {name}: 无法获取数据")

    # 更新全局日期
    old_updated = data.get("updatedAt", "")
    data["updatedAt"] = today_str
    data["benchUpdated"] = today_str
    print(f"\n📅 日期: {old_updated} → {today_str}")

    # 写回文件
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 更新完成！共更新 {changed} 个指数")
    return 0

if __name__ == "__main__":
    exit(main())
