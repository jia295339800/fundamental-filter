"""Two bounded cash identities, after explicit source/period review; no valuation."""
import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
import re
import subprocess

from support import digest, load, json_bytes

MONEY = r"[-−－]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
TOKEN = re.compile(r"(?<![\d,.])(?:--+|[—–]|不适用|[-−－]?\d[\d,]*(?:\.\d+)?%|" + MONEY + r")(?![\d,.])")
IDENTITIES = {
    "经营现金流": ("经营现金流入小计", "经营现金流出小计"),
    "购建支出": ("投资活动现金流出小计", "投资支付", "取得子公司支付", "其他投资支付"),
}


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("金额缺失；空值不得当作0")
    try:
        out = Decimal(str(value).replace(",", "").replace("−", "-").replace("－", "-"))
    except InvalidOperation:
        raise ValueError("金额格式不明") from None
    if not out.is_finite():
        raise ValueError("金额非有限数")
    return out


def summary_amount(part, cash=False):
    """Keep missing cells and signed numbers distinct in the four-column summary."""
    # Reject an isolated/spaced minus: it could be either a missing cell or a sign.
    if re.search(r"(?<![-−－])[-−－]\s+\d", part):
        raise ValueError("金额负号与数值分离，需核原页")
    if re.search(r"[-−－]{2,}\d", part):
        raise ValueError("占位横线与金额粘连，需核原页")
    tokens = [m.group() for m in TOKEN.finditer(part)]
    missing = lambda s: s.startswith("--") or s in ("—", "–", "不适用")
    money = lambda s: bool(re.fullmatch(MONEY, s))
    rate = lambda s: s.endswith("%") or missing(s)
    if len(tokens) != 4 or not rate(tokens[1]) or not rate(tokens[3]):
        raise ValueError("主表四列或同比列无法唯一定位")
    if cash and not (missing(tokens[0]) and missing(tokens[1])):
        raise ValueError("累计现金流列未能由季度缺省单元区分")
    if not cash and not money(tokens[0]):
        raise ValueError("主表本季度金额缺失或归属不明")
    if not money(tokens[2]):
        raise ValueError("累计金额缺失或归属不明")
    # Glued placeholder/minus or an unmatched number must not be partially consumed.
    rest = TOKEN.sub("", part)
    if re.search(r"\d|[-−－]", rest):
        raise ValueError("金额或占位符边界无法唯一识别")
    return number(tokens[2])


@lru_cache(maxsize=4)
def original_pages(filename, fingerprint):
    from disclosures import source_text
    text = source_text(Path(filename))
    pages = {}
    for match in re.finditer(r"(?m)^=== PAGE (\d+) ===\n(.*?)(?=^=== PAGE |\Z)", text, re.S):
        pages[int(match[1])] = match[2]
    return pages


