"""Economic identities and conditional cash-flow provenance; no forecast defaults."""
from copy import deepcopy
from math import fsum, isclose

from calibration import finite, nonnegative


def same(actual, expected):
    return isclose(finite(actual), finite(expected), rel_tol=1e-6, abs_tol=1e-6)


def refs_in(card):
    """Only the new economic/grade blocks; use the caller's existing evidence gate."""
    from research_check import adapt
    card = adapt(card)
    def walk(node):
        if isinstance(node, dict):
            if "证据" in node:
                yield node["证据"]
            for key, value in node.items():
                if key != "证据":
                    yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)
    yield from walk(card.get("等级依据", {}))
    for row in card.get("情景", {}).values():
        yield from walk(row.get("经济关系", {}))
        yield from walk(row.get("非经营及非经常项目", []))


def pe_basis(row):
    """Record the declared profit basis; never infer normalization from a key name."""
    kinds = ("归母净利润", "扣非归母利润", "正常化可持续盈利")
    kind = row.get("利润口径")
    if kind is not None and kind not in kinds:
        raise ValueError("利润口径须为归母净利润、扣非归母利润或正常化可持续盈利")
    if "估值利润亿元" in row and "正常化利润亿元" in row and not same(row["估值利润亿元"], row["正常化利润亿元"]):
        raise ValueError("估值利润与旧利润字段不一致")
    notes = []
    if kind is None:
        notes.append("利润口径未声明；旧字段名称不证明已扣非或已正常化")
    items = row.get("非经营及非经常项目")
    if items is None:
        notes.append("非经营/非经常项目及持续性未说明，不能自动认定利润可持续")
    elif not isinstance(items, list):
        raise ValueError("非经营及非经常项目须为逐项数组；无相关项目时显式给空数组")
    else:
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("特殊损益项目须为对象")
            if any(not isinstance(item.get(k), str) or not item[k].strip()
                   for k in ("项目", "持续性假设", "估值处理")):
                notes.append("特殊损益项目缺名称、持续性假设或估值处理")
    adjustment = row.get("正常化调整说明")
    if kind == "正常化可持续盈利" and not (isinstance(adjustment, str) and adjustment.strip()):
        notes.append("正常化声明缺调整说明，不能视为已完成持续性调整")
    return {"声明口径": kind or "未声明", "数值字段": "估值利润亿元" if "估值利润亿元" in row else "正常化利润亿元（兼容旧字段，不推断正常化）",
            "非经营及非经常项目": items, "正常化调整说明": adjustment, "待核": notes,
            "边界": "这是Agent的口径及持续性声明；程序不验证真实归属、是否应扣非或倍数是否合理"}, notes


def operating(row):
    """Costs may be supplied directly or as fixed, revenue-linked, staged parts."""
    revenue = nonnegative(row["收入亿元"])
    if "营业成本亿元" in row:
        cost = nonnegative(row["营业成本亿元"])
        if "毛利率" in row and not same(revenue - cost, revenue * finite(row["毛利率"])):
            raise ValueError("营业成本与毛利率不一致")
    else:
        margin = finite(row["毛利率"])
        if margin > 1:
            raise ValueError("毛利率不能大于1")
        cost = revenue * (1 - margin)
    expenses = []
    if not isinstance(row.get("费用", []), list):
        raise ValueError("费用须为逐项数组")
    for item in row.get("费用", []):
        if not isinstance(item, dict) or not item.get("名称"):
            raise ValueError("费用须有名称")
        if "金额亿元" in item and not any(k in item for k in ("固定亿元", "收入比例", "阶段亿元")):
            expense = nonnegative(item["金额亿元"])
        else:
            expense = (nonnegative(item.get("固定亿元", 0))
                       + revenue * nonnegative(item.get("收入比例", 0))
                       + nonnegative(item.get("阶段亿元", 0)))
            if "金额亿元" in item and not same(item["金额亿元"], expense):
                raise ValueError("费用直接金额与分解不一致")
        if "合计亿元" in item and not same(item["合计亿元"], expense):
            raise ValueError("费用合计与固定、变动、阶段支出不一致")
        expenses.append(expense)
    ebit = revenue - cost - fsum(expenses)
    finite(ebit)
    if "EBIT亿元" in row and not same(row["EBIT亿元"], ebit):
        raise ValueError("收入、成本、费用与EBIT不勾稽")
    return {"收入亿元": revenue, "营业成本亿元": cost,
            "期间费用亿元": fsum(expenses), "EBIT亿元": ebit}


