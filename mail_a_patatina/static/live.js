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

// A loupe for looking closer at a photo: a click or tap on it turns it on, another turns it off. Under the mouse or
// finger sits a small lens, as big as the patch the loupe shows; the loupe itself floats just above it, or beside
// it where there's no room above, and glides over when it changes sides. Both show the same picture the page
// does, so there's nothing new to save.
for (const figure of document.querySelectorAll(".zoomable")) {
  const img = figure.querySelector("img");
  const loupe = document.createElement("div");
  const lens = document.createElement("div");
  loupe.className = "loupe";
  lens.className = "lens";
  loupe.hidden = lens.hidden = true;
  document.body.append(lens, loupe);
  let on = false;
  let down = null;   // where a finger or pen touched, so a drag doesn't also count as a tap
  let dragged = false;
  let touch = false; // whether the last pointer was a finger or pen, not a mouse
  let touched = 0;   // when a finger last let go: iOS follows a touch with mouse events, which must not move the loupe
  let last = null;   // the latest pointer, drawn on the next frame
  let frame = 0;
  let side = null;   // where the loupe rests: "above", "left" or "right"
  let glide = 0;

  const toggle = (value) => {
    on = value;
    figure.classList.toggle("zooming", on);
    if (!on) return hide();
    // A finger on the photo won't scroll while the loupe is on, so slide all of it into view first: links to the
    // comments (from emails, or after commenting) can open the page with the top of the photo above the screen.
    const r = img.getBoundingClientRect();
    const height = window.visualViewport ? visualViewport.height : innerHeight;
    if (touch && (r.top < 0 || r.bottom > height)) {
      img.scrollIntoView({ block: r.height < height ? "center" : "start", behavior: "smooth" });
    }
  };
  const draw = () => {
    frame = 0;
    const r = img.getBoundingClientRect();
    let x = last.x - r.left;
    let y = last.y - r.top;
    if (!touch && (x < 0 || y < 0 || x > r.width || y > r.height)) return hide();
    // A finger that strays past the edge keeps the loupe at the edge instead of flickering it away.
    x = Math.min(Math.max(x, 0), r.width);
    y = Math.min(Math.max(y, 0), r.height);
    const src = `url("${img.currentSrc || img.src}")`;
    const appearing = loupe.hidden;
    loupe.hidden = lens.hidden = false;

    // The loupe: at least the picture's own sharpness, and never less than 2.5 times what's on screen.
    const zoom = Math.max(2.5, img.naturalWidth / r.width || 0);
    const size = loupe.offsetWidth;
    loupe.style.backgroundImage = src;
    loupe.style.backgroundSize = `${r.width * zoom}px ${r.height * zoom}px`;
    loupe.style.backgroundPosition = `${size / 2 - x * zoom}px ${size / 2 - y * zoom}px`;

    // The lens: covers what the loupe shows, magnified a little, like a glass lying on the photo.
    const d = Math.max(size / zoom, 44);
    const bulge = 1.4;
    lens.style.width = lens.style.height = `${d}px`;
    lens.style.backgroundImage = src;
    lens.style.backgroundSize = `${r.width * bulge}px ${r.height * bulge}px`;
    lens.style.backgroundPosition = `${d / 2 - x * bulge}px ${d / 2 - y * bulge}px`;
    const fx = r.left + x;
    const fy = r.top + y;
    lens.style.transform = `translate3d(${fx - d / 2 + scrollX}px, ${fy - d / 2 + scrollY}px, 0)`;

    // Where the loupe rests, on what's visible of the screen (even when pinched in): above the lens, else beside it.
    const view = window.visualViewport || { offsetLeft: 0, offsetTop: 0, width: innerWidth, height: innerHeight };
    const edge = 8;
    const gap = d / 2 + 16;
    let left = fx - size / 2;
    let top = fy - gap - size;
    let now = "above";
    if (top < view.offsetTop + edge) {
      now = fx > view.offsetLeft + view.width / 2 ? "left" : "right";
      left = now === "left" ? fx - gap - size : fx + gap;
      top = Math.min(Math.max(fy - size / 2, view.offsetTop + edge), view.offsetTop + view.height - size - edge);
    }
    // Kept inside the screen's width: hanging past the edge would widen the page and make it jump sideways.
    left = Math.min(Math.max(left, view.offsetLeft), view.offsetLeft + view.width - size);
    // Changing sides glides there, so the eye can follow it; otherwise it keeps right up with the pointer.
    if (side && now !== side && !appearing) {
      loupe.classList.add("gliding");
      clearTimeout(glide);
      glide = setTimeout(() => loupe.classList.remove("gliding"), 300);
    }
    side = now;
    // Placed on the page, not the screen: iOS moves fixed things around as its toolbars come and go.
    loupe.style.transform = `translate3d(${left + scrollX}px, ${top + scrollY}px, 0)`;
  };
  const show = (e) => {
    if (e.pointerType === "mouse" && Date.now() - touched < 1000) return;
    touch = e.pointerType !== "mouse";
    last = { x: e.clientX, y: e.clientY };
    if (!frame) frame = requestAnimationFrame(draw);
  };
  const hide = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    side = null;
    loupe.classList.remove("gliding");
    loupe.hidden = lens.hidden = true;
  };

  figure.addEventListener("click", (e) => {
    if (e.defaultPrevented || dragged) return;  // a held live photo or a loupe drag, not a tap
    toggle(!on);
    if (on && !touch) show(e);
  });
  figure.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && Date.now() - touched < 1000) return;
    touch = e.pointerType !== "mouse";
    down = [e.clientX, e.clientY];
    dragged = false;
    if (on && touch) show(e);
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
