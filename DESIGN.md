---
name: Jev Router Dashboard
description: A gearbox shift gate for per-turn effort routing.
colors:
  ground: "#dde0e4"
  surface: "#eceef1"
  ink: "#15181c"
  ink-2: "#4a515b"
  rule: "#b6bcc5"
  plate: "#1e2227"
  plate-edge: "#0d0f12"
  plate-ink: "#eceff2"
  plate-ink-2: "#a1a9b3"
  slot: "#0c0e11"
  knob: "#efe9dc"
  engaged: "#2e9d5a"
  engaged-ink: "#186b3a"
  engaged-lamp: "#4fd07f"
  selected: "#c9861b"
  selected-ink: "#8a5600"
  selected-lamp: "#f0b13a"
  neutral: "#8a929c"
  focus: "#1f5fbf"
  error: "#b3261e"
  dark-ground: "#121418"
  dark-surface: "#1a1d22"
  dark-ink: "#e7eaee"
  dark-ink-2: "#9ba3ad"
  dark-rule: "#2e333a"
  dark-plate: "#0b0d10"
  dark-engaged-ink: "#57cf85"
  dark-focus: "#7aa7ff"
typography:
  display:
    fontFamily: "Barlow Condensed (self-hosted, OFL), Bahnschrift SemiCondensed, Arial Narrow, sans-serif"
    fontWeight: 700
    textTransform: uppercase
  label:
    fontFamily: "Barlow Condensed"
    fontSize: "12px"
    fontWeight: 500
    letterSpacing: "0.14em"
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    lineHeight: 1.45
  data:
    fontFamily: "ui-monospace, Cascadia Mono, Consolas, monospace"
    fontSize: "13px"
rounded:
  tile: "3px"
  control: "5px"
  panel: "8px"
  plate: "10px"
spacing:
  tight: "8px"
  standard: "14px"
  section: "34px"
---

# Design System: Jev Router Dashboard

## Overview

**North Star: "The Shift Gate."** Every prompt is a gear change. The hook *selects* a gear; the effort proxy *engages* it on the real request. The dashboard exists to show, per turn, which gear ran and whether it was actually engaged. It refuses the stat-card grid and the generic run-card feed.

Physical scene: a developer at a workstation beside a terminal, in ordinary room light. The surface is light machined aluminum with one dark instrument (the gate plate); dark mode follows the OS and keeps the same roles.

## Colors

Restrained: neutrals plus three signal lamps. Color only ever means state, and every state also has words.

- **Ground / Surface / Rule / Ink:** Machined-aluminum grey with a fine horizontal grain (a 1px repeating line, never a gradient wash). Surface lifts panels; rules separate rows.
- **Plate:** The only dark region. Graphite, four machine screws, an engraved H-pattern gate cut in `slot` black, a bone `knob`.
- **Engaged (green):** The proxy put this gear on the turn's requests. On the plate it glows (`engaged-lamp`); in the log it is `engaged-ink`.
- **Selected (amber):** Jev chose a gear but no request of that turn passed the proxy. The knob turns hollow with an amber ring.
- **Neutral (grey N):** Fallback. Jev did not answer in time; the host's own setting ran. The knob sits in neutral and a dashed ghost ring marks the host default.
- **Focus blue:** Keyboard focus only.

## Typography

- **Display:** Barlow Condensed 700, uppercase, self-hosted from `dashboard/fonts/` (SIL OFL). Gear names, section titles, button labels. The gear name on the plate is the one large moment (56–96px).
- **Label:** Barlow Condensed 500, 12px, tracked 0.14em, uppercase. Column heads and fact keys.
- **Body:** System UI sans for prose and prompts.
- **Data:** Monospace only for session ids, paths, timings, percentages.

## Layout

1240px max width. First viewport: top bar (wordmark, runs path, proxy linkage lamp), the full-width gate plate (gate left, last-turn readout right), then the default-gear form. Below: the shift log, a ruled list whose rows expand in place (`<details>`). Under 1000px the log row stacks; under 760px the plate stacks and the gate caps at 340px. No horizontal scroll at 390px.

## Components

- **Gate plate:** SVG gate drawn from the configured `efforts` list: slots alternate up/down along one rail; neutral sits on the rail between the first two slots. Readout: gear name, engagement lamp line, host/session/when/Jev facts, prompt excerpt (two lines), probability ratio bar with a mono legend, and a footnote stating the measured cache behavior.
- **Gear tiles:** The gate read flat in a log row: one 26px tile per level; the engaged or selected gear is filled ink; a fallback's host default is dashed.
- **Engagement label:** Lamp dot + uppercase state + a sans note (`6 shifted`, `no request passed the proxy`, the fallback reason).
- **Default gear form:** One native `<select>` per provider; "Host's own setting" clears it. Primary button is ink and turns engaged-green on hover.
- **Probability bars:** SVG geometry (never inline styles, CSP `style-src 'self'`); the chosen level is ink, the rest muted.

## Motion

One authored moment: when a new turn arrives, the knob leaves its old slot, runs the rail, drops into the new slot, overshoots 6px and settles (700ms, Web Animations API). Everything else is a 150–180ms color or rotation transition. `prefers-reduced-motion` places the knob instantly and removes transitions.

## Rules

- Never present a model change: the model is inherited and the UI says so.
- Never claim engagement without proxy log evidence; a turn with zero proxied requests is "Selected, not engaged".
- State is never color-only.
- No inline style attributes; widths are SVG geometry.
