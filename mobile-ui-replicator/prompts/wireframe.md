# Wireframe — render layout.json + assets.json as a structural HTML preview

**Goal:** generate `output/<name>/wireframe.html` so the user can verify the structure is right BEFORE you spend 1–3 minutes on Gemini image generation. Catching a misread layer here costs nothing; catching it after asset generation costs ~10 wasted Gemini calls.

The wireframe is intentionally **ugly** — pure colored boxes, system fonts, no painted assets. If it's pretty it's wrong. The wireframe must read as **layered**, not as a long scrolling column.

---

## Hard layout rules (this is the whole point)

1. The phone frame is **`390 × 844`** with `position: relative; overflow: hidden`. Content does not push the phone taller. Ever.
2. Each layer of the screen is a separately-positioned absolute container inside `.phone`. Layers stack on z-index, not in document flow.
3. **Mode controls how the middle area behaves.** Read `screen.mode` from `layout.json`:
   - `mode === "canvas"` → the **background** slot fills `inset: <topbar-h> 0 <bottom-h> 0` and does NOT scroll. Overlays sit on top.
   - `mode === "feed"` → the **content** slot fills `inset: <topbar-h> 0 <bottom-h> 0` with `overflow-y: auto`, and its sections stack vertically inside.
4. The bottom-area height (`<bottom-h>` above) is the height of `sheet.expanded_height` (canvas mode) or the bottombar height (feed mode), or `0` if neither is present.

If you find yourself stacking everything in document flow inside `.scroll`, **stop** — you've reverted to the old broken model.

---

## Slot positioning rules

| Slot | CSS recipe |
|---|---|
| **topbar** (array) | `position: absolute; top: 0; left: 0; right: 0; z-index: 100;` Stack children top→down inside it. Compute its total height; remember it as `<topbar-h>`. |
| **background** (canvas mode) | `position: absolute; top: <topbar-h>; left: 0; right: 0; bottom: <bottom-h>; z-index: 0; overflow: hidden;` Inside, render TWO things: (1) a `.base-frame` rectangle drawn at the dimensions and offset declared by `background.base_bounds` — this represents where the painted base actually sits in the slot. The asset-slot for `base_image_ref` goes inside `.base-frame` filling it. (2) each `overlay` item: if `anchor_to === "base"` (default), position it relative to `.base-frame` using `x_pct / y_pct` of the base-frame's dimensions; if `anchor_to === "slot"`, position relative to the whole `.slot-bg` container. |
| **content** (feed mode) | `position: absolute; top: <topbar-h>; left: 0; right: 0; bottom: <bottom-h>; z-index: 0; overflow-y: auto;` Stack each section block top→down inside. |
| **floating** (array) | Each item: `position: absolute; z-index: <z||40>;` Position by `anchor` + offsets:<br>• `top` → `top: <topbar-h + offset_top>`, `left:50%; transform:translateX(-50%)` <br>• `top-right` → `top: <topbar-h + offset_top>; right: <offset_right or 16>` <br>• `bottom-above-sheet` → `bottom: <bottom-h + offset_bottom>; left:50%; transform:translateX(-50%)` <br>• `bottom-right` → `bottom: <bottom-h + offset_bottom or 16>; right: <offset_right or 16>` <br>• `center` → centered both axes. |
| **secondlayer** (array) | Same anchor rules as floating, but `z-index: <z||60>`. **Always render** as a labeled placeholder even if `default_visible: false` — see "Secondlayer placeholder" below. |
| **sheet** (canvas only) | `position: absolute; bottom: 0; left: 0; right: 0; height: <expanded_height>; z-index: <z||80>;` Render drag handle at top, then internal `content[]` items stacked. |
| **bottombar** (feed only) | `position: absolute; bottom: 0; left: 0; right: 0; height: 70px; z-index: <z||80>;` Render its items as evenly-spaced flex children. |

**Always compute `<topbar-h>` and `<bottom-h>` first**, then render everything else relative to them. Don't hard-code 60 / 220 etc — use the actual numbers (status-bar ~24px, header ~60px, sheet `expanded_height`, bottombar 70px default).

### Rendering the `.base-frame` (critical for canvas mode)

Treat `background.base_bounds` as authoritative. The base-frame's aspect MUST match the base asset's aspect — use the CSS `aspect-ratio` property and let the browser compute the other dimension.

