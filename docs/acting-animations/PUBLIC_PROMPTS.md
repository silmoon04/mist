# Generation record

The 216 acting poses and 126 blink poses are compositions. They reuse original MIST parts and component drawings from 5 selected ImageGen sheets. They are not separately generated full faces.

The component families contain 24 distinct generated cels and 3 distinct canonical cels, counted by exact PNG content. Shared neutral mouths and closed eyes count once. The source manifest records which component files are used by the final compositions.

There were 10 built-in ImageGen calls including revisions. The prompts below are preserved as recorded. Output identifiers are relative asset names; private machine paths are omitted. Earlier drafts are marked as unselected.

Source sheets remain unchanged. Extracted cells use a common family scale, fixed attachment registration and MIST's #5eeadd palette while retaining their alpha silhouettes. `public_provenance.json` records source hashes, reference names, component counts and the manifest hash.

## eyes/round/v1

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/round/source_v1.png` (earlier draft).

References: `components/eyes/round_reference.png`.

```text
Production animation component sheet, using the attached mint open-C eye as the exact character and stroke-design reference. Generate ONLY SIX single left-eye drawings, arranged precisely as 3 columns by 2 rows, row-major. Transparent background, 1536x1024 image, each of six cells 512x512, no labels or frames. Flat solid mint #5eeadd, rounded caps, consistent 28px stroke at sheet scale, no gradient, no glow, no shadow, no outlines. Each cell has eye horizontal center x=256 locally and bottom edge y=365 locally. Open eye approximately 230px wide and 238px tall. Fix width and bottom attachment across all six drawings. This is a slowly closing eyelid on the original abstract MIST C-eye, not an eyeball. Reference opening is on LEFT. Frame 1 exact open C; frame 2 upper rounded arc descends slightly while retaining the left gap and curved lower rim; frame 3 upper rim descends further until top is a gentle nearly horizontal lid connected to the curved right side and lower rim, still open on LEFT; frame 4 shallow lower bowl with upper lid close to bottom rim, one connected silhouette and same lower rim; frame 5 almost closed shallow lower crescent; frame 6 fully closed near-horizontal shallow lower curved mint stroke with round ends, never an upside-down arch. Every intermediate has coherent progression, no sudden new pieces. NO pupils, NO filled eye disk, NO floating eyebrows, NO extra marks, NO eyelashes, NO doubled lines or detached components. The upper lid closes down toward a FIXED bottom rim; do not merely squash or scale the C vertically. All six shapes belong to the SAME eye, at SAME scale, SAME thickness. None of the poses reopen. Show all SIX unique stages in sequence. Preserve true alpha.
```

## eyes/round/v2

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/round/source_v2.png` (earlier draft).

References: `components/eyes/round/source_v1.png`.

```text
Edit this six-cell sprite sheet. Preserve the same six positions, mint color, opening to left, true transparency and clean design. Make the stroke thickness approximately 52px in ALL poses so it matches original MIST, with rounded ends, flat mint no illumination. The six cels must smoothly close all the way to a near-horizontal lower curved line. Same eye width 310px and bottom attachment per row. Cel 1 full open C 310px high; cel2 slightly descended top arc height270px; cel3 top becomes shallow hooked lid, height215px; cel4 top lid almost closed over bottom rim height155px; cel5 very narrow remaining gap height95px, no ring/bowl silhouette or filled wedge; cel6 fully CLOSED simple shallow lower-curved stroke height60px INCLUDING52px stroke thickness. Cel6 should be almost level with tiny downward curvature, NOT a smiley mouth bowl. Treat six cels as SAME eye progressively being occluded downward, not global scaling. Bottom attachment fixed across row at local y425, center localx256. 3 columns ×2rows, transparent, no text, no extra features. No pupils or eyebrows. Art stays flat, not luminous. Preserve the existing sheet layout and produce refined closure progression.
```

## eyes/round/v3

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/round/source_v3.png` (earlier draft).

References: `components/eyes/round/source_v2.png`, `components/eyes/round_reference.png`.

```text
Precise palette correction ONLY. The first attached image is the sprite sheet edit target. The second attached image is the original MIST eye palette reference. Keep all six eye silhouettes, exact positions, exact dimensions, blank spaces and transparent alpha. Change the sprite sheet's neon green-turquoise to the soft PALE MINT CYAN of the second reference: exact sRGB #5eeadd (red94 green234 blue221), flat evenly filled color. No lighting, no gradient, no bloom, no neon brightness, no outer glow. This is a matte flat 2D sprite, not a glowing screen. Remove all shading and color variation inside the strokes. Keep the eye shapes pixel-for-pixel geometrically, six drawings in3columns×2rows. Do not alter the opening or lid geometry. Transparent background. No text. Preserve the fully closed final eyelid.
```

## eyes/round/v4

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/round/source_v4.png` (selected).

References: `components/eyes/round/source_v3.png`, `components/eyes/round_reference.png`.

