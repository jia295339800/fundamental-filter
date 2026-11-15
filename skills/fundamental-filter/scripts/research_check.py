"""Adapt an existing research table to the shared checks; no new valuation math."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from support import json_bytes, load

BLOCKS = ("经营预测", "现金流桥", "增长约束", "项目投入", "价值归属", "直接现金依据", "稳定期")
EXPENSES = ("销售费用亿元", "管理费用亿元", "研发费用亿元", "税金及附加亿元", "其他经营费用亿元")


def fingerprint(card):
    return hashlib.sha256(json.dumps(card, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def adapt(card):
    """Accept row amounts without classifying them as fixed or inventing cash data.

    Existing native 经济关系 stays valid. Shortcuts inherit only explicitly supplied
    scope/evidence; prepare() applies the same economic and missing-evidence checks.
    """
    card = deepcopy(card)
    for label, row in card.get("情景", {}).items():
        shortcuts = [key for key in BLOCKS if key in row]
        if not shortcuts:
            continue
        scope = card.get("研究口径", {})
        if not isinstance(scope, dict):
            raise ValueError("研究口径须为对象")
        economic = row.setdefault("经济关系", {})
        if not isinstance(economic, dict):
            raise ValueError("经济关系须为对象")
        for key in ("币种", "金额单位", "范围"):
            if key in scope:
                if key in economic and economic[key] != scope[key]:
                    raise ValueError(label + "研究口径与经济关系冲突：" + key)
                economic[key] = scope[key]
        economic.setdefault("依据", row.get("依据", card.get("依据", "")))
        economic.setdefault("证据", row.get("证据", card.get("证据", [])))
        for key in shortcuts:
            if key in economic:
                raise ValueError(label + key + "同时使用简表和原生输入，请保留一份")
            block = row.pop(key)
            items = block if isinstance(block, list) else [block]
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError(key + "记录须为对象")
                item.setdefault("依据", economic.get("依据", ""))
                item.setdefault("证据", economic.get("证据", []))
                if key == "经营预测":
                    direct = [name for name in EXPENSES if name in item]
                    if direct and "费用" in item:
                        raise ValueError("直接费用与费用数组同时填写，请保留一份")
                    if not direct and "费用" not in item:
                        raise ValueError("经营表缺费用口径；确无费用时显式给费用空数组及依据，缺证则跳过并说明")
                    if direct:
                        item["费用"] = [{"名称": name.removesuffix("亿元"), "金额亿元": item.pop(name)}
                                      for name in direct]
            economic[key] = block
    return card


def check(card):
    from economics import prepare, refs_in
    # Reference shape is checked here; source truth and historical release remain
    # with the existing evidence workflow, not this arithmetic adapter.
    adapted = adapt(card)
    for refs in refs_in(adapted):
        if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x.strip() for x in refs):
            raise ValueError("需要原文证据引用")
    prepared, audit = prepare(adapted)
    failed = any(x["程度"] == "错误" for x in audit["检查"])
    coverage, submitted = {}, 0
    for label, row in prepared.get("情景", {}).items():
        economic, skipped = row.get("经济关系", {}), row.get("未检查", {})
        if not isinstance(skipped, dict):
            raise ValueError("未检查须为按检查名称索引的对象")
        coverage[label] = {}
        for key in BLOCKS:
            present = bool(economic.get(key))
            applicable = key not in ("直接现金依据", "稳定期") or prepared.get("方法") == "DCF"
            if key == "直接现金依据" and economic.get("现金流桥"):
                applicable = False
            if present and applicable:
                submitted += 1
                coverage[label][key] = {"状态": "失败或未完成" if failed else "已检查",
                                        "说明": "已交给共享prepare；不证明事实或假设正确"}
            else:
                reason = skipped.get(key, {})
                if not isinstance(reason, dict):
                    raise ValueError("未检查原因须含类型和说明")
                kind = reason.get("类型", "未说明")
                if kind not in ("缺证", "不适用", "工具不支持", "未说明"):
                    raise ValueError("未检查类型须为缺证、不适用或工具不支持")
                coverage[label][key] = {"状态": "未检查", "类型": kind,
                                        "说明": reason.get("说明", "未提供输入或未说明适用性")}
                if present and not applicable:
                    coverage[label][key] = {"状态": "未检查", "类型": "不适用",
                                            "说明": "当前方法/现金路径不使用此块，不能计为已检查"}
    return {"状态": "存在矛盾" if failed else ("已检查已声明关系" if submitted else "无可检查关系"),
            "估值等级": audit["等级"], "经济关系检查": audit, "覆盖": coverage,
            "输入SHA256": fingerprint(card), "适配后卡SHA256": fingerprint(prepared),
            "适配后估值卡": prepared,
            "用途": "研究校验，不裁定准入，不生成价格；未检查不等于通过；原件与未来假设仍由Agent复核"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="研究卡或含估值卡的现有JSON；结果写stdout")
    args = parser.parse_args(argv)
    try:
        packet = load(args.input)
        print(json_bytes(check(packet.get("估值卡", packet))).decode(), end="")
    except (KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"输入待核：{exc}\n")


if __name__ == "__main__":
    main()
