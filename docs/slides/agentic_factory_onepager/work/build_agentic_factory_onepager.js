"use strict";

const fs = require("fs");
const path = require("path");
const PptxGenJS = require("pptxgenjs");

const OUT_DIR = path.join(__dirname, "..");
const OUT_FILE = path.join(OUT_DIR, "agentic_trading_factory_onepager.pptx");

function addRoundedPanel(slide, opts) {
  slide.addShape("roundRect", {
    x: opts.x,
    y: opts.y,
    w: opts.w,
    h: opts.h,
    rectRadius: 0.08,
    fill: { color: opts.fill },
    line: { color: opts.line, width: opts.lineWidth || 1.2 },
    shadow: opts.shadow || undefined,
  });
}

function addArrow(slide, x1, y1, x2, y2, color, width) {
  slide.addShape("line", {
    x: x1,
    y: y1,
    w: x2 - x1,
    h: y2 - y1,
    line: {
      color,
      width: width || 2,
      beginArrowType: "none",
      endArrowType: "triangle",
    },
  });
}

function addBoxLabel(slide, text, x, y, w, h, opts = {}) {
  slide.addText(text, {
    x,
    y,
    w,
    h,
    margin: 0.08,
    fontFace: opts.fontFace || "Aptos",
    fontSize: opts.fontSize || 15,
    bold: !!opts.bold,
    color: opts.color || "0E1726",
    align: opts.align || "left",
    valign: opts.valign || "mid",
    breakLine: false,
    fit: "shrink",
  });
}

function addBulletList(slide, items, x, y, w, h, opts = {}) {
  const runs = [];
  items.forEach((item, index) => {
    runs.push({
      text: item,
      options: {
        bullet: { indent: 12 },
        breakLine: index !== items.length - 1,
      },
    });
  });
  slide.addText(runs, {
    x,
    y,
    w,
    h,
    margin: 0.06,
    fontFace: opts.fontFace || "Aptos",
    fontSize: opts.fontSize || 10.5,
    color: opts.color || "334155",
    valign: "top",
    paraSpaceAfterPt: 5,
    fit: "shrink",
  });
}

function ensureHelpersCopied() {
  const srcDir =
    "/Users/benjaminpommeraud/.codex/skills/slides/assets/pptxgenjs_helpers";
  const dstDir = path.join(__dirname, "pptxgenjs_helpers");
  if (fs.existsSync(dstDir)) return;
  fs.cpSync(srcDir, dstDir, { recursive: true });
}

