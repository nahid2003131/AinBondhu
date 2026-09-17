"use strict";

const byId = (id) => document.getElementById(id);
const form = byId("ask-form");
const question = byId("question");
const thread = byId("conversation-thread");
const conversationList = byId("conversation-list");

const CLIENT_STORAGE_KEY = "crpc-client-id";
const ACTIVE_CONVERSATION_KEY = "crpc-active-conversation";
const publicPDFs = new Set([1, 2, 3, 4].map((part) => `crpc-1898-part-${part}.pdf`));

let busy = false;
let activeConversationId = localStorage.getItem(ACTIVE_CONVERSATION_KEY) || "";
let conversations = [];
let activeController = null;
let cancelledByUser = false;

const errorMessages = {
  retrieval_unavailable: "The source library is temporarily unavailable. Please try again shortly.",
  model_rate_limited: "The assistant has reached its request limit. Please wait a little before trying again.",
  model_timeout: "The answer took longer than expected. Please try again.",
  model_not_configured: "The assistant is not configured yet. Please contact the project owner.",
  model_auth_failed: "The assistant could not connect to its answer service. Please contact the project owner.",
  invalid_model_response: "The assistant could not produce a complete answer. Please try again.",
  conversation_storage_unavailable: "Saved conversations are temporarily unavailable. Please try again shortly.",
  conversation_not_found: "That saved conversation could not be found. Start a new question and try again.",
  invalid_conversation: "That saved conversation could not be loaded.",
};

function makeUuid() {
  if (crypto.randomUUID) return crypto.randomUUID();

  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

function clientId() {
  let value = localStorage.getItem(CLIENT_STORAGE_KEY);
  if (!value) {
    value = makeUuid();
    localStorage.setItem(CLIENT_STORAGE_KEY, value);
  }
  return value;
}

function apiHeaders(includeJson = false) {
  const headers = { "X-Client-ID": clientId() };
  if (includeJson) headers["Content-Type"] = "application/json";
  return headers;
}

function announce(text) {
  byId("announcement").textContent = text;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${name}`);
  svg.append(use);
  return svg;
}

function setBusy(value) {
  busy = value;
  byId("submit").disabled = value;
  byId("new-question").disabled = value;
  question.readOnly = value;
  byId("submit").querySelector("span").textContent = value ? "Working…" : "Ask question";
}

function countCharacters() {
  const count = byId("character-count");
  count.hidden = question.value.length < 3000;
  count.textContent = `${question.value.length.toLocaleString()} / 4,000`;
}

function autoResizeQuestion() {
  question.style.height = "auto";
  const next = Math.min(300, Math.max(111, question.scrollHeight));
  question.style.height = `${next}px`;
}

function conversationTitle(text) {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length <= 64 ? clean : `${clean.slice(0, 61).trimEnd()}…`;
}

function setActiveConversation(id, title = "Ask a question") {
  activeConversationId = id || "";

  if (activeConversationId) {
    localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeConversationId);
  } else {
    localStorage.removeItem(ACTIVE_CONVERSATION_KEY);
  }

  byId("conversation-title").textContent = title;
  document.querySelectorAll(".conversation-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.conversationId === activeConversationId);
  });
}

function showHome() {
  setActiveConversation("");
  thread.replaceChildren();
  thread.hidden = true;
  document.body.classList.remove("has-answer");
  byId("result").hidden = true;
  question.value = "";
  countCharacters();
  autoResizeQuestion();
  question.focus();
}

function showThread() {
  thread.hidden = false;
  byId("result").hidden = true;
  document.body.classList.add("has-answer");
}

function statusLabel(status, citations) {
  if (status === "clarify") return "A little more context";
  if (status === "insufficient_evidence") return "Information not available";
  if (citations.length) return "Archive references included";
  return "Conversation";
}

function appendRichText(target, text, citations, sourcePrefix) {
  const ids = new Set(citations.map((citation) => String(citation.id)));

  for (const piece of text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g)) {
    const id = piece.match(/^\[(\d+)\]$/)?.[1];

    if (id && ids.has(id)) {
      const link = element("a", "inline-citation", id);
      const targetId = `${sourcePrefix}-${id}`;
      link.href = `#${targetId}`;
      link.setAttribute("aria-label", `Read source ${id}`);
      link.addEventListener("click", () => {
        const details = byId(targetId);
        if (details) details.open = true;
      });
      target.append(link);
    } else if (piece.startsWith("**") && piece.endsWith("**")) {
      target.append(element("strong", "", piece.slice(2, -2)));
    } else {
      target.append(document.createTextNode(piece));
    }
  }
}

