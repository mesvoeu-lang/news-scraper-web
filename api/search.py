from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import json
import asyncio
import os
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import quote

app = FastAPI(title="Naver News Titles API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 제외 키워드 목록
EXCLUDE_KEYWORDS = [
    "1박2일", "전통시장", "설명회", "야구", "승진", "사과문", "채용", "날씨", "결혼", "합격자",
    "시상식", "청약", "대회", "축제", "당선작", "박람회", "수상자", "페스티벌", "마라톤", "캠페인",
    "귀농", "귀촌", "패션쇼", "미술제", "결방", "문화제", "[포토]", "음악극", "클래식"
]

# 제외 접미사 목록
QUERY_SUFFIXES = ["간", "동안", "만", "간의", "만의", "만에"]

def read_firecrawl_key() -> str:
    """환경변수에서 Firecrawl API 키를 읽어옵니다."""
    return os.environ.get("FIRECRAWL_KEY", "").strip()

def build_search_url(query: str, start: int = 1) -> str:
    """네이버 뉴스 검색 URL을 생성합니다."""
    encoded_query = quote(query)
    return f"https://search.naver.com/search.naver?ssc=tab.news.all&where=news&sm=tab_jum&query={encoded_query}&start={start}"

        def firecrawl_scrape(url: str, api_key: str) -> str:
            """Firecrawl API를 사용해서 웹페이지를 스크래핑합니다."""
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "url": url,
                "formats": ["html"]
            }
            
            response = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                headers=headers,
                json=payload,
                timeout=10  # 타임아웃을 10초로 단축
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("html", "")
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                if "rate limit" in error_msg.lower():
                    raise Exception(f"RATE_LIMIT: {error_msg}")
                else:
                    raise Exception(error_msg)

def extract_titles_from_html(html: str) -> list[tuple[str, str]]:
    """HTML에서 뉴스 제목과 URL을 추출합니다."""
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
        link = (a.get("href") or "").strip()
        if t and link and not re.search(r"바로가기|옵션|더보기|네이버|검색", t):
            items.append((t, link))
    return items

def should_exclude_by_keywords(title: str) -> bool:
    """제목에 제외 키워드가 포함되어 있는지 확인합니다."""
    title_clean = title.replace(" ", "").replace("-", "")
    for keyword in EXCLUDE_KEYWORDS:
        if keyword in title_clean:
            return True
    return False

def should_exclude_by_query_suffix(title: str, query: str) -> bool:
    """제목에 검색어+접미사 패턴이 포함되어 있는지 확인합니다."""
    title_lower = title.lower()
    query_lower = query.lower()
    
    for suffix in QUERY_SUFFIXES:
        patterns = [
            query_lower + suffix,
            query_lower + " " + suffix,
            query_lower.replace(" ", "") + suffix
        ]
        for pattern in patterns:
            if pattern in title_lower:
                return True
    return False

def tokenize(text: str) -> set[str]:
    """텍스트를 단어 단위로 토큰화합니다."""
    # 한글, 영문, 숫자만 추출
    tokens = re.findall(r'[가-힣a-zA-Z0-9]+', text.lower())
    return set(tokens)

def has_overlap_three_or_more(title: str, existing_titles: list[str]) -> bool:
    """새 제목이 기존 제목들과 3개 이상의 공통 단어를 가지는지 확인합니다."""
    new_tokens = tokenize(title)
    
    for existing_title in existing_titles:
        existing_tokens = tokenize(existing_title)
        overlap = new_tokens.intersection(existing_tokens)
        if len(overlap) >= 3:
            return True
    return False

