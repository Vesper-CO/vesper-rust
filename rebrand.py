#!/usr/bin/env python3
"""Renames Horizon's client to Vesper and switches off everything that talks to Horizon's servers.

Usage: python rebrand.py <unpacked horizon source folder>

Every edit is checked: a REQUIRED edit that no longer matches (Horizon changed that code) stops the build,
so a half-rebranded client is never published. OPTIONAL edits only warn.
"""
import pathlib, re, sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "src")
CLIENT_CRATES = ["horizon-client", "horizon-client-render", "horizon-client-core", "horizon-client-input"]
failures, done = [], []


def edit(rel, pattern, repl, required=True, regex=False, flags=0):
    p = root / rel
    if not p.exists():
        (failures if required else done).append(f"missing file {rel}")
        return
    text = p.read_text(encoding="utf-8")
    new, n = (re.subn(pattern, repl, text, flags=flags) if regex else (text.replace(pattern, repl), text.count(pattern)))
    if n == 0:
        (failures if required else done).append(f"{'REQUIRED' if required else 'optional'} edit not found in {rel}: {pattern[:60]!r}")
        return
    p.write_text(new, encoding="utf-8")
    done.append(f"ok  {rel} ({n})")


# ---- window titles, boot splash, title screen -------------------------------------------------
# The window title is set in a few places (Horizon moves it between files, so each is optional; the sweep over every
# client string further down catches any that are left).
for rel in ["crates/horizon-client/src/game.rs", "crates/horizon-client/src/game/no_resources.rs",
            "crates/horizon-client/src/game/window_handler.rs", "crates/horizon-client/src/game/loading_screen.rs"]:
    edit(rel, '.with_title("Horizon")', '.with_title("Vesper")', required=False)
edit("crates/horizon-client/src/game/split/windows.rs", '"Horizon', '"Vesper')
edit("crates/horizon-client/src/ui_theme.rs", 'BOOT_TEXT, "Horizon"', 'BOOT_TEXT, "Vesper"')

# The version line on the title screen ("Horizon 26.x (Rust)") and the nightly label in the corner.
edit("crates/horizon-client/src/title.rs",
     r'let version = format!\("Horizon \{\} \(Rust\)",[^;]*;\s*batch\.text\(atlas, &version[^;]*;', "", regex=True)
edit("crates/horizon-client/src/title.rs",
     r'if let Some\(nightly\) = horizon_core::release::nightly_label\(\) \{\s*batch\.text\(atlas, &nightly[^;]*;\s*\}', "", regex=True)

# ---- settings -----------------------------------------------------------------------------------
edit("crates/horizon-client-render/src/settings_shell.rs", 'Text::literal("Horizon")', 'Text::literal("Vesper")')

# ---- nothing is sent to Horizon's servers ---------------------------------------------------------
edit("crates/horizon-telemetry/src/lib.rs", "        _ => Some(DEFAULT_ENDPOINT.to_owned()),", "        _ => None,")
edit("crates/horizon-cosmetics/src/lib.rs", "pub const ON_BY_DEFAULT: bool = true;", "pub const ON_BY_DEFAULT: bool = false;")
# Discord presence: use Vesper's own Discord application when the build gives its id (VESPER_DISCORD_APP_ID),
# otherwise switch the presence off. Horizon moved this into the horizon-discord crate.
import os
discord_id = os.environ.get("VESPER_DISCORD_APP_ID", "").strip()
if discord_id.isdigit():
    edit("crates/horizon-discord/src/lib.rs", 'pub const DISCORD_APP_ID: &str = "1563857609738690560";',
         f'pub const DISCORD_APP_ID: &str = "{discord_id}";')
else:
    edit("crates/horizon-discord/src/lib.rs", "pub fn start(automated: bool) {\n",
         "pub fn start(automated: bool) {\n    if true {\n        return; // no Discord application of Vesper's own\n    }\n")
