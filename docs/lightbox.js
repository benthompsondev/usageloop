(() => {
  const dialog = document.querySelector(".lightbox");
  const links = Array.from(document.querySelectorAll("[data-lightbox]"));

  if (!dialog || !links.length || typeof dialog.showModal !== "function") {
    return;
  }

  const image = dialog.querySelector(".lightbox-image");
  const status = dialog.querySelector(".lightbox-status");
  const closeButton = dialog.querySelector(".lightbox-close");
  const previousButton = dialog.querySelector(".lightbox-previous");
  const nextButton = dialog.querySelector(".lightbox-next");
  let currentIndex = 0;
  let opener = null;

  const showScreenshot = (index) => {
    currentIndex = (index + links.length) % links.length;
    const link = links[currentIndex];
    const thumbnail = link.querySelector("img");
    image.src = link.href;
    image.alt = thumbnail?.alt || "UsageLoop screenshot";
    status.textContent = `Screenshot ${currentIndex + 1} of ${links.length}`;
  };

  const closeLightbox = () => {
    if (dialog.open) {
      dialog.close();
    }
  };

  links.forEach((link, index) => {
    link.addEventListener("click", (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }

      event.preventDefault();
      opener = link;
      showScreenshot(index);
      document.body.classList.add("lightbox-open");
      dialog.showModal();
      closeButton.focus();
    });
  });

  closeButton.addEventListener("click", closeLightbox);
  previousButton.addEventListener("click", () => showScreenshot(currentIndex - 1));
  nextButton.addEventListener("click", () => showScreenshot(currentIndex + 1));

  dialog.addEventListener("click", (event) => {
    if (!event.target.closest(".lightbox-image, .lightbox-button")) {
      closeLightbox();
    }
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeLightbox();
  });

  dialog.addEventListener("close", () => {
    document.body.classList.remove("lightbox-open");
    image.removeAttribute("src");
    opener?.focus({ preventScroll: true });
  });

  document.addEventListener("keydown", (event) => {
    if (!dialog.open) {
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      showScreenshot(currentIndex - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      showScreenshot(currentIndex + 1);
    }
  });
})();
