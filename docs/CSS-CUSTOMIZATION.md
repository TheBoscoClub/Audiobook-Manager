# CSS Customization Guide

Customize the Audiobook Manager web interface to match your aesthetic preferences.

## CSS Architecture

The web UI uses modular CSS files located in `library/web-v2/css/`:

| File | Purpose |
|------|---------|
| `theme-art-deco.css` | **Primary customization file** - colors, fonts, shadows, spacing |
| `layout.css` | Page structure, header, search section, grid layouts |
| `library.css` | Book cards, covers, editions, metadata display |
| `components.css` | Buttons, inputs, dropdowns, badges, pagination |
| `sidebar.css` | Collection sidebar, filters, toggle buttons |
| `player.css` | Audio player controls, progress bar, track info |
| `modals.css` | Modal dialogs, duplicate management, stat boxes |
| `utilities.css` | Back Office specific styles |

## Quick Customization via CSS Variables

The easiest way to customize is by modifying CSS variables in `theme-art-deco.css`. Changes propagate automatically throughout the UI.

### Color Palette

```css
:root {
    /* Background colors - darker = more depth */
    --deco-black: #080808;      /* Deepest background */
    --deco-charcoal: #141414;   /* Pattern/secondary background */
    --deco-slate: #1e1e1e;      /* Card backgrounds, panels */
    --deco-gray: #333333;       /* Hover states, lighter panels */

    /* Gold accents - the Art Deco signature */
    --gold-bright: #FFD700;     /* Highlights, active states */
    --gold: #DAA520;            /* Primary accent (borders, buttons) */
    --gold-dark: #B8860B;       /* Subtle accents */
    --gold-deep: #8b6914;       /* Sunburst rays, muted gold */

    /* Text colors */
    --cream: #F5DEB3;           /* Standard text */
    --cream-light: #FFF8DC;     /* Bright text (titles, emphasis) */
    --parchment: #e8dcc8;       /* Secondary text */
}
```

**To change the color scheme:**
1. Edit the hex values in `theme-art-deco.css`
2. Keep contrast ratios accessible (light text on dark backgrounds)
3. The gold accents can be changed to any color (blue, green, etc.)

### Typography

```css
:root {
    /* Font families */
    --font-primary: 'Optima', 'Century Gothic', 'Segoe UI', sans-serif;
    --font-serif: 'Georgia', 'Times New Roman', serif;
    --font-mono: 'Consolas', 'Monaco', monospace;
}
```

**To use different fonts:**
1. Add a Google Fonts `@import` at the top of `theme-art-deco.css`
2. Update the `--font-primary` and `--font-serif` variables

Example with Inter + Merriweather:
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Merriweather:wght@400;700&display=swap');

:root {
    --font-primary: 'Inter', sans-serif;
    --font-serif: 'Merriweather', serif;
}
```

### Shadow System

The UI uses a 4-level elevation system for depth:

```css
:root {
    /* Level 1: Subtle (inputs, small buttons) */
    --shadow-elevation-1:
        0 2px 4px rgba(0, 0, 0, 0.3),
        0 1px 2px rgba(0, 0, 0, 0.2);

    /* Level 2: Moderate (cards, panels) */
    --shadow-elevation-2:
        0 4px 8px rgba(0, 0, 0, 0.35),
        0 2px 4px rgba(0, 0, 0, 0.25);

    /* Level 3: Prominent (hover states) */
    --shadow-elevation-3:
        0 8px 16px rgba(0, 0, 0, 0.4),
        0 4px 8px rgba(0, 0, 0, 0.3);

    /* Level 4: High (modals, dropdowns) */
    --shadow-elevation-4:
        0 16px 32px rgba(0, 0, 0, 0.45),
        0 8px 16px rgba(0, 0, 0, 0.35);

    /* Gold glow for Art Deco effect */
    --shadow-gold-glow: 0 0 20px rgba(218, 165, 32, 0.15);
    --shadow-gold-glow-strong: 0 0 30px rgba(218, 165, 32, 0.25);

    /* Inset shadow for recessed elements */
    --shadow-inset: inset 0 2px 4px rgba(0, 0, 0, 0.3);
}
```

**To reduce shadows** (flatter design):
- Set shadow variables to `none` or reduce opacity values

**To increase shadows** (more dramatic):
- Increase blur radius (3rd value) and spread
- Increase opacity in rgba()

### Spacing

```css
:root {
    --spacing-xs: 0.25rem;   /* 4px */
    --spacing-sm: 0.5rem;    /* 8px */
    --spacing-md: 1rem;      /* 16px */
    --spacing-lg: 1.5rem;    /* 24px */
    --spacing-xl: 2rem;      /* 32px */
    --border-radius: 4px;    /* Corner rounding */
}
```

## Module-Specific Customization

### Book Cards (`library.css`)

```css
/* Card appearance */
.book-card {
    background: linear-gradient(...);  /* Card gradient */
    border: 3px solid var(--gold);     /* Border color/width */
    padding: 1.5rem;                   /* Inner spacing */
}

