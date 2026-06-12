# Style Extraction — fill in the STYLE TRANSFER preamble

Look at the user-supplied **style reference image**. Your job is to fill in the `<...>` slots in the template below with terse, concrete observations, then write the result to `output/<name>/style-preamble.txt`.

This file gets prepended to every per-asset prompt in Stage 3 so Gemini reproduces the same painted look across all generated assets.

## Template

```
STYLE TRANSFER TASK.
The attached reference image is for STYLE REFERENCE ONLY. You must reproduce the
painting technique below, but you must NOT reuse any specific subject, character,
object, scene, or composition from the reference. Treat the reference as if you
can see only HOW it was painted, not WHAT it depicts.

The visual language you must faithfully reproduce:
- <BRUSHWORK / TECHNIQUE — e.g. "chalky watercolor with visible dry-brush strokes and scattered white paint speckles">
- <LIGHTING / ATMOSPHERE — e.g. "soft dappled light, atmospheric haze">
- <LINE QUALITY — e.g. "loose painterly edges, never tight line art">
- <PALETTE — name the 4–6 dominant colors using concrete terms ("pale pink", "moss green", "warm cream") not hex>
- <RENDERING STYLE for any characters or figures, in GENERIC terms only — e.g. "chibi proportions, big dot eyes, minimal linework". Describe the drawing approach, NEVER a specific character.>
- <BACKGROUND TREATMENT — e.g. "soft dreamy blur with shallow depth of field" or "flat solid color">
- <TEXTURE — e.g. "handmade watercolor paper grain across the whole canvas">

Generate a NEW image with completely different subject matter than the reference:
- Different species / objects / characters than what the reference shows
- Different scene / composition / arrangement than the reference
- ONLY the painting technique, palette, brushwork, and texture should match

No text, no letters, no numbers, no logos, no UI elements, no borders, no watermarks.
```

## Rules for filling the slots

1. **One bullet per dimension, max ~25 words.** Gemini ignores walls of text.
2. **Concrete > abstract.** "Chalky watercolor with white paint speckles" beats "loose, expressive style".
3. **Name colors descriptively, not by hex.** Gemini understands "honey amber" or "misty blue-green" better than `#E5B870`.
4. **Describe rendering approach, never specific characters.** Write "chibi animal style with dot eyes" — never "the bunny from the reference" or "the same hedgehog character." We want the style hand, not the cast.
5. **Drop the rendering-style bullet if the reference has no characters / figures** (e.g. a pure landscape or abstract texture).
6. **Keep the opening "STYLE REFERENCE ONLY" paragraph and trailing "No text..." line verbatim.** Both are load-bearing — they stop Gemini from copying specific elements and from hallucinating gibberish text.

## Worked example

For a generic watercolor reference image with painted animals, a filled preamble looks like:

```
STYLE TRANSFER TASK.
The attached reference image is for STYLE REFERENCE ONLY. You must reproduce the
painting technique below, but you must NOT reuse any specific subject, character,
object, scene, or composition from the reference. Treat the reference as if you
can see only HOW it was painted, not WHAT it depicts.

The visual language you must faithfully reproduce:
- CHALKY WATERCOLOR with visible dry-brush strokes and scattered white paint speckles across the whole canvas
- Soft atmospheric LIGHT with gentle haze, no harsh shadows
- LOOSE PAINTERLY edges, never tight line art, never digital-clean
- PALETTE: fresh grass greens, warm creams, soft pinks, golden highlights, gentle earth browns
- Chibi animal RENDERING style — round simple shapes, big dot eyes, blush cheeks, minimal linework. (Apply this rendering approach to entirely new species / characters; do not reproduce any specific animal from the reference.)
- Soft DREAMY BLURRED background with shallow depth of field
- Handmade WATERCOLOR PAPER TEXTURE grain throughout

Generate a NEW image with completely different subject matter than the reference:
- Different species / objects / characters than what the reference shows
- Different scene / composition / arrangement than the reference
- ONLY the painting technique, palette, brushwork, and texture should match

No text, no letters, no numbers, no logos, no UI elements, no borders, no watermarks.
```

Save your filled version to `output/<name>/style-preamble.txt`. You'll pass it to `scripts/gen_image.py` via `--style-preamble`.
