---
name: Promo Studio
description: A modern cinematic film suite in obsidian, slate and warm amber.
colors:
  bg: "#090b10"
  bg-surface: "#10141d"
  bg-panel: "#151a24"
  bg-elevated: "#1b2230"
  bg-hover: "#1f2738"
  line: "rgba(255, 255, 255, 0.10)"
  line-subtle: "rgba(255, 255, 255, 0.06)"
  line-strong: "rgba(255, 255, 255, 0.18)"
  accent: "#f59e0b"
  accent-light: "#fbbf24"
  accent-dark: "#d97706"
  accent-ink: "#140e04"
  accent-subtle: "rgba(245, 158, 11, 0.12)"
  cyan: "#38bdf8"
  emerald: "#10b981"
  emerald-subtle: "rgba(16, 185, 129, 0.12)"
  rose: "#f43f5e"
  text: "#f8fafc"
  text-secondary: "#cbd5e1"
  text-muted: "#8492a6"
  text-dim: "#536177"
typography:
  body:
    fontFamily: "Inter, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "14px"
    lineHeight: 1.55
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 650
    lineHeight: 1.15
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  control:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    letterSpacing: "-0.01em"
  technical:
    fontFamily: "JetBrains Mono, SF Mono, Fira Code, Roboto Mono, Consolas, monospace"
    fontSize: "11px"
rounded:
  xs: "4px"
  sm: "6px"
  md: "10px"
  lg: "14px"
  xl: "20px"
  full: "9999px"
spacing:
  tight: "8px"
  small: "12px"
  medium: "16px"
  surface: "24px"
components:
  button-primary:
    textColor: "{colors.accent-ink}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "9px 18px"
    height: "42px"
  button-secondary:
    backgroundColor: "{colors.bg-elevated}"
    textColor: "{colors.text}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "9px 18px"
  field:
    backgroundColor: "{colors.bg-surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "9px 14px"
---

# Design System: Promo Studio

## Overview

**Creative North Star: "Modern cinematic film suite"**

Obsidian ground, deep slate layers and warm amber actions restore the approved film-suite direction from `outputs/Promo-Studio-GitHub/static`. This record follows the active `static/styles.css`, `static/index.html` and `static/app.js`. The restored source is the visual authority; no new exploration, seed or comp is claimed.

The interface combines softly rounded elevated panels, restrained glass treatments and ambient action glows with precise source references. Full-show discovery gives this same material vocabulary a spacious editorial opening and ruled posting-order results. The PDF remains a light paper handoff with dark ink and warm accent rules.

**Key Characteristics:**
- Obsidian ground and layered slate surfaces
- Warm amber actions and focus
- Sans-serif reading with monospaced technical references
- Ambient depth, translucent surfaces and rounded panels
- Source-led ranked results and inspectable coverage

## Colors

Warm amber signals action against cool slate surfaces; frontmatter owns the current source values.

### Primary
- **Warm Amber**, **Amber Light** and **Amber Dark** form primary button gradients and active accents.
- **Amber Ink** keeps primary-action text dark; **Amber Subtle** supplies focus and selection tints.

### Secondary
- **Technical Cyan** marks source timecodes and technical emphasis.
- **Emerald** and its subtle tint signal completed coverage and successful states.
- **Rose** marks failed checks and coverage gaps.

### Neutral
- **Obsidian** is the page ground; **Surface**, **Panel** and **Elevated Slate** separate work regions.
- **Hover Slate** provides interactive feedback.
- **Text**, **Secondary Text**, **Muted Text** and **Dim Text** distinguish reading, supporting labels and quiet context.
- Translucent white rules have subtle, standard and strong roles.

**The Action Signal Rule.** Use amber for primary actions, selected states and keyboard focus; keep cyan and semantic colors tied to their technical or status meanings.

## Typography

**Reading font:** the source's Inter-first sans stack, with platform and Arial fallbacks. **Technical font:** JetBrains Mono-first monospace stack. Georgia-led serif belongs to the import document illustration. A declared family does not prove that the font is downloaded or installed.

### Hierarchy
- **Headline:** frontmatter headline role for compact page titles.
- **Title:** frontmatter title role for panel headings.
- **Body and controls:** compact sans-serif reading, with semibold task labels.
- **Technical:** monospaced source ranges, timeline references and technical labels.
- **Discovery:** regular-weight editorial opening, fluid (36–64px), reducing to (39px) below (700px); ranked titles (28px), reducing to (24px). Uploaded count resolves to (54px), reducing to (32px). Earlier larger count declarations are superseded.

**The Source Reference Rule.** Separate exact source text from technical ranges and editorial reasons; preserve line breaks and keep source references easy to scan.

## Layout

