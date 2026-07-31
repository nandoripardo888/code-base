(() => {
  "use strict";

  const initialTransactionId = document.querySelector('meta[name="review-transaction"]').content;
  const csrfToken = document.querySelector('meta[name="review-csrf"]').content;
  const state = {
    transactionId: initialTransactionId,
    summary: null,
    reviews: [],
    expandedReviews: new Set([initialTransactionId]),
    selectedIndex: 0,
    view: window.matchMedia("(max-width: 900px)").matches ? "unified" : "split",
    fileDiff: null,
    changeRows: [],
    activeChange: -1,
  };

  const elements = {
    title: document.querySelector("#review-title"),
    reviewState: document.querySelector("#review-state"),
    source: document.querySelector("#source-tool"),
    created: document.querySelector("#created-at"),
    additions: document.querySelector("#total-additions"),
    deletions: document.querySelector("#total-deletions"),
    description: document.querySelector("#review-description"),
    reviewCount: document.querySelector("#review-count"),
    reviewTree: document.querySelector("#review-tree"),
    selectedPath: document.querySelector("#selected-path"),
    operationBadge: document.querySelector("#operation-badge"),
    scroller: document.querySelector("#diff-scroller"),
    columnHeadings: document.querySelector("#column-headings"),
    changePosition: document.querySelector("#change-position"),
    complete: document.querySelector("#complete-button"),
    rollback: document.querySelector("#rollback-button"),
    rollbackDialog: document.querySelector("#rollback-dialog"),
    confirmRollback: document.querySelector("#confirm-rollback"),
    themeToggle: document.querySelector("#theme-toggle"),
    toastRegion: document.querySelector("#toast-region"),
    reviewRetention: document.querySelector("#review-retention"),
  };

  const operationLabels = {
    modify: "Modificado",
    create: "Criado",
    delete: "Excluído",
  };

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("code-harness-review-theme", theme);
    elements.themeToggle.setAttribute(
      "aria-label",
      theme === "dark" ? "Usar tema claro" : "Usar tema escuro",
    );
  }

  function initializeTheme() {
    const stored = localStorage.getItem("code-harness-review-theme");
    const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    setTheme(stored === "dark" || stored === "light" ? stored : preferred);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        ...(options.method === "POST" ? { "X-CSRF-Token": csrfToken } : {}),
        ...options.headers,
      },
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = { error: "A resposta local não pôde ser lida." };
    }
    if (!response.ok) {
      const error = new Error(payload.error || "A operação não pôde ser concluída.");
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async function loadReviews() {
    const payload = await request("/api/reviews?limit=200");
    state.reviews = payload.items;
    elements.reviewCount.textContent = payload.total;
    elements.reviewRetention.textContent = `${payload.total} ${payload.total === 1 ? "revisão retida" : "revisões retidas"}`;
    if (state.reviews.length && !state.reviews.some((item) => item.transaction_id === state.transactionId)) {
      state.transactionId = state.reviews[0].transaction_id;
    }
    state.expandedReviews.add(state.transactionId);
    renderReviewTree();
  }

  function reviewStatus(review) {
    if (review.status === "rolled_back") return "desfeita";
    return review.review_state === "reviewed" ? "revisada" : "não revisada";
  }

  function renderReviewTree() {
    elements.reviewTree.replaceChildren();
    for (const review of state.reviews) {
      const expanded = state.expandedReviews.has(review.transaction_id);
      const activeReview = review.transaction_id === state.transactionId;
      const container = document.createElement("div");
      container.className = `review-tree-item${activeReview ? " active-review" : ""}`;

      const reviewButton = document.createElement("button");
      reviewButton.type = "button";
      reviewButton.className = "review-node";
      reviewButton.setAttribute("role", "treeitem");
      reviewButton.setAttribute("aria-expanded", String(expanded));
      reviewButton.setAttribute("aria-selected", String(activeReview));
      reviewButton.append(span("tree-chevron", expanded ? "⌄" : "›"));

      const info = span("review-node-info");
      const title = span(
        "review-node-title",
        review.description || `Revisão de ${formatDate(review.created_at)}`,
      );
      const stats = review.additions === null || review.deletions === null
        ? ""
        : ` · +${review.additions}/−${review.deletions}`;
      const meta = span(
        "review-node-meta",
        `${formatDate(review.created_at)} · ${review.files_changed} ${review.files_changed === 1 ? "arquivo" : "arquivos"}${stats}`,
      );
      const status = span(`review-node-status ${reviewStatus(review).replaceAll(" ", "-")}`, reviewStatus(review));
      info.append(title, meta, status);
      reviewButton.append(info);
      reviewButton.addEventListener("click", () => {
        if (activeReview) {
          if (expanded) state.expandedReviews.delete(review.transaction_id);
          else state.expandedReviews.add(review.transaction_id);
          renderReviewTree();
          return;
        }
        state.expandedReviews.add(review.transaction_id);
        selectReview(review.transaction_id).catch((error) => toast(error.message, true));
      });
      container.append(reviewButton);

      const fileGroup = document.createElement("div");
      fileGroup.className = "review-file-group";
      fileGroup.setAttribute("role", "group");
      fileGroup.hidden = !expanded;
      for (const file of review.files) {
        const fileButton = document.createElement("button");
        fileButton.type = "button";
        fileButton.className = `tree-file${activeReview && file.index === state.selectedIndex ? " active" : ""}`;
        fileButton.setAttribute("role", "treeitem");
        fileButton.setAttribute("aria-selected", String(activeReview && file.index === state.selectedIndex));
        const parts = file.path.split("/");
        const filename = parts.pop();
        const directory = parts.length ? parts.join("/") : "raiz do projeto";
        const icon = span("file-icon", extensionLabel(filename));
        icon.setAttribute("aria-hidden", "true");
        const fileInfo = span("file-info");
        fileInfo.append(
          span("file-name", filename),
          span("file-path", directory),
          span("operation-label", operationLabels[file.operation] || file.operation),
        );
        fileButton.append(icon, fileInfo);
        fileButton.title = file.path;
        fileButton.addEventListener("click", () => {
          state.expandedReviews.add(review.transaction_id);
          if (activeReview) selectFile(file.index).catch((error) => toast(error.message, true));
          else selectReview(review.transaction_id, file.index).catch((error) => toast(error.message, true));
        });
        fileGroup.append(fileButton);
      }
      container.append(fileGroup);
      elements.reviewTree.append(container);
    }
  }

  async function selectReview(transactionId, fileIndex = 0) {
    state.transactionId = transactionId;
    state.selectedIndex = fileIndex;
    state.fileDiff = null;
    state.expandedReviews.add(transactionId);
    const url = new URL(window.location.href);
    url.searchParams.set("review", transactionId);
    url.searchParams.set("file", String(fileIndex));
    window.history.replaceState({}, "", url);
    await loadSummary(fileIndex);
  }

  async function loadSummary(fileIndex = 0) {
    const summary = await request(`/api/reviews/${encodeURIComponent(state.transactionId)}`);
    state.summary = summary;
    renderSummary();
    renderReviewTree();
    if (summary.files.length) {
      const selected = Math.min(Math.max(fileIndex, 0), summary.files.length - 1);
      await selectFile(selected);
    } else {
      renderEmpty("Nenhum arquivo nesta revisão.");
    }
  }

  function renderSummary() {
    const summary = state.summary;
    elements.title.textContent = `Editou ${summary.files_changed} ${summary.files_changed === 1 ? "arquivo" : "arquivos"}`;
    elements.source.textContent = formatSource(summary.source_tool);
    elements.created.textContent = formatDate(summary.created_at);
    elements.created.dateTime = summary.created_at;
    elements.additions.textContent = `+${summary.additions}`;
    elements.deletions.textContent = `−${summary.deletions}`;
    elements.description.textContent = summary.description || "";
    elements.description.hidden = !summary.description;

    const rolledBack = summary.status === "rolled_back";
    const reviewed = summary.review_state === "reviewed";
    elements.reviewState.className = `state-pill${rolledBack ? " rolled-back" : reviewed ? " reviewed" : ""}`;
    elements.reviewState.textContent = rolledBack ? "Desfeito" : reviewed ? "Revisado" : "Não revisado";
    elements.complete.disabled = reviewed || rolledBack;
    elements.complete.textContent = reviewed ? "Revisão concluída" : "Concluir revisão";
    elements.rollback.disabled = rolledBack;
  }

  function formatSource(value) {
    return String(value)
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join("");
  }

  function formatDate(value) {
    try {
      return new Intl.DateTimeFormat("pt-BR", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(value));
    } catch {
      return value;
    }
  }

  function extensionLabel(filename) {
    const extension = filename.includes(".") ? filename.split(".").pop() : "";
    return extension.slice(0, 3).toUpperCase() || "•";
  }

  function span(className, text = "") {
    const element = document.createElement("span");
    element.className = className;
    element.textContent = text;
    return element;
  }

  async function selectFile(index, fullContext = false) {
    state.selectedIndex = index;
    const url = new URL(window.location.href);
    url.searchParams.set("review", state.transactionId);
    url.searchParams.set("file", String(index));
    window.history.replaceState({}, "", url);
    renderReviewTree();
    elements.scroller.innerHTML = '<div class="diff-loading"><span></span><span></span><span></span><span></span></div>';
    try {
      const suffix = fullContext ? "?context=full" : "";
      state.fileDiff = await request(
        `/api/reviews/${encodeURIComponent(state.transactionId)}/files/${index}${suffix}`,
      );
      state.activeChange = -1;
      renderDiff();
    } catch (error) {
      renderError(error.message);
    }
  }

  function renderDiff() {
    const file = state.fileDiff;
    elements.selectedPath.textContent = file.path;
    elements.selectedPath.title = file.path;
    elements.operationBadge.textContent = operationLabels[file.operation] || file.operation;
    elements.operationBadge.className = `operation-badge ${file.operation}`;
    elements.scroller.replaceChildren();

    if (file.binary) {
      renderEmpty("Arquivo binário", "A comparação textual não está disponível para este arquivo.", "binary-state");
      updateChangeNavigation();
      return;
    }
    if (!file.rows.length) {
      renderEmpty("Sem diferenças textuais", "Os snapshots não possuem linhas diferentes.");
      updateChangeNavigation();
      return;
    }

    if (state.view === "split") {
      renderSplitRows(file.rows);
    } else {
      renderUnifiedRows(file.rows);
    }
    state.changeRows = [...elements.scroller.querySelectorAll("[data-change-row]")];
    updateChangeNavigation();
  }

  function renderSplitRows(rows) {
    for (const row of rows) {
      if (row.kind === "collapsed") {
        elements.scroller.append(collapsedElement(row));
        continue;
      }
      const rowElement = document.createElement("div");
      rowElement.className = `diff-row row-${row.kind}`;
      if (row.kind !== "context") rowElement.dataset.changeRow = "true";

      const oldSide = sideElement(
        row.old_line_number,
        row.old_text,
        row.kind === "addition" ? "placeholder" : row.kind === "replacement" ? "deletion" : row.kind,
      );
      const newSide = sideElement(
        row.new_line_number,
        row.new_text,
        row.kind === "deletion" ? "placeholder" : row.kind === "replacement" ? "addition" : row.kind,
      );
      if (row.kind === "replacement" && row.old_text !== null && row.new_text !== null) {
        applyInlineDiff(oldSide.querySelector(".line-code"), newSide.querySelector(".line-code"), row.old_text, row.new_text);
      }
      rowElement.append(oldSide, newSide);
      elements.scroller.append(rowElement);
    }
  }

  function sideElement(lineNumber, text, kind) {
    const side = document.createElement("div");
    side.className = `diff-side ${text === null ? "placeholder" : kind}`;
    const number = document.createElement("span");
    number.className = "line-number";
    number.textContent = lineNumber ?? "";
    const code = document.createElement("code");
    code.className = "line-code";
    code.textContent = text ?? "";
    side.append(number, code);
    return side;
  }

  function renderUnifiedRows(rows) {
    for (const row of rows) {
      if (row.kind === "collapsed") {
        elements.scroller.append(collapsedElement(row));
        continue;
      }
      if (row.kind === "replacement") {
        elements.scroller.append(
          unifiedElement(row.old_line_number, null, row.old_text, "deletion"),
          unifiedElement(null, row.new_line_number, row.new_text, "addition"),
        );
      } else {
        elements.scroller.append(
          unifiedElement(row.old_line_number, row.new_line_number, row.old_text ?? row.new_text, row.kind),
        );
      }
    }
  }

  function unifiedElement(oldNumber, newNumber, text, kind) {
    const row = document.createElement("div");
    row.className = `unified-row ${kind}`;
    if (kind !== "context") row.dataset.changeRow = "true";
    const oldLine = document.createElement("span");
    oldLine.className = "line-number";
    oldLine.textContent = oldNumber ?? "";
    const newLine = document.createElement("span");
    newLine.className = "line-number";
    newLine.textContent = newNumber ?? "";
    const code = document.createElement("code");
    code.className = "line-code";
    code.textContent = text ?? "";
    row.append(oldLine, newLine, code);
    return row;
  }

  function collapsedElement(row) {
    const container = document.createElement("div");
    container.className = "collapsed-row";
    const text = document.createElement("span");
    text.textContent = `${row.hidden_lines} linhas sem alterações`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "expand-context";
    button.textContent = "Expandir contexto";
    button.addEventListener("click", () => selectFile(state.selectedIndex, true));
    container.append(text, button);
    return container;
  }

  function applyInlineDiff(oldCode, newCode, oldText, newText) {
    let prefix = 0;
    const limit = Math.min(oldText.length, newText.length);
    while (prefix < limit && oldText[prefix] === newText[prefix]) prefix += 1;
    let suffix = 0;
    while (
      suffix < limit - prefix &&
      oldText[oldText.length - 1 - suffix] === newText[newText.length - 1 - suffix]
    ) suffix += 1;
    renderInline(oldCode, oldText, prefix, suffix);
    renderInline(newCode, newText, prefix, suffix);
  }

  function renderInline(element, text, prefix, suffix) {
    element.replaceChildren();
    element.append(document.createTextNode(text.slice(0, prefix)));
    const mark = document.createElement("mark");
    mark.className = "inline-change";
    mark.textContent = text.slice(prefix, suffix ? -suffix : undefined);
    element.append(mark, document.createTextNode(suffix ? text.slice(-suffix) : ""));
  }

  function renderEmpty(title, detail = "", className = "empty-state") {
    elements.scroller.replaceChildren();
    const container = document.createElement("div");
    container.className = className;
    const strong = document.createElement("strong");
    strong.textContent = title;
    container.append(strong);
    if (detail) {
      const paragraph = document.createElement("p");
      paragraph.textContent = detail;
      container.append(paragraph);
    }
    elements.scroller.append(container);
  }

  function renderError(message) {
    renderEmpty("Não foi possível carregar o diff", message, "error-state");
  }

  function updateChangeNavigation() {
    const count = state.changeRows.length;
    elements.changePosition.textContent = count
      ? `${Math.max(1, state.activeChange + 1)} de ${count}`
      : "0 de 0";
  }

  function navigateChange(direction) {
    if (!state.changeRows.length) return;
    state.activeChange = (state.activeChange + direction + state.changeRows.length) % state.changeRows.length;
    state.changeRows[state.activeChange].scrollIntoView({ block: "center", behavior: "smooth" });
    updateChangeNavigation();
  }

  async function completeReview() {
    elements.complete.disabled = true;
    try {
      state.summary = await request(
        `/api/reviews/${encodeURIComponent(state.transactionId)}/complete`,
        { method: "POST", body: "{}" },
      );
      renderSummary();
      await loadReviews();
      toast("Revisão concluída. O rollback continua disponível.");
    } catch (error) {
      elements.complete.disabled = false;
      toast(error.message, true);
    }
  }

  async function rollbackReview() {
    elements.rollbackDialog.close();
    elements.rollback.disabled = true;
    try {
      state.summary = await request(
        `/api/reviews/${encodeURIComponent(state.transactionId)}/rollback`,
        { method: "POST", body: "{}" },
      );
      renderSummary();
      await loadReviews();
      toast("Transação desfeita e snapshots anteriores restaurados.");
    } catch (error) {
      elements.rollback.disabled = false;
      const conflicts = error.payload?.conflicts;
      toast(
        conflicts?.length
          ? `Rollback bloqueado: ${conflicts.join(", ")} mudou depois da transação.`
          : error.message,
        true,
      );
    }
  }

  function toast(message, error = false) {
    const item = document.createElement("div");
    item.className = `toast${error ? " error" : ""}`;
    item.textContent = message;
    elements.toastRegion.append(item);
    window.setTimeout(() => item.remove(), 5200);
  }

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("[data-view].active")?.classList.remove("active");
      button.classList.add("active");
      state.view = button.dataset.view;
      elements.columnHeadings.classList.toggle("unified", state.view === "unified");
      renderDiff();
    });
  });

  document.querySelector("#previous-change").addEventListener("click", () => navigateChange(-1));
  document.querySelector("#next-change").addEventListener("click", () => navigateChange(1));
  elements.complete.addEventListener("click", completeReview);
  elements.rollback.addEventListener("click", () => elements.rollbackDialog.showModal());
  elements.confirmRollback.addEventListener("click", (event) => {
    event.preventDefault();
    rollbackReview();
  });
  elements.themeToggle.addEventListener("click", () => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  window.matchMedia("(max-width: 900px)").addEventListener("change", (event) => {
    if (!event.matches || state.view === "unified") return;
    document.querySelector('[data-view="unified"]').click();
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.closest("button, dialog")) return;
    if (event.key.toLowerCase() === "j") navigateChange(1);
    if (event.key.toLowerCase() === "k") navigateChange(-1);
    if (event.key.toLowerCase() === "t") elements.themeToggle.click();
  });

  initializeTheme();
  if (state.view === "unified") {
    document.querySelector("[data-view].active")?.classList.remove("active");
    document.querySelector('[data-view="unified"]').classList.add("active");
    elements.columnHeadings.classList.add("unified");
  }
  if (window.location.protocol === "file:" || initialTransactionId.includes("__")) {
    elements.title.textContent = "Abra pela revisão local";
    elements.reviewState.textContent = "Sem sessão";
    elements.reviewTree.replaceChildren();
    elements.reviewCount.textContent = "0";
    elements.reviewRetention.textContent = "Servidor local não iniciado";
    elements.selectedPath.textContent = "Nenhuma transação carregada";
    elements.complete.disabled = true;
    elements.rollback.disabled = true;
    document.querySelectorAll("button").forEach((button) => {
      if (button !== elements.themeToggle) button.disabled = true;
    });
    renderEmpty(
      "Este arquivo não funciona sozinho",
      "No terminal do projeto, execute “code-harness review latest” e abra a URL http://127.0.0.1 exibida.",
      "error-state",
    );
    return;
  }
  async function initialize() {
    const url = new URL(window.location.href);
    const requestedReview = url.searchParams.get("review");
    const requestedFile = Number.parseInt(url.searchParams.get("file") || "0", 10);
    await loadReviews();
    if (
      requestedReview
      && /^[A-Za-z0-9_-]+$/.test(requestedReview)
      && state.reviews.some((item) => item.transaction_id === requestedReview)
    ) {
      state.transactionId = requestedReview;
    }
    state.expandedReviews.add(state.transactionId);
    await loadSummary(Number.isInteger(requestedFile) ? requestedFile : 0);
  }

  initialize().catch((error) => renderError(error.message));
  window.setInterval(() => {
    loadReviews().catch(() => undefined);
  }, 10000);
})();
