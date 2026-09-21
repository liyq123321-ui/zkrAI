import fs from "node:fs";
import { JSDOM } from "../frontend/aios-main/node_modules/jsdom/lib/api.js";

const path = new URL("../output/firstflight_mindmap_v16.html", import.meta.url);
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

await new Promise((resolve) => setTimeout(resolve, 100));

const document = dom.window.document;
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

assert(document.title.includes("v16"), "page title was not upgraded to v16");
assert(document.querySelectorAll("#prdArticle [data-prd-heading]").length === 136, "expected 136 latest-PRD headings");
assert(document.querySelectorAll("#prdArticle table").length === 45, "expected 45 latest-PRD tables");
assert(document.querySelectorAll("#prdArticle img").length === 12, "expected 12 latest-PRD images");
assert(document.getElementById("prdArticle").textContent.includes("Harness 工程"), "latest Harness wording is missing");
assert(document.getElementById("prdArticle").textContent.includes("Agent 定义与配置自扫"), "latest Agent config scan section is missing");
assert(document.getElementById("prdArticle").textContent.includes("locked: boolean"), "latest immutable-checklist schema is missing");

const decodedImages = [...document.querySelectorAll("#prdArticle img")].map((image) => {
  const encoded = image.src.split(",", 2)[1];
  return Buffer.from(encoded, "base64").length;
});
assert(decodedImages.every((size) => size > 100), "one or more embedded PRD images are invalid");

const allNodeCount = dom.window.eval("Object.keys(nodeMap).length");
const staleNodeLabels = dom.window.eval(`Object.values(nodeMap)
  .filter((node) => /图工程|豆包式布局/.test(String(node.title || "") + " " + String(node.desc || "")))
  .map((node) => ({ id: node.id, title: node.title }))`);
assert(allNodeCount > 100, "the full mind-map data was not indexed");
assert(!staleNodeLabels.some((node) => node.id === "domain-planning" || node.id === "graph-definition"), "a primary planning node retained stale Graph Engineering wording");

const nodes = [...document.querySelectorAll("#nodes .node")];
const jumpButtons = [...document.querySelectorAll("#nodes .node-doc-jump")];
assert(nodes.length > 0, "mind-map nodes did not render");
assert(jumpButtons.length === nodes.length, "not every visible node received a direct PRD button");

const planningNode = nodes.find((node) => node.textContent.includes("Harness 工程、执行图与任务编排"));
assert(planningNode, "latest Harness planning group is missing");
planningNode.querySelector(".node-doc-jump").click();
await new Promise((resolve) => setTimeout(resolve, 30));
assert(document.getElementById("articlePanel").classList.contains("open"), "direct node button did not open the article panel");
assert(document.body.classList.contains("prd-article-open"), "open article state was not recorded on the page");
assert(document.getElementById("articleLocation").textContent.includes("执行图编排"), `planning node mapped to the wrong section: ${document.getElementById("articleLocation").textContent}`);

const qualityNode = nodes.find((node) => node.textContent.includes("评审、问题、变更与验收"));
assert(qualityNode, "quality group is missing");
qualityNode.querySelector(".node-doc-jump").click();
await new Promise((resolve) => setTimeout(resolve, 30));
assert(document.getElementById("articlePanel").classList.contains("open"), "article panel closed while switching nodes");
assert(document.getElementById("articleLocation").textContent.includes("审查与确认机制"), `quality node did not retarget the open panel: ${document.getElementById("articleLocation").textContent}`);

document.getElementById("articleSearchInput").value = "Agent 定义与配置自扫";
document.getElementById("articleSearchBtn").click();
assert(document.getElementById("articleLocation").textContent.includes("搜索命中"), "latest article search did not return a hit");

document.getElementById("articleClose").click();
assert(!document.getElementById("articlePanel").classList.contains("open"), "article panel did not close");
assert(!document.body.classList.contains("prd-article-open"), "page retained stale open state after closing the article");

dom.window.eval("Object.values(nodeMap).forEach((node) => { node._collapsed = false; }); render();");
await new Promise((resolve) => setTimeout(resolve, 30));
const fullyExpandedNodes = document.querySelectorAll("#nodes .node").length;
const fullyExpandedButtons = document.querySelectorAll("#nodes .node-doc-jump").length;
assert(fullyExpandedNodes === allNodeCount, "the fully expanded map did not render every indexed node");
assert(fullyExpandedButtons === fullyExpandedNodes, "one or more expanded nodes are missing a direct PRD button");
assert(errors.length === 0, `browser errors: ${errors.join(" | ")}`);

console.log(JSON.stringify({
  nodes: nodes.length,
  allNodeCount,
  directButtons: jumpButtons.length,
  fullyExpandedNodes,
  fullyExpandedButtons,
  headings: document.querySelectorAll("#prdArticle [data-prd-heading]").length,
  tables: document.querySelectorAll("#prdArticle table").length,
  images: document.querySelectorAll("#prdArticle img").length,
  decodedImageBytes: decodedImages.reduce((sum, size) => sum + size, 0),
  finalLocation: document.getElementById("articleLocation").textContent,
  staleNodeLabels: staleNodeLabels.slice(0, 12),
}, null, 2));

dom.window.close();
