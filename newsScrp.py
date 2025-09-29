import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


NAVER_BASE = (
    "https://search.naver.com/search.naver?"
    "ssc=tab.news.all&where=news&sm=tab_jum&query={query}&start={start}"
)


def read_firecrawl_key() -> str:
    env_key = os.environ.get("FIRECRAWL_KEY", "").strip()
    if env_key:
        return env_key

    # Try Cursor MCP config
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    cfg_path = Path(home) / ".cursor" / "mcp.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            server = data.get("mcpServers", {}).get("mcp-server-firecrawl")
            if server and isinstance(server, dict):
                args = server.get("args", [])
                if isinstance(args, list):
                    for i, v in enumerate(args):
                        if v == "--key" and i + 1 < len(args):
                            return str(args[i + 1]).strip()
        except Exception:
            pass
    return ""


def firecrawl_scrape(url: str, api_key: str) -> str:
    endpoint = "https://api.firecrawl.dev/v1/scrape"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {"url": url, "formats": ["html"]}
    resp = requests.post(endpoint, headers=headers, json=body, timeout=30)
    if resp.status_code == 429:
        # Rate limit - raise with hint
        raise RuntimeError("RATE_LIMIT")
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("html") or data.get("html", "")


def extract_titles_from_html(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[tuple[str, str]] = []

    # Primary selector: user-provided class combo
    sel = ".sds-comps-text.sds-comps-text-ellipsis.sds-comps-text-ellipsis-1.sds-comps-text-type-headline1"
    for el in soup.select(sel):
        t = (el.get_text(strip=True) or "").strip()
        link = ""
        a = el.find_parent("a") or el.find("a")
        if a and a.has_attr("href"):
            link = a.get("href") or ""
        if t and not re.search(r"바로가기|옵션|더보기|네이버|검색", t):
            items.append((t, link))

    if items:
        return items

    # Fallback: legacy anchor
    for a in soup.select("a.news_tit"):
        t = (a.get("title") or a.get_text(strip=True) or "").strip()
        href = a.get("href") or ""
        if t and not re.search(r"바로가기|옵션|더보기|네이버|검색", t):
            items.append((t, href))

    return items


def build_search_url(query: str, start: int) -> str:
    return NAVER_BASE.format(query=requests.utils.quote(query), start=max(1, start))


# 수집 제외 키워드(부분 일치). 공백/하이픈 변형까지 고려해 비교
EXCLUDE_KEYWORDS = [
    "1박2일",
    "전통시장",
    "설명회",
    "야구",
    "승진",
    "사과문",
    "채용",
    "날씨",
    "결혼",
    "합격자",
    "시상식",
    "청약",
    "대회",
    "축제",
    "당선작",

    "박람회",
    "수상자",
    "페스티벌",
    "마라톤",
    "캠페인",
    "귀농",
    "귀촌",
    "패션쇼",
    "미술제",
    "결방",
    "문화제",
    "[포토]",
    "음악극",
    "클래식",
]


def should_exclude_by_keywords(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    t_compact = t.replace(" ", "").replace("-", "")
    for kw in EXCLUDE_KEYWORDS:
        k = kw.lower()
        if k in t or k in t_compact:
            return True
    # '1박 2일' 변형 대응
    if "1박 2일" in title or "1 박 2 일" in title:
        return True
    return False


QUERY_SUFFIXES = ["간", "동안", "만", "간의", "만의", "만에"]


def should_exclude_by_query_suffix(title: str, query: str) -> bool:
    if not title or not query:
        return False
    t = title.lower()
    q = query.lower().strip()
    if not q:
        return False
    q_compact = q.replace(" ", "")
    for suf in QUERY_SUFFIXES:
        s = suf.lower()
        patterns = [q + s, q + " " + s, q_compact + s]
        for p in patterns:
            if p in t:
                return True
    return False

def tokenize(text: str) -> list[str]:
    # 한글/영문/숫자만 단어로 간주, 소문자 정규화
    tokens = re.findall(r"[A-Za-z0-9가-힣]+", (text or "").lower())
    # 한 글자 토큰은 중복 판정에 영향이 적으므로 필터링(선택)
    return [t for t in tokens if len(t) >= 2]


def has_overlap_three_or_more(candidate: str, existing: list[str]) -> bool:
    cand_tokens = set(tokenize(candidate))
    if not cand_tokens:
        return False
    for prev in existing:
        prev_tokens = set(tokenize(prev))
        if len(cand_tokens.intersection(prev_tokens)) >= 3:
            return True
    return False


def collect_titles(query: str, limit: int = 100, max_pages: int = 300, verbose: bool = True) -> list[dict]:
    key = read_firecrawl_key()
    if not key:
        raise SystemExit("Firecrawl API 키가 필요합니다. FIRECRAWL_KEY 또는 .cursor/mcp.json을 확인하세요.")

    results: list[dict] = []
    start = 1
    attempts = 0
    while len(results) < limit and start <= 1 + (max_pages - 1) * 10:
        url = build_search_url(query, start)
        if verbose:
            print(f"[진행] page start={start}, 현재 {len(results)}/{limit}…", flush=True)
        try:
            html = firecrawl_scrape(url, key)
        except RuntimeError as e:
            if str(e) == "RATE_LIMIT":
                # Exponential backoff up to ~10s
                wait = min(10, 2 + attempts * 2)
                if verbose:
                    print(f"[대기] 레이트리밋 - {wait}s 대기 후 재시도", flush=True)
                time.sleep(wait)
                attempts += 1
                continue
            raise
        except Exception as e:
            # Network or server error - small wait and continue
            if verbose:
                print("[재시도] 네트워크 오류 감지, 2s 대기", flush=True)
            time.sleep(2)
            attempts += 1
            if attempts > 5:
                raise
            continue

        attempts = 0
        new_items = extract_titles_from_html(html)
        for t, u in new_items:
            existing_titles = [r["title"] for r in results]
            if t in existing_titles:
                continue
            if should_exclude_by_keywords(t):
                continue
            if should_exclude_by_query_suffix(t, query):
                continue
            if has_overlap_three_or_more(t, existing_titles):
                # 유사 중복(공통 단어 3개 이상) 건너뛰기
                continue
            results.append({"title": t, "url": u or ""})
            if len(results) >= limit:
                break
        if verbose:
            print(f"[수집] 이번 페이지에서 {len(new_items)}건, 누적 {len(results)}건", flush=True)
        start += 10
        time.sleep(0.9)

    return results[:limit]


def save_csv(items: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "url"])
        for it in items:
            writer.writerow([it.get("title", ""), it.get("url", "")])


def main() -> None:
    parser = argparse.ArgumentParser(description="네이버 뉴스 제목 수집(제목만, 최대 100건)")
    parser.add_argument("--query", required=False, help="검색어 (예: 2일). 생략 시 실행 중 입력")
    parser.add_argument("--limit", type=int, default=100, help="최대 수집 건수(<=100)")
    parser.add_argument("--out", default="", help="CSV 출력 경로(생략 시 자동 생성)")
    args = parser.parse_args()

    query = args.query
    if not query:
        try:
            print("검색어를 입력하세요: ", end="", flush=True)
            query = input().strip()
        except EOFError:
            query = ""
    if not query:
        raise SystemExit("검색어가 필요합니다.")

    limit = min(100, max(1, int(args.limit)))
    print(f"[시작] 검색어='{query}', 최대 {limit}건 수집", flush=True)
    items = collect_titles(query, limit=limit, verbose=True)

    # 출력 파일 경로
    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.utcnow().isoformat().replace(":", "-").replace(".", "-")
        safe_q = re.sub(r"[^\w가-힣._-]+", "_", query)
        out_path = Path(f"news_titles_{safe_q}_{ts}.csv")

    save_csv(items, out_path)

    # 콘솔에도 출력
    for it in items:
        print(it.get("title", ""))

    print(f"Saved {len(items)} rows to {out_path.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()


