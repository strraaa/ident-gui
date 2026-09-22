# IDENT × Битрикс24 — visual system

<!-- impeccable:design-schema 1 -->

## Direction

Operate-first status console: show the live data path and next action before configuration detail.

## Scene

Windows desktop used by an administrator in a dim office. Dark graphite surfaces reduce glare; blue is reserved for navigation and primary actions, while green, amber, and red communicate system state.

## Color

- Background `#0B1016`; panels `#111821`, `#161F2B`, `#1B2634`
- Text `#F5F7FB`; secondary `#9CA8B8`; faint `#6D7888`
- Accent `#71A9FF` / action `#3E82F5`
- Success `#57D28B`; warning `#F1B66B`; danger `#FF827D`
- Borders are structural, never decorative: `#2B3747` and `#3D4A5D`

## Typography

Inter/system UI for compact operational reading. Headings use tight tracking (`-.04em`), body text stays at 14px with 1.45 line-height, measurement values use tabular numerals.

## Layout

248px navigation rail; 74px topbar; 34px content gutter; 12px panel radius; 20px section gap. Main first viewport prioritizes the status pipeline, then activity and queue.

## Interaction

Buttons expose hover and keyboard focus. Refresh, restart, retry, and navigation show non-blocking status toasts. Destructive or service-changing actions are explicit; read-only monitoring never opens a modal.

## Accessibility

Visible `:focus-visible`, semantic sections and labels, live status region for toasts, and status labels that use text plus color. Keep contrast at WCAG AA or above.
