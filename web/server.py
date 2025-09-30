from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
import json
import asyncio
import sys
from pathlib import Path

# newsScrp.py의 함수 재사용을 위해 동적 import
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from newsScrp import collect_titles  # type: ignore


app = FastAPI(title="Naver News Titles API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일 서빙
static_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class Item(BaseModel):
    title: str
    url: str


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/search", response_model=list[Item])
def search(q: str = Query(..., description="검색어"), limit: int = 100):
    limit = max(1, min(100, limit))
    items = collect_titles(q, limit=limit, verbose=False)
    return items


@app.get("/api/search")
async def api_search(q: str = Query(..., description="검색어"), limit: int = 100, exclude_keywords: str = Query("", description="제외 키워드 (쉼표로 구분)"), exclude_suffixes: str = Query("", description="제외 접미사 (쉼표로 구분)")):
    """API 엔드포인트 - search-stream과 동일한 기능"""
    return await search_stream(q, limit, exclude_keywords, exclude_suffixes)

@app.get("/search-stream")
async def search_stream(q: str = Query(..., description="검색어"), limit: int = 100, exclude_keywords: str = Query("", description="제외 키워드 (쉼표로 구분)"), exclude_suffixes: str = Query("", description="제외 접미사 (쉼표로 구분)")):
    limit = max(1, min(100, limit))
    
    async def generate():
        # 진행률을 위한 커스텀 collect_titles 함수
        from newsScrp import read_firecrawl_key, build_search_url, firecrawl_scrape, extract_titles_from_html, should_exclude_by_keywords, should_exclude_by_query_suffix, has_overlap_three_or_more
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
            yield f"data: {json.dumps({'error': 'Firecrawl API 키가 필요합니다.'})}\n\n"
            return
            
        results = []
        start = 1
        attempts = 0
        max_pages = 300
        
        # 시작 메시지
        yield f"data: {json.dumps({'type': 'start', 'query': q, 'limit': limit})}\n\n"
        
        while len(results) < limit and start <= 1 + (max_pages - 1) * 10:
            url = build_search_url(q, start)
            
            # 진행 상황 전송
            yield f"data: {json.dumps({'type': 'progress', 'current': len(results), 'total': limit, 'page': start, 'message': f'페이지 {start} 처리 중...'})}\n\n"
            
            try:
                html = firecrawl_scrape(url, key)
            except Exception as e:
                if "RATE_LIMIT" in str(e):
                    wait = min(10, 2 + attempts * 2)
                    yield f"data: {json.dumps({'type': 'wait', 'message': f'레이트리밋 - {wait}초 대기', 'seconds': wait})}\n\n"
                    await asyncio.sleep(wait)
                    attempts += 1
                    continue
                else:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'네트워크 오류: {str(e)}'})}\n\n"
                    await asyncio.sleep(2)
                    attempts += 1
                    if attempts > 5:
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
            await asyncio.sleep(0.9)
        
        # 완료 메시지
        yield f"data: {json.dumps({'type': 'complete', 'total': len(results)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


