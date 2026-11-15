"""Detect questions from comparable, evidenced financial figures."""
from datetime import date
from math import isfinite

FIELDS = {
    "营业收入", "合并净利润", "经营现金流", "购建支出", "净应收",
    "毛利率", "存货周转率", "货币资金", "有息负债", "总资产",
}


def evaluate(packet, as_of):
    periods = {k: date.fromisoformat(packet[k]) for k in ("本期末", "同期末", "期初")}
    current, prior, opening = (periods[k] for k in ("本期末", "同期末", "期初"))
    if not opening <= current <= date.fromisoformat(as_of) or not prior < current:
        raise ValueError("排雷期间与基准日不符")
    fields = packet["数值"]
    if not set(fields) <= FIELDS:
        raise ValueError("存在未知排雷科目")
    for name, row in fields.items():
        if not set(row) <= {"本期", "同期", "期初", "证据"} or not row.get("证据"):
            raise ValueError(f"排雷科目缺少原文或字段无效 {name}")
        for key in ("本期", "同期", "期初"):
            val = row.get(key)
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val)):
                raise ValueError(f"排雷数值无效 {name} {key}")
        if name in {"净应收", "购建支出", "存货周转率", "货币资金", "有息负债", "总资产"}:
            if any(row.get(k) is not None and row[k] < 0 for k in ("本期", "同期", "期初")):
                raise ValueError(f"排雷科目符号无效 {name}")

    signals, gaps = [], []
    checked = []

    def get(name, period="本期"):
        return fields.get(name, {}).get(period)

    def record(title, names, message):
        signals.append({
            "项目": title, "线索": message, "严重性": "复核线索",
            "证据": sorted({ref for name in names for ref in fields[name]["证据"]}),
        })

    cash, profit, capex = (get(k) for k in ("经营现金流", "合并净利润", "购建支出"))
    if cash is None or profit is None:
        gaps.append("经营现金流与同范围合并净利润")
    elif profit > 0:
        checked.append("利润兑现")
        if cash < profit:
            record("利润兑现", ("经营现金流", "合并净利润"),
                   f"经营现金流为合并净利润的 {cash / profit:.1%}，核对回款与营运资金")
    else:
        gaps.append("合并利润为非正，利润现金比率暂不比较")
    if cash is None or capex is None:
        gaps.append("同期间经营现金流与购建长期资产支出")
    else:
        checked.append("经营现金与购建支出")
        if cash < capex:
            record("经营现金与购建支出", ("经营现金流", "购建支出"),
                   f"经营现金流减购建支出 {cash-capex:.2f} 亿元，拆分维持与扩产支出")

    receivable, rev = (get(k) for k in ("净应收", "营业收入"))
    old_ar, old_rev = (get(k, "同期") for k in ("净应收", "营业收入"))
    if (packet.get("同期可比") is True and current.month == prior.month and current.day == prior.day
            and current.year == prior.year + 1 and None not in (receivable, rev, old_ar, old_rev)
            and old_ar > 0 and old_rev > 0):
        checked.append("应收与营收同比")
        ar_growth, sales_growth = receivable / old_ar - 1, rev / old_rev - 1
        if ar_growth > sales_growth:
            record("应收与营收同比", ("净应收", "营业收入"),
                   f"净应收同比 {ar_growth:+.1%}，营收同比 {sales_growth:+.1%}，核对账龄和结算")
    else:
        gaps.append("同比应收与营收缺少相同观察期或可比说明")
        begin = get("净应收", "期初")
        if receivable is not None and begin is not None and 0 < begin < receivable:
            record("应收较期初上升", ("净应收",),
                   f"净应收较期初 {receivable / begin - 1:+.1%}，结合季节性核回款")

    margins = [get("毛利率", k) for k in ("本期", "同期")]
    turns = [get("存货周转率", k) for k in ("本期", "同期")]
    if packet.get("同期可比") is True and (current.year, current.month, current.day) == (prior.year + 1, prior.month, prior.day) and None not in margins + turns:
        checked.append("毛利与存货周转同比")
        if margins[0] > margins[1] and turns[0] < turns[1]:
            record("毛利与存货周转同比", ("毛利率", "存货周转率"), "毛利率升、存货周转降，核对成本、跌价与库龄")
    else:
        gaps.append("可比毛利率与存货周转率")

    money, debt, assets = (get(k) for k in ("货币资金", "有息负债", "总资产"))
    if None in (money, debt, assets) or assets <= 0:
        gaps.append("同日货币资金、有息负债与总资产")
    else:
        checked.append("现金与有息负债")
        if min(money, debt) / assets >= 0.10:
            record("现金与有息负债", ("货币资金", "有息负债", "总资产"),
                   f"现金及有息负债各占总资产 {money / assets:.1%}、{debt / assets:.1%}，核对受限资金与融资用途")
    return {"已计算": checked, "线索": signals, "缺数": gaps}