```text
Precise stroke-weight repair on this six-cell MIST eye blink sheet. KEEP all six eye silhouettes and their outer dimensions, all locations, the same 3×2 layout, and transparent background. Increase ONLY stroke thickness by 30 percent inward, so the open C resembles original attached second eye reference. Current sheet line is too thin. Reference MIST C stroke is 16 percent of eye width, not13percent. Use a consistent54pixelstroke on340pixel-wide glyphs. Keep all stroke ends round. Intermediate cel4/cel5: their top and lower ends approach and join CLEANLY with no doubled nub or jagged overlapping circles atleft. Last closedcel stays a SINGLE shallow lower-curved stroke that is almost horizontal, not a smiling mouth bowl. Rest of geometry must stay unchanged. Flatsoftmint#5eeadd; no pupils,brows,shading,outerglow. Preserve actualalpha. Thisisnotanewdesign; onlyconsistentboldnesscorrection to match reference.
```

## eyes/hook/v1

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/hook/source_v1.png` (earlier draft).

References: `components/eyes/hook_reference.png`.

```text
Production animation component sheet using the attached original MIST angled hook eye as the exact family and stroke-design reference. Generate ONLY SIX single left-eye drawings in exactly 3 columns by 2 rows, row-major. Transparent background, 1536x1024 image, each of six cells 512x512, no labels or frames. Flat solid mint #5eeadd, rounded caps, consistent 30px stroke at sheet scale, NO glow/gradient/shadow. Each cell eye center x=256 locally and bottom edge y=365 locally. Eye approximately 230px wide. Fix scale, width, and bottom attachment across ALL drawings. Six successive coherent upper-lid closure poses: 1 exact slanted hook from reference, diagonal top stroke sloping DOWN from left to right, curved lower rim, opening LEFT; 2 diagonal lid lowered a little at its left end, connected round right corner and same lower U-rim; 3 diagonal lid lowered more and closer to horizontal, preserving open left gap and bottom rim; 4 shallower slit, top lid almost parallel with lower rim; 5 almost shut shallow lower crescent, no hole or separated piece; 6 closed low shallow smile-shaped eyelid stroke, near horizontal with round ends, same bottom. No pose is an upward arch and none reopens. All are the same eye. Do not add pupils, eyeballs, iris, detached eyebrows, floating marks, lashes, or filled eye disks. A single connected thick stroke per eye, not realistic eye anatomy. Draw actual successive lid shapes; no simple global scaling or rotating the entire glyph. The lid moves down, while the lower rim and the attachment stay fixed. Preserve the original clean MIST visual language, with slight hand-drawn softness only in the existing round stroke design. Preserve true alpha.
```

## eyes/hook/v2

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/hook/source_v2.png` (earlier draft).

References: `components/eyes/hook/source_v1.png`.

```text
Edit this six-cell sprite sheet so the eye ACTUALLY CLOSES progressively. Keep original hooked MIST style, mint, samewidth350px and same stroke60px, each local centerx256/bottomy425. Top row: cel1 original openhook totalheight325px; cel2 lid downward totalheight270px; cel3 lid lowersmore totalheight220px. Bottom row: cel4 closefurther with totalheight165px; cel5 almost closed, totalheight110px and no bowl shaped silhouette; cel6 FULLY CLOSED single very shallow lower-curved horizontal line totalheight65px INCLUDING60pxstrokethickness. IMPORTANT finalcel must have NO aperture and NO doubled outline, just rounded nearhorizontal line. Keep actual eye width fixed throughout and bottom attachment fixed; make upperlid descend while lower rim flattens only in last poses. ONE smooth coherent closure, six different sequential frames, not six variations of open hook. True transparent background. Flat solid mint color no light,glow,gradient or shadows. Do not introduce eyebrows or pupils or floating mark. Preserve3columns×2rows evenlyspaced. Finalclosedlinecurves down a tiny amount, NEVER inverted arch.
```

## eyes/hook/v3

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/eyes/hook/source_v3.png` (selected).

References: `components/eyes/hook/source_v2.png`, `components/eyes/hook_reference.png`.

```text
Precise palette correction ONLY. The first attached image is the sprite sheet edit target. The second attached image is the original MIST eye palette reference. Keep all six eye silhouettes, exact positions, exact dimensions, blank spaces and transparent alpha. Change the sprite sheet's neon green-turquoise to the soft PALE MINT CYAN of the second reference: exact sRGB #5eeadd (red94 green234 blue221), flat evenly filled color. No lighting, no gradient, no bloom, no neon brightness, no outer glow. This is a matte flat 2D sprite, not a glowing screen. Remove all shading and color variation inside the strokes. Keep the eye shapes pixel-for-pixel geometrically, six drawings in3columns×2rows. Do not alter the opening or lid geometry. Transparent background. No text. Preserve the fully closed final eyelid.
```

## mouths/smile/v1

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/mouths/smile/source.png` (selected).

References: `brain/art_direction/artist_studio_20260916/reuse/parts/mouth_line_30.png`, `brain/art_direction/artist_studio_20260916/reuse/parts/mouth_smile_arc_37.png`.

