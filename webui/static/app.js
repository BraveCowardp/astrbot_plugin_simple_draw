function closeDetail() {
  const dialog = document.getElementById("detail-dialog");
  if (dialog) {
    dialog.close();
    dialog.remove();
  }
}

function copyPrompt(button) {
  const section = button.closest(".prompt-section");
  const source = section ? section.querySelector(".prompt-copy-source") : null;
  const text = source ? source.value : "";

  if (!source || !text) {
    setCopyButtonState(button, "无内容");
    return;
  }

  if (copyFromSource(source)) {
    setCopyButtonState(button, "已复制");
    return;
  }

  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    navigator.clipboard.writeText(text)
      .then(() => setCopyButtonState(button, "已复制"))
      .catch(() => showManualCopy(button, source));
    return;
  }

  showManualCopy(button, source);
}

function copyFromSource(source) {
  source.removeAttribute("disabled");
  source.focus();
  source.select();
  source.setSelectionRange(0, source.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_error) {
    copied = false;
  }

  return copied;
}

function setCopyButtonState(button, label, source) {
  const original = button.dataset.originalLabel || button.textContent || "复制";
  button.dataset.originalLabel = original;
  button.textContent = label;
  button.classList.add("copied");

  window.setTimeout(() => {
    button.textContent = original;
    button.classList.remove("copied");
    if (source) {
      source.classList.remove("manual-copy-active");
    }
  }, 1200);
}

function showManualCopy(button, source) {
  source.focus();
  source.select();
  source.setSelectionRange(0, source.value.length);
  source.classList.add("manual-copy-active");
  setCopyButtonState(button, "已选中", source);
}

document.addEventListener("click", (event) => {
  const copyButton = event.target.closest(".copy-button");
  if (copyButton) {
    event.preventDefault();
    event.stopPropagation();
    copyPrompt(copyButton);
    return;
  }

  const dialog = document.getElementById("detail-dialog");
  if (dialog && event.target === dialog) {
    closeDetail();
  }
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id !== "modal-root") {
    return;
  }

  const dialog = document.getElementById("detail-dialog");
  if (!dialog || typeof dialog.showModal !== "function") {
    return;
  }

  if (dialog.open) {
    dialog.close();
  }

  try {
    dialog.showModal();
  } catch (_error) {
    dialog.setAttribute("open", "");
  }
});
