# Scaffold HTML — turn layout.json + assets.json into a single index.html

Inputs you have on disk:
- `output/<name>/layout.json` — structure
- `output/<name>/assets.json` — assets (already generated as PNGs in `output/<name>/assets/`)
- `references/forest-journal.html` — the gold-standard reference for what a finished output should look like

Output: one self-contained `output/<name>/index.html` that opens in a browser and looks like the user's UI screenshot, but painted in the user's style.

---

## Use the `frontend-design` skill if available

This step is the quality bottleneck. If your environment has the `frontend-design` skill installed, invoke it — it knows the polish moves (spring curves, spacing rhythm, type pairing, micro-interactions) that turn a structurally-correct page into one that looks shippable.

When invoking, hand it:
- `layout.json` and `assets.json` (the structural plan)
- `references/forest-journal.html` (the visual quality bar — DO NOT let it deviate from this aesthetic)
- This file (`scaffold-html.md`) for the hard constraints below

**Important boundary:** the `frontend-design` skill is good at design but its default impulse is to be creative. We don't want creative — we want faithful replication. Tell it explicitly: "Match the reference's painted/handcrafted aesthetic; don't apply your own design language."

If the skill isn't available, write the HTML yourself following the rest of this document. The output will work, just with weaker design polish.

---

## Hard constraints

1. **Single file.** Inline all CSS in a `<style>` block in `<head>`. Inline all JS in a `<script>` block before `</body>`. No external JS files. CSS may load Google Fonts via `<link>`.
2. **Phone frame fixed at 390 × 844.** Wrap everything in `<div class="phone">`. Add a `.notch` element at the top.
3. **All `<img src>` must reference real files.** Use the exact `filename` from `assets.json`, prefixed with `assets/` (e.g. `<img src="assets/hero.png">`). Don't invent assets. Don't link to placeholders.
4. **No external image URLs.** No Unsplash, no placeholder.com, no `https://...`.
5. **No JS framework.** No React, no Vue, no jQuery. Vanilla JS only — interactions are short (~50 lines).
6. **No console errors.** If you reference `assets/foo.png`, foo.png had better exist.

---

## Visual quality bar (from `references/forest-journal.html`)

Read it before writing anything. The non-obvious things that make it feel hand-crafted, in order of importance:

1. **Paper grain on the phone**. A `.phone::before` with an inline SVG `feTurbulence` filter, multiply-blended at ~38% opacity, tinted sepia. This single texture overlay is what separates "polished" from "obviously vibe-coded".
2. **Edge vignette**. A `.phone::after` radial gradient adds aged-paper warmth at the corners.
3. **Custom scroll-snap and hidden scrollbars** for `.h-scroll`. Native scrollbars destroy the painted aesthetic.
4. **Spring entry animations**. Use `cubic-bezier(.34,1.56,.64,1)` or a multi-keyframe `rise-in-spring` keyframe — never linear ease.
5. **Rotated hand-written labels**. Sticker-like elements get `transform: rotate(-3deg)` and a `Ma Shan Zheng` / handwriting font.
6. **Soft shadows with warm tint**, not pure black. `box-shadow: 0 8px 24px rgba(110, 75, 30, .15)`.
7. **Tile background colors come from `tile_color`** in the layout; map them: `peach #F3D4B8`, `pink #F5CEC8`, `yellow #EFD78C`, `green-soft #E8EFD0`, etc.
8. **Generated PNG icons sit ON colored tiles**, not as bare images. The tile gives the design structure; the painted icon gives it character.

---

## Structure to follow

```
<body>
  <div class="phone">
    <div class="notch"></div>
    <div class="scroll">
      <!-- one block per section in layout.json, in order -->
      <section class="status-bar">...</section>
      <section class="header">...</section>
      <section class="hero">
        <img src="assets/hero.png" alt="">
        <!-- text overlays as separate absolute-positioned elements -->
      </section>
      <section class="icon-row">
        <h2>...</h2>
        <div class="tiles">
          <button class="tile" style="background:var(--peach)">
            <img src="assets/tool-journal.png" alt="">
            <span>手账</span>
          </button>
          ...
        </div>
      </section>
      ...
      <nav class="tab-bar">...</nav>
    </div>
    <!-- decorations (mascot etc.) live OUTSIDE .scroll so they float over everything -->
    <div class="mascot">
      <img src="assets/mascot.png" alt="">
    </div>
  </div>
</body>
```

