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
// around; a finger drags it, with the loupe just above the finger so it doesn't cover the spot (or, near the top
// of the screen, in the upper corner away from the finger). It shows the same picture the page does, so there's
// nothing new to save.
for (const figure of document.querySelectorAll(".zoomable")) {
  const img = figure.querySelector("img");
  const loupe = document.createElement("div");
  loupe.className = "loupe";
  loupe.hidden = true;
  document.body.append(loupe);
  let on = false;
  let down = null;   // where a finger or pen touched, so a drag doesn't also count as a tap
  let dragged = false;
  let touched = 0;   // when a finger last let go: iOS follows a touch with mouse events, which must not move the loupe
  let last = null;   // the latest pointer, drawn on the next frame
  let frame = 0;

  const toggle = (value) => {
    on = value;
    figure.classList.toggle("zooming", on);
    if (!on) hide();
  };
  const draw = () => {
    frame = 0;
    const { x: cx, y: cy, mouse } = last;
    const r = img.getBoundingClientRect();
    let x = cx - r.left;
    let y = cy - r.top;
    if (mouse && (x < 0 || y < 0 || x > r.width || y > r.height)) {
      loupe.hidden = true;
      return;
    }
    // A finger that strays past the edge keeps the loupe at the edge instead of flickering it away.
    x = Math.min(Math.max(x, 0), r.width);
    y = Math.min(Math.max(y, 0), r.height);
    // At least the picture's own sharpness, and never less than 2.5 times what's on screen.
    const zoom = Math.max(2.5, img.naturalWidth / r.width || 0);
    loupe.hidden = false;
    const size = loupe.offsetWidth;
    loupe.style.backgroundImage = `url("${img.currentSrc || img.src}")`;
    loupe.style.backgroundSize = `${r.width * zoom}px ${r.height * zoom}px`;
    loupe.style.backgroundPosition = `${size / 2 - x * zoom}px ${size / 2 - y * zoom}px`;

    // Where it goes on the screen (what's visible of it, even when pinched in), then on the page.
    const view = window.visualViewport || { offsetLeft: 0, offsetTop: 0, width: innerWidth };
    const edge = 8;
    const fx = r.left + x;
    const fy = r.top + y;
    let left = fx - size / 2;
    let top = mouse ? fy - size / 2 : fy - size - 24;
    if (!mouse && top < view.offsetTop + edge) {
      // No room above the finger: beside it, in the upper corner on the other half of the screen.
      left = fx > view.offsetLeft + view.width / 2 ? view.offsetLeft + edge : view.offsetLeft + view.width - size - edge;
      top = Math.max(fy - size / 2, view.offsetTop + edge);
    }
    // Kept inside the screen's width: hanging past the edge would widen the page and make it jump sideways.
    left = Math.min(Math.max(left, view.offsetLeft), view.offsetLeft + view.width - size);
    // Placed on the page, not the screen: iOS moves fixed things around as its toolbars come and go.
    loupe.style.transform = `translate3d(${left + scrollX}px, ${top + scrollY}px, 0)`;
  };
  const show = (e) => {
    const mouse = e.pointerType === "mouse";
    if (mouse && Date.now() - touched < 1000) return;
    last = { x: e.clientX, y: e.clientY, mouse };
    if (!frame) frame = requestAnimationFrame(draw);
  };
  const hide = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    loupe.hidden = true;
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
  // iOS doesn't always honour touch-action, so while the loupe is on, a finger on the photo never scrolls.
  figure.addEventListener("touchmove", (e) => { if (on) e.preventDefault(); }, { passive: false });
  const up = (e) => {
    down = null;
    if (e.pointerType !== "mouse") {
      touched = Date.now();
      hide();
    }
  };
  figure.addEventListener("pointerup", up);
  figure.addEventListener("pointercancel", up);
  figure.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hide(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && on) toggle(false); });
  window.addEventListener("scroll", () => { if (on && !down) hide(); }, { passive: true });
}