function parseAnswerBlocks(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraphLines = [];
  let currentList = null;

  const flushParagraph = () => {
    if (paragraphLines.length) {
      blocks.push({ type: "p", text: paragraphLines.join(" ") });
      paragraphLines = [];
    }
  };
  const flushList = () => {
    if (currentList) {
      blocks.push(currentList);
      currentList = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    const bulletMatch = line.match(/^[-*•]\s+(.*)$/);
    const numberedMatch = line.match(/^\d+[.)]\s+(.*)$/);

    if (bulletMatch) {
      flushParagraph();
      if (!currentList || currentList.type !== "ul") {
        flushList();
        currentList = { type: "ul", items: [] };
      }
      currentList.items.push(bulletMatch[1]);
    } else if (numberedMatch) {
      flushParagraph();
      if (!currentList || currentList.type !== "ol") {
        flushList();
        currentList = { type: "ol", items: [] };
      }
      currentList.items.push(numberedMatch[1]);
    } else {
      flushList();
      paragraphLines.push(line);
    }
  }

  flushParagraph();
  flushList();

  return blocks.length ? blocks : [{ type: "p", text }];
}

function buildAnswerBody(text, citations, sourcePrefix) {
  const container = element("div", "answer-text");
  container.tabIndex = -1;

  for (const block of parseAnswerBlocks(text)) {
    if (block.type === "p") {
      const paragraph = element("p");
      appendRichText(paragraph, block.text, citations, sourcePrefix);
      container.append(paragraph);
    } else {
      const list = element(block.type);
      for (const item of block.items) {
        const li = element("li");
        appendRichText(li, item, citations, sourcePrefix);
        list.append(li);
      }
      container.append(list);
    }
  }

  return container;
}

function validCitations(citations) {
  if (!Array.isArray(citations)) return [];

  return citations.filter((citation) =>
    Number.isInteger(citation.id) &&
    typeof citation.document === "string" &&
    typeof citation.section === "string" &&
    typeof citation.excerpt === "string" &&
    Array.isArray(citation.pdf_pages) &&
    Array.isArray(citation.printed_pages)
  );
}

function buildCitations(citations, sourcePrefix) {
  const wrapper = element("div", "citations");
  if (!citations.length) {
    wrapper.hidden = true;
    return wrapper;
  }

  const heading = element("div", "sources-heading");
  heading.append(icon("i-book"));
  heading.append(element("h3", "", "Sources for this answer"));
  heading.append(element("span", "", `${citations.length} ${citations.length === 1 ? "reference" : "references"}`));
  wrapper.append(heading);

  const list = element("div", "citation-list");

  for (const citation of citations) {
    const details = element("details", "citation");
    details.id = `${sourcePrefix}-${citation.id}`;

    const summary = element("summary");
    summary.append(element("span", "citation-number", citation.id));

    const title = element("span", "citation-title", `Section ${citation.section}`);
    const part = citation.document.match(/part-(\d)\.pdf$/)?.[1];
    const pages = citation.pdf_pages.join(", ");
    const printed = citation.printed_pages.join(", ");
    title.append(
      element(
        "span",
        "citation-meta",
        `${part ? `CrPC · Part ${part}` : citation.document} · PDF page ${pages} · Printed page ${printed}`
      )
    );

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
    list.append(details);
  }

  wrapper.append(list);
  return wrapper;
}