```text
Create a production 6-cel animation sprite sheet consisting ONLY of 6 individual MIST robot mouth drawings. The input images are shape/style references; do not reproduce their black background or empty large canvases. Transparent background. Exactly 3 columns by 2 rows, equal-size cells, row-major time order, large clear gutters, no labels no text no lines no grid no eyes no face. Flat solid mint cyan #5eeadd, softly rounded clean ends, no outline no glow no shading no anatomy no teeth no tongue. All six drawings are centered on the same fixed x and y attachment coordinates inside their cell, mouth width consistent across the sequence, identical thickness to the reference; never recenter based on the mouth changing shape. This is ONE mouth performing ONE gradual action, with very small equal changes between adjacent cels, not six different emotions. Put each mouth in the middle quarter of its cell width so nothing clips. REFERENCE1 is the canonical closed dash. REFERENCE2 is the canonical curved smile. Generate SIX successive morph drawings from the closed flat dash to a modest smile, no huge grin. Cel0 perfectly flat rounded horizontal dash. Cel1 corners only very slightly lifted. Cel2 corners gently higher with shallow center curve. Cel3 small gentle U shaped smile. Cel4 slightly deeper continuous curved smile. Cel5 modest rounded U smile, no more than one quarter of mouth width in depth. Keep the center of the stroke attached at same y; corners lift progressively. One continuous stroke, no disconnected segments or filled mouth.
```

## mouths/bowl/v1

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/mouths/bowl/source.png` (selected).

References: `brain/art_direction/artist_studio_20260916/reuse/parts/mouth_line_30.png`, `brain/art_direction/artist_studio_20260916/reuse/parts/mouth_open_smile_filled_33.png`.

```text
Create a production 6-cel animation sprite sheet consisting ONLY of 6 individual MIST robot mouth drawings. The input images are shape/style references; do not reproduce their black background or empty large canvases. Transparent background. Exactly 3 columns by 2 rows, equal-size cells, row-major time order, large clear gutters, no labels no text no lines no grid no eyes no face. Flat solid mint cyan #5eeadd, softly rounded clean ends, no outline no glow no shading no anatomy no teeth no tongue. All six drawings are centered on the same fixed x and y attachment coordinates inside their cell, mouth width consistent across the sequence, identical thickness to the reference; never recenter based on the mouth changing shape. This is ONE mouth performing ONE gradual action, with very small equal changes between adjacent cels, not six different emotions. Put each mouth in the middle quarter of its cell width so nothing clips. REFERENCE1 is the canonical closed dash. REFERENCE2 is the canonical open rounded bowl. Generate SIX successive drawings of opening one mouth smoothly from flat horizontal dash to small rounded filled bowl. Cel0 flat horizontal rounded dash. Cel1 identical width, bottom edge just 4% lower. Cel2 bottom expands 8% of width below upper lip. Cel3 bottom expands12%. Cel4 bottom expands16%. Cel5 bottom expands20%, modest and compact, not shouting. Upper lip is straight and remains at exactly the same y in all six frames, corners remain fixed at the same x, lower edge progressively bows downward. Flat filled cyan silhouette, no internal strokes, no teeth or tongue, no double shape, no smile crease; make every adjacent change equally small.
```

## mouths/concern/v1

Tool: `built-in image_gen.imagegen`. Transparent background enabled.

Output: `components/mouths/concern/source.png` (selected).

References: `brain/art_direction/artist_studio_20260916/reuse/parts/mouth_line_30.png`, `brain/art_direction/artist_studio_20260916/reuse/parts/mouth_frown_arc_28.png`.

```text
Create a production 6-cel animation sprite sheet consisting ONLY of 6 individual MIST robot mouth drawings. The input images are shape/style references; do not reproduce their black background or empty large canvases. Transparent background. Exactly 3 columns by 2 rows, equal-size cells, row-major time order, large clear gutters, no labels no text no lines no grid no eyes no face. Flat solid mint cyan #5eeadd, softly rounded clean ends, no outline no glow no shading no anatomy no teeth no tongue. All six drawings are centered on the same fixed x and y attachment coordinates inside their cell, mouth width consistent across the sequence, identical thickness to the reference; never recenter based on the mouth changing shape. This is ONE mouth performing ONE gradual action, with very small equal changes between adjacent cels, not six different emotions. Put each mouth in the middle quarter of its cell width so nothing clips. REFERENCE1 is the canonical closed dash. REFERENCE2 suggests the original simple rounded concern arc style but do NOT copy its asymmetric rotation. Generate SIX successive drawings from closed flat dash into a subtle symmetrical concerned frown. Cel0 perfectly flat rounded horizontal dash. Cel1 corners lower by barely 2%. Cel2 corners lower4%. Cel3 corners lower6%. Cel4 corners lower8%. Cel5 corners lower10% of mouth width. The center stays fixed and corners gradually drop creating a shallow upside-down-U arc. Keep it restrained, not a rainbow and not an angry snarl. Same thickness and consistent width. One continuous stroke. No extra feature.
```
