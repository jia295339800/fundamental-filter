"""Apply the reviewed admission decisions consistently across entry points."""

from calendar import monthrange
from datetime import date

INTEGRITY_LOOKBACK_YEARS = 10

ROUTES = ("继续初筛", "强化审查", "待核", "硬性排除")


def review_restatements(route, events, check_refs):
    if route not in ROUTES:
        raise ValueError("准入分流不明")
    for event in events:
        if not event.get("证据"):
            raise ValueError("追溯调整需有原始证据")
        check_refs(event["证据"])
        kind = event["类型"]
        if kind == "重大会计差错":
            decision = "硬性排除"
        elif kind == "待核":
            decision = "待核"
        elif kind in ("同控合并", "实质政策变更"):
            decision = "强化审查"
        elif kind == "仅每股重算":
            decision = "继续初筛"
        elif kind == "纯科目改列":
            impact = event.get("核心总额", {})
            status = [impact.get(key) for key in ("净利润", "净资产", "经营现金流净额")]
            if not event.get("准则依据") or any(item not in ("未变", "变化") for item in status):
                decision = "待核"
            elif "变化" in status:
                decision = "强化审查"
            else:
                decision = "继续初筛"
        else:
            raise ValueError("未知追溯调整类型")
        route = max((route, decision), key=ROUTES.index)
    return route


def review_integrity(route, events, day, check_refs):
    """Apply §2.3.2 to confirmed cases without overriding independent gates."""
    if not isinstance(events, list):
        raise ValueError("诚信时效复核须为逐案列表")
    findings = []
    for event in events:
        if not event.get("证据"):
            raise ValueError("诚信时效复核需有原始证据")
        check_refs(event["证据"])
        raw_date = event["正式认定日"]
        impact = event["现时重大影响"]
        if impact is not None and not isinstance(impact, bool):
            raise ValueError("现时重大影响需为布尔值或 null")
        expiry = None
        if raw_date is not None:
            confirmed = date.fromisoformat(raw_date)
            if confirmed > day:
                raise ValueError("正式认定日期晚于研究基准日")
            year = confirmed.year + INTEGRITY_LOOKBACK_YEARS
            last_day = monthrange(year, confirmed.month)[1]
            expiry = confirmed.replace(year=year, day=min(confirmed.day, last_day))
        if expiry is None:
            decision, reason = "待核", "正式认定日待核"
        elif day < expiry:
            decision, reason = "硬性排除", "重大事项在回看期限内"
        elif impact is True:
            decision, reason = "硬性排除", "同一事项仍有重大现实影响"
        elif impact is None:
            decision, reason = "待核", "同一事项的现时重大影响待核"
        else:
            decision, reason = "继续初筛", "保留历史风险并继续其他检查"
        findings.append({**event, "期满日": expiry.isoformat() if expiry else None,
                         "本项分流": decision, "原因": reason})
        route = max((route, decision), key=ROUTES.index)
    return route, findings


def small_cap_review(review, check_refs):
    if not review:
        return "待核"
    if not isinstance(review.get("成交需求判断"), str) or not review["成交需求判断"].strip():
        raise ValueError("需记录小市值样本的实际成交需求判断")
    if not review.get("证据"):
        raise ValueError("小市值复核需有成交证据")
    check_refs(review["证据"])
    status = review.get("满足成交需求")
    if status is not None and not isinstance(status, bool):
        raise ValueError("成交需求结论需为布尔值或 null")
    if status is False:
        return "硬性排除"
    return "继续初筛" if status is True else "待核"


def valuation_gap(packet):
    gaps = packet.get("估值缺口", [])
    if not isinstance(gaps, list) or any(not isinstance(gap, str) or not gap.strip() for gap in gaps):
        raise ValueError("估值缺口须逐项说明")
    if gaps and packet.get("估值卡"):
        raise ValueError("待核模型不能同时提交估值卡")
    return gaps
