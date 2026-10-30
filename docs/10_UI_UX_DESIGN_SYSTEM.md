# 10 — UI/UX Design System

## 1. Design goal

Build a serious research and media archive workspace. It should feel editorial, precise, calm, and media-first—not like a generic AI dashboard. The interface should be understandable to archivists and researchers who are not developers.

## 2. Required palette

- Brick: `#8C2914`
- Sand: `#E9E6D3`
- Deep sand: `#D8D5C4`
- Light sand: `#F2EFDE`
- Ink: `#222222`
- Rule grey: `#A7A7A7`
- White: `#FFFFFF`

Centralize tokens in one stylesheet or token file. Check contrast before using a colour pair for important text.

## 3. Typography and layout

- Primary typeface: Open Sans, following the existing design guideline.
- Use a deliberate hierarchy and restrained heading sizes.
- Use a spacing scale based on 4 px.
- Prefer square or subtly rounded corners and thin borders.
- Avoid gradients, glassmorphism, excessive shadows, and decorative animation.
- Preserve the logo area in the top-right; do not invent institutional branding.
- Keep main actions in predictable locations.

## 4. Component rules

Create consistent shared components for buttons, forms, tables, dialogs, toasts, skeletons, status labels, filter chips, media cards, review tags, and empty/error states. Do not add a second component framework if the current one already meets the need.

All controls must show appropriate hover, focus, pressed, disabled, loading, and error states.

## 5. AI and review visual language

- Human-confirmed tags: sand fill.
- AI suggestions: neutral/sand surface with dashed outline.
- Selected tag: brick emphasis.
- Confidence must be described accurately and must not imply certainty.
- Use text/icons as well as colour for states.
- Do not present model output as human-approved.

## 6. Motion

Use motion to explain state changes, not to decorate:
- Small controls: approximately 100–180 ms.
- Menus and drawers: approximately 120–260 ms.
- Use subtle opacity/transform transitions.
- Respect `prefers-reduced-motion`.
- Never delay essential actions for animation.
- Avoid animated backgrounds, scroll-jacking, excessive bouncing, and motion on every media tile.

## 7. Media performance

- Use thumbnails for grids and load full-resolution media only when requested.
- Lazy-load offscreen images.
- Reserve image dimensions to avoid layout shifts.
- Support portrait and landscape media.
- Provide useful missing-thumbnail and failed-load states.
- Use pagination or virtualization when measured collection size requires it.
- Preserve filters/search state when navigating back where practical.

## 8. Accessibility

- Semantic HTML and logical heading order.
- Keyboard-accessible navigation, dialogs, menus, and media selection.
- Visible focus rings.
- Proper form labels and errors.
- Accessible status announcements.
- Reduced motion support.
- Meaningful alternative text.
- Test at desktop, laptop, tablet, and mobile widths.

## 9. UI state honesty

Every data-driven screen needs loading, empty, error, and populated states. Only show success after the API confirms success. Do not use fake production metrics or pretend a feature works when its endpoint is not implemented.

Demo data must be clearly labelled. Ingestion controls must distinguish setup, dry-run, and confirmed execution; opening a page must never start a scan.
