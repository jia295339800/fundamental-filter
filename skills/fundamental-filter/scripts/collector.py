"""Discover evidence for a Shenzhen A-share as of a specified close."""
import argparse
import re
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from html import unescape
from pathlib import Path
from statistics import median
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
from support import EvidenceGap, RULE, compact, digest, json_bytes, load, write

CN = "https://www.cninfo.com.cn"
SZ = "https://www.szse.cn"
KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
STATS = "https://datacenter-web.eastmoney.com/api/data/v1/get"
MAX_REQUESTS = 24
MAX_SECONDS = 180
YI = D("100000000")


class Sources:
    def __init__(self, directory, offline, cache=None, as_of=None):
        self.path = directory
        self.offline = offline
        self.manifest = directory / "来源索引.json"
        self.data = load(self.manifest) if offline else {}
        self.start = time.monotonic()
        self.requests = []
        self.cache_events = []
        self.cache = None
        if cache is not None and not offline:
            from research import EvidenceCache
            self.cache = EvidenceCache(cache, as_of)
        if not offline:
            directory.mkdir(parents=True, exist_ok=True)

    def fetch(self, key, url, form=None):
        path = self.path / key
        if self.offline:
            expected = self.data.get(key)
            if not expected or not path.is_file() or digest(path) != expected.get("SHA256"):
                raise EvidenceGap(f"原文快照缺失或校验失败 {key}")
            return path
        if self.cache:
            saved = self.cache.read(url, form)
            if saved:
                body, original = saved
                write(path, body)
                self.data[key] = {"URL": url, "SHA256": original["sha256"], "字节": len(body),
                                  "缓存原始取证UTC": original["retrieved_utc"]}
                write(self.manifest, json_bytes(self.data))
                self.cache_events.append({"文件": key, "URL": url, "SHA256": original["sha256"]})
                return path
        remaining = MAX_SECONDS - (time.monotonic() - self.start)
        if len(self.requests) >= MAX_REQUESTS or remaining < 1:
            raise EvidenceGap("达到取证预算")
        event = {"文件": key, "URL": url}
        self.requests.append(event)
        command = ["curl", "-fsS", "--max-time", str(min(12, remaining)),
                   "--max-filesize", str(16 * 1024 * 1024), "--proto", "=https", url]
        if form:
            command += ["-H", f"Referer: {CN}/", "--data", urlencode(form)]
        response = subprocess.run(command, capture_output=True)
        event["退出码"] = response.returncode
        if response.returncode or not response.stdout:
            raise EvidenceGap(f"原文请求失败 {key}，curl {response.returncode}")
        if self.cache:
            self.cache.put(url, form, response.stdout)
        write(path, response.stdout)
        self.data[key] = {**event, "SHA256": digest(path), "字节": len(response.stdout)}
        write(self.manifest, json_bytes(self.data))
        return path

    def json(self, key, endpoint, params=None, form=None):
        url = endpoint + ("?" + urlencode(params) if params else "")
        return load(self.fetch(key + ".json", url, form))

    def pdf(self, key, path):
        pdf = self.fetch(key + ".pdf", "https://static.cninfo.com.cn/" + path)
        remaining = MAX_SECONDS - (time.monotonic() - self.start)
        if remaining < 1:
            raise EvidenceGap("达到取证预算")
        out = subprocess.run(["swift", str(HERE / "extract.swift"), str(pdf)],
                             capture_output=True, timeout=min(15, remaining))
        if out.returncode or not out.stdout:
            raise EvidenceGap(f"PDF 文本抽取失败 {key}")
        if not self.offline:
            write(pdf.with_suffix(".txt"), out.stdout)
        return out.stdout.decode()


def require(value, why):
    if not value:
        raise EvidenceGap(why)


def discover(src, code, as_of):
    directory = src.json("巨潮深圳证券名录", CN + "/new/data/szse_stock.json")
    matched = [x for x in directory["stockList"] if x["code"] == code and x["category"] == "A股"]
    require(len(matched) == 1, "巨潮未返回唯一 A 股主体")
    master = matched[0]
    listed = src.json("深交所上市名录", SZ + "/api/report/ShowReport/data", {
        "SHOWTYPE": "JSON", "CATALOGID": "1110", "TABKEY": "tab1", "PAGENO": 1, "txtDMorJC": code,
    })[0]
    require(listed["metadata"]["subname"].strip() == str(as_of), "深交所名录快照日期与基准日不符")
    rows = [x for x in listed["data"] if x["agdm"] == code]
    require(len(rows) == 1, "深交所 A 股名录主体未确认")
    listing = rows[0]
    name = compact(unescape(re.sub(r"<[^>]*>", "", listing["agjc"])))
    require(name == compact(master["zwjc"]), "两份主体名录名称不一致")
    return listing, name, master["orgId"]


