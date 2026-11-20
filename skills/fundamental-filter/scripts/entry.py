"""Collect, pause for evidence review, then finalize a dated Shenzhen A-share batch."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from models import value
from economics import refs_in
from risks import evaluate
from calibration import cash_path
from admission import review_integrity, review_restatements, small_cap_review, valuation_gap

HERE = Path(__file__).resolve().parent
COLLECTOR = HERE / "collector.py"
from support import RULE, compact, digest, json_bytes, load, write
from collector import EvidenceGap, Sources

CHECKS = {
    "市场与交易": {"深交所上市名录.json", "基准日市值.json", "个股不复权日线.json"},
    "披露与审计": {"年度报告.pdf", "年度审计.pdf", "三季报.pdf"},
    "资金占用": {"年度占用专项.pdf", "半年占用专项.pdf"},
    "诚信与行业": {"年度报告.pdf"},
    "强化风险": {"半年报.pdf", "三季报.pdf"},
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def source_check(names, directory, manifest):
    if not isinstance(names, list):
        raise ValueError("证据须为文件或 HTTPS 原文列表")
    for name in names:
        if not isinstance(name, str):
            raise ValueError("证据引用类型错误")
        if urlsplit(name).scheme == "https" and urlsplit(name).netloc:
            continue
        key = name.partition("#")[0]
        if key not in manifest or digest(directory / key) != manifest[key]["SHA256"]:
            raise ValueError(f"证据快照缺失或校验失败 {key}")


def supplement(auto, directory, manifest):
    # Different issuers use different titles for the same two statutory tables.
    specs = {
        "年度占用专项": rf"{int(auto['行情时点'][:4]) - 1}年度非经营性资金占用.*专项(?:审计)?说明$",
        "半年占用专项": rf"{auto['行情时点'][:4]}年半年度非经营性资金占用.*情况(?:汇总)?表$",
    }
    missing = {key: rule for key, rule in specs.items() if key + ".pdf" not in manifest}
    if not missing or auto["计量"]["HTTP请求"] + len(missing) > 24:
        return {}
    marker = directory / "补充取证.json"
    if marker.exists():
        return load(marker)
    index = [item for key in sorted(manifest) if key.startswith("公告索引-")
             for item in (load(directory / key).get("announcements") or [])]
    attempts = list(directory.glob("补充-*"))
    src = Sources(directory / f"补充-{len(attempts) + 1:02}", False)
    added = {}
    for key, pattern in missing.items():
        hits = [x for x in index if x["secCode"] == auto["证券"][:6]
                and re.search(pattern, compact(x["announcementTitle"]))]
        if len(hits) != 1:
            continue
        path = hits[0]["adjunctUrl"]
        match = re.fullmatch(r"finalpage/(\d{4}-\d{2}-\d{2})/\d+\.pdf", path, re.I)
        if not match or match[1] >= auto["行情时点"][:10]:
            continue
        try:
            extracted = src.pdf(key, path)
        except (EvidenceGap, subprocess.TimeoutExpired):
            continue
        issuer = auto["主体"]["名称"]
        # A standalone audit cover may use the issuer's legal name instead of its stock abbreviation.
        if issuer not in compact(extracted[:7500]) and (len(issuer) < 3 or issuer[:2] not in compact(extracted[:1800])):
            continue
        filename = f"{src.path.name}/{key}.pdf"
        added[filename] = {**src.data[key + ".pdf"], "验证": "已获取原文，主体与索引代码待逐项复核",
                           "发布日期": match[1]}
    write(marker, json_bytes(added))
    return added


def collect(symbol, day, row, small_cap=False, cache=None):
    snapshot = row / "取证快照.json"
    if snapshot.exists():
        saved = load(snapshot)
        directory = row / saved["目录"]
    else:
        attempts = list(row.glob("取证-*"))
        directory = row / f"取证-{len(attempts) + 1:02}"
        command = [sys.executable, str(COLLECTOR), symbol, day, str(directory)]
        if cache:
            command += ["--cache", str(cache)]
        if small_cap:
            command.append("--small-cap")
        result = subprocess.run(command, capture_output=True, timeout=195)
        if result.returncode or not (directory / "自动准入.json").exists():
            raise ValueError(f"取证进程未完成，退出码 {result.returncode}")
        saved = {"目录": directory.name, "取证指纹": digest(directory / "自动准入.json"),
                 "来源指纹": digest(directory / "来源索引.json")}
        write(snapshot, json_bytes(saved))
    if (digest(directory / "自动准入.json") != saved["取证指纹"]
            or digest(directory / "来源索引.json") != saved["来源指纹"]):
        raise ValueError("取证结论或索引已变化")
    auto = load(directory / "自动准入.json")
    if auto["证券"] != symbol or auto["行情时点"][:10] != day or auto["规则SHA256"] != digest(RULE):
        raise ValueError("取证快照的证券、日期或规则版本不一致")
    if (load(directory / "运行输入.json").get("点名小市值") is True) != small_cap:
        raise ValueError("取证快照的点名研究范围不一致")
    manifest = load(directory / "来源索引.json")
    for key, item in manifest.items():
        if digest(directory / key) != item["SHA256"]:
            raise ValueError(f"原始证据快照已变化 {key}")
    if not auto["已确认硬排"] and auto.get("文档"):
        added = supplement(auto, directory, manifest)
        for key, item in added.items():
            if digest(directory / key) != item["SHA256"]:
                raise ValueError(f"补充原文快照已变化 {key}")
        marker = directory / "补充取证.json"
        if marker.exists():
            saved["补充指纹"] = digest(marker)
        manifest.update(added)
    return auto, saved, directory, manifest


def row_result(symbol, day, row, small_cap=False, cache=None):
    auto, saved, directory, manifest = collect(symbol, day, row, small_cap, cache)
    quote = auto.get("行情", {})
    named = auto.get("研究范围") == "点名单列"
    out = {
        "证券": symbol, "行情时点": auto["行情时点"], "规则": auto["规则"],
        "公司": auto.get("主体", {}).get("名称", symbol), "行情": quote,
        "自动准入": auto["自动结论"], "取证": saved, "自动计量": auto["计量"],
        "补充原文": {k: v for k, v in manifest.items() if k.startswith("补充-")},
        "准入": "待核", "缺口": auto["待核事项"], "估值": None, "条件演算": None,
        "关注": "待核",
    }
    if named:
        out["研究范围"] = "点名单列"
    if auto["已确认硬排"]:
        out.update(准入="硬性排除", 依据=auto["已确认硬排"], 缺口=[], 关注="过滤掉")
        return out
    packet = row / "复核记录.json"
    if not packet.exists():
        return out
    review = load(packet)
    if (review["证券"] != symbol or review["行情时点"] != auto["行情时点"]
            or review["取证指纹"] != saved["取证指纹"] or review["来源指纹"] != saved["来源指纹"]
            or review.get("补充指纹") != saved.get("补充指纹")):
        raise ValueError("复核与本次证券、日期或证据版本不一致")
    def check_refs(refs):
        source_check(refs, directory, manifest)

    route = review_restatements(review["准入"], review.get("追溯复核", []), check_refs)
    route, integrity = review_integrity(route, review.get("诚信时效复核", []), date.fromisoformat(day), check_refs)
    if set(review["检查"]) != CHECKS.keys():
        raise ValueError("复核项目不完整")
    for key, check in review["检查"].items():
        if not isinstance(check["判断"], str) or not check["判断"].strip():
            raise ValueError(f"复核说明为空 {key}")
        source_check(check["证据"], directory, manifest)
        cited = {Path(x.partition("#")[0]).name for x in check["证据"] if x.partition("#")[0] in manifest}
        if route in ("继续初筛", "强化审查") and not CHECKS[key] <= cited:
            raise ValueError(f"复核缺必要原始证据 {key}")
    if route in ("继续初筛", "强化审查"):
        documents = auto.get("文档", {})
        required = ("年度报告", "年度审计", "一季报", "半年报", "三季报", "年度占用专项", "半年占用专项")
        supplemental = {Path(x).stem for x in manifest if x.startswith("补充-")}
        expected_cap = "未达标" if named else "达标"
        if (quote.get("市值门槛判定") != expected_cap or quote["行情缺失日"]
                or not all(documents.get(x, {}).get("验证") or x in supplemental for x in required)
                or not review.get("全部适用准入已复核")):
            raise ValueError("交易、报告原文或适用准入证据未齐")
    out["复核SHA256"] = digest(packet)
    out["准入"] = route
    out["依据"] = review["检查"]
    out["缺口"] = review["剩余待办"]
    out["关注"] = review["关注"]
    if integrity:
        out["诚信时效复核"] = integrity
        out["缺口"] = [*out["缺口"], *(item["原因"] for item in integrity if item["本项分流"] == "待核")]
    if review.get("追溯复核"):
        out["追溯复核"] = review["追溯复核"]
    if named and route in ("继续初筛", "强化审查"):
        exception = small_cap_review(review.get("小市值复核"), lambda refs: source_check(refs, directory, manifest))
        if exception != "继续初筛":
            reason = "实际成交需求待核" if exception == "待核" else "未满足实际成交需求"
            out.update(准入=exception, 缺口=[*out["缺口"], f"§1.3 {reason}"],
                       关注="待核" if exception == "待核" else "过滤掉")
            return out
    if out["准入"] == "硬性排除":
        out["关注"] = "过滤掉"
        return out
    if out["准入"] == "待核":
        out["关注"] = "待核"
        return out
    from disclosures import review_coverage
    coverage = review_coverage(review, row)
    out["披露覆盖"] = coverage
    if not coverage["完整"]:
        out.update(准入="待核", 关注="准入未完整核验", 缺口=[*out["缺口"], *coverage["缺口"]])
        return out
    risk_packet = review.get("排雷底稿")
    if risk_packet:
        for item in risk_packet["数值"].values():
            source_check(item["证据"], directory, manifest)
        from cash_fields import review_cash
        checked_risk, cash_review = review_cash(risk_packet, symbol, row)
        out["财报排雷"] = evaluate(checked_risk, day)
        out["现金字段核验"] = cash_review
        if cash_review["状态"] == "待核":
            out["现金判断状态"] = "待核；未核本期字段已停止参与现金比较"
    gaps = valuation_gap(review)
    if gaps:
        out.update(估值状态="待核", 估值缺口=gaps)
    card = review.get("估值卡")
    if out["准入"] in ("继续初筛", "强化审查") and card:
        if card["截至日"] != day or not card.get("财务期间") or not card.get("证据"):
            raise ValueError("估值时点、期间或依据缺失")
        source_check(card["证据"], directory, manifest)
        if not set(card["证据"]) & manifest.keys():
            raise ValueError("估值须引用至少一份快照原始资料")
        for refs in refs_in(card):
            source_check(refs, directory, manifest)
        values = value(card, quote["基准收盘元"], quote["行情源A股总股数"])
        values.update(财务期间=card["财务期间"], 证据=card["证据"])
        if values["估值等级"] == "C":
            out.update(估值状态="待核", 估值缺口=values["估值缺口"],
                       经济关系检查=values["经济关系检查"], 估值等级="C", 关注="待核，补齐估值锚点后重评")
            return out
        cash = card.get("六年现金回收")
        if cash:
            if card["方法"] not in ("成熟盈利", "合同服务", "EPV") or not cash.get("依据") or not cash.get("证据"):
                raise ValueError("现金回收仅用于已说明证据的成熟盈利校准")
            source_check(cash["证据"], directory, manifest)
            market_cap = quote["基准收盘元"] * quote["行情源A股总股数"] / 100_000_000
            values["六年现金回收"] = {**cash_path(cash, market_cap), "依据": cash["依据"], "证据": cash["证据"]}
        if out["准入"] == "继续初筛" and not named:
            out["估值"] = values
        else:
            out["条件演算"] = values
    return out


def table(rows, day):
    lines = ["# 基本面初筛结果单", "", f"统一基准 {day} 15:00（上海）。", "",
             "| 证券 | 分流 | 基准价与三情景 | 现价反推 | 关注 |",
             "| --- | --- | --- | --- | --- |"]
    for x in rows:
        model = x["估值"] or x["条件演算"]
        market = x.get("行情", {})
        close = f"{market['基准收盘元']:.2f} 元" if "基准收盘元" in market else "待核"
        result, implied = "—", "—"
        if model:
            scope = "条件演算" if x["条件演算"] else "轻量估值"
            points = " / ".join(f"{model[k]:.2f}" for k in ("悲观", "中性", "乐观"))
            result = f"{scope} · {model['分支']} · {points} {model['单位']}"
            result += f" · {model.get('估值等级', 'B')}类"
            if "现价反推利润口径" in model:
                result += f" · 利润口径：{model['现价反推利润口径']}"
            premium = model["较中性溢价百分比"]
            if premium is not None:
                result += f"，较中性 {premium:+.1f}%"
            result += f"，比较基准 {model['比较基准']:.2f}"
            implied = "；".join(f"{key} {val:.3g}" for key, val in model["现价反推"].items())
        route = x["准入"]
        if x.get("研究范围") == "点名单列":
            route += "，点名单列"
        if x.get("估值状态") == "待核":
            result = "估值待核"
        if x["准入"] == "硬性排除" and isinstance(x.get("依据"), list):
            route += "，" + "；".join(x["依据"])
        alert_count = len(x.get("财报排雷", {}).get("线索", []))
        if alert_count:
            route += f"，{alert_count} 项排雷线索"
        lines.append(f"| {x['公司']}（{x['证券']}） | {route} | {close}；{result} | {implied} | {x['关注']} |")
    for x in rows:
        if x.get("诚信时效复核"):
            lines.extend(["", f"**{x['证券']} 诚信事项**", ""])
            for item in x["诚信时效复核"]:
                confirmed = item["正式认定日"] or "待核"
                expiry = item["期满日"] or "待核"
                lines.append(f"- 认定日 {confirmed}，期满日 {expiry}。{item['原因']}。")
        if x.get("估值缺口"):
            lines.extend(["", f"**{x['证券']} 估值待核**", ""])
            lines.extend(f"- {gap}" for gap in x["估值缺口"])
    return ("\n".join(lines) + "\n").encode()


def run(args):
    day = args.date.isoformat()
    if not all(re.fullmatch(r"\d{6}\.SZ", s) for s in args.symbols) or len(set(args.symbols)) != len(args.symbols):
        raise ValueError("本地试跑需要不重复的深圳 A 股代码")
    named = set(args.small_cap)
    if not named <= set(args.symbols):
        raise ValueError("点名小市值代码须在本批证券中")
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    with (out / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = {"证券": args.symbols, "基准日": day, "规则SHA256": digest(RULE),
                    "取证入口SHA256": digest(COLLECTOR), "编排SHA256": digest(Path(__file__)),
                    "点名小市值": sorted(named), "估值SHA256": digest(HERE / "models.py"),
                    "附加计算SHA256": {x: digest(HERE / x) for x in ("admission.py", "risks.py", "calibration.py", "economics.py", "research_check.py", "disclosures.py", "cash_fields.py", "support.py", "extract.swift")}}
        batch = out / "批次.json"
        if batch.exists() and load(batch) != manifest:
            raise ValueError("批次、规则或实现变更，请另开目录")
        if not batch.exists():
            write(batch, json_bytes(manifest))
        started = utc()
        reuse = args.reuse
        if reuse:
            source_batch = load(reuse / "批次.json")
            for key in ("基准日", "规则SHA256", "取证入口SHA256"):
                if source_batch[key] != manifest[key]:
                    raise ValueError("复用批次的日期、规则或取证入口不一致")
        rows = []
        for symbol in args.symbols:
            directory = out / symbol
            directory.mkdir(exist_ok=True)
            try:
                if reuse and not (directory / "取证快照.json").exists():
                    source_row = reuse / symbol
                    saved = load(source_row / "取证快照.json")
                    source_path = source_row / saved["目录"]
                    if (load(source_path / "运行输入.json").get("点名小市值") is True) != (symbol in named):
                        raise ValueError("复用快照的点名研究范围不一致")
                    target = directory / saved["目录"]
                    shutil.copytree(source_path, target)
                    write(directory / "取证快照.json", json_bytes(saved))
                    write(directory / "复用来源.json", json_bytes({
                        "目录": str(source_path.resolve()), "取证指纹": saved["取证指纹"],
                    }))
                row = row_result(symbol, day, directory, symbol in named, getattr(args, "cache", None))
            except (OSError, KeyError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
                row = {"证券": symbol, "公司": symbol, "准入": "待核", "估值": None,
                       "条件演算": None, "关注": "待核，修复证据交接", "缺口": [str(exc)]}
            rows.append(row)
        payload = json_bytes({"批次": manifest, "结果": rows})
        state = out / "结果快照"
        state.mkdir(exist_ok=True)
        revision = hashlib.sha256(payload).hexdigest()[:16]
        path = state / revision
        path.mkdir(exist_ok=True)
        for filename, content in (("结果底稿.json", payload), ("结果单.md", table(rows, day))):
            target = path / filename
            if not target.exists():
                write(target, content)
            elif target.read_bytes() != content:
                raise ValueError(f"已存结果损坏 {target}")
        current = out / "当前结果.json"
        pointer = {"目录": str(path.relative_to(out))}
        if not current.exists() or load(current) != pointer:
            write(current, json_bytes(pointer))
        events = out / "运行记录"
        events.mkdir(exist_ok=True)
        write(events / f"{len(list(events.glob('*.json'))) + 1:02}.json",
              json_bytes({"开始UTC": started, "结束UTC": utc(), "结果版本": revision, "进程": os.getpid()}))
        print(path)
        for row in rows:
            print(f"{row['证券']} {row['准入']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cash-check":
        from cash_fields import main as cash_main
        raise SystemExit(cash_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("disclosures", "formal"):
        from disclosures import main as disclosures_main
        mode = ["formal"] if sys.argv[1] == "formal" else []
        raise SystemExit(disclosures_main(mode + sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "research":
        from research import main as research_main
        raise SystemExit(research_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        from research_check import main as check_main
        raise SystemExit(check_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("history-search", "history-release"):
        from historical_research import main as historical_main
        mode = "search" if sys.argv[1] == "history-search" else "release"
        raise SystemExit(historical_main([mode] + sys.argv[2:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", type=date.fromisoformat)
    parser.add_argument("output", type=Path)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--reuse", type=Path, help="复用同日期、同取证版本的已存原始快照")
    parser.add_argument("--cache", type=Path, help="跨证券共享同日期请求和同版本原文；不复用判断")
    parser.add_argument("--small-cap", action="append", default=[], metavar="CODE.SZ", help="明确点名的小市值单列研究，可多次使用")
    run(parser.parse_args())
