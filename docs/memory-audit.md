# Memory & cleanliness audit — Cinnamon 6.8.4

Branch: `perf/memory-cleanup-audit` (based on `41893dd`). No merge to `master`.

## Method and its limits

This host is Windows with no GJS, Muffin, Valgrind or a X11/Wayland session, so a
real RSS/heap profile of the running shell **was not possible here**. Measurement
was done in two honest, reproducible layers:

1. **Static risk audit** over all 543 source files (211 `.js`, 112 `.c`,
   118 `.h`, 102 `.py`) using `tools/memory-audit.py`. It counts acquisition
   and growth patterns and emits file:line evidence. This is a risk density
   measure, not proof of a leak.
2. **Synthetic allocation benchmark** of the queue pattern found in the
   wallpaper settings loader (`tracemalloc`, Python 3.14.9, N=20,000).
3. A **runtime sampler for Linux** (`tools/measure-memory.sh`) is included for
   the measurement that must happen on a real desktop before any further
   changes are merged.

## Baseline (before changes)

| Pattern | Count | Note |
|---|---:|---|
| JS `.connect()` | 1,234 | 208 `.disconnect()`; not comparable — most use managed signal groups |
| JS `.push()` | 339 | 170 growth-only candidates, mostly function-local scratch arrays |
| C `g_object_ref()` | 92 | refcount balance must be judged per ownership path |
| `g_strdup()` | 133 | |
| JS `timeout_add()` | 91 | 138 `source_remove()`/`remove_timeout()` |
| C `g_malloc`/`g_new` family | 59 | |
| C `g_hash_table_new*` | 35 | mostly `_full` with destroy notifiers |
| whole-file Python reads | 14 | XML parsed from a full in-memory string (3 sites) |

Highest-density files (static): `js/ui/popupMenu.js` (53 connects),
`window-list@cinnamon.org/applet.js` (49), `js/ui/windowManager.js` (38),
`js/ui/virtualKeyboard.js` (36), `js/ui/networkAgent.js` (22 pushes),
`src/cinnamon-window-tracker.c` (18 `g_object_ref`).

**Important nuance:** the top connect sites are largely *not* leaks —
e.g. `js/ui/popupMenu.js:104` and `window-list/.../applet.js:291` use
`this._signals.connect(...)`, a managed signal group that disconnects on
destruction. Blindly adding `disconnect()` there would be wrong. Real leak
candidates need a lifetime review against `js/ui/windowManager.js:320-322`,
where callbacks are connected to the long-lived `global.settings`.

## Confirmed issues and fixes in this branch

### 1. Wallpaper loader drains queues with `list[1:]` — O(n²) allocation

`files/usr/share/cinnamon/cinnamon-settings/modules/cs_backgrounds.py`

- Producer: was `to_load = self._loading_queue[0]; self._loading_queue = self._loading_queue[1:]` (old 1346-1347).
- Consumer: was a `while len(...)>0` loop doing `self._loaded_data[0]` then `self._loaded_data[1:]` (old 1322-1324).
- Every item copy allocated a new list of length n-1, in both the loader thread
  and the GTK progress callback. With hundreds of wallpapers this is quadratic
  allocation churn while the settings UI is open.

Changed to `collections.deque` with `popleft()`/`clear()` (lines 10, 1226,
1232, 1275, 1288, 1323, 1346). Ordering, locking and empty-queue semantics
are unchanged.

Measured on this host (synthetic equivalent, N=20,000):

| Implementation | Peak traced allocation | Time |
|---|---:|---:|
| `list[1:]` drain | 929.1 KiB | 411.2 ms |
| `deque.popleft()` drain | 798.0 KiB | 7.4 ms |

### 2. Wallpaper XML parsed from a fully materialized string

`cs_backgrounds.py:1034` and `:1372`, plus
`files/usr/share/cinnamon/cinnamon-slideshow/cinnamon-slideshow.py:449`.

Old form: `f = open(filename); rootNode = ElementTree.fromstring(f.read()); f.close()`
held the entire XML file as a string *and* the parsed tree at once, and leaked
the file descriptor if parsing raised. Replaced with
`ElementTree.parse(filename).getroot()` — streams the file and is exception-safe.
Static count of whole-file Python reads went from 14 to 11 here, then to 9
after the second pass below.

### 3. Command/run-dialog history unbounded on load

`js/misc/history.js`