### Section → CSS-class mapping

| layout type | container class | notes |
|---|---|---|
| `status-bar` | `.status-bar` | flex row, time left, signal/battery glyphs right (inline SVG) |
| `header` | `.header` | flex row, optional avatar + search-bar pill |
| `hero` | `.hero` | full-bleed `<img>`, text overlays `.hero-title` etc. |
| `icon-row` | `.icon-row` → `.tiles` | grid of `.tile` buttons |
| `horizontal-list` | `.h-scroll` | scroll-snap-x, hide scrollbar |
| `vertical-list` | `.v-list` | flex column of `.card` |
| `mood-row` | `.mood-row` | flex row of circular `.mood` buttons |
| `map-area` | `.map` | absolute-positioned overlay layer for POI/labels |
| `bottom-sheet` | `.sheet` + `.drag-wrap` | drag handle + spring-up JS |
| `tab-bar` | `.tab-bar` | fixed bottom, active item highlighted |

### Decorations

| decoration kind | implementation |
|---|---|
| `draggable-mascot` | absolute `<div class="mascot">`, JS `initDraggable(el, bounds)` keeps it inside `.phone`. Add a CSS `breathe` animation (gentle scale 1 → 1.012). |

### Interactions (vanilla JS, ~50 lines total)

```js
function initDraggable(el, bounds){
  let dragging=false, sx=0, sy=0, ox=0, oy=0;
  el.addEventListener('pointerdown', e=>{
    dragging=true; el.setPointerCapture(e.pointerId);
    sx=e.clientX; sy=e.clientY;
    const r=el.getBoundingClientRect(); ox=r.left; oy=r.top;
  });
  el.addEventListener('pointermove', e=>{
    if(!dragging) return;
    const b=bounds.getBoundingClientRect();
    const x=Math.min(b.right-el.offsetWidth, Math.max(b.left, ox+e.clientX-sx));
    const y=Math.min(b.bottom-el.offsetHeight, Math.max(b.top, oy+e.clientY-sy));
    el.style.left=(x-b.left)+'px'; el.style.top=(y-b.top)+'px';
  });
  el.addEventListener('pointerup', ()=> dragging=false);
}
function initBottomSheet(sheet, handle, expandY){
  // touchstart on handle, drag → translateY, snap to 0 / expandY with spring
}
```

---

## Fonts

Look at `layout.json` `fonts.display` / `fonts.body`. Load via Google Fonts in `<head>`:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=ZCOOL+KuaiLe&family=Ma+Shan+Zheng&display=swap" rel="stylesheet">
```

For Chinese body text, prefer `LXGW WenKai Screen` (CDN: jsdelivr), it pairs with the watercolor aesthetic and falls back to system Chinese fonts gracefully.

---

## Output procedure

1. Open `references/forest-journal.html` and read it. Don't paraphrase — actually look at how `.phone::before` is built, how cards are structured, how `.h-scroll` hides scrollbars.
2. Open `layout.json` and `assets.json`.
3. Verify every `*_ref` in `layout.json` has a matching `id` in `assets.json`, and every asset's filename actually exists in `output/<name>/assets/`. If any are missing, **stop and tell the user** which ones — don't ship a broken page.
4. Write `output/<name>/index.html` directly. Don't wrap it in a markdown fence.
5. After writing, open it in a browser and verify:
   - Phone frame renders 390×844 with notch
   - All images load (no broken-image icons)
   - Sections appear in the right order
   - Mascot is draggable; sheet is pullable (if applicable)
   - DevTools console is clean

If a check fails, fix the file directly — don't ask for confirmation.

---

## Style of code

- Tabs or 2-space indent — pick one and be consistent.
- CSS custom properties for the palette (from `layout.json.palette`).
- One `<style>` block, organized with section comments matching the section order.
- One `<script>` at the end, only the interactions actually used.
