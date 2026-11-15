//! The glue between Vesper's HUD editor menu (`horizon_client::vesper_hud_panel`) and the game: it turns the settings
//! into what the menu shows, and what the player does in the menu into settings. The menu itself is drawn and
//! clicked from `hud_editor.rs`.
use super::Game;
use horizon_client::{
    pvp::Opt,
    pvp_hud::{Layout, WidgetId},
    vesper_hud_panel::{Action, PanelState, Snapshot},
};

/// The switch of each widget, in `WidgetId::ALL` order.
const SWITCHES: [Opt; 13] = [
    Opt::HudArmour,
    Opt::HudEffects,
    Opt::HudHeld,
    Opt::HudPing,
    Opt::HudCoords,
    Opt::HudServer,
    Opt::HudCombo,
    Opt::HudFps,
    Opt::HudCps,
    Opt::HudKeys,
    Opt::HudClock,
    Opt::HudReach,
    Opt::HudSpeed,
];

impl Game<'_> {
    /// The HUD settings as the menu shows them.
    pub(super) fn vesper_snapshot(&self) -> Snapshot {
        let p = &self.settings.extras.pvp;
        let mut s = Snapshot::default();
        for (i, opt) in SWITCHES.iter().enumerate() {
            s.on[i] = opt.switch(p).unwrap_or(false);
            s.scale[i] = p.layout.get(WidgetId::ALL[i]).scale;
        }
        s.text = p.hud.text;
        s.shadow = p.hud.shadow;
        s.background = p.hud.background;
        s.opacity = p.hud.opacity;
        s
    }

    /// Do what the player asked in the menu, and save.
    fn vesper_apply(&mut self, actions: Vec<Action>) {
        if actions.is_empty() {
            return;
        }
        for action in actions {
            let pvp = &mut self.settings.extras.pvp;
            match action {
                Action::Toggle(i) => SWITCHES[i.min(12)].cycle(pvp, false),
                Action::Scale(i, v) => pvp.layout.get_mut(WidgetId::ALL[i.min(12)]).scale = v,
                Action::Text(c) => pvp.hud.text = c,
                Action::Shadow(on) => pvp.hud.shadow = on,
                Action::Background(on) => pvp.hud.background = on,
                Action::Opacity(v) => pvp.hud.opacity = v,
                Action::Preset(i) => {
                    let (text, shadow, background, opacity) = PanelState::preset(i);
                    pvp.hud.text = text;
                    pvp.hud.shadow = shadow;
                    pvp.hud.background = background;
                    pvp.hud.opacity = opacity;
                }
                Action::ResetLayout => pvp.layout = Layout::default(),
                Action::Done => {
                    self.audio.play_button_click();
                    self.close_hud_editor();
                    return;
                }
            }
        }
        self.settings.save(&Self::options_path());
    }

    /// A press in the editor: true when the menu took it (a left press on it, or a right one that would
    /// otherwise send the widget under the menu back to its corner).
    pub(super) fn vesper_press(&mut self, right: bool) -> bool {
        let cursor = self.cursor;
        let Some(e) = self.pvp.editor.as_mut() else {
            return false;
        };
        if right {
            return e.vesper.contains(cursor);
        }
        let Some(actions) = e.vesper.press(cursor) else {
            return false;
        };
        self.vesper_apply(actions);
        true
    }

    /// The cursor moved: the menu or one of its sliders follows it while held.
    pub(super) fn vesper_drag(&mut self) -> bool {
        let cursor = self.cursor;
        let Some(e) = self.pvp.editor.as_mut() else {
            return false;
        };
        let Some(actions) = e.vesper.drag(cursor) else {
            return false;
        };
        self.vesper_apply(actions);
        true
    }

    pub(super) fn vesper_release(&mut self) {
        if let Some(e) = self.pvp.editor.as_mut() {
            e.vesper.release();
        }
    }
}
