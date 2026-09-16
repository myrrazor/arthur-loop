# Arthur Loop launch-site design

## Design thesis

- **Register:** Brand surface for a technical product.
- **Character:** Exact, inspectable, low-drama.
- **Vocabulary:** Terminal frame, prompt glyph, queue ledger, control contract, hairline rules.
- **Signature:** The real status GIF followed by the actual advisor-to-human web-console canvas, then the native menu extra.
- **Quiet elements:** Navigation, install rows, FAQ, and footer use flat surfaces and ordinary links.

## Direction decision

The chosen direction is “written control contract”: system sans for reading, monospace for commands and state, terminal green reserved for action/live state, and an ordered contract rather than a decorative architecture diagram. `[H]` It fits because Arthur Loop’s value is explicit state and enforced transitions.

Rejected directions:

- Generic dark AI launch page with violet gradients, glows, oversized type, and equal feature cards. It would say “AI product” without explaining Arthur Loop.
- Parchment/fantasy identity. The name supports the metaphor, but it fights the terminal-native product and the requested dark wordmark.

## Color and type

Dark tokens use `#0a0d0b` background, `#101612` surface, `#edf7ef` text, `#a3b2a7` muted text, and `#55e878` action. Light mode uses `#f5f7f3`, `#ffffff`, `#152019`, `#4f6054`, and `#167337`.

Verified WCAG contrast pairs:

| Pair | Ratio | Use |
| --- | ---: | --- |
| Dark text / background | 17.81:1 | Body and headings |
| Dark muted / background | 8.82:1 | Secondary copy |
| Dark action ink / action | 11.94:1 | Primary buttons |
| Light text / background | 15.55:1 | Body and headings |
| Light muted / background | 6.22:1 | Secondary copy |
| Light action ink / action | 5.93:1 | Primary buttons |

The UI stack is the operating-system sans. Commands, IDs, state, and the wordmark use the operating-system monospace. No network font requests.

## Layout and surfaces

- Maximum content width: 75rem; readable copy stays around 46rem.
- Desktop uses a two-column hero and section label/content split. Below 860px both become a single reading column.
- Mobile keeps only the install action in navigation and turns method labels into stacked rows.
- Containers mark real boundaries: the contract, command rows, and product screenshots. Feature copy remains a ruled list instead of a card wall.
- Radius is 6–8px for controls and media frames. There is no ambient glow or elevation stack.

## Interaction and access

- Copy buttons expose visible labels and accessible names, report copied/fallback state, and work without the Clipboard API.
- Links and buttons are at least 44px tall where they are primary mobile targets. All keyboard controls retain a 3px `:focus-visible` outline. `[S]`
- FAQ uses native `details`/`summary`; the page remains useful without JavaScript.
- Motion is limited to 160ms color changes. Reduced-motion mode removes smooth scrolling and collapses transitions. `[S]`
- Ordinary content reflows without horizontal page scrolling at 390px. Long commands scroll inside their own bounded rows. `[S]`

## Imagery and claims

Only real Arthur Loop output is used: the VHS status/tick run and a seeded localhost console. No customer marks, testimonials, user counts, benchmarks, or fake charts.
