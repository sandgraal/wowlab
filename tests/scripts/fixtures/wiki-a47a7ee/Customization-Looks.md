# Customization Looks

The **customization sandbox** lets you explore every race's character-customization options from the game's own data tables, keep a library of looks, compare them, and browse it all as a local web page.

> [!IMPORTANT]
> **Data only.** Nothing in `wowlab looks` writes into your install or reaches the game. A saved look is a JSON file in wowlab's own data folder. wowlab checks a look against the game's tables, but it cannot apply one to a character. That happens at the in-game barber shop.

## Browse what exists

```bash
uv run wowlab looks races                              # playable races and their body types
uv run wowlab looks options <race>                     # every option and choice, per body type
uv run wowlab looks options <race> --sex female --class <class>
```

Race and class can be an id or a name, as the lists show them. `--sex` takes the body type as the tables number it (`0` or `1`); `male` and `female` are read as `0` and `1`.

`looks options` also prints what the tables say about each choice: refusals, **"needs *unlock*"** notes, and other flags, so you can see which choices need something you may not have.

All of this comes from the game tables for **one exact build** (default: your flavor's version; use `--build` for another). The tables are downloaded once from wago.tools and cached. See [Game Data](Game-Data).

## Save, show, compare

```bash
uv run wowlab looks save "warm autumn" --race <race> --sex female --choice 12=34 --choice 15=2
uv run wowlab looks show                    # every saved look with its verdict
uv run wowlab looks show "warm autumn"
uv run wowlab looks compare "warm autumn" "cool winter"
```

- `--choice OPTION=CHOICE` takes the two ids as `looks options` lists them; repeat it for each option.
- `save` **checks the look against the build's tables**. A look the tables *refuse* is not saved (exit 1). Notes such as "needs *unlock*" or "unknown to build …" are shown but do not stop the save.
- `--replace` overwrites a saved look with the same name.
- `show` and `compare` re-check every look against one build, so a look saved against an older build tells you if it no longer fits.

## Import from a real character

```bash
uv run wowlab looks import-char "my main"     # from the character whose file was written last
uv run wowlab looks import-char "my alt" --character NAME
```

`import-char` reads the customization the [lab-addon](Lab-Addon-and-Characters) recorded at the character's **last barber-shop visit** and saves it as a look, checked the same way. A character with no recorded visit gets the addon's reason and exit 1. An id the tables do not have is noted "(possibly a hotfix)".

## The local web page

```bash
uv run wowlab looks page                      # writes pages/looks.html under your user data folder
uv run wowlab looks page --out ~/Desktop/looks.html
```

One self-contained HTML file with **no network requests**. It lets you browse races, body types, classes, options and choices, with what the tables say about each choice, and view your saved looks checked against that build. wowlab refuses to write it inside an install.

## Limits, honestly

- The recorded choices cover only the model being viewed at the barber shop.
- A paid change that keeps your race is invisible to the addon; a saved record is dropped only when the race no longer matches.
- Rendering a look in 3D is not part of wowlab today. It needs a way to read models from your game files, which is a separate, later piece of work.
