"""Recalculate a host-reviewed evidence packet with the bundled models."""
import argparse
from datetime import datetime
from pathlib import Path

from calibration import cash_path
from admission import review_integrity, review_restatements, small_cap_review, valuation_gap
from models import value
from economics import refs_in
from risks import evaluate
from support import RULE, digest, json_bytes, load


def checked_refs(refs):
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x.strip() for x in refs):
        raise ValueError("需要原文证据引用")


def calculate(packet):
    cutoff = datetime.fromisoformat(packet["行情时点"])
    if cutoff.utcoffset() is None:
        raise ValueError("行情时点需带时区")
    route = review_restatements(packet["准入"], packet.get("追溯复核", []), checked_refs)
    route, integrity = review_integrity(route, packet.get("诚信时效复核", []), cutoff.date(), checked_refs)
    out = {"证券": packet["证券"], "行情时点": cutoff.isoformat(), "准入": route,
           "政策SHA256": digest(RULE), "估值": None, "条件演算": None}
    named = packet.get("研究范围") == "点名单列"
    if named:
        if packet.get("点名小市值") is not True or packet.get("市值门槛判定") != "未达标":
            raise ValueError("点名单列需确认用户点名和市值未达标")
        out["研究范围"] = "点名单列"
    elif packet.get("市值门槛判定") == "未达标":
        out.update(准入="硬性排除", 缺口=["§1.3 常规池流通市值不足 50 亿元"])
    elif packet.get("市值门槛判定") == "待核" and route != "硬性排除":
        out.update(准入="待核", 缺口=["§1.3 流通市值待核"])
    if integrity:
        out["诚信时效复核"] = integrity
        pending = [item["原因"] for item in integrity if item["本项分流"] == "待核"]
        if pending:
            out["缺口"] = [*out.get("缺口", []), *pending]
    if packet.get("追溯复核"):
        out["追溯复核"] = packet["追溯复核"]
    if out["准入"] in ("待核", "硬性排除"):
        return out
    checked_refs(packet["准入依据"])
    if named:
        exception = small_cap_review(packet.get("小市值复核"), checked_refs)
        if exception != "继续初筛":
            reason = "实际成交需求待核" if exception == "待核" else "未满足实际成交需求"
            out.update(准入=exception, 缺口=[f"§1.3 {reason}"])
            return out
    day = cutoff.date().isoformat()
    if "排雷底稿" in packet:
        risk = packet["排雷底稿"]
        for item in risk["数值"].values():
            checked_refs(item["证据"])
        out["财报排雷"] = evaluate(risk, day)
    gaps = valuation_gap(packet)
    if gaps:
        out.update(估值状态="待核", 估值缺口=gaps)
    card = packet.get("估值卡")
    if not card:
        return out
    if card["截至日"] != day or not card.get("财务期间"):
        raise ValueError("估值口径与行情基准不一致")
    checked_refs(card["证据"])
    for refs in refs_in(card):
        checked_refs(refs)
    result = value(card, packet["基准收盘元"], packet["总股数"])
    result.update(财务期间=card["财务期间"], 证据=card["证据"], 口径=card.get("口径"))
    if result["估值等级"] == "C":
        out.update(估值状态="待核", 估值缺口=result["估值缺口"], 经济关系检查=result["经济关系检查"], 估值等级="C")
        return out
    cash = card.get("六年现金回收")
    if cash:
        if card["方法"] not in ("成熟盈利", "合同服务", "EPV") or not cash.get("依据"):
            raise ValueError("现金回收适用于有依据的成熟盈利校准")
        checked_refs(cash["证据"])
        result["六年现金回收"] = {
            **cash_path(cash, packet["基准收盘元"] * packet["总股数"] / 100_000_000),
            "依据": cash["依据"], "证据": cash["证据"],
        }
    out["估值" if route == "继续初筛" and not named else "条件演算"] = result
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    args = p.parse_args()
    try:
        print(json_bytes(calculate(load(args.input))).decode(), end="")
    except (KeyError, TypeError, ValueError) as exc:
        p.exit(2, f"输入待核：{exc}\n")
