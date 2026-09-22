# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated by the request: dependency-free Python standard-library server with static HTML, CSS, and JavaScript. The dashboard must run locally without a package manager or hosted service.

## Users

Developers and agent operators who use Jev Router and need to audit how their tasks were classified and routed across authenticated CLI workers.

## Product Purpose

Make Jev's existing local `run.json` records quickly understandable: which reasoning effort was selected and by whom, which model was inherited, why fallback occurred, what the subagent did, and whether the task succeeded. The model is never routed, so a model change is never presented as a routing decision.

## Positioning

It reads the router's actual recorded evidence rather than reconstructing decisions from logs or presenting inferred telemetry.

## Operating Context

The dashboard runs beside the Jev CLI on a developer workstation and reads `~/.config/jev-router/runs`. It is used after or during routed tasks for local inspection and debugging.

## Capabilities and Constraints

- Read existing Jev `run.json` files without modifying them.
- Display routing probabilities, the routed effort and its source, the inherited model, fallback, status, duration, and token usage.
- Display sanitized recorded prompts, task dependencies, and effort decisions as an always-open evidence flow.
- Bind to loopback only and make no outbound requests.
- Tolerate missing, historical, or malformed records.
- Keep recorded prompts visible in the local-only evidence flow while redacting common secret patterns.
- Package the companion Codex skill and project under Apache-2.0.

## Brand Commitments

Use the names “Jev Router Dashboard” and `jev-router-dashboard`. Repository ownership and copyright attribution belong to `yjsplay2002`.

## Evidence on Hand

Real Jev runs exist locally under `~/.config/jev-router/runs`; their current schema is represented in tests. No customer claims, benchmarks, or external telemetry should be invented.

## Product Principles

- Recorded evidence over inference.
- Local and private by default.
- Operational clarity before decoration.
- Zero-dependency installation and transparent code.
- Historical records should remain inspectable even when imperfect.

## Accessibility & Inclusion

The web interface must remain keyboard-operable, responsive, readable without color-only status cues, and respectful of reduced-motion preferences.