def cash_bridge(row, ebit=None):
    kind = row["现金流类型"]
    if kind not in ("FCFF", "FCFE"):
        raise ValueError("现金流类型须为FCFF或FCFE")
    detailed = ("资本开支亿元", "折旧摊销亿元", "营运资金增量亿元")
    if any(key in row for key in detailed):
        net = (nonnegative(row["资本开支亿元"]) - nonnegative(row["折旧摊销亿元"])
               + finite(row["营运资金增量亿元"]))
        if "净再投资亿元" in row and not same(row["净再投资亿元"], net):
            raise ValueError("总净再投资与资本开支、折旧、营运资金重复或不勾稽")
    else:
        net = finite(row["净再投资亿元"])
    other = finite(row.get("其他经营非现金调整亿元", 0))
    if kind == "FCFF":
        if ebit is not None:
            if "EBIT亿元" in row and not same(ebit, row["EBIT亿元"]):
                raise ValueError("经营路径与现金桥EBIT不一致")
        else:
            ebit = finite(row["EBIT亿元"])
        nopat = ebit - finite(row["经营现金税亿元"])
        if "税后经营利润亿元" in row and not same(row["税后经营利润亿元"], nopat):
            raise ValueError("EBIT、经营现金税与税后利润不勾稽")
        cash = nopat + other - net
        if "税后利息亿元" in row and "净举债亿元" in row:
            fcfe = cash - nonnegative(row["税后利息亿元"]) + finite(row["净举债亿元"])
            if "FCFE亿元" in row and not same(row["FCFE亿元"], fcfe):
                raise ValueError("FCFF到FCFE融资桥不勾稽")
        else:
            fcfe = None
        fcff = cash
    else:
        cash = finite(row["净利润亿元"]) + other - net + finite(row["净举债亿元"])
        if ebit is not None and "经营现金税亿元" in row and "税后利息亿元" in row:
            if not same(row["净利润亿元"], ebit - finite(row["经营现金税亿元"]) - nonnegative(row["税后利息亿元"])):
                raise ValueError("同范围EBIT、经营税、税后利息与净利润不勾稽")
        fcfe = cash
        fcff = (cash + nonnegative(row["税后利息亿元"]) - finite(row["净举债亿元"])) if "税后利息亿元" in row else None
    if kind + "亿元" in row and not same(row[kind + "亿元"], cash):
        raise ValueError(kind + "与利润、再投资、融资桥不勾稽")
    for amount in (net, fcff, fcfe):
        if amount is not None:
            finite(amount)
    return {"净再投资亿元": net, "FCFF亿元": fcff, "FCFE亿元": fcfe}


