"""Independent calculations for evidence-backed calibration."""
from math import fsum, isfinite


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("校准输入需为已复核有限数值")
    return value


def nonnegative(value):
    if finite(value) < 0:
        raise ValueError("现金扣项与资产负债金额须按正数填入")
    return value


def epv(x, market_cap):
    operating_profit = finite(x["正常EBIT亿元"])
    depreciation = nonnegative(x["正常折旧亿元"])
    maintenance = nonnegative(x["维持资本开支亿元"])
    taxes = nonnegative(x["经营现金税亿元"])
    working_capital = finite(x["维持营运资金增量亿元"])
    cost = finite(x["WACC"])
    if not 0 < cost <= 1:
        raise ValueError("WACC 需为 0 至 1 的正小数")
    surplus_assets = nonnegative(x["超额与非经营资产亿元"])
    debt = nonnegative(x["实际融资债亿元"])
    outside_equity = nonnegative(x["外部权益亿元"])
    power = operating_profit + depreciation - maintenance - taxes - working_capital
    if power <= 0:
        raise ValueError("可持续经营盈余非正，EPV 待重选模型")
    bridge = surplus_assets - debt - outside_equity
    return power / cost + bridge, {"按中性资本成本所需可持续经营盈余亿元": (market_cap - bridge) * cost}


def dcf(x, market_cap):
    annual = x["显性期逐年FCFF亿元"]
    if not isinstance(annual, list) or not annual:
        raise ValueError("DCF 需逐年列示显性期企业自由现金流")
    cost = finite(x["WACC"])
    growth = finite(x["永续增长率"])
    if not 0 < cost <= 1 or not -1 < growth < cost:
        raise ValueError("DCF 要求 0 < WACC ≤ 1 且 -1 < 永续增长率 < WACC")
    terminal_cash = finite(x["永续首年FCFF亿元"])
    if terminal_cash <= 0:
        raise ValueError("DCF 需有正的稳定期首年企业自由现金流")
    try:
        discount = (1 + cost) ** len(annual)
        explicit = fsum(finite(cash) / (1 + cost) ** year for year, cash in enumerate(annual, 1))
    except OverflowError as exc:
        raise ValueError("DCF 显性期现值或期限超出有效范围") from exc
    terminal = terminal_cash / ((cost - growth) * discount)
    operating = explicit + terminal
    bridge = (nonnegative(x["超额与非经营资产亿元"]) - nonnegative(x["实际融资债亿元"])
              - nonnegative(x["外部权益亿元"]))
    if operating <= 0:
        raise ValueError("经营现值非正，DCF 待重选模型")
    expectation = {"固定中性显性期现金及资本成本所需永续首年FCFF亿元":
                   (market_cap - bridge - explicit) * discount * (cost - growth)}
    diagnostics = {"显性期现值亿元": explicit, "终值现值亿元": terminal, "终值占经营价值比例": terminal / operating,
                   "经营价值亿元": operating, "归母权益调整亿元": bridge}
    if not all(isfinite(v) for v in (*expectation.values(), *diagnostics.values())):
        raise ValueError("DCF 现金流现值或反推超出有效范围")
    return operating + bridge, expectation, {k: round(v, 6) for k, v in diagnostics.items()}


def cash_path(card, market_cap):
    annual = card["六年归母可分配现金亿元"]
    if not isinstance(annual, list) or len(annual) != 6:
        raise ValueError("现金回收需逐年给出六个归母现金估计")
    cash = nonnegative(card["期初可归属超额现金亿元"])
    if finite(market_cap) <= 0:
        raise ValueError("股权市值需为正")
    amount = cash
    year = None
    for i, val in enumerate(annual, start=1):
        amount += finite(val)
        if year is None and amount >= market_cap:
            year = i
    total = fsum([cash, *annual])
    return {
        "期初可归属超额现金亿元": cash, "年度现金亿元": annual,
        "六年累计归属现金亿元": round(total, 6),
        "覆盖率": round(total / market_cap, 6),
        "六年末与市值差额亿元": round(total - market_cap, 6),
        "首次覆盖年份": year,
        "比较市值亿元": market_cap,
        "用途": "名义现金回收情景，供成熟盈利或分红型校准",
    }
