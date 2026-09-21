---
name: Jev Router Dashboard
description: A calibrated decision ledger for local routing evidence.
colors:
  registration-blue: "#1859d1"
  paper: "#f4f2ec"
  surface: "#ffffff"
  ink: "#17191d"
  muted-ink: "#666d78"
  rule: "#ccd1d8"
  exception-coral: "#e54b3f"
  success-green: "#1c795b"
  pending-amber: "#aa6500"
typography:
  display:
    fontFamily: "Bahnschrift SemiCondensed, Arial Narrow, Aptos Narrow, sans-serif"
    fontSize: "clamp(40px, 7vw, 78px)"
    fontWeight: 700
    lineHeight: 0.86
    letterSpacing: "-0.04em"
  body:
    fontFamily: "Bahnschrift SemiCondensed, Arial Narrow, Aptos Narrow, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.4
  data:
    fontFamily: "ui-monospace, monospace"
    fontSize: "13px"
    fontWeight: 700
rounded:
  control: "4px"
  record: "6px"
  pill: "999px"
spacing:
  compact: "8px"
  standard: "16px"
  section: "28px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "11px 13px"
  record:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.record}"
    padding: "14px 16px"
---

# Design System: Jev Router Dashboard

## Overview

**Creative North Star: "The Calibrated Decision Ledger"**

The interface treats routing history as operational evidence laid onto a precise work surface. A single blue trace rail makes chronology tangible, while restrained rules and tabular data keep dense records scannable. Status color is sparse and always paired with text.

**Key Characteristics:**

- Continuous routing rail instead of a generic card grid.
- Paper, ink, and registration-blue construction.
- Compact evidence-first density with generous section breaks.
- Exception colors reserved for meaningful state.

## Colors

Registration blue carries topology and interaction; paper and graphite carry almost everything else.

### Primary

- **Registration Blue:** Connects the routing rail, probability bars, focus rings, and links.

### Secondary

- **Exception Coral:** Marks failed or unreadable records.
- **Success Green:** Marks completed records and localhost health.
- **Pending Amber:** Marks incomplete or indeterminate records.

### Neutral

- **Paper:** The workstation canvas.
- **Surface:** Run records and form controls.
- **Ink:** Primary type and decisive controls.
- **Muted Ink:** Paths, timestamps, labels, and secondary evidence.
- **Rule:** Boundaries and tabular separation.

**The Registration Rule.** Blue describes routing structure or interaction; it is not decorative fill.

## Typography

**Display Font:** Bahnschrift SemiCondensed with narrow system fallbacks

**Body Font:** The same condensed workhorse family

**Data Font:** The platform monospace stack

**Character:** Condensed lettering keeps operational density legible. Monospace is reserved for run IDs, filesystem paths, and numeric evidence.

### Hierarchy

- **Display:** Heavy, tightly tracked, and used once for the product title.
- **Title:** Compact task headings around 21px.
- **Body:** Narrow sans at normal reading size.
- **Label:** Small uppercase labels with expanded tracking for telemetry keys.
- **Data:** Monospace for identifiers, paths, timing, and token counts only.

**The Evidence Type Rule.** Use monospace only when character alignment or machine identity carries meaning.

## Layout

Content sits in a centered 1180px maximum-width work area. Aggregate telemetry forms one ruled strip, not a collection of floating cards. Runs follow a vertical chronological rail with short registration ticks. At 760px, telemetry and route facts collapse to two columns, timestamps yield to run identity, and the rail stays visible.

## Elevation & Depth

The system is intentionally flat. Hierarchy comes from paper/surface contrast, 1px rules, and the trace rail rather than shadows or simulated materials.

**The Flat Evidence Rule.** Routing records never float; they register against the shared rail.

## Shapes

Controls use a restrained 4px radius and records use 6px. Pills are reserved for compact state or scope indicators such as “localhost only.” Probability bars and rules remain square.

## Components

### Buttons

- **Shape:** Compact rectangular control with a 4px radius.
- **Primary:** Ink background, white label, and 11px by 13px padding.
- **Focus:** A visible 3px registration-blue outline with separation from the control.

### Cards / Containers

- **Corner Style:** Slightly curved record corners at 6px.
- **Background:** White surface against paper.
- **Shadow Strategy:** None.
- **Border:** One neutral rule, changing to registration blue on hover.
- **Internal Padding:** 14–18px depending on density.

### Inputs / Fields

- **Style:** White field, 1px neutral rule, and 4px corners.
- **Focus:** The same explicit blue focus ring as buttons.

### Routing Rail

Each run attaches to one persistent 1px blue rail. Status dots encode state at the record level, and probability bars reuse the rail color to connect selection evidence to the overall topology.

## Do's and Don'ts

### Do:

- **Do** preserve the continuous routing rail across responsive layouts.
- **Do** pair every state color with a readable status label.
- **Do** keep identifiers and measurement data typographically distinct from prose.
- **Do** use rules and alignment to create hierarchy.

### Don't:

- **Don't** replace the ledger with a same-size metric-card grid.
- **Don't** introduce gradients, glow, glass, or decorative shadows.
- **Don't** use blue as general decoration; it must carry topology or interaction.
- **Don't** expose full prompt content by default.
