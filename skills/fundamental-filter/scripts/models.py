"""Light valuation with explicit, reviewed inputs in CNY."""
from math import isfinite

from calibration import dcf, epv
from economics import pe_basis, prepare, reverse_operating
from research_check import adapt

YI = 100_000_000
METHODS = {
    "成熟盈利": "所选口径估值利润 × PE（实际口径见利润口径说明）",
    "EPV": "正常经营盈余资本化与归母股权桥",
    "DCF": "企业自由现金流折现与归母股权桥",
    "合同服务": "正常化利润 × 现金兑现率 × 现金倍数",
    "周期": "销量、售价与中枢利润率",
    "成长": "存量业务与新增业务分别估值",
    "资产折价": "流动资产折价后扣负债和少数权益",
}
SCENARIOS = ("悲观", "中性", "乐观")


def number(x, minimum=0):
    if isinstance(x, bool) or not isinstance(x, (float, int)) or not isfinite(x) or x < minimum:
        raise ValueError(f"无效估值参数 {x}")
    return x


def positive(x):
    if number(x) == 0:
        raise ValueError("估值计算需要正数")
    return x


def share_count(x):
    if isinstance(x, bool) or not isinstance(x, int):
        raise ValueError("股数须为整数")
    return positive(x)


def fraction(x):
    if number(x) > 1:
        raise ValueError("比例须在 0 至 1")
    return x


def value(card, price, shares):
    card, audit = prepare(adapt(card))
    method = card["方法"]
    if method not in METHODS:
        raise ValueError("未知估值方法")
    positive(price)
    share_count(shares)
    if audit["等级"] == "C":
        return {"分支": method, "方法": METHODS[method], "估值等级": "C", "估值状态": "待核",
                "估值缺口": [x["说明"] for x in audit["检查"] if x["程度"] == "错误"]
                            or card.get("不可估原因", ["宿主判断缺少必要估值锚点"]),
                "经济关系检查": audit, "现价反推": {}, "置信度": "C，暂不可估"}
    rows = card["情景"]
    if set(rows) != set(SCENARIOS):
        raise ValueError("估值方法或三情景不齐")
    if method == "DCF" and len({len(row["显性期逐年FCFF亿元"]) for row in rows.values()}) != 1:
        raise ValueError("DCF 三情景显性期长度须一致")
    positive(price)
    share_count(shares)
    cap = price * shares / YI
    scope = "人民币元/股"
    basis = price
    model_inputs = card.get("资产输入", {})
    if method == "资产折价":
        a_shares = share_count(model_inputs["A股股数"])
        b_shares = model_inputs["B股股数"]
        if isinstance(b_shares, bool) or not isinstance(b_shares, int) or b_shares < 0:
            raise ValueError("B股股数无效")
        if a_shares + b_shares != shares:
            raise ValueError("A/B 股本与基准不一致")
        if b_shares:
            cap = (price * a_shares + positive(model_inputs["B股价格"])
                   * positive(model_inputs["每单位B股币种折人民币"]) * b_shares) / YI
        basis = cap
        scope = "人民币亿元，公司全部普通股权益代理"
    values = []
    dcf_details = {}
    implied = None
    for label in SCENARIOS:
        x = rows[label]
        if not x.get("依据") or not isinstance(x["依据"], str):
            raise ValueError(f"{label}缺少假设依据")
        if method == "成熟盈利":
            multiple = positive(x["PE"])
            profit = x["估值利润亿元"] if "估值利润亿元" in x else x["正常化利润亿元"]
            equity = positive(profit) * multiple
            expectation = {"按中性PE所需估值利润亿元": cap / multiple}
        elif method == "EPV":
            equity, expectation = epv(x, cap)
            positive(equity)
        elif method == "DCF":
            equity, expectation, dcf_details[label] = dcf(x, cap)
            reverse_operating(x, expectation)
            positive(equity)
        elif method == "合同服务":
            margin = positive(fraction(x["归母利润率"]))
            profit = positive(x["收入亿元"]) * margin
            multiple = positive(x["现金倍数"])
            equity = profit * fraction(x["现金兑现率"]) * multiple
            expectation = {"按中性收入、利润率和倍数所需现金兑现率": cap / (profit * multiple)}
        elif method == "周期":
            sales = positive(x["销量百万"]) * positive(x["售价元"]) / 100
            revenue = sales / positive(fraction(x["核心收入占比"]))
            multiple = positive(x["PE"])
            equity = revenue * positive(fraction(x["扣非归母利润率"])) * multiple
            expectation = {"按中性PE所需扣非利润亿元": cap / multiple}
        elif method == "成长":
            legacy = number(x["存量利润亿元"]) * positive(x["存量PE"])
            multiple = positive(x["新增PE"])
            equity = legacy + number(x["新增利润亿元"]) * multiple
            expectation = {"按中性存量及倍数所需新增利润亿元": (cap - legacy) / multiple}
        else:
            net = (number(model_inputs["流动资产亿元"]) - number(model_inputs["全部负债亿元"])
                   - number(model_inputs["少数股东权益代理亿元"]))
            equity = (net - number(model_inputs["净应收亿元"]) * fraction(x["应收折价率"])
                      - number(model_inputs["净存货亿元"]) * fraction(x["存货折价率"])
                      - number(x.get("新增处置费用亿元", 0)))
            expectation = {"按中性情景所需额外归属价值亿元": cap - equity}
        point = max(equity, 0) if method == "资产折价" else equity * YI / shares
        values.append(point)
        if label == "中性":
            implied = expectation
    low, center, high = values
    if not all(isfinite(x) for x in values) or not low <= center <= high:
        raise ValueError("三情景顺序或数值异常")
    result = {
        "分支": method, "方法": METHODS[method], "单位": scope,
        "比较基准": round(basis, 6), "股价元": price, "股数": shares,
        "悲观": round(low, 6), "中性": round(center, 6), "乐观": round(high, 6),
        "较中性溢价百分比": round((basis / center - 1) * 100, 6) if center else None,
        "至悲观差距百分比": round((1 - low / basis) * 100, 6),
        "现价反推": {k: round(v, 6) for k, v in implied.items()},
        "情景输入": rows, "资产输入": model_inputs, "置信度": "低，轻量初筛",
    }
    if dcf_details:
        result["DCF分解"] = dcf_details
    if method == "成熟盈利":
        result["利润口径说明"] = {label: pe_basis(row)[0] for label, row in rows.items()}
        result["现价反推利润口径"] = result["利润口径说明"]["中性"]["声明口径"]
    result["估值等级"] = audit["等级"]
    if audit["等级"] == "A":
        result["置信度"] = "A，宿主复核判断；程序不保证事实或未来准确"
    result["经济关系检查"] = audit
    result["等级说明"] = ("A为宿主关键锚点复核后的判断，程序不证明未来准确"
                          if audit["等级"] == "A" else "B为明确假设下的条件值，不是高置信度内在价值")
    return result