def market(src, code, as_of, listing, name, cap_min):
    def days(sec, key):
        d = src.json(key, KLINE, {
            "secid": "0." + sec, "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57", "klt": 101, "fqt": 0,
            "beg": (as_of - timedelta(days=60)).strftime("%Y%m%d"), "end": as_of.strftime("%Y%m%d"), "lmt": 65,
        })["data"]
        require(d and d["code"] == sec, f"历史日线主体未确认 {sec}")
        rows = [r.split(",") for r in d["klines"]]
        dates = [row[0] for row in rows]
        require(dates == sorted(set(dates)) and all(x <= str(as_of) for x in dates), "历史日线日期顺序异常")
        return rows
    calendar = [r[0] for r in days("399001", "深证成指日线")][-20:]
    require(len(calendar) == 20 and calendar[-1] == str(as_of), "交易日窗口不足或基准日非交易日")
    by_day = {r[0]: r for r in days(code, "个股不复权日线")}
    stats = src.json("基准日市值", STATS, {
        "reportName": "RPT_VALUEANALYSIS_DET", "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")(TRADE_DATE=\'{as_of}\')', "pageSize": 1, "source": "WEB", "client": "WEB",
    })["result"]["data"]
    require(len(stats) == 1, "基准日市值行不唯一")
    x = stats[0]
    require(x["SECUCODE"] == code + ".SZ" and x["TRADE_DATE"][:10] == str(as_of)
            and compact(x["SECURITY_NAME_ABBR"]) == name, "基准日市值主体、简称或日期不匹配")
    close = D(str(x["CLOSE_PRICE"]))
    shares = D(str(x["FREE_SHARES_A"]))
    cap = D(str(x["NOTLIMITED_MARKETCAP_A"]))
    require((close * shares).quantize(D("0.01")) == cap, "流通股本乘收盘价与市值字段不符")
    exchange_shares = D(listing["agltgb"])
    require(close > 0 and shares > 0 and exchange_shares > 0, "股价或流通股数无效")
    # The exchange page reports 0.01 亿股 granularity. Keep a whole last-digit band
    # to avoid assuming a rounding convention, while retaining both source values.
    displayed_unit = D(".01")
    exchange_low = (exchange_shares - displayed_unit) * close
    exchange_high = (exchange_shares + displayed_unit) * close
    vendor_cap = cap / YI
    floor_decision = ("达标" if min(vendor_cap, exchange_low) >= cap_min else
                      "未达标" if max(vendor_cap, exchange_high) < cap_min else "待核")
    precision = ("可对照至名录显示精度" if (shares / YI).quantize(displayed_unit) == exchange_shares
                 else "精确股数待核，保留两源原值")
    available = [by_day[d] for d in calendar if d in by_day]
    require(not available or available[-1][0] != str(as_of) or D(available[-1][2]) == close, "基准收盘价不一致")
    amounts = [D(r[6]) for r in available]
    result = {"基准收盘元": float(close), "行情源流通A股股数": int(shares),
              "行情源流通市值亿元": float(vendor_cap), "行情源A股总股数": int(x["TOTAL_SHARES"]),
              "深交所名录流通股本亿股": float(exchange_shares),
              "深交所名录显示粒度下市值区间亿元": [float(exchange_low), float(exchange_high)],
              "市值门槛判定": floor_decision, "股本精度": precision,
              "窗口": calendar, "行情缺失日": [d for d in calendar if d not in by_day],
              "零成交额日": [row[0] for row in available if D(row[6]) == 0]}
    result["成交额中位数亿元"] = float(median(amounts) / YI) if len(available) == 20 else None
    return result


def filings(src, code, org, as_of):
    start = date(as_of.year - 1, as_of.month, min(as_of.day, 28))
    params = {"stock": f"{code},{org}", "column": "szse", "pageSize": 30,
              "tabName": "fulltext", "seDate": f"{start}~{as_of}"}
    rows = {}
    for page in range(1, 8):
        params["pageNum"] = page
        packet = src.json(f"公告索引-{page}", CN + "/new/hisAnnouncement/query", params, params)
        announcements = packet["announcements"] or []
        for item in announcements:
            require(item["secCode"] == code and item["announcementId"] not in rows, "索引存在串码或重复分页")
            rows[item["announcementId"]] = item
        if packet["hasMore"] is False:
            break
    require(packet["hasMore"] is False and len(rows) == packet["totalAnnouncement"], "公告索引未完整读完")
    return list(rows.values())


