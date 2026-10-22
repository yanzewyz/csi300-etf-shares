#!/usr/bin/env python3
"""校验 ETF 份额快照是否足够新鲜。

用法：
  # 校验仓库内数据文件（CI 用，跑在 update_data.py 之后）
  python scripts/check_freshness.py --source local --max-lag-trading-days 1

  # 校验线上已部署的站点（本地巡检用，不依赖仓库）
  python scripts/check_freshness.py --source https://etf.zluna.ai/data/etf-shares.json

退出码：0 = 正常；1 = 滞后（数据停更）；2 = 无法判定（网络/解析失败）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo

    TIMEZONE: Any = ZoneInfo("Asia/Shanghai")
except Exception:  # Windows 默认没有 tzdata，退回固定 +08:00
    TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

CSI_INDEX_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
CSI300_CODE = "000300"
CSI300_NAME = "沪深300指数"
DEFAULT_LOCAL = Path("public/data/etf-shares.json")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def http_json(url: str, params: dict[str, str] | None, referer: str) -> Any:
    target = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    request = urllib.request.Request(
        target,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_snapshot(source: str) -> dict[str, Any]:
    if source.startswith("http://") or source.startswith("https://"):
        return http_json(source, None, "https://etf.zluna.ai/")
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"snapshot not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_trading_dates(start: date, end: date) -> list[date]:
    payload = http_json(
        CSI_INDEX_URL,
        {
            "indexCode": CSI300_CODE,
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
        },
        "https://www.csindex.com.cn/",
    )
    if str(payload.get("code")) != "200":
        raise RuntimeError(f"CSI index API error: {payload.get('message') or payload}")
    dates: list[date] = []
    for row in payload.get("data") or []:
        if str(row.get("indexCode", "")).zfill(6) != CSI300_CODE:
            continue
        if str(row.get("indexNameCnAll", "")) != CSI300_NAME:
            continue
        dates.append(datetime.strptime(str(row["tradeDate"]), "%Y%m%d").date())
    return sorted(set(dates))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=str(DEFAULT_LOCAL),
        help="本地 JSON 路径或线上 JSON URL",
    )
    parser.add_argument(
        "--max-lag-trading-days",
        type=int,
        default=1,
        help="允许落后的交易日数量（1 = 只允许最新交易日当天/次日未刷出）",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        snapshot = load_snapshot(args.source)
        metadata = snapshot["metadata"]
        latest = datetime.strptime(metadata["latest_data_date"], "%Y-%m-%d").date()
        updated_at = metadata.get("updated_at", "?")
        calendar = fetch_trading_dates(latest, datetime.now(TIMEZONE).date())
    except Exception as error:  # noqa: BLE001
        print(f"无法判定：{type(error).__name__}: {error}", file=sys.stderr)
        return 2

    if not calendar:
        print("无法判定：交易日历为空", file=sys.stderr)
        return 2

    future_dates = [day for day in calendar if day > latest]
    lag = len(future_dates)
    newest_trading_day = calendar[-1]

    if not args.quiet:
        print(f"数据来源        : {args.source}")
        print(f"快照最新日期    : {latest}")
        print(f"快照生成时间    : {updated_at}")
        print(f"最新交易日      : {newest_trading_day}")
        print(f"落后交易日数    : {lag}（阈值 {args.max_lag_trading_days}）")
        if future_dates:
            print(f"缺失交易日      : {', '.join(str(day) for day in future_dates[-8:])}")
        unreviewed = metadata.get("unreviewed_move_count", 0)
        if unreviewed:
            print(f"待复核大额变动  : {unreviewed} 条")

    if lag > args.max_lag_trading_days:
        print(
            f"结果：滞后。数据停在 {latest}，官方交易日历已到 {newest_trading_day}，"
            f"落后 {lag} 个交易日。",
            file=sys.stderr,
        )
        return 1

    print(f"结果：正常。{newest_trading_day} 的数据{'已' if lag == 0 else '即将'}就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
