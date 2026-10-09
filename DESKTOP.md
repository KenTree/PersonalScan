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
```

`app.py` and `ui/` are retained as legacy development tools. The desktop bundle
does not include or run them.
