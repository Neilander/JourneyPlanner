# Analyze UI — turn the user's UI screenshot into layout.json + assets.json

You are looking at one phone-app UI screenshot the user supplied. Produce two JSON files in `output/<name>/`:

1. `layout.json` — the structure (sections, their order, what's inside each).
2. `assets.json` — the per-image asset list with the per-asset prompt body that goes to Gemini.

These two together fully drive Stage 4 (HTML scaffold) and Stage 3 (image generation).

---

## Required input: the user's feature description

**Before you analyze anything, check whether the user gave you a written feature description of the app** (in their chat message, in a `.txt`/`.md` file alongside the screenshot, or pasted in plain text).

A screenshot alone is **not enough** — vision models read pixels but routinely misjudge:
- What an icon represents (search vs filter vs sort)
- What a card category means (recommendations vs history vs trending)
- What text says (especially small / stylized / Chinese characters)
- Which elements are interactive vs decorative
- Hierarchy and intent (is this the home screen? a detail page?)

The description fixes all of that.

### What to do if the description is missing

**Stop and ask.** Don't guess. Send the user a short, single message asking for it. Use roughly this template:

> 跑之前给我描述一下这个 app 的功能，方便我准确解析。简短就行，比如：
> 1. 这是什么 app（一句话）
> 2. 屏幕上从上到下每个区块是干嘛的（用屏幕里出现的真实文案/分类名）
> 3. 哪些元素能交互（按钮、卡片、tab、可拖拽的东西）
> 4. 有没有需要保留的特定文案/标签
>
> （这个对话里直接回我就行，我会接着把剩下的步骤跑完。）

After you ask:
- Wait for the user's reply in the same conversation.
- When the reply comes in, **resume from this same Step 3** — write `layout.json` and `assets.json` using the new info, then continue forward to Step 4 (Generate the painted assets) and Step 5 (Write the HTML).
- **Do NOT restart from Step 1.** Steps 1 and 2 are already done; the `style-preamble.txt` and your read of the reference HTML are still valid.
- **Do NOT stop after writing the JSON files** thinking the user might have more to add. Treat the description reply as the green light to run the whole rest of the pipeline end-to-end.
- **Do NOT respond conversationally without acting.** "Got it, thanks!" is a fail — the user expects you to keep building.

Don't fabricate plausible-sounding section names — you'll be wrong and it'll show.

### How to use the description once you have it

Treat it as **ground truth** that overrides what the screenshot seems to show:
- All `title`, `label`, `placeholder`, `meta` strings in `layout.json` should come from the description verbatim where the user named things, not your guesses.
- Section ordering follows the description's top-to-bottom narration even if you misread the visual hierarchy.
- If the description says "这是 tab 栏" about something you assumed was a card row, trust the description.
- If the description and screenshot conflict, **note the conflict in your response** and ask which to follow — don't silently pick one.

---

## The two hardest judgments

Get these right before anything else; they determine whether the output makes sense.

### Judgment A — UI control vs painted illustration

A phone screenshot has two completely different kinds of stuff overlaid:

| **UI controls** (live in HTML) | **Painted illustration** (lives in a generated image) |
|---|---|
| Search bar, buttons, tab bar | The hero painting, card backgrounds, mascot |
| Status bar, time, battery icon | A small house drawn into the map |
| Text labels (titles, captions) | Painted characters / animals |
| POI pins, badges, chips | Background scenes |

Rule of thumb: **if it carries text the user could meaningfully change, it's a UI control.** Render it via HTML/CSS, never bake it into an image. If you bake "搜索景点..." into the hero PNG, it can never be retranslated and Gemini will probably misspell it anyway.

### Judgment B — what becomes a generated image asset

Generate an image asset only when **the painted look is what gives that element value**. Concrete tests:

- **Hero / cover art** → asset (the painting IS the content).
- **Map base layer** → asset, but with **no text/labels/numbers** in the prompt (street names go on as CSS overlays).
- **Icons that fit the painted style** (mood emoji, tool tiles) → asset.
- **Card thumbnails** → asset.
- **Mascot / decoration character** → asset, marked `post_process: flood-fill-bg`.
- **Tab-bar icons, settings gears, generic chevrons, status-bar glyphs** → **not assets**. Use inline SVG or a Material/SF-symbol-style glyph in CSS. Generating these via Gemini is wasteful and gives ugly results.
- **Avatar** → asset if the user is meant to be a character in the painted world; otherwise a CSS circle is fine.

When in doubt, fewer assets is better. Each asset costs one Gemini call (~5–15s) and is one more thing that can fail.

---

## Layer model — the most important judgment

**Phone UIs are layered, not flat.** Don't think of the screenshot as a vertical stack of sections like a webpage. Think of it as **5 stacked layers** rendered into a fixed 390×844 phone frame:

```
            ┌──────────────────────┐
   z=100 →  │  topbar              │ status bar, app header (always on top)
   z=80  →  │  sheet OR bottombar  │ bottom drag-up sheet, OR fixed tab/tool bar
   z=60  →  │  secondlayer         │ user-triggered modal panels (detail cards, dropdowns)
   z=40  →  │  floating            │ small always-on widgets (progress card, FAB)
   z=0   →  │  background          │ the canvas: map, hero image, OR a scrollable feed
            └──────────────────────┘
```

Your `layout.json` has one slot per layer. You decide which slot each visible thing belongs in.

### Step 0 — detect the screen `mode`

Look at the bottom of the screenshot first. The bottom decides everything:

| What's at the bottom | Mode | Background behavior |
|---|---|---|
| Fixed tab bar / tool bar (icons + labels, doesn't move) | **`feed`** | The middle is a **scrollable list of sections** between topbar and bottombar |
| Bottom sheet with a drag handle (curved top, draggable) | **`canvas`** | The background **fills the whole space** under topbar; sheet overlays at bottom |
| Nothing (full-bleed) | **`canvas`** | Background fills, no fixed bottom |

Examples:
- Instagram feed → `feed` mode (top header + scrollable posts + bottom tab bar)
- Apple Maps → `canvas` mode (map fills + bottom sheet)
- A photo viewer / game / canvas tool → `canvas` mode
- Settings page → `feed` mode

Get this right first — every other decision flows from it.

---

## layout.json schema

```json
{
  "meta": {
    "name": "<project name>",
    "device": "iphone-portrait",
    "frame_width": 390,
    "frame_height": 844,
    "summary": "<one sentence describing what the screen does>"
  },
  "palette": {
    "base": "#F3EAD3",
    "surface": "#F8F0DB",
    "primary": "#7BA142",
    "accent": ["#E09A5A", "#6FA2BD"],
    "ink": "#3A2E1D",
    "ink_mid": "#7A6545"
  },
  "fonts": {
    "display": "<Google Font name for headings>",
    "body":    "<Google Font name for body>"
  },

  "screen": {
    "mode": "canvas",   // "canvas" or "feed" — see Step 0 above

    // ── Always present ──
    "topbar": [
      { "type": "status-bar", "time": "9:41" },
      { "type": "header", "transparent": true,
        "content": { "title": "<title>", "right_icons": [ {"id":"help"}, {"id":"settings"} ] } }
    ],

    // ── canvas mode only — REMOVE this key in feed mode ──
    "background": {
      "type": "map-area",                // or "image", "color", "hero", "hero-illustration"
      "base_image_ref": "<asset id>",

      // REQUIRED: where does the base asset actually sit inside the slot?
      // Without this, overlay positions are guesses. See "Coordinate systems" below.
      //
      // You declare ONLY ONE dimension (width or height); the other is auto-derived
      // from the base asset's `aspect` in assets.json. This is intentional — declaring
      // both would let you write a base-frame shape that doesn't match the asset.
      "base_bounds": {
        "anchor": "center",              // "center" | "top-left" | "top-center" | "bottom-center" | etc.
        "fit": "width",                  // "width" or "height" — which dimension you're constraining
        "size_pct": 65,                  // % of slot in the constrained dimension
        "offset_x_pct": 0,               // shift from anchor in % of slot
        "offset_y_pct": 5                // e.g. push down a bit to clear top floating cards
        // height (or width) is derived: derived_dim = size_pct × (asset.aspect)
      },

      "overlay": [                       // text labels / image overlays / drop-zones placed on/around base
        // Each overlay has TWO coordinate-system options:

        // (a) anchor_to: "base"  — DEFAULT, RECOMMENDED for things glued to the base scene.
        //   x_pct / y_pct are % of base_bounds. <0 or >100 means OUTSIDE the base
        //   (e.g. a cat sitting next to the machine on the cream floor).
        //   width_pct_of_base sizes the overlay relative to base width.
        { "kind": "image-overlay", "image_ref": "<asset id>",
          "anchor_to": "base",
          "x_pct": 50, "y_pct": 8,        // 50% across base, 8% from top of base
          "width_pct_of_base": 40 },

        // (b) anchor_to: "slot"  — for things tied to slot edges, not the base
        //   (rare; usually use floating[] for that).
        { "kind": "label", "text": "...",
          "anchor_to": "slot",
          "x_pct": 50, "y_pct": 30 }      // 50% / 30% of slot
      ]
    },

    // ── feed mode only — REMOVE this key in canvas mode ──
    "content": [
      { "type": "hero", "image_ref": "hero", "height": 320, "overlay": [ ... ] },
      { "type": "icon-row", "title": "<title>", "items": [ ... ] },
      { "type": "horizontal-list", "title": "<title>", "items": [ ... ] },
      { "type": "vertical-list", "title": "<title>", "items": [ ... ] }
    ],

    // ── Optional: small floating widgets (always on screen) ──
    "floating": [
      { "type": "progress-card",
        "anchor": "bottom-above-sheet",  // or "top-right", "bottom-right", "center", etc.
        "offset_bottom": 240,            // px from bottom of phone (or from top of sheet)
        "z": 40,
        "content": { "title": "...", "subtitle": "..." } }
    ],

    // ── Optional: user-triggered modal panels (detail cards, dropdowns, popovers) ──
    "secondlayer": [
      { "type": "detail-card",
        "trigger": "tap landmark",       // describe how the user opens it
        "anchor": "center",              // where it appears
        "size": { "width_pct": 80, "height_pct": 60 },
        "z": 60,
        "default_visible": false,        // wireframe shows it as a labeled placeholder
        "content": { ... } }
    ],

    // ── canvas mode: bottom sheet (mutually exclusive with bottombar) ──
    "sheet": {
      "type": "bottom-sheet",
      "expanded_height": 220,
      "drag_handle": true,
      "expandable": true,
      "z": 80,
      "content": [
        { "kind": "horizontal-list", "title": "<title>", "items": [ ... ] },
        { "kind": "action-bar", "items": [ ... ] }
      ]
    },

    // ── feed mode: bottom tab bar (mutually exclusive with sheet) ──
    "bottombar": {
      "type": "tab-bar",
      "z": 80,
      "items": [ {"label":"<tab label>", "icon":"<icon name>", "active":true} ]
    }
  }
}
```

### Why `base_bounds` only takes ONE dimension

The base asset has a fixed aspect ratio (declared in `assets.json`). If you also declared both `width_pct` AND `height_pct` for the base-frame, those two values could disagree with the asset's aspect — and silently break the layout (the rendered image gets stretched or letterboxed).

So `base_bounds` deliberately takes only **one** of `fit: "width"` or `fit: "height"` plus `size_pct`. The other dimension is computed from the asset's aspect:

- `fit: "width"`, `size_pct: 55`, asset aspect `3:4` (3 wide, 4 tall):
  → width = 55% × slot_width = 215px (when slot is 390 wide)
  → height = 215 × (4/3) = 287px = 40% of a 720-tall slot
- `fit: "height"`, `size_pct: 50`, asset aspect `3:4`:
  → height = 50% × slot_height = 360px
  → width = 360 × (3/4) = 270px = 69% of slot_width

**Rule of thumb**: use `fit: "width"` for portrait-aspect heroes (3:4, 9:16) on a portrait phone. Use `fit: "height"` only when the constrained dimension is height (rare).

### Coordinate systems — read this before placing any overlay

A phone screen has THREE coordinate systems. Confusing them is the #1 source of misplaced overlays.

| Coordinate system | What it is | Used by |
|---|---|---|
| **slot** | The rectangle of the `background` slot itself (e.g. 390 × 720 in the phone after topbar/bottombar/sheet are removed) | `floating[]`, `secondlayer[]`, fallback for overlays with `anchor_to: "slot"` |
| **base** | The rectangle the `base_image_ref` actually occupies inside the slot — defined by `base_bounds`. The base typically does NOT fill the slot; there's cream / margin around it | DEFAULT for `background.overlay` items (`anchor_to: "base"`) |
| **canvas** | The pixel canvas of the generated PNG itself | not used in layout.json — only matters at image-generation time |

When you read positions off the **screenshot**, you're measuring them in slot coordinates (the visible phone screen). When you split a hero into `base + overlays`, you must **re-ground those positions to the base's coordinate system**, because the generated `base.png` will be placed inside the slot at `base_bounds`, not filling it.

Always declare `base_bounds` so this mapping is explicit. The wireframe will render the base inside its declared bounds, with overlays positioned relative to that — so you can see whether your numbers match reality before any image is generated.

### Slot usage rules

| Slot | Purpose | Required? | Cardinality |
|---|---|---|---|
| `topbar` | Fixed top: status bar + (optional) app header | Yes | array, ordered top→down |
| `background` | The main canvas (map/hero/image/color) | Required in `canvas` mode, omit in `feed` | single object |
| `content` | Scrollable section list (the "feed") | Required in `feed` mode, omit in `canvas` | array, ordered top→down |
| `secondlayer` | User-triggered modal-ish overlays | No | array; empty if none |
| `floating` | Always-on small widgets over the canvas | No | array; empty if none |
| `sheet` | Bottom drag-up sheet | No, only `canvas` mode | single object or null |
| `bottombar` | Fixed bottom tab/tool bar | No, only `feed` mode | single object or null |

**Mutually exclusive:** `sheet` and `bottombar` — pick at most one. If both seem present, the one with a drag handle is `sheet`; the one with tab icons is `bottombar`.

**Mutually exclusive:** `background` and `content` — pick exactly one based on `mode`.

### Section / element type catalog

Use these as the `type` value. Extend if needed, but pick from these first.

**topbar:** `status-bar`, `header`
**background (canvas mode):** `map-area`, `image`, `color`, `hero`, `webcam`, `canvas`
**content (feed mode):** `hero`, `icon-row`, `horizontal-list`, `vertical-list`, `mood-row`, `card-grid`, `text-block`, `divider`
**floating:** `progress-card`, `fab`, `tooltip`, `chip-row`, `mini-card`
**secondlayer:** `detail-card`, `dropdown`, `popover`, `modal`, `confirm-dialog`
**sheet:** `bottom-sheet` (always — the inner `content[]` uses element kinds: `horizontal-list`, `vertical-list`, `action-bar`, `text-block`, `chip-row`, `section`)
**bottombar:** `tab-bar`, `tool-bar`

### Anchors (for `floating` and `secondlayer`)

Use one of: `top`, `top-left`, `top-right`, `top-center`, `right`, `bottom-right`, `bottom`, `bottom-center`, `bottom-left`, `left`, `center`, `bottom-above-sheet`. Add `offset_top` / `offset_bottom` / `offset_left` / `offset_right` in pixels for fine positioning.

`*_ref` fields point to an `id` in `assets.json`.

`palette` and `fonts` should reflect what you *see* in the user's screenshot, not your default. Sample 4–6 colors that actually appear. Pick fonts from Google Fonts that approximate the look (round handwritten, serif, mono, Chinese: ZCOOL KuaiLe / Ma Shan Zheng / LXGW WenKai Screen).

---

## assets.json schema

```json
{
  "assets": [
    {
      "id": "hero",
      "filename": "hero.png",
      "aspect": "3:4",
      "role": "scene",
      "prompt": "<per-asset body — composition, subject, mood. NOT the STYLE preamble.>",
      "post_process": "none"
    },
    {
      "id": "mascot",
      "filename": "mascot.png",
      "aspect": "1:1",
      "role": "mascot",
      "prompt": "<isolated subject on plain background — see prompt rules below>",
      "post_process": "flood-fill-bg"
    }
  ]
}
```

### Per-asset prompt body — rules

- **No STYLE block here.** That's prepended automatically by `gen_image.py --style-preamble`.
- **Lead with composition**: "Portrait 3:4, hero focal subject takes lower 60%, sky upper 40%."
- **Specify what's in the frame, not what it looks like.** The look comes from the style preamble.
- **For map bases**: explicitly write `no text, no labels, no street names, no numbers` in the body — Gemini happily writes garbled Chinese into maps if you don't.
- **For mascots**: write `transparent flat cream background, subject centered with breathing room, no shadow, no scenery` so the flood-fill step in Stage 5 has a clean edge to cut.
- **For icons**: write `single icon centered on soft cream background, lots of breathing room` so they fit the colored tiles in CSS.

### `aspect` choices

Use exactly one of these strings — they're the values Gemini's `imageConfig.aspectRatio` accepts and the only ones `gen_image.py` will let through:

- `"1:1"` — icons, mood emoji, avatar, square mascots, tile contents
- `"3:4"` — portrait hero (typical phone hero on a 390-wide frame)
- `"4:3"` — landscape card thumbnails
- `"9:16"` — full-bleed phone hero
- `"16:9"` — wide banner
- `"4:5"` / `"5:4"` / `"2:3"` / `"3:2"` / `"21:9"` — also accepted, use only if a slot really needs them

**Pick the closest to the actual aspect of the slot in the UI.** The dimensions in HTML/CSS will match — if you pick `1:1` for a card slot rendered as 16:9, the image will be letterboxed or cropped poorly.

### `role` values (used by Stage 4 to decide which CSS class wraps the image)

`scene` · `icon` · `card` · `mascot` · `map` · `avatar`.

### `post_process` values

`none` (default) · `flood-fill-bg` (mascots and any icon meant to sit transparently on a colored tile).

---

## Output procedure

1. **Look at the bottom of the screenshot first.** Decide `mode`: `canvas` (sheet at bottom or nothing) vs `feed` (fixed tab/tool bar at bottom). This is Step 0 from the layer model.
2. **Identify each layer**, walking z-order from back to front:
   - Background — what fills the main area? (canvas: a map/image; feed: a scrollable section list)
   - Topbar — what's pinned at top?
   - Floating — any small widgets always on screen? (progress card, FAB, tooltip)
   - Secondlayer — any modal-ish panels that the user **triggers** to open? (detail card on landmark tap, settings dropdown). If you can't actually see them in the screenshot but they're implied by the feature description, list them with `default_visible: false`.
   - Sheet OR bottombar — what's at the bottom?
3. Fill in `meta`, `palette`, `fonts` from what you see.
4. **For `canvas` mode: declare `background.base_bounds` first.** Look at the screenshot and estimate where the painted hero (machine, map, illustration) actually sits inside the available slot — it usually does NOT fill the whole slot. Pick `fit: "width"` (most common for portrait aspect) and a reasonable `size_pct` (50-75% is typical). The other dimension is derived from the asset's `aspect`. Set `offset_y_pct` to nudge the whole scene down/up if floating UI takes the top or bottom.
5. Build the rest of `screen.{topbar, background|content, floating, secondlayer, sheet|bottombar}`. For each `background.overlay`, default `anchor_to: "base"` and express positions as `% of base_bounds` — not slot %.
6. Decide which visible elements need a generated image asset (use Judgment B above) — write `assets.json`.
7. Cross-link: every `*_ref` in `layout.json` must match an `id` in `assets.json`. Validate before writing.
8. Save both to `output/<name>/`.

Output strict JSON. No markdown fences, no commentary. The agent will read these files programmatically.
