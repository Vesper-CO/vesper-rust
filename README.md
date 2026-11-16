# vesper-rust

Builds the Rust client that Vesper's **Rust** instances run: Horizon's client with the name, window title,
logo, version text and settings changed to Vesper, and everything that talked to Horizon's servers turned off
(the "playing now" ping, the cosmetics service, Discord presence).

Based on [Horizon](https://github.com/horizon-rust/horizon) (MIT). See `NOTICE.md` and `LICENSE-HORIZON`.

## One-time setup

1. Create a public GitHub repository named `vesper-rust` under your account and push these files to it.
2. Make a signing key (Git Bash or any shell with OpenSSL):
   ```
   openssl genpkey -algorithm ed25519 -out rust-signing.pem
   openssl pkey -in rust-signing.pem -pubout -outform DER | tail -c 32 | base64
   ```
   The second command prints the **public key**. Keep `rust-signing.pem` private and never commit it.
3. In the repository, go to Settings, Secrets and variables, Actions, and add a secret named
   `RUST_SIGNING_KEY` containing the whole text of `rust-signing.pem`.
4. In Vesper's `src-tauri/src/rust.rs`, paste the public key between the quotes of `VESPER_KEY`.
5. In the repository's **Actions** tab, run **Build Vesper Rust client**. The first build is slow (30 to 60
   minutes). Then it runs every Monday on its own.

Until a build exists, Vesper falls back to Horizon's official build.

## Native mods: every mod of Horizon's library, automatically

Horizon's mod library ([horizon-mods](https://github.com/horizon-rust/horizon-mods)) has over a hundred native mods
(`.hnmod`): Rust code that is compiled into the game itself. Each weekly build runs `vesper_native.py`, which uses the
library's own tooling (the scripts it runs every day to find the mods that do not build) to:

1. take the newest, not taken down, version of every native mod in the published index, so a mod the library gained
   since last week is in the next build with no change here;
2. apply them together on that week's Horizon with horizon-mixins, leaving out the ones it refuses;
3. `cargo check` the game with them, leaving out the mods whose own code has the errors;
4. write `vesper-mods.json` (the mods in the build) beside the client, which Vesper's Mod Library reads: the mods show
   as **Built in** with a switch each, and start switched off.

If the whole set does not build, the next smaller set is tried (the mods in `native-mods.txt`, then none), so a week's
build is never lost to one mod. The run's summary page lists what was built in and why each other mod was left out.

* `native-mods-exclude.txt`: ids that are never built in (one per line).
* `native-mods.txt`: the fallback list, the mods you trust most.
* Run the workflow by hand and pick `listed` or `none` for a smaller build.

**Trust.** Every native mod is code that runs with the game's rights, and this puts the library's mods in a build signed
with your key. The library reviews each mod before publishing it, and Vesper only switches a mod on when the player does,
but the code is in the program: add anything you do not want to `native-mods-exclude.txt`, or use `listed`.

Mods that need something only Horizon's own launcher does (an API package, a post-build step) are left out.

## If a build fails

`rebrand.py` stops the build when Horizon has changed code it edits, so a half-renamed client is never
published. The log lists which edit no longer matches; fix that line in `rebrand.py`.
