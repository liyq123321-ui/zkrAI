import fs from "node:fs";
import { JSDOM } from "../frontend/aios-main/node_modules/jsdom/lib/api.js";

const path = new URL("../output/firstflight_mindmap_v15.html", import.meta.url);
const html = fs.readFileSync(path, "utf8");
const errors = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  url: path.href,
  beforeParse(window) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
    window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
    window.URL.createObjectURL = () => "blob:test";
    window.URL.revokeObjectURL = () => {};
  },
});
dom.window.addEventListener("error", (event) => errors.push(String(event.error || event.message)));

await new Promise((resolve) => setTimeout(resolve, 80));

const document = dom.window.document;
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

assert(document.querySelectorAll("#prdArticle [data-prd-heading]").length === 131, "expected 131 PRD headings");
assert(document.querySelectorAll("#prdArticle table").length === 46, "expected 46 PRD tables");
assert(document.querySelectorAll("#prdArticle img").length === 12, "expected 12 PRD images");
const decodedImages = [...document.querySelectorAll("#prdArticle img")].map((image) => {
  const encoded = image.src.split(",", 2)[1];
  return Buffer.from(encoded, "base64").length;
});
assert(decodedImages.every((size) => size > 100), "one or more embedded PRD images are invalid");
assert(document.querySelectorAll("#nodes .node").length > 0, "mind-map nodes did not render");

document.getElementById("btnArticle").click();
await new Promise((resolve) => setTimeout(resolve, 30));
assert(document.getElementById("articlePanel").classList.contains("open"), "topbar did not open the article panel");
assert(document.getElementById("articleLocation").textContent.includes("图工程编排"), `topbar did not locate the currently selected node: ${document.getElementById("articleLocation").textContent}`);

document.getElementById("articleClose").click();
assert(!document.getElementById("articlePanel").classList.contains("open"), "article panel did not close");

const organizationNode = [...document.querySelectorAll("#nodes .node")].find((node) => node.textContent.includes("组织身份与项目空间"));
assert(organizationNode, "organization node is missing");
organizationNode.click();
document.getElementById("pArticleOpen").click();
await new Promise((resolve) => setTimeout(resolve, 30));
assert(document.getElementById("articlePanel").classList.contains("open"), "node action did not open the article panel");
assert(document.getElementById("articleLocation").textContent.includes("FR-1.5 空间管理"), "node did not locate the mapped PRD section");

document.getElementById("articleSearchInput").value = "模型管理";
document.getElementById("articleSearchBtn").click();
assert(document.getElementById("articleLocation").textContent.includes("搜索命中"), "article search did not return a hit");
assert(errors.length === 0, `browser errors: ${errors.join(" | ")}`);

console.log(JSON.stringify({
  nodes: document.querySelectorAll("#nodes .node").length,
  headings: document.querySelectorAll("#prdArticle [data-prd-heading]").length,
  tables: document.querySelectorAll("#prdArticle table").length,
  images: document.querySelectorAll("#prdArticle img").length,
  decodedImageBytes: decodedImages.reduce((sum, size) => sum + size, 0),
  mappedLocation: document.getElementById("articleLocation").textContent,
}, null, 2));

dom.window.close();