def review_cash(risk, symbol, base):
    """Suppress unverified current cash inputs, preserving unrelated risk checks.

    The host confirms original-page column membership. This verifies the receipt,
    original hash, period, amounts, and fixed identities; it is not machine vision.
    """
    clean = deepcopy(risk)
    evidence = clean.pop("现金原表核验", None)
    fields = clean.get("数值", {})
    active = [name for name in IDENTITIES if fields.get(name, {}).get("本期") is not None]
    if not active:
        return clean, {"状态": "不适用", "字段": {}}
    decisions = {}
    try:
        if not isinstance(evidence, dict) or evidence.get("证券") != symbol:
            raise ValueError("现金原表核验记录未提供或主体不符")
        if evidence.get("本期末") != risk["本期末"] or evidence.get("单位") != "元":
            raise ValueError("现金原表期间或金额单位不符")
        source = evidence["原件"]
        path = (base / source["文件"]).resolve()
        if path.suffix.lower() != ".pdf" or digest(path) != source["SHA256"]:
            raise ValueError("现金原件缺失或哈希不符")
        if not source.get("URL", "").startswith("https://"):
            raise ValueError("现金原件来源缺失")
        items = evidence["字段"]
        pages = original_pages(str(path), source["SHA256"])

        def amount(name):
            item = items[name]
            if (item.get("本期末") != risk["本期末"] or item.get("本期列已核") is not True
                    or item.get("列归属方式") != "原页核对"
                    or not item.get("列归属依据") or not item.get("原文值")
                    or any(isinstance(item.get(k), bool) or not isinstance(item.get(k), int)
                           or item[k] <= 0 for k in ("页码", "表头页码"))):
                raise ValueError(name + "本期原页列归属未确认")
            value = number(item["金额元"])
            if number(item["原文值"]) != value:
                raise ValueError(name + "声明金额与原文值不一致")
            if name != "经营现金流" and value < 0:
                raise ValueError(name + "应为非负现金收支金额")
            if item["表头页码"] not in pages:
                raise ValueError(name + "表头页码不存在")
            literal = str(item["原文值"])
            if not re.search(r"(?<![\d,.−－-])" + re.escape(literal) + r"(?![\d,.])",
                             pages.get(item["页码"], "")):
                raise ValueError(name + "金额不在声明原页，不能认证来源")
            return value

        for name in active:
            try:
                value = amount(name)
                others = [amount(k) for k in IDENTITIES[name]]
                expected = others[0] - sum(others[1:])
                if abs(value - expected) > Decimal("0.02"):
                    raise ValueError(name + "与本期同表小计不勾稽")
                research_value = number(fields[name]["本期"])
                rounding = items[name].get("研究金额舍入")
                comparison = "精确金额一致"
                if rounding is not None:
                    if (not isinstance(rounding, dict) or rounding.get("单位") != "亿元"
                            or rounding.get("方式") != "四舍五入"
                            or type(rounding.get("小数位")) is not int
                            or not 0 <= rounding["小数位"] <= 10):
                        raise ValueError(name + "研究金额舍入声明无效")
                    quantum = Decimal(1).scaleb(-rounding["小数位"])
                    rounded = (value / Decimal(100000000)).quantize(quantum, rounding=ROUND_HALF_UP)
                    if research_value != rounded:
                        raise ValueError(name + "研究值不符合声明的四舍五入结果")
                    comparison = "按声明四舍五入一致"
                elif abs(research_value * Decimal(100000000) - value) > Decimal("0.02"):
                    raise ValueError(name + "研究值与已核原表金额不一致；舍入值须声明精度")
                # Downstream cash comparisons use the verified original amount,
                # so display rounding cannot flip a borderline cash judgement.
                fields[name]["本期"] = float(value / Decimal(100000000))
                decisions[name] = {"状态": "勾稽通过", "金额元": str(value),
                    "研究输入亿元": str(research_value), "金额比较": comparison,
                    "依据": "原件指纹及宿主已确认的各项本期列，固定现金关系通过",
                    "原件SHA256": source["SHA256"]}
            except (KeyError, TypeError, ValueError) as exc:
                decisions[name] = {"状态": "待核", "原因": str(exc)}
    except (KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        decisions = {name: {"状态": "待核", "原因": str(exc)} for name in active}
    for name, row in decisions.items():
        if row["状态"] == "待核":
            fields[name].pop("本期", None)
    return clean, {"状态": "待核" if any(x["状态"] == "待核" for x in decisions.values()) else "勾稽通过",
        "字段": decisions, "边界": "只核本期CFO/购建及所需小计；原页列归属仍由宿主确认，未从线性文本自动认证；未核字段不参与现金判断，不改变准入。"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="原研究复核包，含排雷底稿及现金原表核验")
    args = p.parse_args(argv)
    packet = load(args.input)
    clean, result = review_cash(packet.get("排雷底稿", {}), packet["证券"], args.input.resolve().parent)
    print(json_bytes({"现金字段核验": result, "可用于排雷底稿": clean}).decode(), end="")
