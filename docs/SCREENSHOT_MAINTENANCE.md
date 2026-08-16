# User-guide screenshot maintenance

The screenshots in `docs/media/user-guide/` are part of the product documentation,
not decorative release art. Any change that moves, adds, removes, renames, enables,
or changes the behavior of a documented control must update both the relevant guide
text and every screenshot whose UI is no longer accurate.

## Regenerate the capture set

From the repository root, run:

```powershell
uv run python tools/capture_user_guide.py
```

The capture tool:

- opens the real PyQt desktop application at 1440 × 900;
- uses a temporary settings and data directory;
- disables phone access and does not read the developer's library or credentials;
- adds numbered cyan borders without covering the application labels; and
- writes images into `workspaces/`, `media-library/`, and `dialogs/` subfolders.

After regenerating, inspect every changed PNG at its original resolution. Update the
numbered callout explanation beside that image in `docs/USER_GUIDE.md`; a visually
correct image with stale callout text is still an invalid documentation change.

## Pull-request rule

For every UI change, choose one of these outcomes in the pull-request checklist:

1. Regenerate affected screenshots and update their explanations.
2. State why no documented screen or behavior changed.

Do not keep an old screenshot merely to avoid a binary diff. Screenshots must describe
the UI delivered by the same branch.