Look up the base asset's `aspect` (e.g. `"3:4"`) in `assets.json`. Then build CSS:

```css
.base-frame {
  position: absolute;

  /* one dimension declared from size_pct, the other auto from aspect-ratio */
  /* fit=width: */     width:  <size_pct>%;  aspect-ratio: <asset.aspect>;  /* e.g. 3/4 */
  /* fit=height: */    height: <size_pct>%;  aspect-ratio: <asset.aspect>;

  /* anchor=center + offset: */
  left: calc(50% + <offset_x_pct>%);
  top:  calc(50% + <offset_y_pct>%);
  transform: translate(-50%, -50%);

  border: 3px solid teal;  /* visible debug outline */
}
```

The CSS `aspect-ratio` property is what guarantees the rendered base-frame shape matches the asset's actual shape — without it, the wireframe lies about where the asset ends up.

Inside `.base-frame`, draw a single asset-slot placeholder filling it.

For each `overlay` with `anchor_to: "base"` (default), position it inside `.base-frame` with `position:absolute; left: <x_pct>%; top: <y_pct>%; transform: translate(-50%, -50%)`. Width = `<width_pct_of_base>%` of `.base-frame`. **This is the whole point**: by parenting overlays to `.base-frame`, they move with the base when `base_bounds` shifts. So if the agent later adjusts `offset_y_pct` to dodge floating UI, all overlays follow automatically.

For overlays with `anchor_to: "slot"`, position them on `.slot-bg` directly (sibling of `.base-frame`).

Negative `x_pct` or values >100 are valid — they place the overlay **outside** the base frame (e.g. a cat on the cream floor next to the machine). The `.slot-bg` container has `overflow: hidden`, so anything outside its bounds gets clipped — that's the visual cue for the user that "this overlay is hanging off the screen".

---

## Slot color coding (border + 12% bg tint)

So the user can scan layers at a glance. Border 3px solid; background same hue at ~12% opacity.

| Slot / type | Color | Hex |
|---|---|---|
| `status-bar` (in topbar) | gray | `#94A3B8` |
| `header` (in topbar) | blue | `#3B82F6` |
| `background` whole slot | teal | `#14B8A6` |
| `content` whole slot | indigo | `#6366F1` |
| section types in content (`hero`, `icon-row`, `horizontal-list`, `vertical-list`, `mood-row`) | purple / green / orange / yellow / pink | `#8B5CF6` / `#10B981` / `#F59E0B` / `#EAB308` / `#EC4899` |
| `floating` items | amber | `#F59E0B` |
| `secondlayer` items | violet | `#A855F7` |
| `sheet` whole slot | rose | `#F43F5E` |
| `bottombar` whole slot | slate | `#475569` |

For map/canvas overlays (labels, drop zones, pins): render with a thin white outline + dark pill, so they read as on-canvas markers, not as section blocks.

---

## Header label format (per slot block)

Each block's top-left corner shows a monospace 10–11px label:

```
[<slot>: <type>] · <key meta>
```

Examples:
```
[topbar: status-bar] · 9:41
[topbar: header] · transparent · "SHANGHAI / CITY MODEL" + 2 icons
[background: map-area] · base = map-base.png · 4 labels + 4 drop-zones
[floating: progress-card] · anchor=bottom-above-sheet · offset_bottom=240
[secondlayer: detail-card] · trigger=tap landmark · default hidden
[sheet: bottom-sheet] · expanded=220px · drag-handle · 2 inner blocks
[bottombar: tab-bar] · 5 tabs · active="首页"
```

---

## Asset slot placeholder

Wherever any `*_ref` (`image_ref`, `icon_ref`, `avatar_ref`, `base_image_ref`) appears, render:

```html
<div class="asset-slot">
  <div class="asset-slot-id">hero.png · 3:4 · scene</div>
  <div class="asset-slot-prompt">Portrait 3:4, sunny meadow with three chibi animals…</div>
</div>
```

```css
.asset-slot {
  border: 2px dashed #6B7280;
  background: repeating-linear-gradient(45deg, #F3F4F6 0 8px, #E5E7EB 8px 16px);
  padding: 8px; border-radius: 6px;
  font: 10px/1.4 ui-monospace, Menlo, monospace; color: #1F2937;
}
.asset-slot-id { font-weight: 700; margin-bottom: 4px; }
.asset-slot-prompt { color: #4B5563; }
```

