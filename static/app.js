const toast = document.querySelector("[data-toast]");

const savedTheme = localStorage.getItem("financeGuideTheme") || "gold";
document.documentElement.dataset.theme = savedTheme;

function syncThemeButtons(theme) {
  document.querySelectorAll("[data-theme-option]").forEach((button) => {
    button.classList.toggle("active", button.dataset.themeOption === theme);
  });
}

syncThemeButtons(savedTheme);

document.querySelectorAll("[data-theme-option]").forEach((button) => {
  button.addEventListener("click", () => {
    const theme = button.dataset.themeOption;
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("financeGuideTheme", theme);
    syncThemeButtons(theme);
  });
});

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2600);
}

document.querySelector("[data-import]")?.addEventListener("click", async () => {
  const res = await fetch("/import", { method: "POST" });
  const data = await res.json();
  showToast(data.ok ? "Local files imported." : "Import failed.");
  if (data.ok) setTimeout(() => location.reload(), 600);
});

document.querySelector("[data-refresh]")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const code = button.dataset.refresh;
  button.disabled = true;
  button.textContent = "…";
  showToast("Refreshing from AkShare...");
  try {
    const res = await fetch(`/company/${code}/refresh`, { method: "POST" });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Refresh failed.");
    showToast("Refresh complete.");
    setTimeout(() => location.reload(), 800);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "↻";
  }
});

const notePagesData = document.querySelector("[data-note-pages-json]");
const noteForm = document.querySelector("[data-note-form]");
const editor = document.querySelector("[data-editor]");
const pageIdInput = document.querySelector("[data-page-id]");
const pageTitleInput = document.querySelector("[data-page-title]");
const noteContentInput = document.querySelector("[data-note-content]");
const deletePageIdInput = document.querySelector("[data-delete-page-id]");
const deleteNoteButton = document.querySelector("[data-delete-note]");

function getNotePages() {
  if (!notePagesData) return [];
  try {
    return JSON.parse(notePagesData.textContent);
  } catch {
    return [];
  }
}

function loadNotePage(pageId) {
  const pages = getNotePages();
  const page = pages.find((item) => String(item.id) === String(pageId));
  if (page) {
    pageIdInput.value = page.id;
    if (deletePageIdInput) deletePageIdInput.value = page.id;
    if (deleteNoteButton) deleteNoteButton.disabled = false;
    pageTitleInput.value = page.title || "";
    editor.innerHTML = page.content_html || "";
  } else {
    pageIdInput.value = "";
    if (deletePageIdInput) deletePageIdInput.value = "";
    if (deleteNoteButton) deleteNoteButton.disabled = true;
    pageTitleInput.value = "";
    editor.innerHTML = "";
  }
}

if (noteForm && editor) {
  const firstPage = getNotePages()[0];
  if (firstPage) {
    loadNotePage(firstPage.id);
  } else {
    loadNotePage("new");
  }
}

document.querySelectorAll("[data-page-target]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-page-target]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    loadNotePage(button.dataset.pageTarget);
  });
});

document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => {
    editor?.focus();
    document.execCommand(button.dataset.command, false, null);
  });
});

document.querySelector("[data-link]")?.addEventListener("click", () => {
  let url = window.prompt("Enter URL");
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  editor?.focus();
  const selection = window.getSelection();
  const selectedText = selection?.toString();
  if (selectedText && selection.rangeCount) {
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return;
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = selectedText;
    range.deleteContents();
    range.insertNode(link);
    selection.removeAllRanges();
    selection.selectAllChildren(link);
  } else {
    document.execCommand("insertHTML", false, `<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`);
  }
});

function linkifyEditorContent(root) {
  if (!root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement?.closest("a")) continue;
    if (/((https?:\/\/|www\.)[^\s<]+|[a-z0-9.-]+\.[a-z]{2,}[^\s<]*)/i.test(node.nodeValue || "")) {
      nodes.push(node);
    }
  }

  nodes.forEach((node) => {
    const span = document.createElement("span");
    span.innerHTML = (node.nodeValue || "").replace(
      /((https?:\/\/|www\.)[^\s<]+|[a-z0-9.-]+\.[a-z]{2,}[^\s<]*)/gi,
      (match) => {
        const clean = match.replace(/[.,;:!?)]$/, "");
        const suffix = match.slice(clean.length);
        const href = /^https?:\/\//i.test(clean) ? clean : `https://${clean}`;
        return `<a href="${href}" target="_blank" rel="noreferrer">${clean}</a>${suffix}`;
      }
    );
    node.replaceWith(...span.childNodes);
  });
}

document.querySelector("[data-image]")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    editor?.focus();
    document.execCommand("insertImage", false, reader.result);
    event.target.value = "";
  };
  reader.readAsDataURL(file);
});

noteForm?.addEventListener("submit", () => {
  linkifyEditorContent(editor);
  noteContentInput.value = editor?.innerHTML || "";
});

editor?.addEventListener("click", (event) => {
  const link = event.target.closest("a");
  if (!link) return;
  event.preventDefault();
  window.open(link.href, "_blank", "noopener,noreferrer");
});

deleteNoteButton?.addEventListener("click", (event) => {
  if (!deletePageIdInput?.value) {
    event.preventDefault();
    showToast("Select a note page first.");
    return;
  }
  if (!window.confirm("Delete this note page?")) {
    event.preventDefault();
  }
});

const generalNoteForm = document.querySelector("[data-general-note-form]");
const generalEditor = document.querySelector("[data-general-editor]");
const generalContentInput = document.querySelector("[data-general-note-content]");

document.querySelectorAll("[data-general-command]").forEach((button) => {
  button.addEventListener("click", () => {
    generalEditor?.focus();
    document.execCommand(button.dataset.generalCommand, false, null);
  });
});

document.querySelector("[data-general-link]")?.addEventListener("click", () => {
  let url = window.prompt("Enter URL");
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  generalEditor?.focus();
  const selection = window.getSelection();
  const selectedText = selection?.toString();
  if (selectedText && selection.rangeCount) {
    const range = selection.getRangeAt(0);
    if (!generalEditor?.contains(range.commonAncestorContainer)) return;
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = selectedText;
    range.deleteContents();
    range.insertNode(link);
    selection.removeAllRanges();
    selection.selectAllChildren(link);
  } else {
    document.execCommand("insertHTML", false, `<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`);
  }
});

document.querySelector("[data-general-image]")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    generalEditor?.focus();
    document.execCommand("insertImage", false, reader.result);
    event.target.value = "";
  };
  reader.readAsDataURL(file);
});

generalNoteForm?.addEventListener("submit", () => {
  linkifyEditorContent(generalEditor);
  generalContentInput.value = generalEditor?.innerHTML || "";
});

generalEditor?.addEventListener("click", (event) => {
  const link = event.target.closest("a");
  if (!link) return;
  event.preventDefault();
  window.open(link.href, "_blank", "noopener,noreferrer");
});
