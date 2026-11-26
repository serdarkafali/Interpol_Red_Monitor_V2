// Draws the face boxes over the notice photos (ADR 0013).
//
// The database keeps a box in pixels of the original image, but the page shows the
// image smaller. Once an image has loaded its real size is known, so every box is
// turned into percentages of that size and stays in place at any width.

function placeBoxes(frame) {
  const image = frame.querySelector("img");
  if (!image.naturalWidth) {
    return; // The image could not be loaded; keep the boxes hidden.
  }
  for (const box of frame.querySelectorAll(".face-box")) {
    const percent = (value, size) => `${(Number(value) / size) * 100}%`;
    box.style.left = percent(box.dataset.x, image.naturalWidth);
    box.style.top = percent(box.dataset.y, image.naturalHeight);
    box.style.width = percent(box.dataset.width, image.naturalWidth);
    box.style.height = percent(box.dataset.height, image.naturalHeight);
    box.hidden = false;
  }
}

for (const frame of document.querySelectorAll(".frame")) {
  const image = frame.querySelector("img");
  if (image.complete) {
    placeBoxes(frame);
  } else {
    image.addEventListener("load", () => placeBoxes(frame));
  }
}

// Pointing at a possible match lights up the face of this notice it belongs to.
for (const row of document.querySelectorAll("tr[data-face-id]")) {
  const boxes = document.querySelectorAll(`.face-box[data-face-id="${row.dataset.faceId}"]`);
  row.addEventListener("mouseenter", () => boxes.forEach((box) => box.classList.add("lit")));
  row.addEventListener("mouseleave", () => boxes.forEach((box) => box.classList.remove("lit")));
}
