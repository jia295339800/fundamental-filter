"""Keep historical search bodies outside researcher-visible stdout until version review.

Discovery captures provider stdout/stderr before parsing. It emits candidate
metadata only. Release requires reviewed publication/version evidence and an
exact source hash. This is an output boundary, not an OS sandbox or a semantic
proof that a mutable webpage is the historical version.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def day(value):
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def url_dates(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    # Issuer document paths provide a useful dated identity. Generic URL dates
    # are conflict checks only, not proof of original publication.
    dates = [day(x) for x in re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", parts.path)]
    # Eastmoney announcement/report IDs encode a document date. Use only as a
    # veto against a newer artifact, never as proof of a public release time.
    coded = re.search(r"H[23]_(?:AN|AP)(20\d{6})", parts.path, re.I)
    if coded:
        d = coded[1]
        dates.append(day(d[:4] + "-" + d[4:6] + "-" + d[6:]))
    dates = [x for x in dates if x]
    authoritative = host == "static.cninfo.com.cn" or host.endswith(".sse.com.cn")
    return dates, authoritative


def records(raw, provider):
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        return records("\n".join(x.get("text", "") for x in data["content"] if x.get("type") == "text"), provider)
    if data is not None:
        rows = data if isinstance(data, list) else data.get("results", data.get("data", []))
        if not isinstance(rows, list):
            raise ValueError("unparsed_payload")
        return [dict(title=x.get("title", ""), url=x.get("url", ""),
                     publication=x.get("publishedDate", x.get("published_on", x.get("time", ""))),
                     revision=x.get("revisedDate", x.get("version_on", "")))
                for x in rows if isinstance(x, dict)]
    if provider != "exa":
        raise ValueError("unparsed_payload")
    result = []
    for block in re.split(r"(?m)^Title: ", raw)[1:]:
        found = re.search(r"(?m)^URL: (\S+)", block)
        published = re.search(r"(?m)^Published: ([^\n]+)", block)
        if found:
            result.append(dict(title=block.splitlines()[0], url=found[1],
                               publication=published[1] if published else "", revision=""))
    if not result:
        raise ValueError("unparsed_payload")
    return result


def discover(raw, provider, cutoff):
    groups = {}
    for row in records(raw, provider):
        groups.setdefault(row["url"], []).append(row)
    candidates, held = [], []
    for url, rows in groups.items():
        token = hashlib.sha256(url.encode()).hexdigest()[:16]
        parts = urlsplit(url)
        reason = None
        intrinsic, authoritative = url_dates(url)
        claimed = [day(x["publication"]) for x in rows]
        revisions = [day(x["revision"]) for x in rows if x["revision"]]
        known = intrinsic + [x for x in claimed + revisions if x]
        if parts.scheme not in ["https", "http"] or not parts.netloc:
            reason = "invalid_source_url"
        elif any(x > cutoff for x in known):
            reason = "after_cutoff"
        elif any(x["revision"] and day(x["revision"]) is None for x in rows):
            reason = "invalid_revision_date"
        elif len(set(x for x in claimed if x)) > 1:
            reason = "conflicting_publication_dates"
        elif not any(claimed) and not (authoritative and intrinsic):
            reason = "publication_unknown_or_invalid"
        if reason:
            # No held title, URL, content, server error or body can leak here.
            held.append(dict(id=token, reason=reason))
            continue
        publication = max([x for x in claimed if x] + (intrinsic if authoritative else []))
        title = str(rows[0]["title"]).splitlines()[0][:300] if rows[0]["title"] else ""
        candidates.append(dict(id=token, title=title, url=url, publication_candidate=publication.isoformat(),
            date_basis="issuer_document_path" if authoritative and intrinsic else "provider_metadata_only",
            status="original_version_review_required", body_released=False))
    return dict(cutoff=cutoff.isoformat(), mode="metadata_only", candidates=candidates,
                held=held, original_version_verified=False)


def capture(args):
    if args.input:
        return args.input.read_text(encoding="utf-8"), ""
    if args.provider == "exa":
        call = "exa.web_search_exa(query: " + json.dumps(args.query, ensure_ascii=False) + ", numResults: " + str(args.limit) + ")"
        proc = subprocess.run(["mcporter", "call", call], capture_output=True, text=True, timeout=55)
        if proc.returncode:
            raise ValueError("provider_failed")
        return proc.stdout, proc.stderr
    params = urlencode(dict(keyword=args.query, size=args.limit, search_type="title", enddate=args.cutoff.isoformat()))
    request = Request("https://api.yanbaoke.cn/skills/search_report?" + params,
                      headers={"X-Skill-Version": "2.0.4", "X-Skill-ID": "yanbaoke-research-report-download"})
    with urlopen(request, timeout=40) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    obj = json.loads(raw)
    if not obj.get("success"):
        raise ValueError("provider_failed")
    return raw.decode("utf-8"), ""


def search(args):
    args.out.mkdir(parents=True, exist_ok=True)
    raw, err = capture(args)
    if len(raw.encode()) > 8 * 1024 * 1024:
        raise ValueError("provider_payload_too_large")
    private = args.out / "取证端未审返回.txt"
    private.write_text(raw, encoding="utf-8")
    private.chmod(0o600)
    result = discover(raw, args.provider, args.cutoff)
    result["provider"] = args.provider
    result["raw_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    result["provider_stderr_suppressed"] = bool(err)
    (args.out / "候选元数据.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def release(args):
    manifest = json.loads(args.manifest.read_text())
    entries = manifest if isinstance(manifest, list) else manifest.get("sources", [])
    found = [x for x in entries if x.get("url") == args.url]
    if len(found) != 1:
        raise ValueError("unique_version_evidence_required")
    item = found[0]
    pub, ver = day(item.get("published_on")), day(item.get("version_on"))
    if not pub or not ver or ver < pub or not item.get("date_evidence"):
        raise ValueError("publication_and_version_evidence_required")
    intrinsic, _ = url_dates(args.url)
    if max([pub, ver] + intrinsic) > args.cutoff:
        raise ValueError("after_cutoff")
    blob = args.file.read_bytes()
    if hashlib.sha256(blob).hexdigest() != item.get("sha256"):
        raise ValueError("source_hash_mismatch")
    if blob.startswith(b"%PDF"):
        proc = subprocess.run(["swift", str(Path(__file__).with_name("extract.swift")), str(args.file)],
                              capture_output=True, text=True, timeout=55)
        if proc.returncode or not proc.stdout.strip():
            raise ValueError("verified_document_extraction_failed")
        text = proc.stdout
    else:
        text = blob.decode("utf-8")
    return dict(cutoff=args.cutoff.isoformat(), status="released_after_version_review", url=args.url,
                publication=pub.isoformat(), version=ver.isoformat(), sha256=item["sha256"], body=text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    for mode in ["search", "release"]:
        s = sub.add_parser(mode)
        s.add_argument("--cutoff", required=True, type=date.fromisoformat)
        if mode == "search":
            s.add_argument("--provider", choices=["exa", "yanbaoke"], required=True)
            s.add_argument("--query", default="")
            s.add_argument("--limit", type=int, default=5, choices=range(1, 21))
            s.add_argument("--out", type=Path, required=True)
            s.add_argument("--input", type=Path, help="Replay captured provider output without network")
        else:
            s.add_argument("--manifest", type=Path, required=True)
            s.add_argument("--url", required=True)
            s.add_argument("--file", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.mode == "search" and not args.input and not args.query:
            raise ValueError("query_required")
        result = search(args) if args.mode == "search" else release(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        allowed = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc)) else "capture_or_parse_failed"
        print(json.dumps({"status": "held", "reason": allowed, "body_released": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
