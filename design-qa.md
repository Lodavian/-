# 股票候选搜索 Design QA

## Evidence

- Source visual truth:
  - `C:\Users\PERSPE~1\AppData\Local\Temp\codex-clipboard-4f098a28-7b66-466d-a275-b2170370708e.png` — existing desktop screen, 1469 × 939 px.
  - `C:\Users\PERSPE~1\AppData\Local\Temp\codex-clipboard-244253cb-221e-4e67-b238-a639f8f813cb.jpg` — Webull mobile candidate hierarchy, 1200 × 2670 px.
- Browser-rendered implementation: `E:\python\Multi-broker trading\极速下单\qa-search-desktop.png` — 1265 × 712 px.
- Focused implementation crop: `E:\python\Multi-broker trading\极速下单\qa-search-focused.png` — 500 × 250 px.
- Browser viewport: 1280 × 720 CSS px, device pixel ratio 1.25. The in-app capture excludes a small browser edge, producing 1265 × 712 px; no density resampling was used.
- State: LIVE account connected, NCT entered, candidate popover open, no orders submitted.

## Full-view comparison

The search control remains in the existing desktop symbol strip and preserves the project's dark Liquid Glass tokens, typography, card radii, borders, and spacing. The candidate list overlays the market cards without moving the trading layout. The two NCT namesakes remain visually distinguishable by company, exchange, and trading status.

## Focused comparison

The mobile reference's hierarchy—company name first, then symbol and exchange—is preserved in the focused crop. The implementation adds explicit `可交易` / `不可交易` status pills because this is a direct-order terminal. The API-provided company name (`INTERCONT CAYMAN LTD`) is intentionally used instead of copying the mobile screenshot's older display name.

## Findings

- No actionable P0, P1, or P2 mismatch remains.
- Fonts and typography: matches the existing terminal's Inter / Segoe UI stack and hierarchy; candidate metadata remains readable at terminal density.
- Spacing and layout rhythm: popover aligns with the input, keeps consistent 11–16 px internal spacing, and no longer falls behind market cards.
- Colors and visual tokens: blue symbol emphasis, green tradable state, red unavailable state, and translucent surfaces reuse existing semantic tokens.
- Image quality and asset fidelity: no new raster assets or substitute icons are required for this data-only interaction.
- Copy and content: company, symbol, exchange, product type, and status are sourced from Webull's instrument response; unavailable instruments cannot be selected.

## Comparison history

1. Initial browser capture found the popover painted below the following market-card stacking context (P1). Added an explicit stacking order to the symbol strip and workspace.
2. Post-fix evidence in `qa-search-desktop.png` and `qa-search-focused.png` shows the complete two-row popover above the market area with both status pills visible.

## Interaction verification

- Entering `NCT` returned two Webull candidates.
- Selecting `INTERCONT CAYMAN LTD · NASDAQ · 可交易` changed the active symbol and subscription to NCT.
- The NYSE historical listing is shown as unavailable and cannot become a new trading target.
- Direct Enter without a chosen candidate requests candidates instead of blindly switching.
- Keyboard navigation, Escape dismissal, outside-click dismissal, empty/loading/error states, and stale-response rejection are implemented.
- Browser console after a clean full flow: no errors.
- Order count remained `0 ORDERS`; no order or cancellation action was invoked.

## Follow-up polish

- P3: The in-app browser surface did not expose viewport resizing, so the existing CSS narrow breakpoint was code-reviewed but not captured as a separate 390 px screenshot in this pass.

final result: passed
