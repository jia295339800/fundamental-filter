"""Evidence reuse and decision branches around the existing research tools.

The host Agent supplies business judgments, not a score or industry thresholds.
This module verifies their evidence boundary and routes the next research action.
It never upgrades admission or implements valuation math.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from support import EvidenceGap, digest, json_bytes, load, write


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class EvidenceCache:
    """Content-addressed raw bytes; request identity includes date or immutable URL.

    Original documents share by exact version URL, dynamic endpoints by as-of date.
    Corruption fails visibly instead of turning into an unnoticed network refill.
    Never substitutes one company's data or an older quote for a missing response.
    """
    def __init__(self, directory, as_of):
        self.path = Path(directory)
        self.as_of = date.fromisoformat(as_of).isoformat()
        for name in ("requests", "blobs"):
            (self.path / name).mkdir(parents=True, exist_ok=True)

    def key(self, url, form):
        immutable = bool(re.search(r"/finalpage/\d{4}-\d{2}-\d{2}/[^/]+\.pdf$", url, re.I)) and not form
        return fingerprint({"url": url, "form": form, "as_of": None if immutable else self.as_of})

    def read(self, url, form=None):
        p = self.path / "requests" / (self.key(url, form) + ".json")
        if not p.exists():
            return None
        record = load(p)
        if record["url"] != url or record["form"] != form:
            raise EvidenceGap("缓存请求身份冲突")
        blob = self.path / "blobs" / record["sha256"]
        if not blob.is_file() or digest(blob) != record["sha256"]:
            raise EvidenceGap("缓存原文缺失或指纹变化")
        return blob.read_bytes(), record

    def put(self, url, form, body):
        sha = hashlib.sha256(body).hexdigest()
        blob = self.path / "blobs" / sha
        if blob.exists() and digest(blob) != sha:
            raise EvidenceGap("缓存原文指纹变化")
        if not blob.exists():
            write(blob, body)
        record = {"url": url, "form": form, "sha256": sha, "as_of": self.as_of,
                  "retrieved_utc": datetime.now(timezone.utc).isoformat(), "bytes": len(body)}
        write(self.path / "requests" / (self.key(url, form) + ".json"), json_bytes(record))


def evidence(batch, base):
    """Local original snapshots only; publication AND version must fit cutoff.

    This is a reuse boundary, not a replacement for history-search/history-release
    or verification of the truth/primary status of a document by the host Agent.
    """
    cutoff = date.fromisoformat(batch["基准日"])
    refs = {}
    for item in batch.get("证据", []):
        ident = item["id"]
        if ident in refs:
            raise ValueError("证据id重复")
        path = (base / item["文件"]).resolve()
        pub, ver = date.fromisoformat(item["公开日"]), date.fromisoformat(item["版本日"])
        if pub > ver or max(pub, ver) > cutoff:
            raise ValueError("证据公开/版本晚于边界或逆序：" + ident)
        url = item["URL"]
        if urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
            raise ValueError("原文URL须为HTTPS")
        dated = re.findall(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", urlsplit(url).path)
        if any(date.fromisoformat(x) > cutoff for x in dated):
            raise ValueError("原文路径晚于边界：" + ident)
        if not path.is_file() or digest(path) != item["SHA256"]:
            raise ValueError("复用原件指纹失效：" + ident)
        if not item.get("日期依据"):
            raise ValueError("缺原始公开/版本日期依据：" + ident)
        refs[ident] = item
    return refs


def cited(ids, symbol, refs, as_of, market=False):
    if not ids or not isinstance(ids, list):
        return False
    for ident in ids:
        if ident not in refs:
            raise ValueError("引用未归档证据：" + str(ident))
        r = refs[ident]
        if symbol not in r.get("证券", []) and "共享" not in r.get("证券", []):
            raise ValueError("复用证据串公司：" + ident)
        if market and (r.get("类型") != "行情" or r.get("数据日") != as_of):
            raise ValueError("行情必须同一基准日：" + ident)
    return True


def decide(row, refs, as_of, base, task=None):
    symbol = row["证券"]
    task = row.get("任务", task) or {"类型": "自主调度"}
    if not isinstance(task, dict) or task.get("类型") not in ("完整研究", "定向问题", "批量快筛", "自主调度"):
        raise ValueError("研究任务类型不支持")
    explicit = task["类型"] in ("完整研究", "定向问题")
    if explicit and not task.get("目标"):
        raise ValueError("显式研究任务需要目标")
    out = {"证券": symbol, "准入": "未完成复核", "研究动作": None,
           "原因": [], "下一步": [], "正式估值": None}
    # Reuse the SAME existing calculation and admission gates when a packet exists.
    if row.get("完整研究输入"):
        packet = load((base / row["完整研究输入"]).resolve())
        if packet["证券"] != symbol or packet["行情时点"][:10] != as_of:
            raise ValueError("完整研究交接的公司或时点不一致")
        from disclosures import formal
        checked = formal(packet, (base / row["完整研究输入"]).resolve().parent)
        out.update(准入=checked["准入"], 正式估值=checked,
                   完整研究输入SHA256=fingerprint(packet),
                   披露覆盖复核SHA256=fingerprint(checked.get("披露覆盖")))
        if checked["准入"] == "硬性排除" and not explicit:
            out.update(研究动作="规则停止", 原因=["已有准入入口确认硬排；不继续估值"])
            return out
        if checked.get("估值等级") == "C" and not explicit:
            out.update(研究动作="修正共享检查", 原因=checked.get("估值缺口", []),
                       下一步=["沿用原入口修正矛盾或补必要锚点；不带冲突交付价格"])
            return out
    # Explicit user scope does not need a screening permission or five-question
    # card. Admission still gates ordinary valuation; this dispatch is not a
    # claim that the business research or a targeted answer has been completed.
    if explicit:
        out["任务"] = task
        if task["类型"] == "完整研究":
            out.update(研究动作="执行完整研究", 原因=["按用户明确范围，历史优先级不裁减任务"],
                       下一步=["直接执行workflow.md；复用原件并核时效，完成经营、财务、必要预测、资本现金、选法及原共享检查",
                             "正式估值与入池按原准入；缺证交付有边界的分析，不强制目标价"])
            if out["准入"] == "硬性排除":
                out.update(研究动作="交付硬排证据与受限分析", 下一步=["核实硬排证据并解释受限范围，停止普通估值和入池；保留可核业务/风险事实"])
        else:
            out.update(研究动作="执行定向问题", 原因=["聚焦用户指定问题，历史低频/待核不阻止事实核验"],
                       下一步=[task["目标"], "只取必要证据；已有可检查数字交原共享入口，不强制完整研报或普通估值"])
        out["边界"] = "任务执行指向，非研究已完成或准入放行；原估值/硬排/证据规则不变"
        return out
    brief = row.get("快筛", {})
    missing = []
    for key in ("赚钱机制", "经营变化", "价格要求", "阻断因素"):
        block = brief.get(key, {})
        if not block.get("判断") or not cited(block.get("证据"), symbol, refs, as_of):
            missing.append(key)
    price = brief.get("价格要求", {})
    if price.get("行情证据"):
        cited(price["行情证据"], symbol, refs, as_of, market=True)
    else:
        missing.append("同日行情")
    if missing:
        out.update(研究动作="补齐快筛", 原因=["尚不足以比较继续研究价值"], 下一步=missing)
        return out
    baseline = row.get("当前判断", {})
    action = baseline.get("行动")
    if action not in ("继续", "低频保留", "停止本轮") or not baseline.get("理由"):
        raise ValueError("当前判断需声明行动与经济理由")
    protection = row.get("遗漏复核", {})
    if action in ("停止本轮", "低频保留"):
        if not protection.get("分析") or not cited(protection.get("证据"), symbol, refs, as_of):
            out.update(研究动作="补齐快筛", 原因=["停止前需核查短期利润/现金流是否遮蔽周期、成长、资产或转型价值"],
                       下一步=["补充与本企业适用的反面机会线索，不以单指标排除"])
            return out
    question = row.get("关键问题")
    if question:
        if not question.get("问题") or not question.get("取证目标") or not question.get("失败出口"):
            raise ValueError("关键问题需具体取证目标及失败出口")
        answers = question.get("答案分支", [])
        if len(answers) < 2 or any(not x.get("条件") or not x.get("理由") or
                                  x.get("行动") not in ("继续", "低频保留", "停止本轮") for x in answers):
            raise ValueError("关键问题至少有两个有经济理由的答案分支")
        actions = {x["行动"] for x in answers}
        out["关键问题"] = question
        if len(actions) > 1:
            if question.get("取证状态") == "已查仍缺" or question.get("可得性") == "暂无来源":
                out.update(研究动作="暂停补证", 原因=["问题能改判，但当前取证已无有效进展；保留机会，不当作过滤"],
                           下一步=[question["失败出口"]])
            else:
                out.update(研究动作="定向补证", 原因=["不同合理答案会改变研究去留"],
                           下一步=question["取证目标"])
            return out
        # Same branch regardless of answer: do not keep researching this detail.
        action = next(iter(actions))
        if action != "继续" and (not protection.get("分析") or not cited(protection.get("证据"), symbol, refs, as_of)):
            out.update(研究动作="补齐快筛", 原因=["停止前的机会反证未完成"], 下一步=["遗漏复核"])
            return out
        out["原因"].append("该问题各答案不改变研究去留，不继续花资源补它")
    if action == "继续":
        if out["准入"] == "待核":
            out.update(研究动作="准入补证", 下一步=["按原v1.6补适用准入，算术研究不等于正式估值许可"])
        else:
            complete = bool(out["正式估值"] and (out["正式估值"].get("估值") or out["正式估值"].get("条件演算")))
            out.update(研究动作="交付完整研究" if complete else "进入完整研究",
                       下一步=[] if complete else ["复用快筛和原件；按原准入、经营研究、entry.py check与entry.py formal继续"])
    else:
        out.update(研究动作=action, 下一步=[baseline.get("重启条件", "新事实或价格改变关键条件时重评")])
    out["原因"].append(baseline["理由"])
    out["边界"] = "研究优先级判断，不替代准入裁定或A/B/C估值；程序不证明商业判断真实"
    return out


def schedule(batch, base):
    refs = evidence(batch, base)
    market_day = batch.get("行情基准日", batch["基准日"])
    if date.fromisoformat(market_day) > date.fromisoformat(batch["基准日"]):
        raise ValueError("行情日期晚于信息边界")
    symbols = [x["证券"] for x in batch["公司"]]
    if len(symbols) != len(set(symbols)):
        raise ValueError("证券重复")
    result = {"基准日": batch["基准日"], "行情基准日": market_day, "输入SHA256": fingerprint(batch),
            "结果": [decide(x, refs, market_day, base, batch.get("任务")) for x in batch["公司"]],
            "成本": {"模型输入Token": None, "模型输出Token": None,
                     "说明": "调度程序无法观察宿主模型用量；不把未知记零，外部运行凭据另附"}}
    code = {x: digest(Path(__file__).with_name(x)) for x in
            ("research.py", "calculate.py", "models.py", "economics.py", "research_check.py", "admission.py", "risks.py", "calibration.py", "support.py", "disclosures.py", "extract.swift")}
    from support import RULE
    code["政策"] = digest(RULE)
    result["实现SHA256"] = code
    idle = {"规则停止", "停止本轮", "低频保留", "暂停补证", "交付完整研究"}
    result["任务队列"] = [{"任务ID": fingerprint({"公司输入": row, "动作": out["研究动作"]}),
                       "证券": out["证券"], "动作": out["研究动作"], "下一步": out["下一步"]}
                      for row, out in zip(batch["公司"], result["结果"]) if out["研究动作"] not in idle]
    result["运行指纹"] = fingerprint({"输入": batch, "完整研究": [x.get("完整研究输入SHA256") for x in result["结果"]],
                                  "披露覆盖": [x.get("披露覆盖复核SHA256") for x in result["结果"]], "代码": code})
    return result


def fetch(args):
    # Batch network access stays in the existing bounded collector transport.
    from collector import Sources
    batch = load(args.input)
    day = date.fromisoformat(batch["基准日"]).isoformat()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("批量取证输出非空；使用新目录保留旧快照")
    args.output.mkdir(parents=True, exist_ok=True)
    src = Sources(args.output, False, cache=args.cache, as_of=day)
    rows = []
    for item in batch["请求"]:
        try:
            if Path(item["文件"]).name != item["文件"]:
                raise ValueError("请求文件须为单个文件名")
            path = src.fetch(item["文件"], item["URL"], item.get("表单"))
            rows.append({"文件": path.name, "状态": "取得", "SHA256": digest(path)})
        except (EvidenceGap, ValueError, OSError) as exc:
            rows.append({"文件": item["文件"], "状态": "缺证", "原因": str(exc)})
    result = {"基准日": day, "结果": rows, "请求": src.requests, "缓存": src.cache_events,
              "成本": {"HTTP请求": len(src.requests), "缓存命中": len(src.cache_events),
                       "墙钟秒": round(time.monotonic() - src.start, 4),
                       "模型输入Token": None, "模型输出Token": None},
              "边界": "取得原始响应不等于完成内容、主体与公开版本审核；历史研究仍走原日期隔离入口"}
    write(args.output / "批量取证结果.json", json_bytes(result))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("route", help="Agent五问与关键问题分支转为研究动作")
    s.add_argument("input", type=Path); s.add_argument("output", type=Path)
    f = sub.add_parser("fetch", help="批量原件请求；沿用collector传输与共享缓存")
    f.add_argument("input", type=Path); f.add_argument("output", type=Path)
    f.add_argument("--cache", type=Path, required=True)
    args = p.parse_args(argv)
    started = time.monotonic()
    try:
        if args.mode == "fetch":
            result = fetch(args)
        else:
            batch = load(args.input)
            result = schedule(batch, args.input.resolve().parent)
            args.output.mkdir(parents=True, exist_ok=True)
            name = result["运行指纹"] + ".json"
            target = args.output / name
            if target.exists():
                if load(target)["输入SHA256"] != result["输入SHA256"]:
                    raise ValueError("已有调度结果身份冲突")
                result = load(target)
            else:
                result["成本"]["调度程序秒"] = round(time.monotonic() - started, 6)
                write(target, json_bytes(result))
            write(args.output / "当前结果.json", json_bytes({"文件": name}))
        print(json_bytes(result).decode(), end="")
        return 2 if any(x.get("状态") == "缺证" for x in result.get("结果", [])) else 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json_bytes({"状态": "待核", "原因": str(exc)}).decode(), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
