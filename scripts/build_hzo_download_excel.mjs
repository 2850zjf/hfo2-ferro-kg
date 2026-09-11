import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = path.resolve(import.meta.dirname, "..");
const inputPath = process.argv[2] || path.join(projectRoot, "data/literature/hzo_high_quality_download_list.json");
const outputDir = process.argv[3] || path.join(projectRoot, "outputs/hzo_literature_curation");
const inputBase = path.basename(inputPath, path.extname(inputPath));
const outputFileName = inputBase.includes("missing")
  ? "hzo_missing_high_quality_strong_relevance_download_list.xlsx"
  : "hzo_high_quality_strong_relevance_download_list.xlsx";
const outputPath = path.join(outputDir, outputFileName);

const rows = JSON.parse(await fs.readFile(inputPath, "utf8"));

const palette = {
  ink: "#2F3441",
  muted: "#687080",
  line: "#D7DCEA",
  paleBlue: "#D3E8F1",
  paleLilac: "#DED8ED",
  paleMint: "#E9F5F1",
  paleRose: "#F5E0E3",
  paleGray: "#F5F7FB",
  white: "#FFFFFF",
};

const workbook = Workbook.create();

function colName(index) {
  let name = "";
  let n = index + 1;
  while (n > 0) {
    const rem = (n - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

function applyWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRange(`${colName(index)}:${colName(index)}`).format.columnWidthPx = width;
  });
}

function styleSheet(sheet, colCount, rowCount) {
  const endCol = colName(colCount - 1);
  sheet.getRange(`A1:${endCol}1`).format.font = { bold: true, size: 16, color: palette.ink };
  sheet.getRange(`A2:${endCol}2`).format.font = { color: palette.muted, size: 10 };
  sheet.getRange(`A4:${endCol}4`).format.fill = palette.paleBlue;
  sheet.getRange(`A4:${endCol}4`).format.font = { bold: true, color: palette.ink };
  sheet.getRange(`A4:${endCol}4`).format.wrapText = true;
  sheet.getRange(`A4:${endCol}${Math.max(4, rowCount)}`).format.borders = {
    preset: "all",
    style: "thin",
    color: palette.line,
  };
  sheet.getRange(`A5:${endCol}${Math.max(5, rowCount)}`).format.wrapText = true;
  sheet.getRange(`A5:${endCol}${Math.max(5, rowCount)}`).format.font = { size: 9, color: palette.ink };
  sheet.getRange(`A1:${endCol}${Math.max(5, rowCount)}`).format.verticalAlignment = "top";
  sheet.getRange(`A4:${endCol}4`).format.horizontalAlignment = "center";
  sheet.getRange(`A1:${endCol}${Math.max(5, rowCount)}`).format.rowHeightPx = 28;
  sheet.getRange(`A1:${endCol}1`).format.rowHeightPx = 34;
  sheet.getRange(`A2:${endCol}2`).format.rowHeightPx = 40;
}

function writeTableSheet({ name, title, subtitle, columns, data, widths }) {
  const sheet = workbook.worksheets.add(name);
  const headers = columns.map((column) => column.label);
  const matrix = data.map((row) => columns.map((column) => row[column.key] ?? ""));
  const colCount = columns.length;
  const rowCount = 4 + Math.max(1, matrix.length);
  sheet.getRange(`A1:${colName(colCount - 1)}1`).values = [[title, ...Array(colCount - 1).fill("")]];
  sheet.getRange(`A2:${colName(colCount - 1)}2`).values = [[subtitle, ...Array(colCount - 1).fill("")]];
  sheet.getRange(`A4:${colName(colCount - 1)}4`).values = [headers];
  if (matrix.length) {
    sheet.getRange(`A5:${colName(colCount - 1)}${rowCount}`).values = matrix;
  }
  applyWidths(sheet, widths);
  styleSheet(sheet, colCount, rowCount);
  return sheet;
}

