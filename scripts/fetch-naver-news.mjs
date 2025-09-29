import axios from "axios";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import * as cheerio from "cheerio";
import { format as csvFormat } from "@fast-csv/format";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function fetchHtml(url) {
  const response = await axios.get(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
      "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
      Referer: "https://search.naver.com/",
    },
    // Naver may redirect based on language/region; allow redirects
    maxRedirects: 5,
    timeout: 20000,
    responseType: "text",
    decompress: true,
    validateStatus: (s) => s >= 200 && s < 400,
  });
  return response.data;
}

function extractNews($) {
  // Naver 뉴스 검색 결과에서 제목은 보통 a.news_tit 요소에 있음
  const items = [];
  $("a.news_tit").each((_, el) => {
    const title = $(el).attr("title")?.trim() || $(el).text().trim();
    const link = $(el).attr("href")?.trim() || "";
    if (title) {
      items.push({ title, link });
    }
  });
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

async function main() {
  const url =
    "https://search.naver.com/search.naver?ssc=tab.news.all&where=news&sm=tab_jum&query=2%EC%9D%BC";
  const html = await fetchHtml(url);
  // 디버그: 실제 응답 HTML 저장
  const debugPath = path.join(__dirname, "..", "debug_naver_news.html");
  try {
    fs.writeFileSync(debugPath, html, { encoding: "utf8" });
    console.log(`Saved debug HTML to ${debugPath}`);
  } catch {}
  const $ = cheerio.load(html);
  const items = extractNews($);

  if (!items.length) {
    console.error("검색 결과에서 뉴스 제목을 찾지 못했습니다. 셀렉터 변경이 필요할 수 있습니다.");
  }

  const outFile = path.join(__dirname, "..", "naver_news_titles.csv");
  await writeCsv(items, outFile);
  console.log(`Saved ${items.length} rows to ${outFile}`);
}

main().catch((err) => {
  console.error("오류 발생:", err?.message || err);
  process.exit(1);
});


