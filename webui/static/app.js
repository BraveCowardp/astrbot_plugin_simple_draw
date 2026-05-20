function closeDetail() {
  const dialog = document.getElementById("detail-dialog");
  if (dialog) {
    dialog.close();
    dialog.remove();
  }
}

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

document.addEventListener("click", (event) => {
  const dialog = document.getElementById("detail-dialog");
  if (dialog && event.target === dialog) {
    closeDetail();
  }
});
