"use strict";
const byId = (id) => document.getElementById(id);
const form = byId("ask-form");
const question = byId("question");
let busy = false;
let lastQuestion = "";
let lastAnswer = "";
const publicPDFs = new Set([1, 2, 3, 4].map((part) => `crpc-1898-part-${part}.pdf`));

function announce(text) { byId("announcement").textContent = text; }
function setBusy(value) {
  busy = value;
  byId("submit").disabled = value;
  byId("new-question").disabled = value;
  byId("retry").disabled = value;
  question.readOnly = value;
  byId("answer-panel").setAttribute("aria-busy", String(value));
  byId("loading").hidden = !value;
  byId("submit").querySelector("span").textContent = value ? "Working…" : "Ask question";
}
function countCharacters() {
  const count = byId("character-count");
  count.hidden = question.value.length < 3000;
  count.textContent = `${question.value.length.toLocaleString()} / 4,000`;
}
function clearResponse() {
  byId("answer-text").replaceChildren();
  byId("citation-list").replaceChildren();
  for (const id of ["citations", "answer-disclaimer", "request-error", "copy-answer"]) byId(id).hidden = true;
  byId("response-label").textContent = "";
  byId("copy-answer").title = "Copy answer";
  lastAnswer = "";
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function renderAnswer(text, citations) {
  const target = byId("answer-text");
  const ids = new Set(citations.map((citation) => String(citation.id)));
  // Only text, emphasis and known citation links are rendered; no model HTML.
  for (const piece of text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g)) {
    const id = piece.match(/^\[(\d+)\]$/)?.[1];
    if (id && ids.has(id)) {
      const link = element("a", "inline-citation", id);
      link.href = `#source-${id}`;
      link.setAttribute("aria-label", `Read source ${id}`);
      link.addEventListener("click", () => { byId(`source-${id}`).open = true; });
      target.append(link);
    } else if (piece.startsWith("**") && piece.endsWith("**")) {
      target.append(element("strong", "", piece.slice(2, -2)));
    } else target.append(document.createTextNode(piece));
  }
}
function renderCitations(citations) {
  byId("citations").hidden = citations.length === 0;
  byId("citation-count").textContent = `${citations.length} ${citations.length === 1 ? "reference" : "references"}`;
  for (const citation of citations) {
    const details = element("details", "citation");
    details.id = `source-${citation.id}`;
    const summary = element("summary");
    summary.append(element("span", "citation-number", citation.id));
    const title = element("span", "citation-title", `Section ${citation.section}`);
    const part = citation.document.match(/part-(\d)\.pdf$/)?.[1];
    const pages = citation.pdf_pages.join(", ");
    title.append(element("span", "citation-meta", `${part ? `CrPC · Part ${part}` : citation.document} · PDF page ${pages} · Printed page ${citation.printed_pages.join(", ")}`));
    summary.append(title);
    details.append(summary);
    const content = element("div", "citation-body");
    content.append(element("blockquote", "", citation.excerpt));
    if (publicPDFs.has(citation.document)) {
      const link = element("a", "", "Open original PDF ↗");
      const page = Number(citation.pdf_pages[0]);
      link.href = `/sources/${encodeURIComponent(citation.document)}${Number.isInteger(page) && page > 0 ? `#page=${page}` : ""}`;
      link.target = "_blank";
      link.rel = "noopener";
      content.append(link);
    }
    details.append(content);
    byId("citation-list").append(details);
  }
}
const errorMessages = {
  retrieval_unavailable: "The source library is temporarily unavailable. Please try again shortly.",
  model_rate_limited: "The assistant has reached its request limit. Please wait a little before trying again.",
  model_timeout: "The answer took longer than expected. Please try again.",
  model_not_configured: "The assistant is not configured yet. Please contact the project owner.",
  model_auth_failed: "The assistant could not connect to its answer service. Please contact the project owner.",
  invalid_model_response: "The assistant could not produce a complete answer. Please try again.",
};
async function ask(text) {
  if (busy || !text.trim()) return;
  if (text.length > 4000) { question.reportValidity(); return; }
  lastQuestion = text.trim();
  clearResponse();
  document.body.classList.add("has-answer");
  byId("result").hidden = false;
  byId("asked-question").textContent = lastQuestion;
  setBusy(true);
  announce("Preparing your answer.");
  byId("result").scrollIntoView({ behavior: "auto", block: "start" });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);
  try {
    const response = await fetch("/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: lastQuestion }), signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok || data.status === "error") {
      throw new Error(errorMessages[data.error?.code] || "The assistant is temporarily unavailable. Please try again shortly.");
    }
    if (typeof data.answer !== "string" || !Array.isArray(data.citations)) throw new Error("An incomplete response was received. Please try again.");
    const citations = data.citations.filter((c) => Number.isInteger(c.id) && typeof c.document === "string" && typeof c.excerpt === "string" && Array.isArray(c.pdf_pages) && Array.isArray(c.printed_pages));
    renderAnswer(data.answer, citations);
    renderCitations(citations);
    byId("response-label").textContent = data.status === "clarify" ? "A little more context" : data.status === "insufficient_evidence" ? "Information not available" : citations.length ? "Archive references included" : "Conversation";
    if (data.disclaimer) { byId("answer-disclaimer").textContent = data.disclaimer; byId("answer-disclaimer").hidden = false; }
    lastAnswer = data.answer;
    byId("copy-answer").hidden = false;
    question.value = "";
    countCharacters();
    announce(data.status === "clarify" ? "A clarification is needed. Read the answer and include the missing details in your next question." : "Your answer is ready.");
    byId("answer-text").focus({ preventScroll: true });
  } catch (error) {
    byId("request-error").hidden = false;
    byId("error-message").textContent = error.name === "AbortError" ? "The request timed out. Please try again." : error instanceof TypeError || error instanceof SyntaxError ? "Could not reach the assistant. Check your connection and try again." : error.message;
    question.value = lastQuestion;
    countCharacters();
    announce("The request could not be completed.");
  } finally { clearTimeout(timer); setBusy(false); }
}
form.addEventListener("submit", (event) => { event.preventDefault(); ask(question.value); });
question.addEventListener("input", countCharacters);
question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy) form.requestSubmit();
  }
});
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => {
  question.value = button.dataset.question;
  countCharacters();
  question.focus();
}));
byId("new-question").addEventListener("click", () => {
  if (busy) return;
  clearResponse();
  document.body.classList.remove("has-answer");
  byId("result").hidden = true;
  question.value = "";
  lastQuestion = "";
  countCharacters();
  question.focus();
});
byId("retry").addEventListener("click", () => ask(lastQuestion));
byId("copy-answer").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(lastAnswer); announce("Answer copied."); byId("copy-answer").title = "Copied"; }
  catch { announce("Copy is unavailable. Select the answer text to copy it."); }
});
document.querySelectorAll("[data-dialog]").forEach((button) => button.addEventListener("click", () => byId(button.dataset.dialog).showModal()));
document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
  if (event.target === dialog) {
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  }
}));