function build() {
  ensureHelpersCopied();
  const {
    warnIfSlideHasOverlaps,
    warnIfSlideElementsOutOfBounds,
  } = require("./pptxgenjs_helpers/layout");

  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "OpenAI Codex";
  pptx.company = "AgenticTrading";
  pptx.subject = "Agentic trading factory overview";
  pptx.title = "How the Agentic Trading Factory Works";
  pptx.lang = "en-US";
  pptx.theme = {
    headFontFace: "Aptos Display",
    bodyFontFace: "Aptos",
    lang: "en-US",
  };

  const slide = pptx.addSlide();
  slide.background = { color: "F5F7FB" };

  slide.addShape("rect", {
    x: 0,
    y: 0,
    w: 13.333,
    h: 0.45,
    fill: { color: "102A43" },
    line: { color: "102A43", transparency: 100 },
  });

  slide.addText("How the Agentic Trading Factory Works", {
    x: 0.55,
    y: 0.55,
    w: 6.6,
    h: 0.45,
    fontFace: "Aptos Display",
    fontSize: 24,
    bold: true,
    color: "102A43",
    margin: 0,
  });

  slide.addText(
    "AgenticTrading is the research and control plane: it invents, tests, ranks, governs, and packages strategies before anything reaches the execution repo.",
    {
      x: 0.57,
      y: 1.0,
      w: 10.3,
      h: 0.4,
      fontFace: "Aptos",
      fontSize: 10.5,
      color: "486581",
      margin: 0,
      fit: "shrink",
    }
  );

  slide.addShape("roundRect", {
    x: 10.95,
    y: 0.58,
    w: 1.78,
    h: 0.44,
    rectRadius: 0.06,
    fill: { color: "D9F99D" },
    line: { color: "84CC16", width: 1.1 },
  });
  addBoxLabel(slide, "No live trading here", 11.12, 0.68, 1.45, 0.18, {
    fontSize: 9.5,
    bold: true,
    color: "365314",
    align: "center",
  });

  const laneY = 2.0;
  const laneH = 4.0;
  const boxH = 3.05;

  addRoundedPanel(slide, {
    x: 0.55,
    y: laneY,
    w: 2.1,
    h: boxH,
    fill: "E8F1FB",
    line: "7AA5D2",
  });
  addBoxLabel(slide, "1. Inputs", 0.73, 2.18, 1.55, 0.28, {
    fontSize: 18,
    bold: true,
    color: "16324F",
  });
  addBulletList(
    slide,
    [
      "Structured ideas from ideas.md",
      "Execution state snapshots and portfolio evidence",
      "Research datasets and feature stores",
      "Existing lineages, scorecards, and manifests",
    ],
    0.73,
    2.58,
    1.72,
    1.95
  );
  slide.addText("What the factory learns from", {
    x: 0.73,
    y: 4.64,
    w: 1.7,
    h: 0.22,
    fontFace: "Aptos",
    fontSize: 9,
    italic: true,
    color: "486581",
    margin: 0,
  });

  addRoundedPanel(slide, {
    x: 2.95,
    y: 1.82,
    w: 4.55,
    h: 3.65,
    fill: "FFF4D6",
    line: "E8B84F",
    lineWidth: 1.4,
  });
  addBoxLabel(slide, "2. Agentic Research Loop", 3.2, 2.03, 2.65, 0.28, {
    fontSize: 18,
    bold: true,
    color: "6B4E00",
  });
  slide.addText(
    "Cheap/local agents first for search, validation, deterministic prep. Stronger agents only for invention, review, and cross-file strategy work.",
    {
      x: 3.2,
      y: 2.35,
      w: 3.95,
      h: 0.38,
      fontFace: "Aptos",
      fontSize: 9.5,
      color: "7C5D00",
      margin: 0,
      fit: "shrink",
    }
  );

  const subW = 1.84;
  const subH = 0.84;
  const subX1 = 3.22;
  const subX2 = 5.22;
  const subY1 = 2.88;
  const subY2 = 3.95;

  [
    {
      x: subX1,
      y: subY1,
      title: "Invent + mutate",
      body: "Generate challengers, hypotheses, and family-specific variants",
    },
    {
      x: subX2,
      y: subY1,
      title: "Run experiments",
      body: "Goldfish workspaces, backtests, walkforward, stress, paper leagues",
    },
    {
      x: subX1,
      y: subY2,
      title: "Score + compare",
      body: "Rank ROI, robustness, novelty, maturity, and family fit",
    },
    {
      x: subX2,
      y: subY2,
      title: "Review actions",
      body: "Hold, retrain, rework, replace, or retire weak lineages",
    },
  ].forEach((item) => {
    addRoundedPanel(slide, {
      x: item.x,
      y: item.y,
      w: subW,
      h: subH,
      fill: "FFFDF5",
      line: "E8B84F",
      lineWidth: 1.0,
    });
    addBoxLabel(slide, item.title, item.x + 0.12, item.y + 0.11, 1.5, 0.18, {
      fontSize: 11.5,
      bold: true,
      color: "6B4E00",
    });
    slide.addText(item.body, {
      x: item.x + 0.12,
      y: item.y + 0.3,
      w: 1.56,
      h: 0.4,
      fontFace: "Aptos",
      fontSize: 8.6,
      color: "7C5D00",
      margin: 0,
      fit: "shrink",
    });
  });

  slide.addText("Lineage registry + memory preserve provenance across each cycle.", {
    x: 3.22,
    y: 4.99,
    w: 3.95,
    h: 0.2,
    fontFace: "Aptos",
    fontSize: 9,
    italic: true,
    color: "7C5D00",
    margin: 0,
  });

  addRoundedPanel(slide, {
    x: 7.82,
    y: laneY,
    w: 2.32,
    h: boxH,
    fill: "EAFBF3",
    line: "58B68B",
  });
  addBoxLabel(slide, "3. Governance", 8.0, 2.18, 1.6, 0.28, {
    fontSize: 18,
    bold: true,
    color: "134E39",
  });
  addBulletList(
    slide,
    [
      "Promotion rules and manifest checks",
      "Independent evidence and maturity gates",
      "Human signoff before any real-trading push",
      "Approved lineage status and artifact publication",
    ],
    8.0,
    2.58,
    1.88,
    1.95,
    { color: "245B45" }
  );
  slide.addText("Stops weak or ambiguous winners from slipping through", {
    x: 8.0,
    y: 4.64,
    w: 1.9,
    h: 0.22,
    fontFace: "Aptos",
    fontSize: 9,
    italic: true,
    color: "3A6B57",
    margin: 0,
    fit: "shrink",
  });

  addRoundedPanel(slide, {
    x: 10.46,
    y: laneY,
    w: 2.32,
    h: boxH,
    fill: "FDEDEC",
    line: "D97A72",
  });
  addBoxLabel(slide, "4. Publish + handoff", 10.68, 2.18, 1.72, 0.28, {
    fontSize: 17,
    bold: true,
    color: "7A1F1A",
  });
  addBulletList(
    slide,
    [
      "Approved manifests",
      "Packaged model artifacts",
      "Candidate context payloads",
      "Execution adapters consume explicit contracts only",
    ],
    10.68,
    2.58,
    1.82,
    1.95,
    { color: "7A2E28" }
  );
  slide.addText("Feeds the separate Arbitrage execution repo", {
    x: 10.68,
    y: 4.64,
    w: 1.78,
    h: 0.22,
    fontFace: "Aptos",
    fontSize: 9,
    italic: true,
    color: "9A3D36",
    margin: 0,
    fit: "shrink",
  });

  addArrow(slide, 2.66, 3.45, 2.95, 3.45, "3B82F6", 2.2);
  addArrow(slide, 7.52, 3.45, 7.82, 3.45, "F59E0B", 2.2);
  addArrow(slide, 10.15, 3.45, 10.46, 3.45, "10B981", 2.2);

  slide.addText("execution evidence", {
    x: 9.82,
    y: 5.42,
    w: 1.4,
    h: 0.16,
    fontFace: "Aptos",
    fontSize: 8.5,
    color: "64748B",
    margin: 0,
    align: "center",
  });
  addArrow(slide, 10.2, 5.22, 3.2, 5.22, "94A3B8", 1.7);

  slide.addShape("roundRect", {
    x: 0.75,
    y: 6.18,
    w: 12.02,
    h: 0.62,
    rectRadius: 0.05,
    fill: { color: "0F172A" },
    line: { color: "0F172A", width: 1 },
  });
  slide.addText(
    "Control plane inside this repo: ideas -> agentic research -> experiment orchestration -> ranking + maintenance -> promotion governance -> packaged outputs",
    {
      x: 1.0,
      y: 6.37,
      w: 11.5,
      h: 0.18,
      fontFace: "Aptos",
      fontSize: 11.2,
      color: "E2E8F0",
      align: "center",
      margin: 0,
      fit: "shrink",
    }
  );

  slide.addText(
    "Boundary rule: the execution repo remains separate. AgenticTrading reads state and starts runners only through explicit adapters and approved artifacts.",
    {
      x: 0.7,
      y: 6.95,
      w: 12.0,
      h: 0.2,
      fontFace: "Aptos",
      fontSize: 9.6,
      color: "475569",
      align: "center",
      margin: 0,
      fit: "shrink",
    }
  );

  warnIfSlideHasOverlaps(slide, pptx, {
    muteContainment: true,
    ignoreDecorativeShapes: true,
  });
  warnIfSlideElementsOutOfBounds(slide, pptx);

  fs.mkdirSync(OUT_DIR, { recursive: true });
  return pptx.writeFile({ fileName: OUT_FILE });
}

build().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