Truncate the prompt to ~80 chars + `…`. Slot dimensions should reflect aspect when practical (e.g., `aspect-ratio: 3/4` on hero slots).

---

## Secondlayer placeholder (always rendered)

Even if `default_visible: false`, draw the secondlayer panel as a translucent labeled box at its anchor — the whole point is that the user can confirm the agent is **aware** of the modal/popover.

```html
<div class="seclayer">
  <div class="seclayer-tag">SECONDLAYER · detail-card</div>
  <div class="seclayer-meta">trigger: tap landmark · anchor: center · 80% × 60%</div>
  <div class="seclayer-content">…short description of what's inside…</div>
</div>
```

```css
.seclayer {
  background: rgba(168, 85, 247, .12);
  border: 2px dashed #A855F7;
  border-radius: 10px;
  padding: 8px;
  color: #4C1D95;
  font: 10px/1.4 ui-monospace, Menlo, monospace;
}
.seclayer-tag { font-weight: 700; }
.seclayer-meta { color: #6B7280; margin: 2px 0 6px; }
```

Add a small "(hidden by default)" tag if `default_visible === false`.

---

## Required: top banner with summary

Above the phone frame, show one line and a swatch row:

```
WIREFRAME · <name> · mode=<canvas|feed> · <slot summary> · <M assets to generate>
palette: [swatches]    fonts: <display> + <body>
```

`slot summary` example: `topbar(2) · background(map-area) · floating(1) · secondlayer(1) · sheet(220px)`.

Five swatches: base, surface, primary, ink, accent[0].

This banner is the single best place to catch "agent misread the colors entirely" or "agent picked the wrong mode" before any image generation.

---

## Required: bottom panel with the asset list

Below the phone frame, render a compact table of every asset in `assets.json`:

```
ASSETS TO GENERATE (8)
 #   id        filename       aspect  role    prompt (truncated)
 1   hero      hero.png       3:4     scene   "Portrait 3:4, sunny meadow…"
 2   mascot    mascot.png     1:1     mascot  "Centered chibi fox on flat cream…"
 ...
Total: 8 assets · estimated 30–90s parallel
```

Monospace, dense rows. The user spots wrong aspects, missing assets, misclassified roles here.

---

## Output procedure

1. Read `output/<name>/layout.json` and `output/<name>/assets.json`.
2. Read `screen.mode` first. Branch on canvas vs feed.
3. Compute `<topbar-h>` (sum of topbar children heights — ~24 for status-bar + ~60 for header is a fine default) and `<bottom-h>` (sheet.expanded_height OR bottombar height OR 0).
4. Build the phone container: `position: relative; width:390; height:844; overflow:hidden`.
5. Emit each slot using its CSS recipe from the table above.
6. For every `*_ref` (including those nested in overlays / floating content / sheet content), emit an asset-slot placeholder.
7. Emit secondlayer placeholders for every entry — even hidden ones.
8. Emit the top banner and the bottom asset table.
9. Write to `output/<name>/wireframe.html` and run `open` on it.
10. Send the user a summary and ask:

   > Wireframe 出来了：mode=`<canvas|feed>`，`<slot 摘要>`，`<M>` 张图待生成。打开 `output/<name>/wireframe.html` 看一下分层和定位对不对。
   >
   > - 回 **ok / 继续** → 我开始 Step 4 出图（约 30s–3min）
   > - 回 **改 [slot/section]: [说明]** → 我改 layout/assets 并重新出 wireframe
   > - 回 **skip** → 跳过确认直接出图（不推荐）

11. **Do NOT proceed to Step 4 until you get explicit confirmation.** This gate is the whole point.

---

## What NOT to do

- **Don't put everything in `.scroll`** in document flow. That's the old flat-stack model. Use the absolute slot rules above.
- **Don't omit secondlayer placeholders.** They must show even when hidden.
- **Don't load Google Fonts.** System fonts only.
- **Don't add animations / transitions / hover states.**
- **Don't try to make it look like the final output.** Pretty wireframes hide structural mistakes.
- **Don't generate any images.** Zero Gemini calls in this step.
- **Don't write `index.html`.** That's Step 5.
