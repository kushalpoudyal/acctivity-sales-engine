# Acctivity.ai brand guide

Applies to every generated deck and PDF; `documents.py` builds them from these
rules and the files in this folder (`Hack_A_API/api/brand/`). Change both together.
Layouts follow the Acctivity slide template (`Acctivity-Template.pdf`, 26 slides, 19/11).
Colours, fonts, logos, background and illustrations come from the Acctivity brand assets pack;
the roles, sizes and layouts below are how we apply them.

## Logo

| File | What it is | Use |
|---|---|---|
| `logo/acctivity-logo.png` | "Acctivity.ai" wordmark, Slate Grey + Steel Blue, transparent, 2558×432 | The logo. Cover, title slide, first-page header. |
| `logo/acctivity-mark.png` | "Acc" mark, white on a Steel Blue square, 801×801 | Not used in generated outputs (the template footer is the wordmark only). |

- Use the files as supplied: never recolour, redraw, crop, stretch, rotate or add effects; keep the aspect ratio.
- The wordmark has no white version, so place it only on white or the light background image,
  never on a dark or busy area.
- Clear space around the wordmark: at least half its height. Minimum width: 3.5 cm on a cover, 2.5 cm elsewhere.
- Always write the name as "Acctivity.ai" (or "Acctivity"), never "ACCTIVITY" or "Activity".

## Colours

| Name | Hex | Role |
|---|---|---|
| Slate Grey | `#3D443D` | Primary. Headings, body text, dark panels. |
| Steel Blue | `#4396BA` | Primary accent. Key words in titles, big numbers, rules, links, icons. |
| Light Pink | `#EFB9B9` | Soft fills: callout boxes, highlight panels. |
| Gold | `#F2C406` | Small highlights only: a bar, a dot, an underline. |
| Rouge | `#CC3232` | Rare emphasis, at most one element per page. |
| Light Grey | `#AFA0A9` | Dividers, borders, secondary chart series. |
| White | `#FFFFFF` | Page and slide background. |
| Navy (template) | `#152D47` | Headings and titles, big numbers. |
| Ink (template) | `#4C4C4D` | Body text. |
| Panel (template) | `#EDF5F8` | Panel and info-cell fill. |
| Panel rule (template) | `#C3D3DA` | Thin panel borders. |

- Slate Grey and Steel Blue (the logo colours) carry the design. The four accents together cover
  no more than about 10% of any page.
- Text: body text is Slate Grey on white, or white on Slate Grey. Steel Blue text only at 18 pt or larger.
  Never set text in Gold, Light Pink or Light Grey on white (too little contrast).
- Charts: Steel Blue, Slate Grey, Light Pink, Gold, Light Grey, in that order.
- No other colours, gradients or drop shadows.

## Typography

| Use | Font | Files |
|---|---|---|
| Headings, titles, big numbers, pull quotes | Lora Regular, Navy (Italic for quotes) | `fonts/Lora-*.ttf` |
| Body copy, bullets, tables, labels, captions | Inter Regular (Medium/SemiBold for labels and emphasis) | `fonts/Inter_18pt-*.ttf` |

- Slides: cover title 40 pt, heading 30 pt, body 17–18 pt (12.5–16 pt inside cards), footers 10–12 pt.
- Documents: title 30 pt, H1 22 pt, H2 13 pt, body 10.5 pt, line spacing about 1.3.
- Sentence case for headings. Small Inter SemiBold all-caps labels above headings are fine, letter-spaced.
- PDF: register and embed the TTF files (e.g. reportlab `TTFont`) so the real fonts show.
- PPTX: set the font names `Lora` and `Inter` on every run, including table cells.
  python-pptx cannot embed fonts, so viewers without them see a fallback.

## Imagery

Use only the supplied watercolour sketch illustrations: loose ink lines, soft washes in warm sand
and grey-blue, on white paper. Never add stock photos, clip art, icon sets or images in another style,
and never recolour, filter, frame or add shadows to the illustrations.

| File | Shows | Good for |
|---|---|---|
| `images/style-reference.jpg` | Two colleagues walking with folders | Cover, introduction |
| `images/colleagues-walking.jpg` | Two women walking and talking outside | Team, partnership, next steps |
| `images/lawyer-with-client.jpg` | Lawyer smiling across a desk at a client | Client experience, service |
| `images/lawyers-on-stairs.jpg` | Three lawyers walking down stairs | The firm, its people |
| `images/lawyers-in-lobby.jpg` | Lawyers crossing a glass lobby | Busy practice, workload |
| `images/lawyers-in-plaza.jpg` | People crossing a plaza outside offices | Growth, the wider practice |
| `images/team-gathering.jpg` | Team talking by glass doors | Collaboration, onboarding |
| `images/office-workspace.jpg` | Desk, laptop and bookshelves | Workflow, the product at work |
| `images/office-bookshelves.jpg` | Sunlit office with files and bookshelves | Records, documents, compliance |
| `images/office-plants.jpg` | Bright office with plants | Calm, organised practice |
| `images/office-facade-sunset.jpg` | Glass office building at sunset | Vision, the road ahead |
| `images/office-walkway.jpg` | Office building and walkway | Closing slide, contact page |

- At most one illustration per slide or page: the cover or title, at-a-glance, section dividers and the closing
  slide. Dense content slides get none.
- Place illustrations large: bleeding off one edge, or filling one side of a split layout. Their white
  paper edges blend into a white page, so don't box them.
- **Never place an illustration, avatar or boxed image over a background.** Illustrations sit only on the plain
  white page: a full-height panel bleeding off the left or right edge (37.5% of the slide width), a full-width
  band bleeding off the top of a PDF cover, or inline on white where the paper edges blend in.
- `background/acctivity-background.jpg` (watercolour wash) is not used in generated outputs; the template
  uses plain pages throughout.

## Layout and presentation

- Calm, airy and editorial: lots of white space, left-aligned text, margins of at least 0.5 in on
  slides and 2 cm in documents.
- Slides are 16:9 (13.333 × 7.5 in):
  - Title slide: illustration panel on the left; firm logo, Navy Lora title, Ink subtitle and light
    "Prepared for" / "Date" cells on the right.
  - Content slides: white, Navy Lora heading top-left (no rule), optional outlined Steel Blue tag above it
    (PROPOSED, APPENDIX), at most 6 panels or 4 numbered rows.
  - Closing slide: illustration panel on the left, next steps and "Prepared for" / "Prepared by" cells.
  - Footer on every slide: the small wordmark bottom-left, nothing else (no page number, no mark).
- Documents (PDF) are A4: an illustration band bleeding off the top of the cover, Navy Lora headings
  without rules, Ink Inter body, and the small wordmark plus page number in the footer.
- Panels and callouts: flat Panel fill with a thin Panel rule border, square corners, no shadows;
  Steel Blue fill with white text for a single key callout. Tables: Slate Grey header row with white text, thin Light Grey row dividers.
- Tone: warm, plain and confident. Short sentences, no hype, no emoji, no exclamation marks.

## Prospect firm's logo

If the firm's logo is supplied, it may appear once, on the cover or title slide, smaller than the
Acctivity wordmark, beside "Prepared for <firm>". It must not suggest that the firm wrote or endorsed
the material. Acctivity.ai is always the visible author, and the design uses Acctivity colours.
