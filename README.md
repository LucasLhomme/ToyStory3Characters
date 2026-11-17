# Toy Story 3 Characters for Disney Infinity 3.0

Woody, Jessie, Buzz Lightyear and Emperor Zurg from *Toy Story 3: The Video Game*, playable in Disney Infinity 3.0
through [CrabeLoader](https://github.com/LucasLhomme/CrabeLoader).

## Install (players)

1. Install CrabeLoader.
2. Unzip `ts3_characters-<version>.zip` into the game's `mods\` folder, so that you get `mods\ts3_characters\mod.json`.
3. Start the game: the four characters are in the character grid.

## Build (developers)

The build reads the game installs of Disney Infinity 3.0 and Toy Story 3, so it only runs on a machine that has them,
plus Blender 5.2 and the DIM2 tool folders (`CrabeLoader/tools`, `DisneyInfinityCreator`) next to this repo.

```powershell
.\tools\package.ps1 -Version 1.0.0     # build, render the icons, zip into dist\
.\deploy.ps1                           # copy build\mod into the game's mods\ folder
```

Hand fixes in Blender: open `build\blender_scene\ts3_characters.blend`
(`blender ... --python tools\blender_open_editing.py`), move vertices or paint weights, then run
`tools\blender_export_edits.py`; the next build applies `edits\<char>.npz`.

## Releases

Pushing a tag `vX.Y.Z` runs `.github/workflows/release.yml`: it builds on a **self-hosted runner** (GitHub's own
machines do not have the games) and opens a **draft** release with `ts3_characters-X.Y.Z.zip`. The tag must match
`mod.json`'s `version`, otherwise the job stops.

```powershell
git tag v1.0.0
git push origin v1.0.0
```

### Runner setup (once, on the build PC)

1. GitHub > repository > Settings > Actions > Runners > *New self-hosted runner* > Windows x64, and follow the
   download / `config.cmd` steps it shows (default labels `self-hosted, windows`).
2. Run it as your own user (`run.cmd`, or the service under your account) so it sees the game folders, Python
   (numpy, scipy, Pillow, cryptography) and Blender.
3. If the DIM2 tools are not in `E:\Dev\DIM2`, change `DIM2_ROOT` in the workflow; set `BLENDER` if Blender is
   not in its default folder.
