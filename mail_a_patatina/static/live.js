// Live photos play like in Immich: while the mouse rests on them, or while a finger holds them.
for (const box of document.querySelectorAll(".live")) {
  const video = box.querySelector("video");
  let timer = null;
  let held = false;

  const play = () => {
    if (box.closest(".zooming")) return;  // looking through the loupe at the still
    video.play().then(() => box.classList.add("playing")).catch(() => {});
  };
  const stop = () => {
    video.pause();
    video.currentTime = 0;
    box.classList.remove("playing");
  };

  box.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") play(); });
  box.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") stop(); });

  box.addEventListener("touchstart", () => {
    held = false;
    timer = setTimeout(() => { held = true; play(); }, 250);
  }, { passive: true });
  const release = () => {
    clearTimeout(timer);
    if (held) stop();
  };
  box.addEventListener("touchend", release);
  box.addEventListener("touchcancel", release);
  box.addEventListener("touchmove", () => clearTimeout(timer), { passive: true });  // scrolling, not holding

  // Holding plays the photo; it shouldn't also open it or bring up the long-press menu.
  box.addEventListener("click", (e) => {
    if (held) {
      e.preventDefault();
      held = false;
    }
  });
  box.addEventListener("contextmenu", (e) => e.preventDefault());
}

// Families that keep their photos on the server: no "Save video as…" on right-click. (Images already
// let clicks through to the link or page behind them, so their menu has no "Save image".)
if (document.body.classList.contains("protect")) {
  document.addEventListener("contextmenu", (e) => { if (e.target.closest("video")) e.preventDefault(); });
}

// A loupe for looking closer at a photo: a click or tap on it turns it on, another turns it off. A mouse moves it
// around; a finger drags it, held a little above the finger so it doesn't cover the spot. It shows the same
// picture the page does, so there's nothing new to save.
for (const figure of document.querySelectorAll(".zoomable")) {
  const img = figure.querySelector("img");
  const loupe = document.createElement("div");
  loupe.className = "loupe";
  loupe.hidden = true;
  document.body.append(loupe);
  let on = false;
  let down = null;   // where a finger or pen touched, so a drag doesn't also count as a tap
  let dragged = false;

  const toggle = (value) => {
    on = value;
    figure.classList.toggle("zooming", on);
    if (!on) loupe.hidden = true;
  };
  const show = (e) => {
    const r = img.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    if (x < 0 || y < 0 || x > r.width || y > r.height) {
      loupe.hidden = true;
      return;
    }
    // At least the picture's own sharpness, and never less than 2.5 times what's on screen.
    const zoom = Math.max(2.5, img.naturalWidth / r.width || 0);
    const size = loupe.offsetWidth || 260;
    const lift = e.pointerType === "mouse" ? 0 : size * 0.7;
    loupe.style.backgroundImage = `url("${img.currentSrc || img.src}")`;
    loupe.style.backgroundSize = `${r.width * zoom}px ${r.height * zoom}px`;
    loupe.style.backgroundPosition = `${size / 2 - x * zoom}px ${size / 2 - y * zoom}px`;
    loupe.style.left = `${e.clientX - size / 2}px`;
    loupe.style.top = `${e.clientY - size / 2 - lift}px`;
    loupe.hidden = false;
  };

  figure.addEventListener("click", (e) => {
    if (e.defaultPrevented || dragged) return;  // a held live photo or a loupe drag, not a tap
    toggle(!on);
    if (on && e.pointerType === "mouse") show(e);
  });
  figure.addEventListener("pointerdown", (e) => {
    down = [e.clientX, e.clientY];
    dragged = false;
    if (on && e.pointerType !== "mouse") show(e);
  });
  figure.addEventListener("pointermove", (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 8) dragged = true;
    if (on && (e.pointerType === "mouse" || down)) show(e);
  });
  const up = (e) => {
    down = null;
    if (e.pointerType !== "mouse") loupe.hidden = true;
  };
  figure.addEventListener("pointerup", up);
  figure.addEventListener("pointercancel", up);
  figure.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") loupe.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && on) toggle(false); });
  window.addEventListener("scroll", () => { if (on) loupe.hidden = true; }, { passive: true });
}
