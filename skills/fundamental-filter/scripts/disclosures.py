"""Expose statutory disclosure text and verify its review before formal delivery.

Locators select text, not admission decisions. The host reviews applicability and
uses the existing restatement event types and rule combiner.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

from support import RULE, compact, digest, json_bytes, load, write

HERE = Path(__file__).resolve().parent
TOPICS = ("审计意见", "会计政策及估计变更", "重大及前期差错更正")
MARK = re.compile(r"^=== PAGE (\d+) ===$")


def level(line):
    line = compact(line)
    if re.match(r"^[一二三四五六七八九十百]+、", line): return 1
    # A title can start with a year after 、; only dot separators need the
    # decimal-number guard (e.g. 1.23 must not become a section heading).
    if re.match(r"^\d+、", line) or re.match(r"^\d+[.．](?!\d)", line): return 3
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]", line): return 2
    if re.match(r"^[（(]\d+[）)]", line): return 4
    return 0


def heading(line):
    text = compact(line)
    if len(text) > 85 or re.search(r"\.{3,}|…|\.{2}.*\d+$", text):
        return None
    text = re.sub(r"^[（(一二三四五六七八九十百\d、.．）)]+", "", text).rstrip("：:")
    if text == "审计意见": return TOPICS[0]
    if text in ("公司对会计政策、会计估计变更原因及影响的分析说明",
                "与上年度财务报告相比，会计政策、会计估计变更或重大会计差错更正的情况说明",
                "重要会计政策和会计估计变更", "重要会计政策和会计估计的变更",
                "重要会计政策变更", "重要会计估计变更", "会计政策变更", "会计估计变更"):
        return TOPICS[1]
    if text in ("公司对重大会计差错更正原因及影响的分析说明", "前期会计差错更正",
                "重大会计差错更正", "会计差错更正"):
        return TOPICS[2]
    return None


def source_text(path):
    if path.suffix.lower() == ".pdf":
        p = subprocess.run(["swift", str(HERE / "extract.swift"), str(path)],
                           capture_output=True, timeout=30)
        if p.returncode or not p.stdout.strip():
            raise ValueError("PDF正文提取失败：" + path.name)
        return p.stdout.decode("utf-8")
    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8")
        if not any(MARK.match(line.strip()) for line in text.splitlines()):
            raise ValueError("归档文本缺原页标记，不能冒充已定位原文")
        return text
    raise ValueError("披露原件仅支持PDF或已归档的带页码文本")


def checkbox(text):
    text = compact(text)[:180]
    checked = r"[√☑✓✔]"
    if re.search(checked + r"适用[□☐]不适用", text): return "适用"
    if re.search(r"[□☐]适用" + checked + "不适用", text): return "不适用"
    return "未标注或无法识别"


def sections(text, item, max_chars):
    lines = text.splitlines(); pages = []; page = 1
    for line in lines:
        m = MARK.match(line.strip())
        if m: page = int(m[1])
        pages.append(page)
    found = []
    for i, line in enumerate(lines):
        topic = heading(line)
        if topic is None: continue
        depth = level(line); end = len(lines); boundary_known = True
        for j in range(i + 1, len(lines)):
            other = level(lines[j])
            # An unnumbered 其他 with its own immediately following checkbox
            # is a separate disclosure item, not this policy/estimate's state.
            if (topic == TOPICS[1] and compact(lines[j]) == "其他"
                    and j + 1 < len(lines)
                    and checkbox(lines[j + 1]) != "未标注或无法识别"):
                # Recognize the template's separate supplement only after a
                # nested first-adoption item. Otherwise 其他 has no reliable
                # hierarchy: expose the excerpt but do not certify coverage.
                boundary_known = any(level(s) > depth and "首次执行" in compact(s)
                                     and "准则" in compact(s) for s in lines[i + 1:j])
                end = j; break
            if (other and depth and other <= depth) or (
                topic == TOPICS[0] and "形成审计意见的基础" in compact(lines[j])):
                end = j; break
        raw = "\n".join(lines[i:end]).strip()
        # Ignore table-of-contents labels with no substantive body.
        if len(compact(raw)) < 24 and checkbox(raw) == "未标注或无法识别": continue
        # Without a recognized target hierarchy, adjacent checkboxes cannot
        # establish coverage. Preserve the excerpt for review, but fail closed.
        complete = len(raw) <= max_chars and boundary_known and (topic != TOPICS[1] or depth > 0)
        body = raw[:max_chars]
        token = hashlib.sha256((item['id'] + str(i) + topic + body).encode()).hexdigest()[:16]
        found.append({"id": token, "项目": topic, "原件id": item['id'],
                      "页码": [pages[i], pages[max(i, end - 1)]], "行范围": [i + 1, end],
                      "引用": item['URL'].split('#')[0] + "#page=" + str(pages[i]),
                      "原文勾选": checkbox(body), "正文": body, "完整": complete})
    return found


def gather(request, base):
    from research import evidence
    symbol = request['证券']; day = request['基准日']
    refs = evidence(request, base)
    if not refs: raise ValueError("没有披露原件")
    max_chars = request.get('段落字符上限', 8000)
    if not isinstance(max_chars, int) or not 300 <= max_chars <= 30000:
        raise ValueError("段落字符上限须在300至30000之间")
    canonical = deepcopy(request); fragments = []; errors = []
    for item in canonical['证据']:
        if symbol not in item.get('证券', []):
            raise ValueError("披露原件主体不匹配：" + item['id'])
        item['文件'] = str((base / item['文件']).resolve())
        try: fragments.extend(sections(source_text(Path(item['文件'])), item, max_chars))
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append({"原件id": item['id'], "原因": str(exc)})
    policy = RULE.read_text(encoding='utf-8')
    start = policy.index('### 2.3 '); end = policy.index('### 2.3.1', start)
    return {"证券": symbol, "基准日": day, "请求": canonical,
            "规则SHA256": digest(RULE), "相关规则原文": policy[start:end].strip(),
            "正文": fragments, "提取缺口": errors,
            "边界": "候选正文定位，不是语义裁定；复核全部同主题候选，尤其管理报告与附注的不同适用期间。无候选或截断不等于无事项。"}


def review_coverage(packet, base):
    """Validate actual excerpts, binding, and event linkage; never invent events."""
    gaps = []; coverage = packet.get('披露覆盖')
    if not isinstance(coverage, dict):
        return {"完整": False, "缺口": ["必查披露正文及复核记录未取得"]}
    try:
        receipt_file = (base / coverage['文件']).resolve()
        if digest(receipt_file) != coverage['SHA256']:
            raise ValueError("披露覆盖文件指纹不符")
        saved = load(receipt_file)
        if saved['证券'] != packet['证券'] or saved['基准日'] != packet.get('信息截止', packet['行情时点'][:10]):
            raise ValueError("披露覆盖主体或时点不匹配")
        # Re-extract ORIGINAL bytes, not a host-authored summary or stale text hash.
        rebuilt = gather(saved['请求'], receipt_file.parent)
        if saved != rebuilt: raise ValueError("披露正文或规则与原始来源不一致")
        if saved['提取缺口']: gaps.append("有披露原件正文提取失败")
        rows = coverage.get('复核', [])
        if (not isinstance(rows, list) or len(rows) != len(TOPICS)
                or any(not isinstance(r, dict) for r in rows)
                or {r.get('项目') for r in rows} != set(TOPICS)):
            raise ValueError("必查披露复核须覆盖三个项目且不重复")
        events = packet.get('追溯复核', [])
        for topic in TOPICS:
            fragments = [p for p in saved['正文'] if p['项目'] == topic]
            row = next(r for r in rows if r['项目'] == topic)
            if not fragments or any(not p['完整'] for p in fragments):
                gaps.append(topic + "正文缺失或被截断"); continue
            if set(row.get('正文id', [])) != {p['id'] for p in fragments}:
                gaps.append(topic + "未复核全部已定位正文"); continue
            state = row.get('适用状态')
            if state not in ('适用', '不适用') or not row.get('判断') or '§2.3' not in row.get('规则条款', ''):
                gaps.append(topic + "适用状态或规则复核未完成"); continue
            if state == '不适用' and any(p['原文勾选'] == '适用' for p in fragments):
                gaps.append(topic + "不适用判断与原文勾选冲突"); continue
            indices = row.get('追溯事件', [])
            if topic != TOPICS[0] and state == '适用' and not indices:
                gaps.append(topic + "已适用事项尚未交追溯复核"); continue
            for index in indices:
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(events):
                    raise ValueError("追溯事件索引无效")
                if not set(events[index].get('证据', [])) & {p['引用'] for p in fragments}:
                    raise ValueError("追溯事件未引用对应披露正文")
        return {"完整": not gaps, "缺口": gaps, "文件": str(receipt_file),
                "SHA256": coverage['SHA256'], "复核": rows,
                "范围": "仅必查披露的正文与事件衔接，其他准入条件仍由原流程复核"}
    except (KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        return {"完整": False, "缺口": [str(exc)]}


def formal(packet, base):
    """Formal delivery wrapper. Frozen numerical replay stays in calculate.py."""
    from calculate import calculate
    from cash_fields import review_cash
    packet = deepcopy(packet)
    cash_review = None
    if packet.get('排雷底稿'):
        packet['排雷底稿'], cash_review = review_cash(packet['排雷底稿'], packet['证券'], base)
    brief = deepcopy(packet); brief.pop('估值卡', None)
    out = calculate(brief)
    coverage = review_coverage(packet, base)
    if out['准入'] in ('继续初筛', '强化审查'):
        if not coverage['完整']:
            out.update(准入='待核', 缺口=[*out.get('缺口', []), *coverage['缺口']])
        else:
            out = calculate(packet)
    out['披露覆盖'] = coverage
    if cash_review is not None:
        out['现金字段核验'] = cash_review
        if cash_review['状态'] == '待核':
            out['现金判断状态'] = '待核；未核本期字段已停止参与现金比较，不据此生成现金结论'
    out['用途'] = '正式研究交付；其他准入仍须原复核。calculate.py仅作冻结数值复算。'
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    extract = sub.add_parser('extract', help='从原件定位并展示必查正文及相关规则')
    extract.add_argument('input', type=Path); extract.add_argument('output', type=Path)
    show = sub.add_parser('show', help='按主题阅读已提取正文，避免工具输出截断')
    show.add_argument('input', type=Path); show.add_argument('topic', choices=TOPICS)
    final = sub.add_parser('formal', help='正式准入及估值交付，缺正文不放行')
    final.add_argument('input', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == 'extract':
            result = gather(load(args.input), args.input.resolve().parent)
            write(args.output, json_bytes(result))
        elif args.mode == 'show':
            data = load(args.input)
            result = {"项目": args.topic, "相关规则原文": data['相关规则原文'],
                      "正文": [p for p in data['正文'] if p['项目'] == args.topic],
                      "提取缺口": data['提取缺口']}
        else: result = formal(load(args.input), args.input.resolve().parent)
        print(json_bytes(result).decode(), end='')
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.exit(2, f"披露取证待核：{exc}\n")


if __name__ == '__main__': main()