`_save()` trimmed to `_limit` (512) before persisting, but `_init()` and
`_historyChanged()` loaded `global.settings.get_strv()` with no trim: an
oversized or hand-edited persisted value stayed resident until the next write.
Added `_trimHistory()` (line 118) and called it at load (line 27), on settings
change (line 47), and from `_save()` (line 124). No change to the persisted
format or user-visible behavior.

### 4. Second pass — shell retention and settings churn

All of these were source-verified before editing; none required runtime evidence.

- `js/ui/main.js:194,1147-1149` — `_errorLogStack` had no bound; every `_log()`
  call pinned a fresh object for the session (the code itself warns about
  gesture debug log volume at `:1148-1153`). Now capped at 500 entries.
- `js/ui/lookingGlass.js:547-548,601-616` — Melange `_results`/`rawResults`
  grew forever and `_results[].o` pinned arbitrary eval'd objects. Both arrays
  are capped at 50, evicted entries have `o` nulled, and `index` is now a
  monotonic counter so `getResult()` (`:628-631`) still finds by index.
- `js/ui/lookingGlass.js:306-309,373-389` — `Inspector` was added to
  `Main.uiGroup` on construction but never destroyed on close, retaining the
  last inspected actor. `_close()` now guards re-entry, clears target refs and
  destroys the actor.
- `js/ui/popupMenu.js:1616-1625,1873-1886` — `global.menuStack` retained menus
  destroyed while open; `close()` also spliced with index `-1` when the entry
  was already gone. Both paths now remove defensively with an index guard.
- `js/ui/messageTray.js:685-704` — repeated `notify()` of the same notification
  re-added `clicked`/`destroy` closures capturing the Source. Connections now
  happen once per notification object.
- `src/cinnamon-perf-log.c:825-861` — `g_string_free(..., FALSE)` was handed to
  `write_string()` and never freed. Now written, freed, and returned via a
  local, matching the `replay_to_json` path at `:923-924`.
- `ExtensionCore.py:4,1069,1085-1096` — batched spices list drained with
  `del pending[:10]` (O(n²)); now a `deque` with `popleft()`.
- `cs_themes.py:787,839,897,931` — one 5 ms GLib timer per theme per refresh;
  progress is now applied directly, keeping only the final hide timer.
- `JsonSettingsWidgets.py:173-177,210-214` — whole-file `read()` + manual
  `close()`; now `with open(...)` + `json.load()`, preserving exception wrapping.

## Verification actually run

- `python3 -m py_compile` on all eight changed Python files — passed.
- `node --check` on `main.js`, `lookingGlass.js`, `popupMenu.js`,
  `messageTray.js`, `history.js` — passed.
- Stubbed Node harness exercising `HistoryManager` with a 600-entry setting:
  bounded to 5 on init, on `addItem()`/save, and on settings change — passed.
- Static audit re-run after both passes: `py_whole_read` 14 → 9 (the scanner
  now excludes its own source so it cannot count its pattern strings).
- GitHub CI on PR #1: Pattern Check passed, Build passed on LMDE 7 and Mint 27;
  Mint 22 failed only on pre-existing Codespell typos in files this branch
  does not touch (identical failures on an upstream run from 2026-11-22).

## Recommended next work (measure first)

1. **Run `tools/measure-memory.sh 2 120` on a real Linux session.** Do a
   baseline, then repeat with applets/extensions toggled; compare min/avg/max
   RSS. If RSS grows across enable/disable cycles, the suspect is a retained
   module/actor, not a static queue.
2. **Profile the suspect with `sysprof` or `valgrind --tool=massif`** for one
   full shell session. Static counts cannot distinguish managed signal groups
   from real leaks.
3. **Audit lifecycle of `js/ui/windowManager.js:320-322`** and other
   `global.settings.connect()` calls in long-lived singletons: ensure each
   owning object has a `destroy` path and that callbacks don't capture actors.
4. **Review `src/st/st-texture-cache.c` eviction** under repeated theme/icon
   changes — 9 `g_object_ref`, texture caches are a classic retained-memory
   source. This needs runtime evidence before touching C.
5. `js/ui/gestures/tracking.js:59-62` uses `shift()` per motion event; the
   window is only 150 ms so memory impact is negligible, but a ring buffer
   would remove the O(n) shift. Low priority.

No changes were merged and no visual/appearance asset was touched. The branch
was pushed to origin and PR #1 opened for review.
