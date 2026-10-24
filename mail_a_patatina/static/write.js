// The journal editor: an entry is a list of blocks, text or a row of photos. Photos are picked from Immich
// in a full-screen picker. Saving sends the whole entry as JSON; until it's saved, what's typed is also kept
// in this browser, so a lost connection or an expired sign-in never costs anyone their text.
(() => {
  const editor = document.getElementById("editor");
  if (!editor) return;
  const data = JSON.parse(document.getElementById("entry-data").textContent);
  const T = data.texts;
  const say = (text, values = {}) => (T[text] || text).replace(/\{(\w+)\}/g, (_, k) => values[k]);
  const $ = (id) => document.getElementById(id);
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children.filter((c) => c != null));
    return node;
  };
  // Drawn, not typed: symbols like ✕ and ✓ are missing from some phones' fonts.
  const PATHS = {
    remove: '<path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
    play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>',
  };
  const icon = (name) => {
    const span = el("span", { className: `icon icon-${name}` });
    span.innerHTML = `<svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">${PATHS[name]}</svg>`;
    return span;
  };

  const entry = data.entry;
  let blocks = entry.blocks;
  let dirty = false;
  let saving = false;
  const fields = { title: $("title"), date: $("date"), place: $("place") };
  const storageKey = () => data.storage + (entry.slug || "new");

  // --- keeping a copy on this device -------------------------------------------------

  const store = {
    get() {
      try { return JSON.parse(localStorage.getItem(storageKey()) || "null"); } catch { return null; }
    },
    set(value) {
      try { localStorage.setItem(storageKey(), JSON.stringify(value)); } catch { /* private mode: no copy */ }
    },
    clear(key = storageKey()) {
      try { localStorage.removeItem(key); } catch { /* nothing to clear */ }
    },
  };

  const snapshot = () => ({
    title: fields.title.value, date: fields.date.value, place: fields.place.value, blocks,
  });

  let keepTimer = null;
  function changed() {
    dirty = true;
    status(say("Not saved yet"));
    clearTimeout(keepTimer);
    keepTimer = setTimeout(() => store.set({ version: entry.version, ...snapshot() }), 400);
  }

  function apply(copy) {
    fields.title.value = copy.title || "";
    fields.date.value = copy.date || fields.date.value;
    fields.place.value = copy.place || "";
    blocks = copy.blocks || blocks;
    render();
  }

  const kept = store.get();
  if (kept) {
    const notice = $("restore");
    if (kept.version === entry.version) {
      apply(kept);
      dirty = true;
      notice.textContent = say("Restored what you wrote on this device before.");
      notice.hidden = false;
    } else {
      // The entry was saved from somewhere else since: offer what's on this device, don't impose it.
      const button = el("button", { type: "button", className: "secondary", textContent: say("Restore what I wrote") });
      button.addEventListener("click", () => { apply(kept); changed(); notice.hidden = true; });
      notice.append(say("You wrote more on this device than was saved."), " ", button);
      notice.hidden = false;
    }
  }

  // --- the blocks ---------------------------------------------------------------------

  function grow(area) {
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight + 2}px`;
  }

  function tools(i) {
    const tool = (label, text, action, disabled) => {
      const b = el("button", { type: "button", className: "tool", title: say(text), disabled }, label);
      b.setAttribute("aria-label", say(text));
      b.addEventListener("click", () => { action(); changed(); render(); });
      return b;
    };
    return el("div", { className: "block-tools" },
      tool("↑", "Move up", () => blocks.splice(i - 1, 0, blocks.splice(i, 1)[0]), i === 0),
      tool("↓", "Move down", () => blocks.splice(i + 1, 0, blocks.splice(i, 1)[0]), i === blocks.length - 1),
      tool(icon("remove"), "Remove", () => blocks.splice(i, 1)));
  }

  function thumb(photo, onRemove) {
    const caption = el("input", { value: photo.caption || "", placeholder: say("Caption"), maxLength: 300 });
    caption.addEventListener("input", () => { photo.caption = caption.value; changed(); });
    const remove = el("button", { type: "button", className: "tool remove", title: say("Remove") }, icon("remove"));
    remove.setAttribute("aria-label", say("Remove"));
    remove.addEventListener("click", onRemove);
    return el("div", { className: "thumb" },
      el("div", { className: "thumb-image" },
        el("img", { src: `${data.urls.thumb}/${photo.id}`, alt: photo.caption || "", loading: "lazy" }), remove),
      caption);
  }

  function render() {
    const list = $("blocks");
    list.replaceChildren();
    blocks.forEach((block, i) => {
      if (block.kind === "text") {
        const area = el("textarea", { value: block.text, placeholder: say("Write here…"), rows: 4 });
        area.addEventListener("input", () => { block.text = area.value; grow(area); changed(); });
        list.append(el("div", { className: "block text-block" }, area, tools(i)));
        requestAnimationFrame(() => grow(area));
      } else {
        const grid = el("div", { className: "thumbs" });
        block.photos.forEach((photo, j) => grid.append(thumb(photo, () => {
          block.photos.splice(j, 1);
          if (!block.photos.length) blocks.splice(i, 1);
          changed();
          render();
        })));
        const more = el("button", { type: "button", className: "link", textContent: `+ ${say("Add more photos here")}` });
        more.addEventListener("click", () => picker.open(block));
        list.append(el("div", { className: "block photo-block" }, grid, more, tools(i)));
      }
    });
  }

  $("add-text").addEventListener("click", () => {
    blocks.push({ kind: "text", text: "" });
    changed();
    render();
    const areas = $("blocks").querySelectorAll("textarea");
    areas[areas.length - 1].focus();
  });
  $("add-photos").addEventListener("click", () => picker.open(null));
  for (const field of Object.values(fields)) field.addEventListener("input", changed);

  // --- the photo picker ----------------------------------------------------------------

  const picker = (() => {
    const box = $("picker");
    const grid = $("picker-grid");
    const day = $("picker-day");
    const more = $("picker-more");
    const add = $("picker-add");
    const empty = $("picker-empty");
    let target = null; // the photo block to add to, or null for a new one
    let mode = "day";
    let after = "";
    const chosen = new Map(); // id -> photo
    let loading = 0;

    const inEntry = () => new Set(blocks.flatMap((b) => (b.kind === "photos" ? b.photos.map((p) => p.id) : [])));

    function count() {
      add.disabled = chosen.size === 0;
      add.textContent = say("Add photos ({n})", { n: chosen.size });
    }

    function tile(photo, used) {
      const button = el("button", { type: "button", className: "pick" + (used ? " used" : ""), disabled: used },
        el("img", { src: `${data.urls.thumb}/${photo.id}`, alt: "", loading: "lazy" }),
        photo.type === "VIDEO" ? el("span", { className: "badge" }, icon("play"), ` ${say("video")}`) : null,
        photo.time ? el("span", { className: "time", textContent: photo.time.slice(11, 16) }) : null,
        el("span", { className: "check" }, icon("check")));
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        if (chosen.has(photo.id)) chosen.delete(photo.id); else chosen.set(photo.id, photo);
        button.classList.toggle("chosen", chosen.has(photo.id));
        button.setAttribute("aria-pressed", String(chosen.has(photo.id)));
        count();
      });
      return button;
    }

    async function load(fresh) {
      const ticket = ++loading;
      if (fresh) { grid.replaceChildren(); after = ""; }
      empty.hidden = true;
      more.hidden = true;
      if (fresh) grid.append(el("p", { className: "hint", textContent: say("Loading…") }));
      const params = new URLSearchParams({ mode, day: day.value || fields.date.value, after });
      let answer;
      try {
        const r = await fetch(`${data.urls.photos}?${params}`, { credentials: "same-origin" });
        answer = await r.json();
        if (!r.ok) throw new Error(answer.error);
      } catch {
        if (ticket !== loading) return;
        grid.replaceChildren();
        empty.textContent = say("That didn't work");
        empty.hidden = false;
        return;
      }
      if (ticket !== loading) return; // the tab or day changed in the meantime
      if (fresh) grid.replaceChildren();
      const used = inEntry();
      for (const photo of answer.photos) grid.append(tile(photo, used.has(photo.id)));
      after = answer.after;
      more.hidden = !after;
      more.textContent = say("Show more");
      if (!grid.children.length) {
        empty.textContent = say(mode === "day" ? "Nothing found for this day." : "No photos here yet.");
        empty.hidden = false;
      }
    }

    function show(newMode) {
      mode = newMode;
      for (const tab of box.querySelectorAll(".tab")) {
        tab.classList.toggle("active", tab.dataset.mode === mode);
        tab.setAttribute("aria-selected", String(tab.dataset.mode === mode));
      }
      day.parentElement.hidden = mode !== "day";
      load(true);
    }

    for (const tab of box.querySelectorAll(".tab")) tab.addEventListener("click", () => show(tab.dataset.mode));
    day.addEventListener("change", () => load(true));
    more.addEventListener("click", () => load(false));
    function close() {
      box.hidden = true;
      document.body.classList.remove("picking");
    }
    $("picker-close").addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !box.hidden) close(); });

    add.addEventListener("click", () => {
      const photos = [...chosen.values()].map((p) => ({ id: p.id, caption: "", file: p.file }));
      if (target && blocks.includes(target)) target.photos.push(...photos);
      else blocks.push({ kind: "photos", photos });
      // Where the photos were taken is a good guess for where the entry is about.
      const place = [...chosen.values()].find((p) => p.place);
      if (!fields.place.value && place) fields.place.value = place.place;
      close();
      changed();
      render();
    });

    return {
      open(block) {
        target = block;
        chosen.clear();
        count();
        day.value = fields.date.value;
        box.hidden = false;
        document.body.classList.add("picking");
        show("day");
      },
    };
  })();

  // --- saving -------------------------------------------------------------------------

  function status(text, error = false) {
    const line = $("status");
    line.replaceChildren(text);
    line.classList.toggle("error", error);
  }

  function buttons() {
    $("save-primary").textContent = say(entry.draft ? "Publish" : "Save changes");
    $("save-secondary").textContent = say(entry.draft ? "Save as draft" : "Turn back into a draft");
    // Once published, going back to a draft is rare: a quiet link, not a second big button.
    $("save-secondary").className = entry.draft ? "secondary" : "link";
  }

  async function save(publish) {
    if (saving) return;
    saving = true;
    status(say("Saving…"));
    const before = storageKey();
    try {
      const r = await fetch(data.urls.save, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csrf: data.csrf, slug: entry.slug, version: entry.version, publish, ...snapshot() }),
      });
      let answer;
      try { answer = await r.json(); } catch { answer = { error: say("That didn't work") }; }
      if (answer.retry && answer.csrf !== data.csrf) {
        data.csrf = answer.csrf; // signed in again meanwhile, in another tab
        saving = false;
        return save(publish);
      }
      if (!r.ok || answer.error) {
        status(answer.error || say("That didn't work"), true);
        if (answer.signed_out) {
          store.set({ version: entry.version, ...snapshot() });
          $("status").append(" ", el("a", { href: data.urls.signin, target: "_blank", textContent: say("Sign in again") }));
        }
        return;
      }
      entry.slug = answer.slug;
      entry.version = answer.version;
      entry.draft = !answer.published;
      store.clear(before);
      store.clear();
      dirty = false;
      $("restore").hidden = true;
      history.replaceState(null, "", data.urls.edit + entry.slug);
      buttons();
      status(say(answer.published ? "Published" : "Saved as a draft"));
    } catch {
      store.set({ version: entry.version, ...snapshot() });
      status(say("That didn't work"), true);
    } finally {
      saving = false;
    }
  }

  $("save-primary").addEventListener("click", () => save(true));
  $("save-secondary").addEventListener("click", () => save(false));
  window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });

  buttons();
  render();
})();