function appendUserMessage(text, messageKey = `local-${Date.now()}`) {
  showThread();

  const article = element("article", "thread-message thread-user");
  article.dataset.messageKey = messageKey;

  const label = element("div", "thread-user-label", "You");
  const body = element("p", "thread-user-text", text);

  article.append(label, body);
  thread.append(article);
  return article;
}

function appendAssistantMessage(message, messageKey = `assistant-${Date.now()}`) {
  showThread();

  const citations = validCitations(message.citations);
  const article = element("article", "thread-message thread-assistant");
  article.dataset.messageKey = messageKey;

  const heading = element("div", "answer-heading");
  const mark = element("span", "answer-mark");
  mark.append(icon("i-scales"));

  heading.append(mark);
  heading.append(element("strong", "", "CrPC assistant"));
  heading.append(element("span", "response-label", statusLabel(message.status, citations)));

  const copy = element("button", "icon-button");
  copy.type = "button";
  copy.title = "Copy answer";
  copy.setAttribute("aria-label", "Copy answer");
  copy.append(icon("i-copy"));
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      copy.title = "Copied";
      announce("Answer copied.");
    } catch {
      announce("Copy is unavailable. Select the answer text to copy it.");
    }
  });
  heading.append(copy);

  const sourcePrefix = `source-${String(messageKey).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const answer = buildAnswerBody(message.content, citations, sourcePrefix);

  article.append(heading, answer);
  article.append(buildCitations(citations, sourcePrefix));

  if (message.disclaimer) {
    article.append(element("p", "answer-disclaimer", message.disclaimer));
  } else if (message.status && citations.length) {
    article.append(
      element(
        "p",
        "answer-disclaimer",
        "Legal information only; not legal advice. Based on an archived edition; current amendments are not verified."
      )
    );
  }

  thread.append(article);
  return article;
}

function appendLoading() {
  showThread();

  const article = element("article", "thread-message thread-assistant thread-loading");
  article.id = "thread-loading";

  const heading = element("div", "answer-heading");
  const mark = element("span", "answer-mark");
  mark.append(icon("i-scales"));
  heading.append(mark, element("strong", "", "CrPC assistant"));

  const loading = element("div", "loading");
  const dots = element("span", "loading-dots");
  dots.setAttribute("aria-hidden", "true");
  dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  loading.append(dots, document.createTextNode(" Preparing your answer…"));

  const stop = element("button", "text-button stop-button", "Stop");
  stop.type = "button";
  stop.addEventListener("click", () => {
    cancelledByUser = true;
    activeController?.abort();
  });
  loading.append(stop);

  article.append(heading, loading);
  thread.append(article);
  return article;
}

function appendThreadError(message, retryText) {
  showThread();

  const article = element("article", "thread-message thread-assistant");
  const box = element("div", "request-error");
  box.setAttribute("role", "alert");
  box.append(element("strong", "", "We couldn’t complete this request."));
  box.append(element("p", "", message));

  if (retryText) {
    const retry = element("button", "text-button", "Try again");
    retry.type = "button";
    retry.append(document.createTextNode(" "));
    retry.append(icon("i-arrow"));
    retry.addEventListener("click", () => ask(retryText));
    box.append(retry);
  }

  article.append(box);
  thread.append(article);
}

function removeLoadingIndicator() {
  const loadingEl = byId("thread-loading");
  if (!loadingEl) return Promise.resolve();

  return new Promise((resolve) => {
    const finish = () => {
      loadingEl.remove();
      resolve();
    };
    loadingEl.addEventListener("transitionend", finish, { once: true });
    loadingEl.classList.add("thread-loading-exit");
    setTimeout(finish, 200);
  });
}

function scrollToLatest() {
  requestAnimationFrame(() => {
    thread.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
  });
}

async function parseApiResponse(response) {
  const data = await response.json();
  if (!response.ok || data.status === "error") {
    const error = new Error(
      errorMessages[data.error?.code] ||
      data.error?.message ||
      "The assistant is temporarily unavailable. Please try again shortly."
    );
    error.code = data.error?.code;
    throw error;
  }
  return data;
}

async function createConversation(firstQuestion) {
  const response = await fetch("/conversations", {
    method: "POST",
    headers: apiHeaders(true),
    body: JSON.stringify({ title: conversationTitle(firstQuestion) }),
  });

  const data = await parseApiResponse(response);

  if (typeof data.id !== "string" || typeof data.title !== "string") {
    throw new Error("The saved conversation could not be created.");
  }

  setActiveConversation(data.id, data.title);
  return data;
}

function renderConversationList() {
  const previousRects = new Map();
  for (const child of conversationList.children) {
    if (child.dataset?.conversationId) {
      previousRects.set(child.dataset.conversationId, child.getBoundingClientRect());
    }
  }

  conversationList.replaceChildren();

  if (!conversations.length) {
    conversationList.append(element("p", "conversation-empty", "No saved conversations yet."));
    return;
  }

  for (const conversation of conversations) {
    const displayTitle = conversation.title || "Untitled conversation";

    const item = element("div", "conversation-item");
    item.dataset.conversationId = conversation.id;
    item.classList.toggle("active", conversation.id === activeConversationId);

    const openButton = element("button", "conversation-item-main");
    openButton.type = "button";
    const title = element("span", "conversation-item-title", displayTitle);
    openButton.append(icon("i-chat"), title);
    openButton.addEventListener("click", () => loadConversation(conversation.id));

    const actions = element("div", "conversation-item-actions");

    const renameButton = element("button", "conversation-action");
    renameButton.type = "button";
    renameButton.title = "Rename conversation";
    renameButton.setAttribute("aria-label", `Rename "${displayTitle}"`);
    renameButton.append(icon("i-edit"));
    renameButton.addEventListener("click", (event) => {
      event.stopPropagation();
      startRename(item, conversation);
    });

    const deleteButton = element("button", "conversation-action conversation-action-delete");
    deleteButton.type = "button";
    deleteButton.title = "Delete conversation";
    deleteButton.setAttribute("aria-label", `Delete "${displayTitle}"`);
    deleteButton.append(icon("i-trash"));
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      handleDeleteClick(deleteButton, conversation);
    });

    actions.append(renameButton, deleteButton);
    item.append(openButton, actions);
    conversationList.append(item);
  }

  if (!previousRects.size) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  requestAnimationFrame(() => {
    for (const child of conversationList.children) {
      const id = child.dataset?.conversationId;
      const before = id && previousRects.get(id);
      if (!before) continue;

      const after = child.getBoundingClientRect();
      const deltaY = before.top - after.top;
      if (!deltaY) continue;

      child.style.transition = "none";
      child.style.transform = `translateY(${deltaY}px)`;
      requestAnimationFrame(() => {
        child.style.transition = "transform .28s ease";
        child.style.transform = "";
      });
    }
  });
}

function touchConversation(id) {
  const index = conversations.findIndex((conversation) => conversation.id === id);
  if (index > 0) {
    const [conversation] = conversations.splice(index, 1);
    conversations.unshift(conversation);
    renderConversationList();
  }
}

function startRename(item, conversation) {
  if (item.querySelector(".conversation-rename-form")) return;

  const originalTitle = conversation.title || "Untitled conversation";
  const main = item.querySelector(".conversation-item-main");
  const actions = item.querySelector(".conversation-item-actions");
  main.hidden = true;
  actions.hidden = true;

  const form = element("form", "conversation-rename-form");
  const input = element("input", "conversation-rename-input");
  input.type = "text";
  input.value = originalTitle;
  input.maxLength = 200;
  input.setAttribute("aria-label", "Conversation name");

  const save = element("button", "conversation-action");
  save.type = "submit";
  save.title = "Save name";
  save.append(icon("i-check"));

  const cancel = element("button", "conversation-action");
  cancel.type = "button";
  cancel.title = "Cancel rename";
  cancel.append(icon("i-close"));

  const endRename = () => {
    form.remove();
    main.hidden = false;
    actions.hidden = false;
  };

  cancel.addEventListener("click", endRename);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nextTitle = conversationTitle(input.value.trim()) || originalTitle;

    if (nextTitle === originalTitle) {
      endRename();
      return;
    }

    save.disabled = true;
    input.disabled = true;

    try {
      const response = await fetch(`/conversations/${encodeURIComponent(conversation.id)}`, {
        method: "PATCH",
        headers: apiHeaders(true),
        body: JSON.stringify({ title: nextTitle }),
      });
      const data = await parseApiResponse(response);

      conversation.title = typeof data.title === "string" ? data.title : nextTitle;
      if (conversation.id === activeConversationId) {
        byId("conversation-title").textContent = conversation.title;
      }
      endRename();
      renderConversationList();
      announce("Conversation renamed.");
    } catch (error) {
      announce("The conversation could not be renamed.");
      save.disabled = false;
      input.disabled = false;
      input.focus();
    }
  });

  form.append(input, save, cancel);
  item.append(form);
  input.focus();
  input.select();
}

function handleDeleteClick(button, conversation) {
  if (button.dataset.confirming) {
    deleteConversation(conversation);
    return;
  }

  button.dataset.confirming = "true";
  button.classList.add("confirming");
  button.title = "Click again to delete";
  button.setAttribute("aria-label", `Confirm deleting "${conversation.title || "conversation"}"`);

  const reset = () => {
    delete button.dataset.confirming;
    button.classList.remove("confirming");
    button.title = "Delete conversation";
    button.setAttribute("aria-label", `Delete "${conversation.title || "conversation"}"`);
  };

  setTimeout(() => {
    if (button.isConnected && button.dataset.confirming) reset();
  }, 3000);
}

async function deleteConversation(conversation) {
  try {
    const response = await fetch(`/conversations/${encodeURIComponent(conversation.id)}`, {
      method: "DELETE",
      headers: apiHeaders(),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(errorMessages[data.error?.code] || "The conversation could not be deleted.");
    }

    conversations = conversations.filter((item) => item.id !== conversation.id);
    renderConversationList();

    if (conversation.id === activeConversationId) {
      showHome();
    }

    announce("Conversation deleted.");
  } catch (error) {
    announce(error.message || "The conversation could not be deleted.");
  }
}

async function loadConversations() {
  try {
    const response = await fetch("/conversations", { headers: apiHeaders() });
    const data = await parseApiResponse(response);
    conversations = Array.isArray(data.conversations) ? data.conversations : [];
    renderConversationList();
  } catch (error) {
    conversationList.replaceChildren(
      element("p", "conversation-empty", "Saved conversations are unavailable.")
    );
  }
}

async function loadConversation(conversationId) {
  if (busy) return;

  setBusy(true);
  announce("Loading conversation.");

  try {
    const response = await fetch(`/conversations/${encodeURIComponent(conversationId)}`, {
      headers: apiHeaders(),
    });
    const data = await parseApiResponse(response);

    if (!Array.isArray(data.messages)) throw new Error("The saved conversation is incomplete.");

    setActiveConversation(data.id, data.title || "Conversation");
    thread.replaceChildren();

    if (!data.messages.length) {
      showThread();
      thread.append(element("p", "thread-empty", "This conversation does not have any messages yet."));
    } else {
      data.messages.forEach((message, index) => {
        if (message.role === "user") {
          appendUserMessage(message.content, message.id || `user-${index}`);
        } else if (message.role === "assistant") {
          appendAssistantMessage(
            {
              content: message.content,
              status: message.status,
              citations: message.citations || [],
            },
            message.id || `assistant-${index}`
          );
        }
      });
    }

    question.value = "";
    countCharacters();
    autoResizeQuestion();
    announce("Conversation loaded.");
    question.focus();
    scrollToLatest();
  } catch (error) {
    if (error.code === "conversation_not_found") {
      showHome();
      await loadConversations();
    }
    announce("The conversation could not be loaded.");
  } finally {
    setBusy(false);
  }
}

async function ask(text) {
  if (busy || !text.trim()) return;
  if (text.length > 4000) {
    question.reportValidity();
    return;
  }

  const cleanQuestion = text.trim();
  const isNewConversation = !activeConversationId;
  setBusy(true);
  announce("Preparing your answer.");

  let optimisticUser = null;

  try {
    if (isNewConversation) {
      await createConversation(cleanQuestion);
      await loadConversations();
    }

    optimisticUser = appendUserMessage(cleanQuestion);
    question.value = "";
    countCharacters();
    autoResizeQuestion();
    appendLoading();
    scrollToLatest();

    cancelledByUser = false;
    const controller = new AbortController();
    activeController = controller;
    const timer = setTimeout(() => controller.abort(), 120000);

    try {
      const response = await fetch("/ask", {
        method: "POST",
        headers: apiHeaders(true),
        body: JSON.stringify({
          question: cleanQuestion,
          conversation_id: activeConversationId,
        }),
        signal: controller.signal,
      });

      const data = await parseApiResponse(response);

      if (typeof data.answer !== "string" || !Array.isArray(data.citations)) {
        throw new Error("An incomplete response was received. Please try again.");
      }

      await removeLoadingIndicator();

      appendAssistantMessage(
        {
          content: data.answer,
          status: data.status,
          citations: data.citations,
          disclaimer: data.disclaimer,
        },
        `turn-${Date.now()}`
      );

      if (!isNewConversation) touchConversation(activeConversationId);
      announce(
        data.status === "clarify"
          ? "A clarification is needed. Read the answer and include the missing details in your next question."
          : "Your answer is ready."
      );
      scrollToLatest();
    } finally {
      clearTimeout(timer);
      activeController = null;
    }
  } catch (error) {
    byId("thread-loading")?.remove();

    if (error.name === "AbortError") {
      appendThreadError(
        cancelledByUser ? "Request cancelled." : "The request timed out. Please try again.",
        cleanQuestion
      );
    } else if (error instanceof TypeError || error instanceof SyntaxError) {
      appendThreadError("Could not reach the assistant. Check your connection and try again.", cleanQuestion);
    } else {
      appendThreadError(error.message, cleanQuestion);
    }

    if (!optimisticUser && !activeConversationId) {
      showHome();
      question.value = cleanQuestion;
      countCharacters();
      autoResizeQuestion();
    }

    announce("The request could not be completed.");
    scrollToLatest();
  } finally {
    setBusy(false);
    question.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(question.value);
});

question.addEventListener("input", () => {
  countCharacters();
  autoResizeQuestion();
});

question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy) form.requestSubmit();
  }
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    question.value = button.dataset.question;
    countCharacters();
    autoResizeQuestion();
    question.focus();
  });
});

byId("new-question").addEventListener("click", () => {
  if (busy) return;
  showHome();
});

document.querySelectorAll("[data-dialog]").forEach((button) => {
  button.addEventListener("click", () => byId(button.dataset.dialog).showModal());
});

document.querySelectorAll(".close-dialog").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});

document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    ) {
      dialog.close();
    }
  });
});

async function initialize() {
  clientId();
  byId("result").hidden = true;
  await loadConversations();

  if (activeConversationId) {
    await loadConversation(activeConversationId);
  } else {
    showHome();
  }
}

initialize();
