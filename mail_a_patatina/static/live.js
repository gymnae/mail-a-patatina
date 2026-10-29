// Live photos play like in Immich: while the mouse rests on them, or while a finger holds them.
for (const box of document.querySelectorAll(".live")) {
  const video = box.querySelector("video");
  let timer = null;
  let held = false;

  const play = () => {
    if (box.closest(".zooming, .zoomed")) return;  // looking closely at the still
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

// A loupe for looking closer at a photo with the mouse: a click turns it on, another turns it off. Under the mouse
// lies a small lens showing the patch the loupe shows; the loupe itself floats just above it, or beside it where
// there's no room above, and glides over when it changes sides. Both show the same picture the page does, so
// there's nothing new to save. (Fingers zoom the photo itself instead, like in a photos app: see below.)
// The page shows Immich's preview. Looking closer, with the loupe or the fingers, loads the full picture in the
// background, as Immich's own viewer does when zooming in, and swaps it in once it's ready.
const sharpen = (img) => {
  const url = img.dataset.large;
  if (!url) return;
  delete img.dataset.large;
  const large = new Image();
  large.src = url;
  large.decode().then(() => { img.src = url; }).catch(() => {});
};

for (const figure of document.querySelectorAll(".zoomable")) {
  const img = figure.querySelector("img");
  const loupe = document.createElement("div");
  const lens = document.createElement("canvas");
  loupe.className = "loupe";
  lens.className = "lens";
  loupe.hidden = lens.hidden = true;
  document.body.append(lens, loupe);
  let on = false;
  let touched = 0;   // when a finger last let go: iOS follows a touch with mouse events, which mustn't open the loupe
  let last = null;   // the latest pointer, drawn on the next frame
  let frame = 0;
  let side = null;   // where the loupe rests: "above", "left" or "right"
  let glide = 0;
  let pixels = null; // the photo's pixels for the lens, once loaded; false where the browser won't hand them out
  img.addEventListener("load", () => { pixels = null; });  // the full picture came in

  // The lens bends the photo like a glass lying on it: most magnified in the middle, bunching up towards the
  // rim, where the colours drift apart a little. Its rim shows exactly the patch the loupe shows.
  const bend = (x, y, patch) => {
    if (pixels === null) {
      if (!img.complete || !img.naturalWidth) return false;  // not loaded yet: try again next time
      try {
        // An original can have 50 million pixels; a few million are plenty under a lens.
        const fit = Math.min(1, 4096 / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement("canvas");
        c.width = Math.round(img.naturalWidth * fit);
        c.height = Math.round(img.naturalHeight * fit);
        const g = c.getContext("2d", { willReadFrequently: true });
        g.drawImage(img, 0, 0, c.width, c.height);
        pixels = { data: g.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height };
      } catch {
        pixels = false;
      }
    }
    if (!pixels) return false;
    const { data, w, h } = pixels;
    const n = lens.width;
    const g = lens.getContext("2d");
    const out = g.createImageData(n, n);
    const o = out.data;
    const k = w / img.getBoundingClientRect().width;  // photo pixels per pixel on screen
    const cx = x * k;
    const cy = y * k;
    const reach = (patch / 2) * k;
    const half = n / 2;
    const sample = (sx, sy, channel) => {
      sx = Math.min(Math.max(sx, 0), w - 1.001);
      sy = Math.min(Math.max(sy, 0), h - 1.001);
      const x0 = sx | 0;
      const y0 = sy | 0;
      const fx = sx - x0;
      const fy = sy - y0;
      const i = (y0 * w + x0) * 4 + channel;
      const top = data[i] + (data[i + 4] - data[i]) * fx;
      const bottom = data[i + w * 4] + (data[i + w * 4 + 4] - data[i + w * 4]) * fx;
      return top + (bottom - top) * fy;
    };
    for (let j = 0; j < n; j++) {
      const dy = (j + 0.5 - half) / half;
      for (let i = 0; i < n; i++) {
        const dx = (i + 0.5 - half) / half;
        const rr = dx * dx + dy * dy;
        if (rr > 1) continue;
        const f = (0.4 + 0.6 * rr) * reach;   // two and a half times the magnification in the middle as at the rim
        const fringe = 0.05 * rr * rr;         // red outward, blue inward, only near the rim
        const shade = rr > 0.7 ? 1 - (rr - 0.7) * 0.6 : 1;
        const p = (j * n + i) * 4;
        o[p] = sample(cx + dx * f * (1 + fringe), cy + dy * f * (1 + fringe), 0) * shade;
        o[p + 1] = sample(cx + dx * f, cy + dy * f, 1) * shade;
        o[p + 2] = sample(cx + dx * f * (1 - fringe), cy + dy * f * (1 - fringe), 2) * shade;
        o[p + 3] = Math.min(1, (1 - Math.sqrt(rr)) * half) * 255;  // a smooth round edge
      }
    }
    g.putImageData(out, 0, 0);
    return true;
  };

  const toggle = (value) => {
    on = value;
    figure.classList.toggle("zooming", on);
    if (on) sharpen(img);
    else hide();
  };
  const draw = () => {
    frame = 0;
    const r = img.getBoundingClientRect();
    const x = last.x - r.left;
    const y = last.y - r.top;
    if (x < 0 || y < 0 || x > r.width || y > r.height) return hide();
    const src = `url("${img.currentSrc || img.src}")`;
    const appearing = loupe.hidden;
    loupe.hidden = lens.hidden = false;

    // The loupe: three times what's on screen, the same before and after the full picture comes in.
    const zoom = 3;
    const size = loupe.offsetWidth;
    loupe.style.backgroundImage = src;
    loupe.style.backgroundSize = `${r.width * zoom}px ${r.height * zoom}px`;
    loupe.style.backgroundPosition = `${size / 2 - x * zoom}px ${size / 2 - y * zoom}px`;

    // The lens: shows what the loupe shows, a little smaller than that patch.
    const patch = size / zoom;
    const d = Math.max(patch * 0.8, 56);
    const n = Math.round(d * Math.min(devicePixelRatio || 1, 2));
    lens.style.width = lens.style.height = `${d}px`;
    if (lens.width !== n) lens.width = lens.height = n;
    if (!bend(x, y, patch)) {
      // No pixels to bend (the browser wouldn't share them): the patch, plainly magnified.
      lens.getContext("2d").clearRect(0, 0, n, n);
      lens.style.backgroundImage = src;
      lens.style.backgroundSize = `${(r.width * d) / patch}px ${(r.height * d) / patch}px`;
      lens.style.backgroundPosition = `${d / 2 - (x * d) / patch}px ${d / 2 - (y * d) / patch}px`;
    }
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
      glide = setTimeout(() => loupe.classList.remove("gliding"), 500);
    }
    side = now;
    // Placed on the page, not the screen: iOS moves fixed things around as its toolbars come and go.
    loupe.style.transform = `translate3d(${left + scrollX}px, ${top + scrollY}px, 0)`;
  };
  const show = (e) => {
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
    // A held live photo, or any tap: fingers zoom the photo itself.
    if (e.defaultPrevented || Date.now() - touched < 1000) return;
    toggle(!on);
    if (on) show(e);
  });
  figure.addEventListener("touchend", () => { touched = Date.now(); });
  figure.addEventListener("pointermove", (e) => { if (on && e.pointerType === "mouse") show(e); });
  figure.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") hide(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && on) toggle(false); });
  window.addEventListener("scroll", () => { if (on) hide(); }, { passive: true });
}

// Fingers zoom the photo in place, like in a photos app: a double tap zooms in on that spot and back out, two
// fingers pinch, and one finger moves the zoomed photo around. At normal size a finger scrolls the page as usual,
// and holding a live photo still plays it.
for (const figure of document.querySelectorAll(".zoomable")) {
  const photo = figure.firstElementChild;  // the image, or the live photo's box around it
  const img = figure.querySelector("img");
  let scale = 1;
  let tx = 0;
  let ty = 0;
  let start = null;  // the zoom and fingers when the current gesture began
  let tap = null;    // the last quick tap, to tell a double tap
  let moved = false;

  const set = (s, x, y, smooth = false) => {
    const w = photo.offsetWidth;
    const h = photo.offsetHeight;
    scale = s;
    // The photo always fills its frame: no empty space past its edges.
    tx = Math.min(0, Math.max(w - w * s, x));
    ty = Math.min(0, Math.max(h - h * s, y));
    photo.classList.toggle("settling", smooth);
    photo.style.transform = s === 1 ? "" : `translate3d(${tx}px, ${ty}px, 0) scale(${s})`;
    figure.classList.toggle("zoomed", s > 1);
    if (s > 1) sharpen(img);
  };
  // As far as photo apps go: ten times.
  const most = 10;
  // Where a finger is, on the photo as it lies unzoomed.
  const at = (t) => {
    const r = photo.getBoundingClientRect();
    return { x: t.clientX - (r.left - tx), y: t.clientY - (r.top - ty) };
  };
  const middle = (ts) => {
    const a = at(ts[0]);
    const b = ts[1] ? at(ts[1]) : a;
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, d: Math.hypot(a.x - b.x, a.y - b.y) };
  };
  const begin = (e) => {
    const m = middle(e.touches);
    start = { scale, tx, ty, n: e.touches.length, ...m };
  };

  // Safari's own pinch would zoom the whole page instead.
  figure.addEventListener("gesturestart", (e) => e.preventDefault());
  figure.addEventListener("touchstart", (e) => {
    if (e.touches.length === 1) moved = false;
    else moved = true;  // a second finger: a pinch, not a tap
    begin(e);
  }, { passive: true });
  figure.addEventListener("touchmove", (e) => {
    if (!start) return;
    // A second finger that came down beside the photo told only the page: start over from both fingers.
    if (e.touches.length !== start.n) {
      moved = true;
      begin(e);
    }
    const m = middle(e.touches);
    if (Math.hypot(m.x - start.x, m.y - start.y) > 8) moved = true;
    if (e.touches.length < 2 && scale === 1) return;  // one finger on the photo at normal size: the page scrolls
    e.preventDefault();
    let s = scale;
    if (e.touches.length >= 2 && start.d) s = Math.min(most, Math.max(1, (start.scale * m.d) / start.d));
    // The spot that was between the fingers stays between them.
    const ux = (start.x - start.tx) / start.scale;
    const uy = (start.y - start.ty) / start.scale;
    set(s, m.x - ux * s, m.y - uy * s);
  }, { passive: false });
  figure.addEventListener("touchend", (e) => {
    if (e.touches.length) return begin(e);  // one of two fingers lifted: carry on with the other
    start = null;
    if (moved || e.changedTouches.length !== 1) return (tap = null);
    const t = e.changedTouches[0];
    const now = Date.now();
    if (tap && now - tap.time < 350 && Math.hypot(t.clientX - tap.x, t.clientY - tap.y) < 40) {
      tap = null;
      e.preventDefault();  // no click after it
      if (scale > 1) return set(1, 0, 0, true);
      const p = at(t);
      const s = 3;
      return set(s, p.x * (1 - s), p.y * (1 - s), true);
    }
    tap = { time: now, x: t.clientX, y: t.clientY };
  });
  figure.addEventListener("touchcancel", () => { start = null; });
  photo.addEventListener("transitionend", () => photo.classList.remove("settling"));
  // A new size of the page (turning the phone) starts over at normal size.
  window.addEventListener("resize", () => { if (scale > 1) set(1, 0, 0); });
}
