import axios from "axios";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import * as cheerio from "cheerio";
import { format as csvFormat } from "@fast-csv/format";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const NAVER_URL_BASE =
  "https://search.naver.com/search.naver?ssc=tab.news.all&where=news&sm=tab_jum&query=2%EC%9D%BC";

function buildPageUrl(startIndex) {
  // Naver 뉴스 검색의 페이지네이션은 start=1,11,21... 형식
  const startParam = startIndex <= 1 ? 1 : startIndex;
  return `${NAVER_URL_BASE}&start=${startParam}`;
}

function findFirecrawlKeyFromCursorConfig() {
  try {
    const cursorConfigPath = path.join(
      process.env.USERPROFILE || process.env.HOME || "",
      ".cursor",
      "mcp.json"
    );
    const raw = fs.readFileSync(cursorConfigPath, "utf8");
    const json = JSON.parse(raw);
    const server = json?.mcpServers?.["mcp-server-firecrawl"];
    const args = server?.args || [];
    const keyIndex = args.findIndex((a) => a === "--key");
    if (keyIndex !== -1 && args[keyIndex + 1]) {
      return args[keyIndex + 1];
    }
  } catch {}
  return process.env.FIRECRAWL_KEY || "";
}

async function firecrawlScrape(url, apiKey) {
  const endpoint = "https://api.firecrawl.dev/v1/scrape";
  const { data } = await axios.post(
    endpoint,
    {
      url,
      formats: ["html"],
    },
    {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      timeout: 30000,
    }
  );
  return data;
}

async function firecrawlExtract(url, apiKey) {
  const endpoint = "https://api.firecrawl.dev/v1/extract";
  const { data } = await axios.post(
    endpoint,
    {
      url,
      // CSS 셀렉터로 제목과 링크를 함께 추출
      selectors: {
        titles: "a.news_tit",
        links: "a.news_tit@href",
      },
    },
    {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      timeout: 30000,
    }
  );
  return data;
}