/* Book title */
.book-title {
    font-size: 1rem;
    font-weight: bold;
    color: var(--gold);
}

/* Author/narrator text */
.book-author { color: var(--cream-light); }
.book-narrator { font-style: italic; }
```

### Buttons (`components.css`)

```css
/* Primary button (gold) */
.btn-primary {
    background: linear-gradient(180deg, var(--gold) 0%, var(--gold-dark) 100%);
    color: var(--deco-black);
}

/* Secondary button (outline) */
.btn-secondary {
    background: transparent;
    border: 2px solid var(--gold);
    color: var(--gold);
}
```

### Header (`layout.css`)

The sunburst header effect:
```css
.library-header {
    background:
        /* Sunburst rays */
        repeating-conic-gradient(
            from 0deg at 50% 100%,
            #5a4210 0deg 2deg,      /* Ray color */
            transparent 2deg 10deg  /* Ray spacing */
        ),
        /* Base gradient */
        linear-gradient(180deg, #121212 0%, #050505 100%);
}
```

**To remove the sunburst:**
```css
.library-header {
    background: linear-gradient(180deg, var(--deco-slate) 0%, var(--deco-black) 100%);
}
```

## Creating a Custom Theme

For major changes, create a new theme file:

1. Copy `theme-art-deco.css` to `theme-custom.css`
2. Modify colors, fonts, shadows as desired
3. Update `library.css` import at line 6:
   ```css
   @import url('theme-custom.css');
   ```

### Alternative Theme Ideas

**Dark Blue/Silver (Modern):**
```css
--deco-black: #0a0f1a;
--deco-charcoal: #141d2e;
--gold: #94a3b8;        /* Silver */
--gold-bright: #e2e8f0;
```

**Dark Green/Gold (Library):**
```css
--deco-black: #0a120a;
--deco-charcoal: #1a2e1a;
--gold: #c9a227;
```

**Purple/Pink (Cyberpunk):**
```css
--deco-black: #0d0015;
--deco-charcoal: #1a0a2e;
--gold: #ff6b9d;
--gold-bright: #ff9ecf;
```

## After Making Changes

CSS changes take effect immediately on browser refresh. No restart required.

If you've modified files in the project directory:
```bash
# Deploy changes to production
sudo rsync -av library/web-v2/css/ /opt/audiobooks/library/web-v2/css/
sudo chown -R audiobooks:audiobooks /opt/audiobooks/library/web-v2/css/
```

## Accessibility Notes

When customizing colors:
- Maintain minimum 4.5:1 contrast ratio for normal text
- Maintain minimum 3:1 contrast ratio for large text and UI components
- Test with browser DevTools color contrast checker
- Consider users with color vision deficiency (avoid red/green as sole differentiators)