def documents(src, rows, as_of, name, report, gaps):
    year, last = as_of.year, as_of.year - 1
    # This acceptance entry covers the post-Q3 / pre-year-end disclosure window.
    patterns = {
        "年度报告": rf"{last}年(?:年度报告|年报)(?:全文)?$",
        "年度审计": rf"{last}年(?:年度|度)?审计报告$",
        "一季报": rf"{year}年(?:第)?一季度报告$",
        "半年报": rf"{year}年半年度报告(?:全文)?$",
        "三季报": rf"{year}年(?:第)?三季度报告$",
        "年度占用专项": rf"{last}年度非经营性资金占用.*专项说明$",
        "半年占用专项": rf"{year}年半年度非经营性资金占用.*汇总表$",
    }
    texts = {}
    report["文档"] = {}
    for key, pattern in patterns.items():
        try:
            hits = [item for item in rows if re.search(pattern, compact(item["announcementTitle"]))
                    and not (key == "年度审计" and "内部控制" in item["announcementTitle"])]
            require(len(hits) == 1, f"{key}未找到唯一可识别公告")
            item = hits[0]
            path = item["adjunctUrl"]
            match = re.fullmatch(r"finalpage/(\d{4}-\d{2}-\d{2})/\d+\.pdf", path, re.I)
            require(match and match[1] < str(as_of), f"{key}公开日期未能锁定在行情基准前")
            url = "https://static.cninfo.com.cn/" + path
            report["文档"][key] = {"原文": url, "发布日期": match[1]}
            txt = src.pdf(key, path)
            require(name in compact(txt[:3500]), f"{key}主体未核实")
            texts[key] = txt
            report["文档"][key]["验证"] = "原文已取回并核对主体"
        except (EvidenceGap, subprocess.TimeoutExpired) as exc:
            gaps.append(f"§2.3 {key}：{exc}")
    return texts


def financials(text, report, gaps):
    from cash_fields import summary_amount
    original = text.split("=== PAGE 3 ===", 1)[0]
    flat = compact(original)
    flag = r"公司是否需追溯调整或重述以前年度会计数据□是[√☑✓]否"
    if not re.search(flag, flat):
        gaps.append("§2.3 三季报追溯重述原因及范围需复核")
        return
    report["三季报重述"] = "报告勾选无需追溯调整或重述"
    fields = [
        ("前三季收入亿元", "营业收入（元）", "归属于上市公司股东的净利润"),
        ("前三季归母净利润亿元", "归属于上市公司股东的净利润（元）", "归属于上市公司股东的扣除非经常性损益"),
        ("前三季扣非归母净利润亿元", "扣除非经常性损益的净利润（元）", "经营活动产生的现金流量净额"),
        ("前三季经营现金流亿元", "经营活动产生的现金流量净额（元）", "基本每股收益"),
    ]
    values = {}
    for label, start, end in fields:
        # Heading matching tolerates line wraps; numeric cells retain boundaries.
        heading = lambda s: r"\s*".join(re.escape(c) for c in s)
        match = re.search(heading(start) + r"(.*?)" + heading(end), original, re.S)
        try:
            if not match:
                raise ValueError("字段或列边界缺失")
            values[label] = float(summary_amount(match[1], cash="现金流" in label) / YI)
        except ValueError as exc:
            gaps.append(f"§2.3 {label}表格列识别待核：{exc}")
    report["原文数值"] = values
    report["现金字段核验"] = {"经营现金流": {"状态": "待核",
        "说明": "主表金额提取不等于原表现金勾稽通过；使用现金判断前交cash-check或正式复核路径"}}


