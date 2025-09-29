import axios from "axios";
import * as fs from "fs";
import * as path from "path";
import cheerio from "cheerio";

async function main() {
  const targetUrl = "https://search.naver.com/search.naver?ssc=tab.news.all&where=news&sm=tab_jum&query=2%EC%9D%BC";
  const outDir = path.join(process.cwd(), "data");
  const outFile = path.join(outDir, "naver_2일_news_titles.csv");

  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  const res = await axios.get(targetUrl, { headers: { "User-Agent": "Mozilla/5.0" } });
  const $ = cheerio.load(res.data);

  // 네이버 뉴스 목록에서 제목 후보들 추출
  const titles = new Set<string>();
  $("a.news_tit, a.title, a.news_tit._sp_each_title").each((_, el) => {
    const t = $(el).attr("title") || $(el).text();
    const trimmed = (t || "").trim();
    if (trimmed) titles.add(trimmed.replace(/\s+/g, " "));
  });

  // 대체 셀렉터 (레이아웃 변화 대응)
  $("div.news_area a, div.news_tit a").each((_, el) => {
    const t = $(el).attr("title") || $(el).text();
    const trimmed = (t || "").trim();
    if (trimmed && trimmed.length > 4) titles.add(trimmed.replace(/\s+/g, " "));
  });

  const rows = ["title"]; // CSV 헤더
  for (const t of titles) {
    // CSV 이스케이프
    const safe = '"' + t.replace(/"/g, '""') + '"';
    rows.push(safe);
  }

  fs.writeFileSync(outFile, rows.join("\n"), "utf8");
  console.log(`Saved ${titles.size} titles to ${outFile}`);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