def prepare(card):
    """Derive/check only declared economic relationships; legacy math stays usable."""
    card = deepcopy(card)
    findings, diagnostics = [], {}

    def issue(level, label, text):
        findings.append({"程度": level, "情景": label, "说明": text})

    def evidence(node, label, name):
        if not isinstance(node, dict):
            raise ValueError(name + "需为对象")
        refs = node.get("证据")
        if not (isinstance(refs, list) and refs and all(isinstance(r, str) and r.strip() for r in refs)
                and isinstance(node.get("依据"), str) and node["依据"].strip()):
            issue("条件", label, name + "缺少可定位依据；计算不代表事实已核实")

    requested = card.get("估值等级", "B")
    if requested not in ("A", "B", "C"):
        raise ValueError("估值等级须为A、B或C")
    if card.get("方法") == "成熟盈利":
        declared = {row["利润口径"] for row in card.get("情景", {}).values()
                    if isinstance(row.get("利润口径"), str)}
        if len(declared) > 1:
            issue("错误", "全卡", "三情景利润口径不一致；应使用同一归属和调整口径比较")
    for label, x in card.get("情景", {}).items():
        if card.get("方法") == "成熟盈利":
            try:
                _, notes = pe_basis(x)
                for note in notes:
                    issue("条件", label, note)
            except ValueError as exc:
                issue("错误", label, str(exc))
        e = x.get("经济关系")
        if e is None:
            issue("条件", label, "旧式输入未列经济来源；保留条件计算，不支持自动升为A")
            continue
        if not isinstance(e, dict):
            raise ValueError("经济关系须为对象")
        d = diagnostics[label] = {"经营预测": [], "现金流桥": [], "增长与再投资": [], "项目时滞": []}
        evidence(e, label, "经济关系")
        if e.get("币种") != "CNY" or e.get("金额单位") != "亿元" or not e.get("范围"):
            issue("错误", label, "经济关系需声明CNY、亿元及同一经济范围；原币须先转换")
        try:
            def scope(row, period):
                for key in ("币种", "金额单位", "范围"):
                    if key in row and row[key] != e.get(key):
                        raise ValueError("经营预测/现金桥与公共" + key + "不一致")
                if "期" in row and (isinstance(row["期"], bool) or not isinstance(row["期"], int) or row["期"] != period):
                    raise ValueError("经营预测/现金桥期序不一致")
            operations = e.get("经营预测", [])
            for index, row in enumerate(operations, 1):
                evidence(row, label, "经营预测")
                scope(row, index)
                d["经营预测"].append(operating(row))
            bridges = e.get("现金流桥", [])
            if operations and bridges and len(operations) != len(bridges):
                raise ValueError("经营预测与现金桥期间数不一致")
            for index, row in enumerate(bridges):
                evidence(row, label, "现金流桥")
                scope(row, index + 1)
                ebit = d["经营预测"][index]["EBIT亿元"] if operations else None
                d["现金流桥"].append(cash_bridge(row, ebit))
                if operations and row["现金流类型"] == "FCFE" and "经营现金税亿元" not in row:
                    issue("条件", label, "FCFE桥缺经营税到净利润衔接，不能仅凭融资换算升为A")
            if card["方法"] == "DCF":
                if bridges:
                    cash = [row["FCFF亿元"] for row in d["现金流桥"]]
                    if any(v is None for v in cash):
                        raise ValueError("FCFE不能直接用WACC折现；须有税后利息和净举债以转回FCFF")
                    if "显性期逐年FCFF亿元" in x:
                        if len(cash) != len(x["显性期逐年FCFF亿元"]) or any(not same(a, b) for a, b in zip(cash, x["显性期逐年FCFF亿元"])):
                            raise ValueError("显性FCFF数组与经营现金桥不一致")
                    else:
                        x["显性期逐年FCFF亿元"] = cash
                else:
                    direct = e.get("直接现金依据", {})
                    evidence(direct, label, "直接FCFF")
                    if direct.get("现金流类型") != "FCFF":
                        raise ValueError("直接现金用于企业DCF时必须声明FCFF归属")
                    if not direct.get("再投资处理"):
                        issue("条件", label, "直接现金未说明维持、增长投入是否已扣，不能判为A")
                stable = e.get("稳定期", {})
                evidence(stable, label, "稳定期")
                if "税后经营利润亿元" in stable and "增量ROIC" in stable:
                    nopat = finite(stable["税后经营利润亿元"])
                    roc = finite(stable["增量ROIC"])
                    if nopat <= 0 or roc <= 0:
                        raise ValueError("稳定期经营桥需要正NOPAT和正增量ROIC")
                    rr = finite(x["永续增长率"]) / roc
                    expected = nopat * (1 - rr)
                    if "再投资率" in stable and not same(stable["再投资率"], rr):
                        raise ValueError("稳定期增长、ROIC与再投资率不一致")
                    if "永续首年FCFF亿元" in x and not same(x["永续首年FCFF亿元"], expected):
                        raise ValueError("终值现金与稳定NOPAT、增长和再投资不一致")
                    x["永续首年FCFF亿元"] = expected
                    d["稳定期"] = {"NOPAT亿元": nopat, "再投资率": rr, "FCFF亿元": expected, "增量ROIC": roc}
                    if roc > finite(x["WACC"]):
                        if not stable.get("超额回报依据"):
                            issue("条件", label, "稳定ROIC高于WACC，长期竞争优势尚待解释")
                    if "收入亿元" in stable and finite(stable["收入亿元"]) <= 0:
                        raise ValueError("稳定收入须为正")
                    if "经营现金税率" in stable and not 0 <= finite(stable["经营现金税率"]) < 1:
                        raise ValueError("稳定现金税率须在0到1之间，不含1")
                    if "收入亿元" in stable and "经营现金税率" in stable and "营业利润率" in stable:
                        revenue, tax, margin = finite(stable["收入亿元"]), finite(stable["经营现金税率"]), finite(stable["营业利润率"])
                        if not same(nopat, revenue * margin * (1 - tax)):
                            raise ValueError("稳定收入、利润率、税与NOPAT不勾稽")
                elif not stable.get("再投资与增长说明"):
                    issue("条件", label, "直接终值现金未解释增长与再投资；保留B类条件值")
            for row in e.get("增长约束", []):
                evidence(row, label, "增长约束")
                if row["类型"] == "稳定回报":
                    g = finite(row["再投资率"]) * finite(row["增量ROIC"])
                    if row.get("既有资产效率不变") is not True:
                        issue("条件", label, "回报或效率变化时g=再投资率×ROIC不能解释全部增长")
                    elif "利润增长率" in row and not same(g, row["利润增长率"]):
                        raise ValueError("已声明效率不变，但增长与再投资、增量ROIC不勾稽")
                    d["增长与再投资"].append({"仅新增投资贡献增长率": g})
                elif row["类型"] == "收入资本":
                    ratio = finite(row["增量销售资本比"])
                    if ratio <= 0:
                        raise ValueError("增量销售资本比需为正")
                    investment = finite(row["新增收入亿元"]) / ratio
                    if "净再投资亿元" in row and not same(row["净再投资亿元"], investment):
                        raise ValueError("新增收入、销售资本比与总净再投资不勾稽（已含营运资金）")
                    if investment < 0 and not row.get("资本释放依据"):
                        issue("条件", label, "收入下降不保证资本可回收；负再投资需单独取证")
                    if "期" in row:
                        period = row["期"]
                        if isinstance(period, bool) or not isinstance(period, int) or not 1 <= period <= len(bridges):
                            raise ValueError("当期收入资本约束须对应有效现金桥期数")
                        if not same(investment, d["现金流桥"][period - 1]["净再投资亿元"]):
                            raise ValueError("增长所需再投资与同一期现金桥不一致；若有时滞应改用项目投入说明")
                        base_revenue = (d["经营预测"][period - 2]["收入亿元"] if period > 1 and operations
                                        else row.get("基期收入亿元"))
                        if operations and base_revenue is not None:
                            delta = d["经营预测"][period - 1]["收入亿元"] - finite(base_revenue)
                            if not same(row["新增收入亿元"], delta):
                                raise ValueError("收入资本约束的净新增收入与经营路径不一致")
                    d["增长与再投资"].append({"所需净再投资亿元": investment})
                else:
                    raise ValueError("未知增长约束类型")
            allocated = {}
            for row in e.get("项目投入", []):
                evidence(row, label, "项目投入")
                start, finish = row["投入期"], row["收益期"]
                if (isinstance(start, bool) or isinstance(finish, bool) or not isinstance(start, int)
                        or not isinstance(finish, int) or start < 0 or finish < 1 or finish < start):
                    raise ValueError("项目投入/收益期须为整数，收益不得早于投入；0期仅指已发生投入")
                amount = nonnegative(row["投入亿元"])
                d["项目时滞"].append({"投入期": start, "收益期": finish, "间隔期数": finish - start})
                if start and bridges:
                    field = row["投入科目"]
                    if field not in ("资本开支亿元", "净再投资亿元") or start > len(bridges):
                        raise ValueError("项目投入科目或显性投入期无效")
                    key = (start, field)
                    allocated[key] = allocated.get(key, 0) + amount
                elif start and not bridges:
                    issue("条件", label, "直接现金路径的项目投入须由宿主核是否已扣，程序未强行补扣")
            for (period, field), amount in allocated.items():
                available = (bridges[period - 1].get(field) if field != "净再投资亿元"
                             else d["现金流桥"][period - 1][field])
                if available is None or finite(available) + 1e-6 < amount:
                    raise ValueError("同一期项目投入未被所声明的现金流科目覆盖")
            seen = {}
            for item in e.get("价值归属", []):
                identity = (item["项目"], item["范围"])
                position = item["位置"]
                if position not in ("经营现金", "超额与非经营资产", "融资债", "外部权益"):
                    raise ValueError("未知价值归属位置")
                if identity in seen:
                    raise ValueError("同一经济项目重复计值：" + item["项目"])
                seen[identity] = position
                if item.get("须从企业价值扣除") is True and position == "经营现金":
                    raise ValueError("同一优先索偿已在企业现金扣除又要求在股权桥扣除")
        except (KeyError, TypeError, ValueError) as exc:
            issue("错误", label, str(exc))
    grade = requested
    if requested == "A":
        basis = card.get("等级依据", {})
        evidence(basis, "全卡", "A类判断")
        if basis.get("关键经营与估值锚点已复核") is not True:
            issue("条件", "全卡", "A类需宿主复核关键锚点；数学检查不能自行证明高置信度")
    if any(f["程度"] == "错误" for f in findings) or requested == "C":
        grade = "C"
    elif findings:
        grade = "B"
    return card, {"等级": grade, "检查": findings, "分情景": diagnostics,
                  "边界": "程序只核已声明关系及来源接口，不能验证原件事实、未来假设或预测准确性"}


def reverse_operating(row, expectation):
    """Hold the explicit FCFF, WACC, g and stable ROC fixed; never infer a unique story."""
    stable = row.get("经济关系", {}).get("稳定期", {})
    if "增量ROIC" not in stable:
        return
    rr = row["永续增长率"] / finite(stable["增量ROIC"])
    if 1 - rr <= 0:
        return
    required_cash = expectation["固定中性显性期现金及资本成本所需永续首年FCFF亿元"]
    nopat = required_cash / (1 - rr)
    expectation["固定中性显性现金、WACC、g及稳定ROIC所需永续首年NOPAT亿元"] = nopat
    if "收入亿元" in stable and "经营现金税率" in stable:
        revenue, tax = finite(stable["收入亿元"]), finite(stable["经营现金税率"])
        if revenue > 0 and 0 <= tax < 1:
            expectation["再固定中性稳定收入与税率所需营业利润率"] = nopat / (revenue * (1 - tax))