function countBy(values, key) {
  const counts = new Map();
  for (const row of values) {
    const value = row[key] || "未标注";
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
}

const firstBatch = rows.filter((row) => String(row.download_batch || "").startsWith("第一批"));
const toDownload = rows.filter((row) => row.local_library_status === "建议下载/补库");

const mainColumns = [
  { key: "priority", label: "优先级" },
  { key: "download_batch", label: "下载批次" },
  { key: "theme", label: "主题" },
  { key: "title", label: "题名" },
  { key: "year", label: "年份" },
  { key: "venue", label: "期刊/会议" },
  { key: "doi", label: "DOI" },
  { key: "cited_by_count", label: "引用数参考" },
  { key: "local_library_status", label: "本地状态" },
  { key: "recommended_action", label: "建议动作" },
  { key: "download_url", label: "下载/入口链接" },
  { key: "kg_value", label: "对KG/benchmark的价值" },
  { key: "why_include", label: "为什么纳入" },
];

writeTableSheet({
  name: "第一批必下",
  title: "HfO2/HZO 强相关第一批下载清单",
  subtitle: "优先覆盖奠基论文、综述路线图、HZO工艺-结构-性能、界面/氧空位、相结构、计算机制和少量代表器件。",
  columns: mainColumns,
  data: firstBatch,
  widths: [80, 170, 150, 420, 64, 180, 190, 86, 150, 190, 310, 260, 380],
});

writeTableSheet({
  name: "建议下载补库",
  title: "建议下载或补库的强相关文献",
  subtitle: "已结合本地 DOI/题名做去重；本表优先显示本地未确认已有的文章。引用数仅供参考，强相关性优先。",
  columns: mainColumns,
  data: toDownload,
  widths: [80, 170, 150, 420, 64, 180, 190, 86, 150, 190, 310, 260, 380],
});

writeTableSheet({
  name: "全候选",
  title: "HfO2/HZO 高质量强相关候选池",
  subtitle: "排序综合强相关度、质量、经典/标志性属性、本地已有状态；C类弱相关已过滤。",
  columns: [
    ...mainColumns,
    { key: "strong_relevance_score", label: "强相关分" },
    { key: "quality_score", label: "质量分" },
    { key: "combined_score", label: "综合分" },
    { key: "oa_status", label: "OA状态" },
    { key: "queries_hit", label: "命中检索式" },
  ],
  data: rows,
  widths: [80, 170, 150, 420, 64, 180, 190, 86, 150, 190, 310, 260, 380, 86, 78, 78, 78, 360],
});

const summarySheet = workbook.worksheets.add("说明");
const summaryRows = [
  ["用途", "用于决定下一批要手动下载/补库的 HfO2/HZO 铁电强相关论文。"],
  ["筛选原则", "强相关优先，高被引只是参考；重点支持知识图谱、Pr/2Pr benchmark、物理约束和计算闭环。"],
  ["候选总数", rows.length],
  ["第一批必下", firstBatch.length],
  ["明确缺失/建议下载", toDownload.length],
  ["数据源", "OpenAlex Works API + 本地 FerroKG DOI/题名去重"],
  ["生成时间", new Date().toISOString().slice(0, 10)],
];
summarySheet.getRange("A1:D1").values = [["HfO2-FerroKG 文献下载决策表", "", "", ""]];
summarySheet.getRange("A3:B9").values = summaryRows;
summarySheet.getRange("A11:B11").values = [["优先级", "含义"]];
summarySheet.getRange("A12:B14").values = [
  ["S 必下", "奠基/标志性或强相关且高质量，优先进入论文原型文献库。"],
  ["A 强推荐", "对本体、benchmark、物理机制或计算闭环有直接价值。"],
  ["B 补充", "专题扩展，用于补足某个机制或器件场景。"],
];
summarySheet.getRange("A1:D1").format.font = { bold: true, size: 18, color: palette.ink };
summarySheet.getRange("A3:B14").format.borders = { preset: "all", style: "thin", color: palette.line };
summarySheet.getRange("A3:A14").format.fill = palette.paleLilac;
summarySheet.getRange("A3:B14").format.wrapText = true;
applyWidths(summarySheet, [160, 620, 140, 140]);

const themeRows = [
  ["主题", "篇数"],
  ...countBy(rows, "theme").map((item) => [item.name, item.count]),
  [],
  ["优先级", "篇数"],
  ...countBy(rows, "priority").map((item) => [item.name, item.count]),
  [],
  ["本地状态", "篇数"],
  ...countBy(rows, "local_library_status").map((item) => [item.name, item.count]),
];
const themeSheet = workbook.worksheets.add("主题统计");
themeSheet.getRange("A1:D1").values = [["主题覆盖与本地缺口", "", "", ""]];
themeSheet.getRange(`A3:B${themeRows.length + 2}`).values = themeRows;
themeSheet.getRange(`A3:B${themeRows.length + 2}`).format.borders = { preset: "all", style: "thin", color: palette.line };
themeSheet.getRange("A1:D1").format.font = { bold: true, size: 16, color: palette.ink };
themeSheet.getRange(`A3:B${themeRows.length + 2}`).format.wrapText = true;
themeSheet.getRange("A3:B3").format.fill = palette.paleBlue;
applyWidths(themeSheet, [260, 90, 160, 160]);

const querySheet = workbook.worksheets.add("检索策略");
const queries = [
  "ferroelectric hafnium oxide HfO2 HZO",
  "Hf0.5Zr0.5O2 ferroelectric thin film remanent polarization",
  "hafnium zirconium oxide ferroelectric annealing electrode oxygen vacancy",
  "HfO2 ferroelectric orthorhombic phase Pca21 phase stability",
  "ferroelectric HfO2 density functional theory oxygen vacancy phase transition",
  "hafnia based ferroelectrics review HfO2 HZO",
  "HfO2 ferroelectric FeFET HZO transistor memory",
  "HfO2 ferroelectric tunnel junction HZO FTJ",
  "wake up fatigue retention HfO2 HZO ferroelectric",
  "epitaxial Hf0.5Zr0.5O2 ferroelectric rhombohedral orthorhombic",
  "doped hafnium oxide ferroelectric silicon yttrium aluminum gadolinium",
  "machine learning ferroelectric hafnium oxide HZO",
  "classic seed searches: Böscke 2011, oxygen migration, HZO rhombohedral, Y-doped HfO2, roadmap/reviews",
];
querySheet.getRange("A1:C1").values = [["检索策略与筛选口径", "", ""]];
querySheet.getRange(`A3:A${queries.length + 2}`).values = queries.map((query) => [query]);
querySheet.getRange("A16:B20").values = [
  ["强相关条件", "必须同时命中 HfO2/HZO/hafnia 相关材料词和 ferroelectric/polarization/phase/device/property 证据。"],
  ["质量参考", "期刊/会议、引用数、是否综述/标志性论文、是否开放获取；不以引用数单独排序。"],
  ["本地状态", "通过本地 papers 表 DOI 和题名粗匹配判断，下载前仍建议核对 PDF 质量。"],
  ["弱相关处理", "BaTiO3/PZT/BiFeO3/聚合物等非HfO2主线自动降权或过滤。"],
  ["下一步", "下载后放入 data/raw_pdfs，再运行 manifest/parse/chunk/extraction 流程。"],
];
querySheet.getRange("A1:C1").format.font = { bold: true, size: 16, color: palette.ink };
querySheet.getRange(`A3:A${queries.length + 2}`).format.borders = { preset: "all", style: "thin", color: palette.line };
querySheet.getRange("A16:B20").format.borders = { preset: "all", style: "thin", color: palette.line };
querySheet.getRange("A16:A20").format.fill = palette.paleMint;
querySheet.getRange("A3:B20").format.wrapText = true;
applyWidths(querySheet, [620, 520, 120]);

const check = await workbook.inspect({
  kind: "table",
  range: "第一批必下!A1:M12",
  include: "values",
  tableMaxRows: 12,
  tableMaxCols: 13,
});
console.log(check.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "formula error scan",
});
console.log(errors.ndjson || "no formula errors");

await workbook.render({ sheetName: "第一批必下", range: "A1:M18", scale: 1 });
await workbook.render({ sheetName: "说明", range: "A1:B14", scale: 1 });

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, rows: rows.length, firstBatch: firstBatch.length, toDownload: toDownload.length }, null, 2));