# ---- remaining visible text and links -------------------------------------------------------------
edit("crates/horizon-client/src/no_resources.rs", "Download the launcher at playhorizon.dev, then press Play there.", "Open Vesper and press Play there.", required=False)
edit("crates/horizon-client/src/lan/mod.rs", "This server is for the Horizon client: playhorizon.dev", "This server is for the Vesper client: github.com/jakedev2/vesper", required=False)
edit("crates/horizon-client/Cargo.toml", 'description = "Horizon native development client"', 'description = "Vesper"', required=False)

# Any other string that starts with the name: menus, tooltips, logs, the F3 screen, user agents.
count = 0
for crate in CLIENT_CRATES:
    for f in (root / "crates" / crate / "src").rglob("*.rs"):
        t = f.read_text(encoding="utf-8")
        n = re.sub(r'"Horizon(?=["\s:{/])', '"Vesper', t)
        if n != t:
            f.write_text(n, encoding="utf-8"); count += 1
done.append(f"ok  text strings in {count} client source files")

# Language files, every language.
langs = list((root / "crates/horizon-client/assets/horizon/lang").glob("*.json"))
for f in langs:
    t = f.read_text(encoding="utf-8")
    f.write_text(re.sub(r"\bHorizon\b", "Vesper", t), encoding="utf-8")
done.append(f"ok  {len(langs)} language files")

# The first window (painted before the engine loads) draws the name big; it lives in horizon-threads, which the
# client-crate sweep above does not reach.
edit("crates/horizon-threads/src/boot_paint.rs", '"Horizon".encode_utf16()', '"Vesper".encode_utf16()', required=False)

# ---- Vesper's HUD editor ------------------------------------------------------------------------------------
# Replaces Horizon's HUD editor buttons with Vesper's menu (patches/vesper_hud_panel.rs, drawn with the design system's
# Ui, and patches/vesper_hud.rs, the glue to the settings) and adds a small button for it to the pause menu's icon row,
# beside the game's own. Every edit is checked first and all are made together, or none: a Horizon update that moves
# this code only skips the editor (Horizon's own stays) instead of breaking the build.
# Set VESPER_HUD_EDITOR = False to turn it off.
VESPER_HUD_EDITOR = True