The restored application places sequential workflow navigation above the working surface. Import, source selection, brief and cut review retain their own working regions. At (1100px), the brief and cut-review columns collapse to one column. At (800px), the header wraps, import stacks and the prompt composer becomes vertical; workflow navigation can scroll horizontally.

Full-show discovery replaces the manual rail with a quiet return bar. Its centered surface caps at (1400px), with side insets (52px). The source ledger sits beside a search form (350px), with a generous gutter (80px). At (1000px), the form narrows (310px) and gutters tighten. Below (700px), the form comes first, followed by the initially collapsed source ledger. The mobile episode map scrolls within a bounded area (330px). Ranked results become one reading column. The count reflects actual uploaded episodes.

The timeline is a horizontally scrollable source reference with minimum-width segments (140px), original source ranges and separate cumulative promo ranges. Posting-order results use page scrolling, with ordered cuts beside the editorial explanation on desktop and below it on mobile. Coverage and interruption details remain expandable.

## Elevation & Depth

The restored system uses ambient shadows, translucent slate, background blur and localized glows. Buttons, work panels, review cards and dialogs can carry depth; do not apply the superseded flat-only restriction. Discovery's source and result areas use fine rules and whitespace within this same world.

### Shadow Vocabulary
- **Small:** button and compact control depth, from the source shadow-sm token.
- **Medium:** supporting work cards, from shadow-md.
- **Large:** dialogs and lifted containers, from shadow-lg.
- **Amber glow:** action emphasis and hover feedback, from shadow-glow and accent-glow.

**The Material Continuity Rule.** Keep slate layers, soft depth and warm action glows coherent across manual and discovery surfaces.

## Shapes

The source radius scale moves from small control corners to generous panel corners and pill labels. Buttons and text fields share medium corners. Work cards and review regions use larger rounded silhouettes. Discovery ledger tiles retain compact corners and clear ruled boundaries. Checkboxes use styled square geometry with a CSS-drawn check; icons elsewhere use inline SVG where provided.

## Components

### Buttons
Primary actions use the amber light-to-dark gradient, dark ink, a translucent highlight border and subtle text highlight. Hover warms and brightens the gradient with an ambient glow; active state shifts down (1px). Shared minimum height is (42px). Secondary buttons use elevated slate, strong rules and subtle blur. Quiet actions use muted text and a transparent surface, gaining a faint white fill on hover. Disabled opacity is (.45). Keyboard focus uses an amber outline (2px), offset (3px).

### Inputs / Fields
Surface-slate fill, standard rule, medium corners and the frontmatter field padding. Focus shifts the border to amber, adds a soft tinted ring and raises the fill. Selects retain a visible chevron. Labels remain explicit.

### Navigation
Numbered workflow stages support the manual sequence. Selected and completed states receive the restored amber/status treatment. Full-show discovery hides the manual rail and mode switcher; its return bar restores the manual workspace.

### Cards / Containers
Slate panels, translucent fills, rounded corners, thin rules and ambient shadows define the restored suite. Discovery's brief uses a desktop dividing rule instead of a filled card. Mobile removes that vertical divider and places the form before the source ledger.

### Source Timeline and Review
Timeline blocks use slate gradients, amber top rules and monospaced source ranges in cyan. Hover shifts emphasis toward cyan and adds subtle lift. Dialogue, hook/cliffhanger explanations, source checks and editorial reasons remain distinct. Proposals retain explicit apply/keep actions. Optional spoiler findings stay attached to the reviewed draft and retain evidence and uncertainty.

### Full-show Ledger and Ranked Results
Native disclosure controls expose numbered episode tiles and exact source lines. Completed episode tiles use emerald tint with explicit status text. Rank-first rows pair an amber rank with a regular-weight title, posting reason, ordered episode/timecode cuts, hook, cliffhanger and unanswered ending. Coverage gaps use rose and remain visible beside partial results. Saved searches and run details stay inspectable.

### Motion
State changes use the source fast/normal transitions and shared easing. Source timeline hover lifts are subtle. Reduced-motion preferences disable animations and transitions.

## Do's and Don'ts

### Do:
- **Do** use the restored slate and amber tokens across manual and discovery modes.
- **Do** preserve distinct source text, technical timecodes and editorial reasons.
- **Do** keep focus, completion, failure and incomplete coverage states explicit.
- **Do** retain the form-first mobile discovery order and accessible disclosures.

### Don't:
- **Don't** substitute obsolete palette values for the restored source tokens.
- **Don't** treat decorative illustration typography as the general heading family.
- **Don't** remove source references or obscure incomplete coverage to simplify a result.

Not canonized: uppercase kickers/eyebrows and glyph-based status icons carried by the restored build remain craft defects, not reusable rules. Dim text tokens are recorded as source evidence, not proof of contrast compliance. Superseded count sizes are excluded. Platform fallbacks are compatibility declarations, not a newly chosen display-font direction.