function extractTitlesFromHtml(html) {
  const $ = cheerio.load(html);
  const items = [];
  // 1) 지정하신 클래스 조합의 요소에서 제목과 상위 앵커 href 추출
  const titleSelector = ".sds-comps-text.sds-comps-text-ellipsis.sds-comps-text-ellipsis-1.sds-comps-text-type-headline1";
  $(titleSelector).each((_, el) => {
    const title = $(el).text().trim();
    const anchor = $(el).closest("a");
    const link = anchor.attr("href")?.trim() || "";
    const isGarbage = /바로가기|이전|다음|옵션|Keep|저장|네이버|검색/i.test(title || "");
    const isValidLink = /^https?:\/\//.test(link) && !/search\.naver\.com/.test(link);
    const looksLikeNews = /(news\.|media\.|n\.news\.|article|read)/i.test(link);
    if (title && !isGarbage && isValidLink && looksLikeNews) items.push({ title, link });
  });
  // 2) 보조: 전통 셀렉터도 함께 확인
  if (!items.length) {
    $("a.news_tit").each((_, el) => {
      const title = $(el).attr("title")?.trim() || $(el).text().trim();
      const link = $(el).attr("href")?.trim() || "";
      const isGarbage = /바로가기|이전|다음|옵션|Keep|저장|네이버|검색/i.test(title || "");
      const isValidLink = /^https?:\/\//.test(link) && !/search\.naver\.com/.test(link);
      const looksLikeNews = /(news\.|media\.|n\.news\.|article|read)/i.test(link);
      if (title && !isGarbage && isValidLink && looksLikeNews) items.push({ title, link });
    });
  }
  if (items.length) return items;
  // 2) 정규식 폴백: <a class="news_tit" href="...">... or title="..."
  const anchorRegex = /<a\s+[^>]*class=["'][^"']*news_tit[^"']*["'][^>]*>/gi;
  const hrefRegex = /href=["']([^"']+)["']/i;
  const titleAttrRegex = /title=["']([^"']+)["']/i;
  let m;
  while ((m = anchorRegex.exec(html)) !== null) {
    const tag = m[0];
    const hrefMatch = tag.match(hrefRegex);
    const titleAttrMatch = tag.match(titleAttrRegex);
    const link = hrefMatch ? hrefMatch[1] : "";
    let title = titleAttrMatch ? titleAttrMatch[1] : "";
    if (!title) {
      // 태그 다음의 텍스트 노드 추출 시도
      const after = html.slice(m.index + tag.length, m.index + tag.length + 200);
      const textMatch = after.match(/([^<]{5,120})/);
      title = textMatch ? textMatch[1].trim() : "";
    }
    const isGarbage = /바로가기|이전|다음|옵션|Keep|저장|네이버|검색/i.test(title || "");
    const isValidLink = /^https?:\/\//.test(link) && !/search\.naver\.com/.test(link);
    const looksLikeNews = /(news\.|media\.|n\.news\.|article|read)/i.test(link);
    if (title && !isGarbage && isValidLink && looksLikeNews) items.push({ title, link });
  }
  return items;
}

async function writeCsv(rows, outPath) {
  await new Promise((resolve, reject) => {
    const ws = fs.createWriteStream(outPath, { encoding: "utf8" });
    const csvStream = csvFormat({ headers: true });
    csvStream.on("error", reject).on("finish", resolve);
    csvStream.pipe(ws);
    rows.forEach((r) => csvStream.write(r));
    csvStream.end();
  });
}

async function writeCsvAppend(rows, outPath) {
  const fileExists = fs.existsSync(outPath);
  await new Promise((resolve, reject) => {
    const ws = fs.createWriteStream(outPath, { encoding: "utf8", flags: "a" });
    const csvStream = csvFormat({ headers: !fileExists });
    csvStream.on("error", reject).on("finish", resolve);
    csvStream.pipe(ws);
    rows.forEach((r) => csvStream.write(r));
    csvStream.end();
  });
}

function delay(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

async function main() {
  const apiKey = findFirecrawlKeyFromCursorConfig();
  if (!apiKey) {
    console.error("Firecrawl API 키를 찾을 수 없습니다. 환경변수 FIRECRAWL_KEY 또는 .cursor/mcp.json을 확인하세요.");
    process.exit(1);
  }

  const all = [];
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const outFile = path.join(__dirname, "..", `naver_news_titles_${ts}.csv`);
  // 최대 3000건 목표, 네이버 페이지당 10건 가정 → 300페이지(여유 320)
  for (let start = 1; start <= 3200 && all.length < 3000; start += 10) {
    const pageUrl = buildPageUrl(start);
    let result;
    // 레이트리밋 대응: 최대 5회 재시도, 3초 대기
    for (let attempt = 1; attempt <= 5; attempt++) {
      try {
        result = await firecrawlScrape(pageUrl, apiKey);
        break;
      } catch (e) {
        const msg = (e?.response?.data?.error || e?.message || "").toString();
        if (/Rate limit exceeded/i.test(msg) && attempt < 5) {
          await delay(3000);
          continue;
        }
        throw e;
      }
    }
    const html = result?.data?.html || result?.html || "";
    if (!html) continue;
    const items = extractTitlesFromHtml(html);
    const newOnes = [];
    for (const it of items) {
      if (it.title && !all.find((x) => x.title === it.title)) {
        all.push(it);
        newOnes.push(it);
      }
      if (all.length >= 3000) break;
    }
    if (newOnes.length) {
      await writeCsvAppend(newOnes, outFile);
      console.log(`Page start=${start}: appended ${newOnes.length}, total=${all.length}`);
    }
    await delay(1200); // 페이지 간 속도 제한
  }
  if (!fs.existsSync(outFile)) {
    await writeCsv(all, outFile);
  }
  console.log(`Saved ${all.length} rows to ${outFile}`);
}

main().catch((err) => {
  console.error("오류 발생:", err?.response?.data || err?.message || err);
  process.exit(1);
});


