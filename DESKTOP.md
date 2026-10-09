# PersonalScan for Mac

PersonalScan uses native Qt desktop widgets. It runs the Gmail scanner in a
background thread, with no local HTTP server or browser dashboard.

## Run and build

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python desktop.py
.venv/bin/python build_desktop.py
open dist/PersonalScan.app
```

The app bundle includes Python and its runtime dependencies. It targets Apple
Silicon Macs. Ollama and a downloaded model are optional external dependencies
for local AI; source excerpts work without them. The build has an ad-hoc local
signature, not an Apple notarization for distribution.

## Private settings

Desktop settings and the imported Google Desktop OAuth client are stored in
`~/Library/Application Support/PersonalScan/`, outside this repository. Gmail
authorization remains in macOS Keychain under `personal-scanner-gmail`, preserving
existing account IDs and authorizations.

Running from source migrates the repo's private `config.json` and `credentials.json`
on first launch. To migrate when launching a fresh bundle:

```sh
open dist/PersonalScan.app --args --import-from "$PWD"
```

Private settings and OAuth credentials are never included in the app build.
Settings lets you add accounts, import credentials, authorize the selected
account, and adjust lookback, sender rules, and the local model. Adding an account
requires saving or choosing Authorize selected. Account authorizations are
independent; choose the matching Gmail account on Google's sign-in page.

Choose **All Gmail accounts** to scan every configured account one by one, or
choose a single account. Scan limits are **1–150 threads per account**. The
combined summary groups notable thread summaries by Gmail address, and all result
tabs include the source account. A failed account is shown in Retrieval failures
and the remaining accounts continue. Authorize accounts individually in Settings.

Mentor addresses, game studio domains, and bank domains each have their own list
editor. Use **+** to add an entry, double-click to edit, or remove the selected
entry. **Save and close** accepts the list into the Settings draft; save Settings
to apply it. **Leave without saving** discards that dialog's changes. Leaving
Settings without saving also discards changes accepted in the list editors.

The scanner looks back the configured number of
days and excludes spam, trash, social, and promotions before fetching threads.
Threads scanned lists successfully retrieved threads; Retrieval failures lists
thread IDs that could not be read after retries. A limit warning means older
matching threads may remain. Results stay in memory and clear when switching
accounts. Cancel stops browser authorization immediately, including when Google
denies access without returning to the app. Its isolated helper and local OAuth
callback listener are shut down, and controls become available again. Closing
during authorization performs the same cancellation. During scans, cancellation
stops between requests; an in-flight Google or model request must finish first.

## Development verification

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest test_desktop
.venv/bin/python -m unittest test_scanner test_accounts
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest test_file_manager
```

`app.py` and `ui/` are retained as legacy development tools. The desktop bundle
does not include or run them.

## Local AI File Manager (testing)

Open **File Manager** from the desktop window. Choose personal folders with
**+ Add folder**, set a minimum number of days since modification (90 by default),
and click **Review with local AI**. Downloads and Desktop are offered initially;
scanning only starts when requested. Folder choices are stored in private desktop
settings. The model is the local Ollama model configured in Settings.

The inventory inspects filenames, paths, byte sizes, modification dates, and
filesystem access timestamps; it does not read file contents. Older screenshots,
schoolwork-related filenames/folders, and Downloads files become candidates.
Hidden files, symbolic links, system/library/build folders, application/document
packages, cloud placeholders, private key/database file types, and filenames
suggesting sensitive or important records are skipped. These exclusions are
heuristics, not a complete classification of important files.

The inventory is bounded at 5,000 regular files and local AI analysis at 40
candidates by default (adjustable up to 200), preferring the oldest candidates.
Coverage and analysis limits and unreadable folders are reported. Results appear
under **Suggested for review**, **Keep**, and **Review failures**, including facts
supporting each model suggestion. Invalid or incomplete model output produces
review failures rather than fabricated recommendations. The app never deletes,
moves, or renames reviewed files; **Reveal in Finder** lets you inspect them.

An old modification date does not establish when a file was downloaded or whether
it is still useful. Filesystem access dates can reflect automated activity rather
than human use. The app cannot determine whether schoolwork was actually
submitted, or prove that a file was never used again. Local AI recommendations
are tentative, with the original metadata available for your review.

File review uses an owned Ollama process with cloud features disabled. Only
filenames, relative paths, sizes, and computed facts reach the local model. No
cloud API is used, and the feature works without Gmail authorization. Cancel and
closing the review window interrupt the review and stop its owned model server,
including a pending inference request. Review results stay in memory.