def run(args):
    m = re.fullmatch(r"(\d{6})\.SZ", args.symbol)
    if not m or not (date(args.as_of.year, 11, 1) <= args.as_of <= date(args.as_of.year, 12, 31)):
        raise SystemExit("本验收入口支持 11–12 月的深圳 A 股收盘基准")
    code, as_of = m[1], args.as_of
    inputs = {"证券": args.symbol, "基准日": str(as_of)}
    if args.small_cap:
        inputs["点名小市值"] = True
    if args.offline:
        require(load(args.output / "运行输入.json") == inputs, "回放输入与批次不符")
    src = Sources(args.output, args.offline, cache=getattr(args, "cache", None), as_of=args.as_of.isoformat())
    start = datetime.now(timezone.utc)
    if not args.offline:
        write(args.output / "运行输入.json", json_bytes(inputs))
    gaps, hard = [], []
    report = {"证券": args.symbol, "行情时点": f"{as_of}T15:00:00+08:00",
              "规则": RULE.name, "规则SHA256": digest(RULE), "自动结论": "待核", "估值": None}
    rule = RULE.read_text().split("### 1.3 ", 1)[1].split("### 1.4 ", 1)[0]
    limit = D(re.search(r"流通市值须\*\*大于或等于人民币 (\d+) 亿元", rule)[1])
    normal = D(re.search(r"\| \*\*大于或等于人民币 (\d+) 亿元", rule)[1])
    reduced = D(re.search(r"\| \*\*大于或等于人民币 (\d+) 万元", rule)[1]) / D("10000")
    try:
        listing, name, org = discover(src, code, as_of)
        report["主体"] = {"名称": name, "机构号": org, "上市日期": listing["agssrq"],
                          "板块": listing["bk"], "行业": listing["sshymc"]}
        require(listing["bk"] in ("主板", "创业板"), "无法确认板块适用性")
        anniversary = (as_of.year - 1, as_of.month, as_of.day)
        listed = date.fromisoformat(listing["agssrq"])
        if (listed.year, listed.month, listed.day) > anniversary:
            hard.append("§1.2 上市未满 12 个月")
        if name.upper().startswith(("ST", "*ST")):
            hard.append("§2.2 基准日 ST 或 *ST")
        if name.endswith("退"):
            gaps.append("§2.1 简称存在退市迹象，核对退市程序")
        if not hard:
            try:
                quote = market(src, code, as_of, listing, name, limit)
                report["行情"] = quote
                amount = quote["成交额中位数亿元"]
                quote["流动性"] = ("待核" if amount is None else "常规流动性" if amount >= normal
                                   else "流动性偏低" if amount >= reduced else "低流动性")
                if quote["市值门槛判定"] == "未达标":
                    if args.small_cap:
                        report["研究范围"] = "点名单列"
                        gaps.append("§1.3 核实点名小市值样本的实际成交需求")
                    else:
                        hard.append("§1.3 两来源流通市值均低于门槛")
                elif quote["市值门槛判定"] == "待核":
                    gaps.append("§1.3 流通股本口径可能改变市值门槛判断")
                if len(quote["零成交额日"]) == 20:
                    hard.append("§1.4 20 个交易日均零成交额")
                if quote["行情缺失日"]:
                    gaps.append("§1.4 行情缺日，需区分停牌与数据遗漏")
            except (EvidenceGap, KeyError, TypeError, ValueError) as exc:
                gaps.append(f"§1.3–1.4 行情与交易资格待核：{exc}")
        if not hard:
            rows = filings(src, code, org, as_of)
            report["公告数"] = len(rows)
            report["标题线索"] = [x["announcementTitle"] for x in rows
                                if re.search("会计差错|重述|警示函|处罚|纪律处分|监管措施|资金占用|退市", x["announcementTitle"])]
            texts = documents(src, rows, as_of, name, report, gaps)
            annual = compact(texts.get("年度报告", ""))
            audit = compact(texts.get("年度审计", ""))
            if "审计意见类型标准的无保留意见" in annual and "公允反映" in audit[:12000]:
                report["年度审计"] = "年报意见字段与独立审计原件相符，标准无保留"
            else:
                gaps.append("§2.3 审计意见及独立原件需复核")
            if "三季报" in texts:
                financials(texts["三季报"], report, gaps)
            gaps += ["§2.3.1 核读资金占用专项表，区分占用与经营往来",
                     "§2.1/2.3/2.4 独立核验退市、重大违法违规及实质经营",
                     "§3 判断关联交易和现金兑现风险是否触发强化审查"]
    except (EvidenceGap, KeyError, IndexError, TypeError, ValueError) as exc:
        gaps.append(f"取证停点：{exc}")
    if hard:
        report["自动结论"] = "硬性排除"
    report["已确认硬排"] = hard
    report["待核事项"] = gaps
    report["计量"] = {"开始UTC": start.isoformat(), "结束UTC": datetime.now(timezone.utc).isoformat(),
                    "墙钟秒": round(time.monotonic() - src.start, 3), "HTTP请求": len(src.requests),
                    "付费API": 0, "模型调用": 0}
    if src.cache:
        report["计量"]["缓存命中"] = len(src.cache_events)
    if not args.offline:
        write(args.output / "请求记录.json", json_bytes(src.requests))
        write(args.output / "自动准入.json", json_bytes(report))
    print(json_bytes(report).decode(), end="")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("symbol")
    p.add_argument("as_of", type=date.fromisoformat)
    p.add_argument("output", type=Path)
    p.add_argument("--offline", action="store_true", help="仅重读已保存快照并输出到 stdout")
    p.add_argument("--small-cap", action="store_true", help="为明确点名对象采集小市值例外的复核资料")
    p.add_argument("--cache", type=Path, help="同日期动态请求/同版本原件的共享缓存")
    run(p.parse_args())
