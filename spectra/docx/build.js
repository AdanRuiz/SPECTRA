// SPECTRA: doc.json -> .docx base. Las citas van como marcadores §§CITn§§ que spectra/docx_export.py
// convierte en campos de Zotero. Uso: node build.js <doc.json> <salida.docx>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, HeadingLevel, LevelFormat, BorderStyle, Footer, PageNumber,
} = require("docx");

const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const FONT = "Cambria";
const CONTENT_W = 9026; // A4 con márgenes de 1440 DXA

function runs(rs, extra = {}) {
  return rs.map((r) => {
    if (r.cite !== undefined) return new TextRun({ text: `§§CIT${r.cite}§§`, ...extra });
    return new TextRun({
      text: r.text, bold: r.bold || extra.bold, italics: r.italics || extra.italics,
      subScript: r.subScript, superScript: r.superScript, font: r.font || extra.font, size: extra.size,
    });
  });
}

const children = [];
let listInstance = 0;
for (const b of data.blocks) {
  switch (b.t) {
    case "title":
      children.push(new Paragraph({ heading: HeadingLevel.TITLE, alignment: AlignmentType.CENTER, children: runs(b.runs) }));
      break;
    case "h1":
      children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(b.runs) }));
      break;
    case "h2":
      children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: runs(b.runs) }));
      break;
    case "p":
      children.push(new Paragraph({ alignment: AlignmentType.JUSTIFIED, children: runs(b.runs) }));
      break;
    case "caption":
      children.push(new Paragraph({ style: "Leyenda", children: runs(b.runs) }));
      break;
    case "img": {
      const maxW = 600; // px (≈ 15.9 cm)
      const w = Math.min(maxW, b.w), h = Math.round((b.h * w) / b.w);
      children.push(new Paragraph({
        alignment: AlignmentType.CENTER, keepNext: true,
        children: [new ImageRun({ type: "png", data: fs.readFileSync(b.path), transformation: { width: w, height: h },
          altText: { title: "Figura", description: "Figura del artículo", name: path.basename(b.path) } })],
      }));
      break;
    }
    case "list":
      listInstance += 1;
      for (const it of b.items) {
        children.push(new Paragraph({
          alignment: AlignmentType.JUSTIFIED,
          numbering: { reference: b.ordered ? "numerada" : "vinetas", level: 0, instance: listInstance },
          children: runs(it),
        }));
      }
      break;
    case "table": {
      const n = b.head.length;
      const weights = b.lens.map((x) => Math.max(8, Math.min(60, x)));
      const tot = weights.reduce((a, c) => a + c, 0);
      const widths = weights.map((x) => Math.floor((CONTENT_W * x) / tot));
      widths[n - 1] += CONTENT_W - widths.reduce((a, c) => a + c, 0);
      const border = { style: BorderStyle.SINGLE, size: 4, color: "999999" };
      const borders = { top: border, bottom: border, left: border, right: border };
      const cell = (rs, i, head) => new TableCell({
        borders, width: { size: widths[i], type: WidthType.DXA },
        margins: { top: 60, bottom: 60, left: 90, right: 90 },
        shading: head ? { fill: "EDEDED", type: ShadingType.CLEAR, color: "auto" } : undefined,
        children: [new Paragraph({ children: runs(rs, { size: 17, bold: head }) })],
      });
      children.push(new Table({
        width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths,
        rows: [new TableRow({ tableHeader: true, children: b.head.map((c, i) => cell(c, i, true)) }),
          ...b.rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, i, false)) }))],
      }));
      break;
    }
    case "bibliography":
      b.refs.forEach((r, i) => {
        const rr = runs(r);
        if (i === 0) rr.unshift(new TextRun("§§BIBL_BEGIN§§"));
        if (i === b.refs.length - 1) rr.push(new TextRun("§§BIBL_END§§"));
        children.push(new Paragraph({ style: "Bibliografia", children: rr }));
      });
      break;
  }
}

const doc = new Document({
  creator: "SPECTRA",
  title: data.title || "SPECTRA",
  customProperties: data.prefs.map((v, i) => ({ name: `ZOTERO_PREF_${i + 1}`, value: v })),
  styles: {
    default: { document: { run: { font: FONT, size: 22 }, paragraph: { spacing: { after: 120, line: 276 } } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, font: FONT }, paragraph: { spacing: { after: 240 }, alignment: AlignmentType.CENTER } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: FONT, color: "1F3864" }, paragraph: { spacing: { before: 300, after: 120 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: FONT, color: "2F5496" }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1, keepNext: true } },
      { id: "Leyenda", name: "Leyenda", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 19, italics: true }, paragraph: { spacing: { before: 80, after: 200 } } },
      { id: "Bibliografia", name: "Bibliografía", basedOn: "Normal", quickFormat: true,
        run: { size: 21 }, paragraph: { indent: { left: 720, hanging: 720 }, spacing: { after: 100 } } },
    ],
  },
  numbering: {
    config: [
      { reference: "vinetas", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numerada", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: [PageNumber.CURRENT], size: 18 })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(process.argv[3], buf);
  console.log(process.argv[3], buf.length, "bytes");
});