@app.get("/api/search")
async def search_stream(
    q: str = Query(..., description="검색어"), 
    limit: int = 100, 
    exclude_keywords: str = Query("", description="제외 키워드 (쉼표로 구분)"),
    exclude_suffixes: str = Query("", description="제외 접미사 (쉼표로 구분)")
):
    limit = max(1, min(100, limit))
    
    async def generate():
        # 진행률을 위한 커스텀 collect_titles 함수
        import time
        
        # 제외 키워드 처리
        custom_exclude_keywords = []
        if exclude_keywords:
            custom_exclude_keywords = [k.strip() for k in exclude_keywords.split(',') if k.strip()]
        
        # 제외 접미사 처리
        custom_exclude_suffixes = []
        if exclude_suffixes:
            custom_exclude_suffixes = [s.strip() for s in exclude_suffixes.split(',') if s.strip()]
        
        key = read_firecrawl_key()
        if not key:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Firecrawl API 키가 필요합니다.'})}\n\n"
            return
        
        # API 키 확인 로그
        yield f"data: {json.dumps({'type': 'debug', 'message': f'API 키 확인됨: {key[:10]}...'})}\n\n"
        
        results = []
        start = 1
        attempts = 0
        max_pages = 300
        
        # 시작 메시지
        yield f"data: {json.dumps({'type': 'start', 'query': q, 'limit': limit})}\n\n"
        
        # Vercel 타임아웃 방지를 위해 최대 페이지 수 제한
        max_pages = min(3, limit // 5 + 1)  # 최대 3페이지까지만
        timeout_start = time.time()
        timeout_limit = 120  # 2분 타임아웃 (Vercel 5분 제한 고려)
        
        while len(results) < limit and start <= 1 + (max_pages - 1) * 10:
            # 타임아웃 체크
            if time.time() - timeout_start > timeout_limit:
                yield f"data: {json.dumps({'type': 'timeout', 'message': '시간 제한으로 인해 검색을 중단합니다. 현재까지 수집된 결과를 반환합니다.'})}\n\n"
                break
                
            url = build_search_url(q, start)
            
            # 진행 상황 전송
            yield f"data: {json.dumps({'type': 'progress', 'current': len(results), 'total': limit, 'page': start, 'message': f'페이지 {start} 처리 중...'})}\n\n"
            
            try:
                yield f"data: {json.dumps({'type': 'debug', 'message': f'Firecrawl API 호출 시작: {url}'})}\n\n"
                html = firecrawl_scrape(url, key)
                yield f"data: {json.dumps({'type': 'debug', 'message': f'Firecrawl API 응답 받음, HTML 길이: {len(html)}'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'debug', 'message': f'Firecrawl API 오류: {str(e)}'})}\n\n"
                if "RATE_LIMIT" in str(e):
                    wait = min(5, 1 + attempts)  # 대기 시간 단축
                    yield f"data: {json.dumps({'type': 'wait', 'message': f'레이트리밋 - {wait}초 대기', 'seconds': wait})}\n\n"
                    await asyncio.sleep(wait)
                    attempts += 1
                    continue
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'네트워크 오류: {str(e)}'})}\n\n"
                    await asyncio.sleep(1)  # 대기 시간 단축
                    attempts += 1
                    if attempts > 3:  # 재시도 횟수 감소
                        break
                    continue
            
            attempts = 0
            new_items = extract_titles_from_html(html)
            new_count = 0
            
            for title, url in new_items:
                existing_titles = [r["title"] for r in results]
                if title in existing_titles:
                    continue
                if should_exclude_by_keywords(title):
                    continue
                # 커스텀 제외 키워드 체크
                if custom_exclude_keywords:
                    should_skip = False
                    for keyword in custom_exclude_keywords:
                        if keyword in title.replace(" ", "").replace("-", ""):
                            should_skip = True
                            break
                    if should_skip:
                        continue
                # 커스텀 제외 접미사 체크
                if custom_exclude_suffixes:
                    should_skip = False
                    for suffix in custom_exclude_suffixes:
                        # 검색어 + 접미사 패턴 체크
                        patterns = [q + suffix, q + " " + suffix, q.replace(" ", "") + suffix]
                        for pattern in patterns:
                            if pattern.lower() in title.lower():
                                should_skip = True
                                break
                        if should_skip:
                            break
                    if should_skip:
                        continue
                
                if should_exclude_by_query_suffix(title, q):
                    continue
                if has_overlap_three_or_more(title, existing_titles):
                    continue
                    
                results.append({"title": title, "url": url or ""})
                new_count += 1
                
                # 새 항목 추가 시 즉시 전송
                yield f"data: {json.dumps({'type': 'item', 'item': {'title': title, 'url': url or ''}, 'total': len(results)})}\n\n"
                
                if len(results) >= limit:
                    break
            
            yield f"data: {json.dumps({'type': 'page_complete', 'page': start, 'found': new_count, 'total': len(results)})}\n\n"
            start += 10
            await asyncio.sleep(0.2)  # 대기 시간 더 단축
        
        # 완료 메시지
        yield f"data: {json.dumps({'type': 'complete', 'total': len(results)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/plain")

@app.get("/api/health")
def health():
    return {"status": "ok", "message": "News Scraper API is running"}

# Vercel에서 실행하기 위한 핸들러
def handler(request):
    return app(request.scope, request.receive, request.send)
