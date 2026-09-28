#!/usr/bin/env python3
"""paper-radar：小目标检测方向的 IF≥8 期刊周报生成器。

数据源：arXiv API（export.arxiv.org）、Crossref、OpenAlex、Semantic Scholar；纯标准库，零 pip 依赖。
核心机制：
  1. 按主题短语组检索最近 N 天的 cs.CV / eess.IV 新提交；
  2. 只保留达到配置中 JIF 阈值的期刊论文，以及有此类期刊录用信息的 arXiv 记录；
  3. 去重键 = DOI、arXiv ID 和标准化标题；录用状态变化时重新浮现。
     录用信息变化时重新浮现到「已录用」区。
输出：config.json 指定的周报目录 + stdout 摘要（供 agent 读取后推送）。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
TIMEOUT = 30
RETRIES = 3
REQUEST_GAP = 3.1          # arXiv 礼貌限速：1 请求 / 3 秒
EXTERNAL_GAP = 1.0          # Crossref polite use
SEMANTIC_SCHOLAR_GAP = 1.1  # Semantic Scholar API key introductory limit: 1 request / second
STATE_TTL_DAYS = 400       # 状态库保留时长
ABSTRACT_MAX = 600         # 周报里摘要截断长度
_ARXIV_PROBE_RESULT = None


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_query(phrases: list, categories: list, since: datetime, until: datetime) -> str:
    phrase_clauses = []
    for p in phrases:
        phrase_clauses.append(f'abs:"{p}"')
        phrase_clauses.append(f'ti:"{p}"')
    cat_clauses = [f"cat:{c}" for c in categories]
    date_clause = f"submittedDate:[{since:%Y%m%d%H%M} TO {until:%Y%m%d%H%M}]"
    return ("(" + " OR ".join(phrase_clauses) + ")"
            + " AND (" + " OR ".join(cat_clauses) + ")"
            + " AND " + date_clause)


def fetch(query: str, max_results: int) -> list:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    body = urlencode(params).encode("utf-8")
    get_url = f"{ARXIV_API}?{urlencode(params)}"
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urlrequest.Request(
                ARXIV_API,
                data=body,
                headers={
                    "User-Agent": "paper-radar/3.0",
                    "Accept": "application/atom+xml",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
                return parse_atom(resp.read())
        except HTTPError as e:
            if e.code == 406:
                probe_ok, diagnostic = diagnose_arxiv_api()
                if probe_ok:
                    time.sleep(REQUEST_GAP)
                    fallback = urlrequest.Request(
                        get_url,
                        headers={"User-Agent": "paper-radar/3.0", "Accept": "application/atom+xml"},
                    )
                    try:
                        with urlrequest.urlopen(fallback, timeout=TIMEOUT) as resp:
                            return parse_atom(resp.read())
                    except (HTTPError, URLError, TimeoutError, OSError) as fallback_error:
                        raise RuntimeError(
                            f"长查询 POST 返回 HTTP 406；{diagnostic}；长查询 GET fallback 失败（{fallback_error}）"
                        ) from e
                raise RuntimeError(f"长查询 POST 返回 HTTP 406；{diagnostic}") from e
            last_err = e
            if attempt < RETRIES:
                time.sleep(5 * attempt)
        except (URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    raise RuntimeError(f"arXiv API 请求失败（已重试 {RETRIES} 次）：{last_err}")


def diagnose_arxiv_api() -> tuple:
    """A short GET distinguishes a long-query/POST rejection from API reachability failure."""
    global _ARXIV_PROBE_RESULT
    if _ARXIV_PROBE_RESULT is not None:
        return _ARXIV_PROBE_RESULT
    time.sleep(REQUEST_GAP)
    params = urlencode({"search_query": "all:neural", "start": 0, "max_results": 1})
    req = urlrequest.Request(
        f"{ARXIV_API}?{params}",
        headers={"User-Agent": "paper-radar/3.0", "Accept": "application/atom+xml"},
    )
    try:
        with urlrequest.urlopen(req, timeout=TIMEOUT) as response:
            parse_atom(response.read())
        _ARXIV_PROBE_RESULT = (True, "短 GET 诊断查询成功；接口可访问，正在尝试长查询 GET fallback")
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        _ARXIV_PROBE_RESULT = (False, f"短 GET 诊断查询也失败（{error}）")
    return _ARXIV_PROBE_RESULT


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_atom(data: bytes) -> list:
    root = ET.fromstring(data)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        def txt(ns, name):
            el = entry.find(ns + name)
            return _clean(el.text) if el is not None and el.text else ""
        raw_id = txt(ATOM, "id")                      # http://arxiv.org/abs/2401.12345v2
        base_id = re.sub(r"v\d+$", "", raw_id.rsplit("/abs/", 1)[-1])
        version_match = re.search(r"(v\d+)$", raw_id.rsplit("/abs/", 1)[-1])
        cats = [c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")]
        pdf_url = ""
        for link in entry.findall(f"{ATOM}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
        papers.append({
            "id": base_id,
            "arxiv_id": base_id,
            "arxiv_version": version_match.group(1) if version_match else "",
            "title": txt(ATOM, "title"),
            "abstract": txt(ATOM, "summary"),
            "authors": [a.findtext(f"{ATOM}name", default="").strip()
                        for a in entry.findall(f"{ATOM}author")],
            "published": txt(ATOM, "published")[:10],
            "updated": txt(ATOM, "updated")[:10],
            "comment": txt(ARXIV, "comment"),
            "journal_ref": txt(ARXIV, "journal_ref"),
            "doi": txt(ARXIV, "doi"),
            "categories": cats,
            "pdf_url": pdf_url,
            "pdf_urls": [pdf_url] if pdf_url else [],
            "pdf_candidates": ([{"url": pdf_url, "version": "arXiv " + (version_match.group(1) if version_match else "latest"),
                                 "priority": 1, "origin": "arXiv"}] if pdf_url else []),
            "source": "arXiv",
            "sources": ["arXiv"],
            "publication_type": "preprint",
            "venue": "",
            "publisher": "",
            "landing_url": f"https://arxiv.org/abs/{base_id}",
        })
    return papers


def _doi(value: str) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", (value or "").strip(), flags=re.I).lower()


def _date_from_parts(parts) -> str:
    try:
        values = list(parts[0])
        year = int(values[0])
        month = int(values[1]) if len(values) > 1 and values[1] else 1
        day = int(values[2]) if len(values) > 2 and values[2] else 1
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return ""


def _fetch_json(url: str, headers=None) -> dict:
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            request_headers = {"User-Agent": "paper-radar/3.0"}
            request_headers.update(headers or {})
            req = urlrequest.Request(url, headers=request_headers)
            with urlrequest.urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            last_error = error
            if attempt < RETRIES:
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                delay = _retry_delay(retry_after, attempt)
                time.sleep(delay)
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < RETRIES:
                time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"请求失败（已重试 {RETRIES} 次）：{last_error}")


def _retry_delay(retry_after: str, attempt: int) -> int:
    if not retry_after:
        return min(30, 2 ** attempt)
    try:
        delay = int(retry_after)
    except ValueError:
        try:
            retry_time = parsedate_to_datetime(retry_after)
            if retry_time.tzinfo is None:
                retry_time = retry_time.replace(tzinfo=timezone.utc)
            delay = int((retry_time - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            delay = 2 ** attempt
    return min(60, max(1, delay))


def _make_paper(title: str, abstract: str, authors: list, published: str,
                doi: str, venue: str, publisher: str, source: str,
                landing_url: str, pdf_candidates: list, publication_type: str) -> dict:
    clean_doi = _doi(doi)
    pdf_candidates = sorted(pdf_candidates, key=lambda item: item.get("priority", 0), reverse=True)
    return {
        "id": clean_doi or re.sub(r"[^a-z0-9]+", "", title.lower()),
        "arxiv_id": "",
        "title": _clean(title),
        "abstract": _clean(abstract),
        "authors": authors,
        "published": published,
        "updated": published,
        "comment": "",
        "journal_ref": _clean(venue),
        "doi": clean_doi,
        "categories": [],
        "pdf_url": pdf_candidates[0]["url"] if pdf_candidates else "",
        "pdf_urls": list(dict.fromkeys(item["url"] for item in pdf_candidates if item.get("url"))),
        "pdf_candidates": pdf_candidates,
        "source": source,
        "sources": [source],
        "publication_type": publication_type,
        "venue": _clean(venue),
        "publisher": _clean(publisher),
        "landing_url": landing_url or (f"https://doi.org/{clean_doi}" if clean_doi else ""),
    }


def fetch_crossref(phrase: str, since, until, max_results: int) -> list:
    params = {
        "query.bibliographic": phrase,
        "filter": f"from-pub-date:{since:%Y-%m-%d},until-pub-date:{until:%Y-%m-%d},type:journal-article",
        "rows": min(max_results, 100),
    }
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    payload = _fetch_json(f"{CROSSREF_API}?{urlencode(params)}")

    papers = []
    for item in payload.get("message", {}).get("items", []):
        title = (item.get("title") or [""])[0]
        doi = _doi(item.get("DOI", ""))
        if not title or not doi or item.get("type") != "journal-article":
            continue
        date_parts = item.get("published-online", {}).get("date-parts") or item.get("published-print", {}).get("date-parts") or item.get("issued", {}).get("date-parts")
        published = _date_from_parts(date_parts)
        venue = (item.get("container-title") or [""])[0]
        authors = []
        for author in item.get("author", []):
            name = " ".join(filter(None, [author.get("given"), author.get("family")])).strip()
            if name:
                authors.append(name)
        pdf_candidates = [{"url": link.get("URL", ""), "version": "publisher published version",
                           "priority": 3, "origin": "Crossref"}
                          for link in item.get("link", [])
                          if "pdf" in (link.get("content-type", "") + link.get("intended-application", "")).lower()]
        papers.append(_make_paper(title, item.get("abstract", ""), authors, published,
                                  doi, venue, item.get("publisher", ""), "Crossref",
                                  item.get("URL", ""), pdf_candidates, "journal-article"))
    return papers


def _rebuild_abstract(index: dict) -> str:
    if not isinstance(index, dict):
        return ""
    words = []
    for word, positions in index.items():
        words.extend((position, word) for position in positions)
    return " ".join(word for _, word in sorted(words))


def fetch_openalex(phrase: str, since, until, max_results: int) -> list:
    params = {
        "search": phrase,
        "filter": f"from_publication_date:{since:%Y-%m-%d},to_publication_date:{until:%Y-%m-%d},type:article",
        "per-page": min(max_results, 100),
    }
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    payload = _fetch_json(f"{OPENALEX_API}?{urlencode(params)}")

    papers = []
    for item in payload.get("results", []):
        title = item.get("title", "")
        doi = _doi(item.get("doi", ""))
        if not title or not doi:
            continue
        location = item.get("primary_location") or {}
        journal = location.get("source") or {}
        raw_type = (location.get("raw_type", "") or "").lower()
        if journal.get("type") not in (None, "journal") or "proceedings" in raw_type:
            continue
        oa_location = item.get("best_oa_location") or {}
        pdf_candidates = []
        if oa_location.get("pdf_url"):
            version = oa_location.get("version", "")
            priority = 4 if version == "publishedVersion" else 2 if version == "acceptedVersion" else 1
            pdf_candidates.append({"url": oa_location["pdf_url"], "version": version or "OpenAlex OA file",
                                   "priority": priority, "origin": "OpenAlex"})
        if location.get("is_oa"):
            if location.get("pdf_url"):
                version = location.get("version", "")
                priority = 4 if version == "publishedVersion" else 2 if version == "acceptedVersion" else 1
                pdf_candidates.append({"url": location["pdf_url"], "version": version or "OpenAlex OA file",
                                       "priority": priority, "origin": "OpenAlex"})
        authors = [entry.get("author", {}).get("display_name", "")
                   for entry in item.get("authorships", [])
                   if entry.get("author", {}).get("display_name")]
        venue = journal.get("display_name", "") or location.get("raw_source_name", "")
        publisher = journal.get("host_organization_name", "")
        papers.append(_make_paper(title, _rebuild_abstract(item.get("abstract_inverted_index")),
                                  authors, item.get("publication_date", ""), doi, venue,
                                  publisher, "OpenAlex", location.get("landing_page_url", ""),
                                  pdf_candidates, "journal-article"))
    return papers


def fetch_semantic_scholar(phrase: str, since, until, max_results: int) -> list:
    year_range = str(since.year) if since.year == until.year else f"{since.year}-{until.year}"
    params = urlencode({
        "query": phrase,
        "year": year_range,
        "limit": min(max_results, 1000),
        "fields": "title,abstract,authors,year,publicationDate,externalIds,journal,publicationVenue,openAccessPdf,url,publicationTypes",
    })
    headers = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    payload = _fetch_json(f"{SEMANTIC_SCHOLAR_API}?{params}", headers=headers)

    papers = []
    for item in payload.get("data", []):
        title = item.get("title", "")
        published = item.get("publicationDate", "") or ""
        if not title or not published:
            continue
        try:
            publication_day = datetime.strptime(published[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if not (since.date() <= publication_day.date() <= until.date()):
            continue

        external_ids = item.get("externalIds") or {}
        doi = _doi(external_ids.get("DOI", ""))
        raw_arxiv_id = str(external_ids.get("ArXiv", "") or external_ids.get("arXiv", "") or "")
        raw_arxiv_id = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", raw_arxiv_id, flags=re.I)
        arxiv_id = re.sub(r"v\d+$", "", raw_arxiv_id.strip())
        venue_info = item.get("publicationVenue") or {}
        journal_info = item.get("journal") or {}
        venue = venue_info.get("name", "") or journal_info.get("name", "")
        if not venue:
            continue
        venue_type = (venue_info.get("type", "") or "").lower()
        publication_types = {str(value).lower() for value in (item.get("publicationTypes") or []) if value}
        is_journal = "journalarticle" in publication_types or bool(journal_info.get("name")) or venue_type == "journal"

        authors = [author.get("name", "") for author in (item.get("authors") or []) if author.get("name")]
        pdf_info = item.get("openAccessPdf") or {}
        pdf_candidates = []
        if pdf_info.get("url"):
            pdf_candidates.append({
                "url": pdf_info["url"],
                "version": "Semantic Scholar open-access copy",
                "priority": 2,
                "origin": "Semantic Scholar",
            })
        paper = _make_paper(
            title, item.get("abstract", ""), authors, published, doi, venue, "",
            "Semantic Scholar", item.get("url", ""), pdf_candidates,
            "journal-article" if is_journal else "preprint",
        )
        if arxiv_id:
            paper["arxiv_id"] = arxiv_id
            paper["arxiv_url"] = f"https://arxiv.org/abs/{arxiv_id}"
        papers.append(paper)
    return papers


def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def merge_sources(papers: list) -> list:
    merged = []
    by_doi = {}
    by_title = {}
    for paper in papers:
        doi = _doi(paper.get("doi", ""))
        title_key = _title_key(paper.get("title", ""))
        existing = by_doi.get(doi) if doi else None
        if existing is None and title_key:
            existing = by_title.get(title_key)
        if existing is None:
            paper["sources"] = list(dict.fromkeys(paper.get("sources", [paper.get("source", "unknown")])) )
            merged.append(paper)
            if doi:
                by_doi[doi] = paper
            if title_key:
                by_title[title_key] = paper
            continue

        sources = list(dict.fromkeys(existing.get("sources", []) + paper.get("sources", [paper.get("source", "unknown")])) )
        is_published = paper.get("publication_type") == "journal-article"
        if is_published:
            for field in ("published", "venue", "publisher", "landing_url", "journal_ref", "doi"):
                if paper.get(field):
                    existing[field] = paper[field]
            existing["publication_type"] = "journal-article"
        for field in ("abstract", "authors"):
            if not existing.get(field) and paper.get(field):
                existing[field] = paper[field]
        candidates = existing.get("pdf_candidates", []) + paper.get("pdf_candidates", [])
        by_url = {}
        for candidate in candidates:
            url = candidate.get("url", "")
            if url and (url not in by_url or candidate.get("priority", 0) > by_url[url].get("priority", 0)):
                by_url[url] = candidate
        existing["pdf_candidates"] = sorted(by_url.values(), key=lambda item: item.get("priority", 0), reverse=True)
        existing["pdf_urls"] = [item["url"] for item in existing["pdf_candidates"]]
        existing["pdf_url"] = existing["pdf_urls"][0] if existing["pdf_urls"] else existing.get("pdf_url", "")
        existing["sources"] = sources
        existing["source"] = ", ".join(sources)
        existing["matched_topics"] = list(dict.fromkeys(existing.get("matched_topics", []) + paper.get("matched_topics", [])))
        if paper.get("arxiv_id"):
            existing["arxiv_id"] = paper["arxiv_id"]
            existing["arxiv_version"] = paper.get("arxiv_version", "")
            existing["arxiv_url"] = f"https://arxiv.org/abs/{paper['arxiv_id']}"
        if doi:
            existing["doi"] = doi
            existing["id"] = doi
            by_doi[doi] = existing
        if title_key:
            by_title[title_key] = existing
    return merged


def _pdfinfo_executable() -> str:
    executable = shutil.which("pdfinfo")
    if executable:
        return executable
    return ""

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_public_pdfs(papers: list, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pdfinfo = _pdfinfo_executable()
    existing = list(output_dir.rglob("*.pdf"))
    hashes = {_sha256(path) for path in existing}
    existing_names = [(path, _title_key(path.stem)) for path in existing]
    staging_dir = Path(tempfile.mkdtemp(prefix="paper-radar-", dir=output_dir))
    try:
        for paper in papers:
            title_key = _title_key(paper.get("title", ""))
            doi_key = re.sub(r"[^a-z0-9]+", "", _doi(paper.get("doi", "")))
            arxiv_id = paper.get("arxiv_id", "")
            is_journal = paper.get("publication_type") == "journal-article"
            duplicate = next((path for path, name in existing_names
                              if (doi_key and doi_key in name)
                              or (not is_journal and arxiv_id and arxiv_id.replace(".", "") in name)
                              or (len(title_key) > 32 and title_key in name
                                  and (not is_journal or not name.startswith("2026arxiv")))), None)
            if duplicate:
                paper.update(download_status="重复，已存在", local_path=str(duplicate))
                continue
            if not pdfinfo:
                paper.update(download_status="未下载：找不到 pdfinfo")
                continue

            candidates = paper.get("pdf_candidates", [])
            if is_journal:
                # A journal record must archive the published version; an older arXiv or accepted manuscript is not a substitute.
                candidates = [item for item in candidates if item.get("priority", 0) >= 3]
            if not candidates and paper.get("pdf_url") and not is_journal:
                candidates = [{"url": paper["pdf_url"], "version": "arXiv latest", "priority": 1, "origin": "arXiv"}]
            if not candidates:
                paper.update(download_status=("未找到开放的正式发表版 PDF" if is_journal else "未找到开放 PDF"))
                continue

            downloaded = False
            errors = []
            for index, candidate in enumerate(candidates):
                url = candidate.get("url", "")
                if not url:
                    continue
                staging = staging_dir / f"{len(existing_names)}_{index}.pdf"
                try:
                    req = urlrequest.Request(url, headers={"User-Agent": "paper-radar/2.0"})
                    with urlrequest.urlopen(req, timeout=60) as response, staging.open("wb") as output:
                        shutil.copyfileobj(response, output)
                    if staging.stat().st_size <= 10 * 1024 or staging.read_bytes()[:5] != b"%PDF-":
                        raise ValueError("不是有效 PDF 或文件过小")
                    info = subprocess.run([pdfinfo, str(staging)], capture_output=True, text=True,
                                          encoding="utf-8", errors="ignore")
                    if info.returncode != 0 or "Pages:" not in info.stdout:
                        raise ValueError("pdfinfo 校验失败")
                    digest = _sha256(staging)
                    if digest in hashes:
                        paper.update(download_status="重复，SHA-256 相同", downloaded_version=candidate.get("version", ""))
                        staging.unlink(missing_ok=True)
                        downloaded = True
                        break
                    slug = re.sub(r"[^A-Za-z0-9]+", "_", paper["title"]).strip("_")[:88]
                    prefix = f"2026_arXiv_{arxiv_id}_" if arxiv_id else f"2026_journal_{doi_key[:24]}_"
                    destination = output_dir / f"{prefix}{slug}.pdf"
                    os.replace(staging, destination)
                    hashes.add(digest)
                    existing_names.append((destination, _title_key(destination.stem)))
                    status = "已下载正式发表版本并通过校验" if is_journal else "已下载 arXiv 版本并通过校验"
                    paper.update(download_status=status, local_path=str(destination), sha256=digest,
                                 downloaded_version=candidate.get("version", ""))
                    downloaded = True
                    break
                except Exception as error:
                    errors.append(str(error))
                    staging.unlink(missing_ok=True)
            if not downloaded:
                prefix = "正式发表版 PDF 未能下载/验证" if is_journal else "预印本 PDF 未能下载/验证"
                paper.update(download_status=prefix + ("：" + errors[-1] if errors else ""))
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def match_venues(paper: dict, venues: list) -> list:
    haystack = " || ".join(filter(None, [paper.get("comment", ""), paper.get("journal_ref", ""),
                                         paper.get("venue", ""), paper.get("doi", "")]))
    if not haystack:
        return []
    matched = set()
    for v in venues:
        for pat in v["patterns"]:
            if re.search(pat, haystack, flags=re.IGNORECASE):
                matched.add(v["name"])
                break
    return sorted(matched)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def prune_state(state: dict) -> dict:
    cutoff = (datetime.now() - timedelta(days=STATE_TTL_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in state.items() if v.get("first_seen", "9999") >= cutoff}


def state_keys(paper: dict) -> list:
    keys = []
    doi = _doi(paper.get("doi", ""))
    arxiv_id = paper.get("arxiv_id", "")
    if doi:
        keys.append(f"doi:{doi}")
    if arxiv_id:
        keys.extend([f"arxiv:{arxiv_id}", arxiv_id])  # bare ID keeps compatibility with the old state file
    title_key = _title_key(paper.get("title", ""))
    if title_key:
        keys.append(f"title:{title_key}")
    return list(dict.fromkeys(keys))


def prior_state(paper: dict, state: dict):
    return next((state[key] for key in state_keys(paper) if key in state), None)


def fmt_authors(authors: list) -> str:
    if not authors:
        return "未知"
    if len(authors) <= 3:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + f" 等 {len(authors)} 人"


def fmt_venue_line(paper: dict, venue_names: list) -> str:
    venue = paper.get("venue", "") or paper.get("journal_ref", "")
    publisher = paper.get("publisher", "")
    label = venue or "、".join(venue_names)
    if publisher:
        label += f"；出版社：{publisher}"
    evidence = []
    if paper.get("source"):
        evidence.append(f"来源 {paper['source']}")
    if paper.get("doi"):
        evidence.append(f"DOI {paper['doi']}")
    if paper.get("comment"):
        evidence.append(f"arXiv comment「{paper['comment']}」")
    if paper.get("journal_ref") and paper.get("journal_ref") != venue:
        evidence.append(f"journal_ref「{paper['journal_ref']}」")
    return label + ("（依据 " + "；".join(evidence) + "）" if evidence else "")


def paper_entry(paper: dict, venue_names: list, idx: int) -> str:
    lines = [f"**{idx}. {paper['title']}**"]
    if paper.get("publication_type") == "journal-article":
        lines.append(f"- 🏫 期刊：{fmt_venue_line(paper, venue_names)}")
    elif venue_names:
        lines.append(f"- 🏫 录用：{fmt_venue_line(paper, venue_names)}")
    if paper.get("impact_factor") is not None:
        lines.append(
            f"- 📊 影响因子：{paper['impact_factor']:.1f}"
            f"（{paper.get('impact_factor_data_year')} JIF，JCR {paper.get('impact_factor_release_year')}）"
        )
    date_label = "在线发表" if paper.get("publication_type") == "journal-article" else "提交"
    lines.append(f"- 👥 {fmt_authors(paper.get('authors', []))} · {date_label} {paper.get('published', '未知日期')}")
    links = []
    if paper.get("doi"):
        links.append(f"[DOI](https://doi.org/{paper['doi']})")
    if paper.get("arxiv_id"):
        links.append(f"[arXiv](https://arxiv.org/abs/{paper['arxiv_id']})")
    elif paper.get("landing_url"):
        links.append(f"[出版页面]({paper['landing_url']})")
    if paper.get("pdf_url"):
        links.append(f"[PDF]({paper['pdf_url']})")
    if links:
        lines.append("- 🔗 " + " | ".join(links))
    if paper.get("publisher"):
        lines.append(f"- 🏢 出版商：{paper['publisher']}")
    if paper.get("downloaded_version"):
        lines.append(f"- 📚 保存版本：{paper['downloaded_version']}")
    elif paper.get("arxiv_version"):
        lines.append(f"- 📚 arXiv 版本：{paper['arxiv_version']}")
    if paper.get("sources"):
        lines.append(f"- 🔎 元数据来源：{'、'.join(paper['sources'])}")
    cat = ", ".join(paper.get("categories", [])[:3])
    if cat:
        lines.append(f"- 🏷️ {cat}")
    abstract = paper["abstract"]
    if len(abstract) > ABSTRACT_MAX:
        abstract = abstract[:ABSTRACT_MAX] + "……"
    lines.append(f"- 📄 {abstract}")
    if paper.get("download_status"):
        lines.append(f"- ⬇️ 下载：{paper['download_status']}" + (f" · {paper['local_path']}" if paper.get("local_path") else ""))
    return "\n".join(lines)


def _source_status(config: dict) -> str:
    available = config.get("_source_available", [])
    failures = config.get("_source_failures", {})
    parts = ["可用：" + "、".join(available)] if available else []
    for source, errors in failures.items():
        if not errors:
            continue
        combined_errors = " ".join(errors)
        codes = sorted(set(re.findall(r"HTTP Error \d+|WinError \d+", combined_errors)))
        detail = "、".join(codes) if codes else errors[0][:100]
        if source == "arXiv" and "短 GET 诊断查询成功" in combined_errors:
            detail += "；短 GET 可达"
        elif source == "arXiv" and "短 GET 诊断查询也失败" in combined_errors:
            detail += "；短 GET 也失败"
        parts.append(f"{source} 有 {len(errors)} 个查询失败（{detail}）")
    return "；".join(parts) if parts else "本次未记录到来源状态"


def build_report(papers: list, config: dict, since: datetime, until: datetime, days: int, state: dict) -> tuple:
    """返回 (markdown 文本, 新期刊数, 新录用数, 总数)"""
    topic_names = [t["name"] for t in config["topics"]]
    threshold = float(config.get("min_impact_factor", 8.0))
    all_venues = [journal for journal in config["journals"]
                  if float(journal.get("impact_factor", 0)) >= threshold]
    for p in papers:
        p["venue_sig"] = match_venues(p, all_venues)
        matched_journals = [journal for journal in all_venues if journal["name"] in p["venue_sig"]]
        p["impact_factor"] = max((float(journal["impact_factor"]) for journal in matched_journals), default=None)
        p["impact_factor_data_year"] = config.get("impact_factor_data_year")
        p["impact_factor_release_year"] = config.get("impact_factor_release_year")
    # 每篇论文归属第一个命中的主题
    for p in papers:
        matched = p.get("matched_topics", [])
        p["topic"] = matched[0] if matched else "未分类"
        p["extra_topics"] = [n for n in matched if n != p["topic"]]

    # 期刊论文按出版指纹去重；arXiv 录用信息变化时重新浮现。
    journal_new, accepted = [], []
    for p in papers:
        p["to_report"] = False
        old = prior_state(p, state)
        old_sig = sorted(old.get("venue_sig", [])) if old else None
        new_sig = sorted(p["venue_sig"])
        if p.get("publication_type") == "journal-article":
            if not new_sig:
                continue
            fingerprint = "|".join([p.get("doi", ""), p.get("published", ""), p.get("venue", "")])
            if not old or old.get("publication_fingerprint") != fingerprint:
                journal_new.append(p)
                p["to_report"] = True
        elif new_sig:
            if old_sig != new_sig:
                accepted.append(p)
                p["to_report"] = True

    def section(title, group, venue_section):
        out = [f"\n## {title}（{len(group)}）\n"]
        if not group:
            out.append("_本期无。_\n")
            return out
        for tname in topic_names:
            sub = [p for p in group if p["topic"] == tname]
            if not sub:
                continue
            out.append(f"\n### 📌 {tname}\n")
            for i, p in enumerate(sub, 1):
                extra = f"（同时匹配：{'、'.join(p['extra_topics'])}）" if p["extra_topics"] else ""
                out.append(paper_entry(p, p["venue_sig"] if venue_section else [], i) + extra + "\n")
        return out

    md = [f"# 📡 paper-radar 周报 · {until:%Y-%m-%d}",
          "",
          f"- 覆盖窗口：{since:%Y-%m-%d} ~ {until:%Y-%m-%d}（近 {days} 天）",
          f"- 数据源：arXiv、Crossref、OpenAlex、Semantic Scholar · arXiv 分类 {'/'.join(config['categories'])}",
          f"- 检索状态：{_source_status(config)}",
          f"- 筛选条件：仅收录 {config.get('impact_factor_data_year')} JIF ≥ {threshold:g} 的目标期刊（JCR {config.get('impact_factor_release_year')}）；不收录会议和未录用预印本",
          f"- 本期：**新期刊论文 {len(journal_new)} 篇** · **新增高影响因子期刊录用信息 {len(accepted)} 条**",
          "- 期刊及影响因子数据：见 `config.json`；每年按最新 JCR 更新"]
    md += section("一、📰 新发表期刊论文", journal_new, True)
    md += section("二、🎓 arXiv / Semantic Scholar 记录中新增的高影响因子期刊录用", accepted, True)
    md.append("\n---\n*由 paper-radar 生成；仅报告达到 JIF 阈值的期刊论文及其录用信息。*")
    return "\n".join(md), len(journal_new), len(accepted), len(papers)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="paper-radar：小目标检测 IF≥8 期刊周报")
    ap.add_argument("--days", type=int, default=None, help="回溯天数（默认取 config.days_window，周一例跑=7）")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--max-per-query", type=int, default=None, help="每个主题查询的最多条数（默认 config）")
    ap.add_argument("--no-save", action="store_true", help="只生成报告，不更新去重状态（试跑用）")
    ap.add_argument("--no-download", action="store_true", help="不下载开放 PDF")
    args = ap.parse_args()

    config = load_config(Path(args.config))
    days = args.days or config.get("days_window", 7)
    max_per_query = args.max_per_query or config.get("max_per_query", 60)
    until = datetime.now()
    since = until - timedelta(days=days)

    # 四个来源独立抓取；单一接口故障时继续使用其他来源。
    collected = []
    successful_sources = set()
    source_failures = {name: [] for name in ("arXiv", "Crossref", "OpenAlex", "Semantic Scholar")}
    arxiv_truncated = False
    external_max = config.get("external_max_per_query", 20)
    for topic in config["topics"]:
        q = build_query(topic["phrases"], config["categories"], since, until)
        try:
            results = fetch(q, max_per_query)
        except RuntimeError as e:
            source_failures["arXiv"].append(str(e))
            print(f"⚠️ arXiv 主题「{topic['name']}」抓取失败：{e}", file=sys.stderr)
            results = []
        else:
            successful_sources.add("arXiv")
            if len(results) >= max_per_query:
                arxiv_truncated = True
        for p in results:
            p["matched_topics"] = [topic["name"]]
            collected.append(p)
        print(f"  主题「{topic['name']}」：{len(results)} 篇", file=sys.stderr)
        time.sleep(REQUEST_GAP)

    for topic in config["topics"]:
        for phrase in topic["phrases"]:
            try:
                results = fetch_crossref(phrase, since, until, external_max)
            except (RuntimeError, HTTPError, URLError, TimeoutError, OSError) as error:
                source_failures["Crossref"].append(str(error))
                print(f"⚠️ Crossref「{phrase}」抓取失败：{error}", file=sys.stderr)
                results = []
            else:
                successful_sources.add("Crossref")
            for paper in results:
                paper["matched_topics"] = [topic["name"]]
                collected.append(paper)
            time.sleep(EXTERNAL_GAP)

    for topic in config["topics"]:
        for phrase in topic["phrases"][:2]:
            try:
                results = fetch_openalex(phrase, since, until, external_max * 2)
            except (RuntimeError, HTTPError, URLError, TimeoutError, OSError) as error:
                source_failures["OpenAlex"].append(str(error))
                print(f"⚠️ OpenAlex「{phrase}」抓取失败：{error}", file=sys.stderr)
                results = []
            else:
                successful_sources.add("OpenAlex")
            for paper in results:
                paper["matched_topics"] = [topic["name"]]
                collected.append(paper)
            time.sleep(EXTERNAL_GAP)

    for topic in config["topics"]:
        for phrase in topic["phrases"][:2]:
            try:
                results = fetch_semantic_scholar(phrase, since, until, external_max * 2)
            except (RuntimeError, HTTPError, URLError, TimeoutError, OSError) as error:
                source_failures["Semantic Scholar"].append(str(error))
                print(f"⚠️ Semantic Scholar「{phrase}」抓取失败：{error}", file=sys.stderr)
                results = []
            else:
                successful_sources.add("Semantic Scholar")
            for paper in results:
                paper["matched_topics"] = [topic["name"]]
                collected.append(paper)
            time.sleep(SEMANTIC_SCHOLAR_GAP)

    if not successful_sources:
        raise RuntimeError("arXiv、Crossref、OpenAlex、Semantic Scholar 均无法访问；本期未生成周报。")
    config["_source_available"] = sorted(successful_sources)
    config["_source_failures"] = source_failures
    merged = merge_sources(collected)
    threshold = float(config.get("min_impact_factor", 8.0))
    eligible_journals = [journal for journal in config["journals"]
                         if float(journal.get("impact_factor", 0)) >= threshold]
    if not eligible_journals:
        raise RuntimeError(f"config.json 中没有达到 JIF ≥ {threshold:g} 的期刊")
    papers = []
    for paper in merged:
        matched = [journal for journal in eligible_journals
                   if journal["name"] in match_venues(paper, [journal])]
        if not matched:
            continue
        paper["venue_sig"] = sorted(journal["name"] for journal in matched)
        paper["impact_factor"] = max(float(journal["impact_factor"]) for journal in matched)
        paper["impact_factor_data_year"] = config.get("impact_factor_data_year")
        paper["impact_factor_release_year"] = config.get("impact_factor_release_year")
        papers.append(paper)
    print(f"  跨源合并：{len(collected)} 条来源记录 → {len(merged)} 篇唯一论文；JIF 筛选后 {len(papers)} 篇；可用来源：{'、'.join(sorted(successful_sources))}", file=sys.stderr)
    state_path = Path(config.get("state_file", ROOT / "state" / "seen.json"))
    state = load_state(state_path)
    build_report(papers, config, since, until, days, state)
    if not args.no_download:
        output_dir = Path(config.get("download_dir", ROOT / "data" / "pdfs"))
        download_public_pdfs([p for p in papers if p.get("to_report")], output_dir)
    report_md, n_journal, n_acc, n_total = build_report(papers, config, since, until, days, state)

    # 写周报（同日重跑不覆盖已有文件）
    reports_dir = Path(config.get("reports_dir", ROOT / "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = reports_dir / f"{until:%Y-%m-%d}_week.md"
    report_path = base
    n = 2
    while report_path.exists():
        report_path = reports_dir / f"{until:%Y-%m-%d}_week_{n}.md"
        n += 1
    report_path.write_text(report_md, encoding="utf-8")

    # 更新去重状态
    if not args.no_save:
        today = f"{until:%Y-%m-%d}"
        state = prune_state(state)
        for p in papers:
            old = prior_state(p, state)
            item_state = {
                "first_seen": old.get("first_seen", today) if old else today,
                "title": p["title"][:120],
                "venue_sig": p["venue_sig"],
            }
            if p.get("publication_type") == "journal-article":
                item_state["publication_fingerprint"] = "|".join([p.get("doi", ""), p.get("published", ""), p.get("venue", "")])
            for key in state_keys(p):
                state[key] = item_state
        save_state(state_path, state)

    print(f"\n✅ 周报已生成：{report_path}")
    print(f"   JIF 筛选后 {n_total} 篇 | 新期刊论文 {n_journal} 篇 | 新录用 {n_acc} 条"
          + ("（--no-save，状态未更新）" if args.no_save else ""))
    if arxiv_truncated:
        print("\n⚠️ arXiv 查询达到 max-per-query 上限，结果可能被截断；可加大 --max-per-query。")


if __name__ == "__main__":
    main()