def vesper_hud_editor():
    here = pathlib.Path(__file__).resolve().parent / "patches"
    panel_src, glue_src = here / "vesper_hud_panel.rs", here / "vesper_hud.rs"
    if not (panel_src.exists() and glue_src.exists()):
        done.append("skipped Vesper HUD editor: patches/ not found")
        return
    crate = root / "crates/horizon-client/src"
    editor = crate / "game/hud_editor.rs"
    screens = crate / "game/screens.rs"
    game = crate / "game.rs"
    lib = crate / "client_lib.rs"
    if not all(p.exists() for p in (editor, screens, game, lib)):
        done.append("skipped Vesper HUD editor: a Horizon file moved")
        return
    texts = {p: p.read_text(encoding="utf-8") for p in (editor, screens, game, lib)}

    # the tail of draw_hud_editor (the style buttons) is replaced by the menu
    t = texts[editor]
    start = t.find("        let places = buttons((w, h));\n")
    end = t.find("    /// A press: a button, else the widget under the cursor")
    draw_new = (
        "        // Vesper: the HUD editor menu replaces the buttons.\n"
        "        let snap = self.vesper_snapshot();\n"
        "        let cursor = self.cursor;\n"
        "        if let Some(e) = self.pvp.editor.as_mut() {\n"
        "            e.vesper.draw(atlas, batch, cursor, &snap);\n"
        "        }\n"
        "    }\n"
    )
    ok = 0 < start < end
    edits = []  # (file, old, new)
    if ok:
        texts[editor] = t[:start] + draw_new + t[end:]
        edits += [
            (editor, "    /// Opened from the PvP & HUD page: Done goes back there.\n    from_options: bool,\n}\n",
             "    /// Opened from the PvP & HUD page: Done goes back there.\n    from_options: bool,\n"
             "    /// Vesper's menu.\n    pub(super) vesper: horizon_client::vesper_hud_panel::PanelState,\n}\n"),
            (editor, "    pub(super) fn hud_editor_press(&mut self, right: bool) {\n        let size = self.gui_size();\n",
             "    pub(super) fn hud_editor_press(&mut self, right: bool) {\n        if self.vesper_press(right) {\n            return;\n        }\n"
             "        let size = self.gui_size();\n"),
            (editor, "            if right || !ui::hovered(self.cursor, x, y, BUTTON_W, BUTTON_H) {\n",
             "            if true || right || !ui::hovered(self.cursor, x, y, BUTTON_W, BUTTON_H) {\n"),
            (editor, "            e.drag = Some((id, self.cursor.0 - x, self.cursor.1 - y));\n",
             "            e.drag = Some((id, self.cursor.0 - x, self.cursor.1 - y));\n            e.vesper.select(id.index());\n"),
            (editor, "    pub(super) fn hud_editor_drag(&mut self) {\n",
             "    pub(super) fn hud_editor_drag(&mut self) {\n        if self.vesper_drag() {\n            return;\n        }\n"),
            (editor, "    pub(super) fn hud_editor_release(&mut self) {\n",
             "    pub(super) fn hud_editor_release(&mut self) {\n        self.vesper_release();\n"),
            (game, "mod hud_editor;\n", "mod hud_editor;\nmod vesper_hud;\n"),
            (lib, "pub mod ui_theme;\n", "pub mod ui_theme;\npub mod vesper_hud_panel;\n"),
            (screens, "        // SpriteIconButton.withTootip: the label as a tooltip.\n        if let Some(tip) = tooltip {\n",
             "        // Vesper: the HUD editor button, after the icon row.\n        {\n"
             "            let (vx, vy, _) = pause_icons(w, h, links, additions.is_some(), &self.pause_rows())[3];\n"
             "            let (vx, vy) = (vx + 24.0, vy);\n"
             "            let hover = ui::hovered(self.cursor, vx, vy, 20.0, 20.0);\n"
             "            ui::button(batch, atlas, vx, vy, 20.0, 20.0, &Text::empty(), true, hover);\n"
             "            horizon_client::vesper_hud_panel::draw_pause_icon(batch, atlas, vx, vy, hover);\n"
             "            if hover {\n                tooltip = Some(Text::literal(\"Vesper HUD editor\"));\n            }\n        }\n"
             "        // SpriteIconButton.withTootip: the label as a tooltip.\n        if let Some(tip) = tooltip {\n"),
            (screens, "        let friends = icons[FRIENDS_ICON];\n        let hit = pause_buttons(\n",
             "        // Vesper: the HUD editor button, after the icon row.\n"
             "        if ui::hovered(self.cursor, icons[3].0 + 24.0, icons[3].1, 20.0, 20.0) {\n"
             "            self.pause_focus = None;\n            self.audio.play_button_click();\n"
             "            self.open_hud_editor(false);\n            return Ok(());\n        }\n"
             "        let friends = icons[FRIENDS_ICON];\n        let hit = pause_buttons(\n"),
        ]
    for f, old, _ in edits:
        if texts[f].count(old) != 1:
            ok = False
    if not ok:
        done.append("skipped Vesper HUD editor: Horizon changed the HUD editor or pause menu code")
        return
    for f, old, new in edits:
        texts[f] = texts[f].replace(old, new, 1)
    for f, text in texts.items():
        f.write_text(text, encoding="utf-8")
    (crate / "vesper_hud_panel.rs").write_text(panel_src.read_text(encoding="utf-8"), encoding="utf-8")
    (crate / "game/vesper_hud.rs").write_text(glue_src.read_text(encoding="utf-8"), encoding="utf-8")
    done.append("ok  Vesper HUD editor (menu, pause menu button)")


if VESPER_HUD_EDITOR:
    vesper_hud_editor()

print("\n".join(done))
if failures:
    print("\nFAILED:\n" + "\n".join(failures))
    sys.exit(1)
