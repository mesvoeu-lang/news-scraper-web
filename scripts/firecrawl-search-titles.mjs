import axios from "axios";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import * as cheerio from "cheerio";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function getFirecrawlKey() {
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
    if (keyIndex !== -1 && args[keyIndex + 1]) return args[keyIndex + 1];
  } catch {}
  return process.env.FIRECRAWL_KEY || "";
}

function buildNaverUrl(query, startIndex) {
  const q = encodeURIComponent(query);
  const startParam = startIndex <= 1 ? 1 : startIndex;
  return `https://search.naver.com/search.naver?ssc=tab.news.all&where=news&sm=tab_jum&query=${q}&start=${startParam}`;
}

async function firecrawlScrape(url, apiKey) {
  const endpoint = "https://api.firecrawl.dev/v1/scrape";
  const { data } = await axios.post(
    endpoint,
    { url, formats: ["html"] },
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

function extractTitles(html) {
  const $ = cheerio.load(html);
  const titles = [];
  const titleSelector = ".sds-comps-text.sds-comps-text-ellipsis.sds-comps-text-ellipsis-1.sds-comps-text-type-headline1";
  $(titleSelector).each((_, el) => {
    const t = $(el).text().trim();
    if (t && !/바로가기|옵션|더보기|네이버|검색/i.test(t)) titles.push(t);
  });
  if (titles.length) return titles;
  $("a.news_tit").each((_, el) => {
    const t = $(el).attr("title")?.trim() || $(el).text().trim();
    if (t && !/바로가기|옵션|더보기|네이버|검색/i.test(t)) titles.push(t);
  });
  return titles;
}

async function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function main() {
  const queryArgIndex = process.argv.indexOf("--query");
  const query = queryArgIndex !== -1 ? process.argv[queryArgIndex + 1] : "";
  const limitArgIndex = process.argv.indexOf("--limit");
  const limit = limitArgIndex !== -1 ? Math.min(100, parseInt(process.argv[limitArgIndex + 1] || "100", 10)) : 100;
  if (!query) {
    console.error("--query <검색어> 를 지정하세요.");
    process.exit(1);
  }

  const apiKey = getFirecrawlKey();
  if (!apiKey) {
    console.error("Firecrawl API 키가 필요합니다. .cursor/mcp.json 또는 FIRECRAWL_KEY를 확인하세요.");
    process.exit(1);
  }

  const collected = [];
  for (let start = 1; start <= 300 && collected.length < limit; start += 10) {
    const url = buildNaverUrl(query, start);
    let result;
    for (let attempt = 1; attempt <= 5; attempt++) {
      try {
        result = await firecrawlScrape(url, apiKey);
        break;
      } catch (e) {
        const msg = (e?.response?.data?.error || e?.message || "").toString();
        if (/Rate limit exceeded/i.test(msg) && attempt < 5) {
          await delay(2500);
          continue;
        }
        throw e;
      }
    }
    const html = result?.data?.html || result?.html || "";
    if (!html) continue;
    const titles = extractTitles(html);
    for (const t of titles) {
      if (!collected.includes(t)) collected.push(t);
      if (collected.length >= limit) break;
    }
    await delay(900);
  }

  // 출력: 제목만
  collected.slice(0, limit).forEach((t) => console.log(t));
}

main().catch((err) => {
  console.error("오류:", err?.response?.data || err?.message || err);
  process.exit(1);
});


