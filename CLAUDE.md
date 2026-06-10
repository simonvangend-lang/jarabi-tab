# jarabi-tab — guitar tab parser + score viewer

Parses Derek Gripper-style guitar tab PDFs (`parse_tab.py`) into
`scores/<id>.json`, rendered by the PWA viewer (`index.html`) deployed to
GitHub Pages. `add_score.py` is the parse → register → commit → push pipeline.

## Rules

- **After ANY change to `parse_tab.py`, run `python3 tests/run_regression.py`
  before committing.** It re-parses the test tabs and reports a structured
  diff against known-good baselines (see `tests/README.md`). Past parser
  fixes have silently introduced note pile-ups that forced full reverts —
  this catches them.
- Never commit a regenerated `scores/*.json` without the regression check
  passing or the diff being visually verified in the viewer.
- `scores/jarabi.json` has a hand-edited `bpm: 200` (parser default is 80).
  If jarabi is ever regenerated, restore the bpm manually.
- The source tab PDFs are copyrighted — they live in `tests/tabs/`
  (gitignored) and on the T9 drive. Never commit them; this repo is public.
- **Deploy with `python3 deploy.py "commit message"`** — it bumps the
  service-worker cache version in `sw.js`, runs the regression check if
  `parse_tab.py` changed, commits tracked changes, pushes, and polls the
  live URL until the new version is served. Never bump `sw.js` by hand or
  declare a deploy done without that verification.
- The unrelated `xhosa-vocab/` and `xhosa-vocab-desktop/` folders are
  separate projects that happen to live in this directory; don't touch them
  from here.
