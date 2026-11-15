//! Vesper's HUD editor menu, drawn over the game while the HUD editor is open and styled like the launcher
//! (night glass, one purple accent), in four small tabs: Elements, Looks, Colors and Boxes.
//!
//! It draws with the design system's `Ui` (smooth rounded corners, the launcher's fonts) and holds no game state: the
//! game hands it a [`Snapshot`] of the settings each frame and applies the [`Action`]s it returns, so this file knows
//! nothing about options files or screens. Layout is in "units" (1/480 of the screen's width, like the Java overlay of
//! the same menu), turned into dp.
use crate::hud::{HudAtlas, HudBatch};
use crate::pvp::Colour;
use crate::ui_theme::{Face, Ui, color};

/// The widgets, in `WidgetId::ALL` order.
pub const WIDGETS: [&str; 13] = [
    "Armour",
    "Effects",
    "Held item",
    "Ping",
    "Coordinates",
    "Server address",
    "Combo",
    "FPS",
    "CPS",
    "Keystrokes",
    "Clock",
    "Reach",
    "Speed",
];
const TABS: [&str; 4] = ["Elements", "Looks", "Colors", "Boxes"];
const PRESETS: [&str; 4] = ["Vesper Night", "Glass", "Transparent", "Neon"];
/// (text, shadow, background, opacity) of each preset.
const PRESET_LOOKS: [(Colour, bool, bool, u8); 4] = [
    (Colour::White, true, true, 55),
    (Colour::White, true, true, 20),
    (Colour::White, true, false, 47),
    (Colour::Cyan, true, true, 65),
];

const PANEL: u32 = color::hex(0x100E26, 0xF5);
const LINE: u32 = color::hex(0xAFA2FF, 0x55);
const TEXT: u32 = color::hex(0xECEBF7, 255);
const MUTED: u32 = color::hex(0xA09FBA, 255);
const ACCENT: u32 = color::hex(0x8F7CFF, 255);
const ACCENT2: u32 = color::hex(0xC9BFFF, 255);
const SOFT: u32 = color::hex(0x8F7CFF, 0x42);
const CTRL: u32 = color::hex(0x211E42, 255);
const CTRL_HOVER: u32 = color::hex(0x302B63, 255);
const TRACK: u32 = color::hex(0x38336A, 255);
const DARK: u32 = color::hex(0x070614, 0x66);
const TILE: u32 = color::hex(0x0F1017, 255);
const ON_ACCENT: u32 = color::hex(0x0D0B1F, 255);

/// What the menu shows: the HUD settings as they are now.
#[derive(Clone)]
pub struct Snapshot {
    pub on: [bool; 13],
    /// Each widget's size in percent (50 to 200).
    pub scale: [u8; 13],
    pub text: Colour,
    pub shadow: bool,
    pub background: bool,
    /// The boxes' opacity in percent.
    pub opacity: u8,
}
impl Default for Snapshot {
    fn default() -> Self {
        Self {
            on: [false; 13],
            scale: [100; 13],
            text: Colour::White,
            shadow: true,
            background: true,
            opacity: 47,
        }
    }
}

/// What the player asked for; the game applies it and saves.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Action {
    /// Show or hide widget `i`.
    Toggle(usize),
    /// Widget `i`'s size in percent.
    Scale(usize, u8),
    Text(Colour),
    Shadow(bool),
    Background(bool),
    Opacity(u8),
    /// Apply look `i` of the Looks tab.
    Preset(usize),
    /// Every widget back to its corner and size.
    ResetLayout,
    Done,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Hit {
    Close,
    Drag,
    Tab(usize),
    Row(usize),
    Switch(usize),
    Size,
    Opacity,
    Preset(usize),
    Swatch(Colour),
    Shadow,
    Background,
    Reset,
    Done,
}

#[derive(Default)]
pub struct PanelState {
    tab: usize,
    selected: usize,
    /// The panel's top left in dp once the player has moved it.
    pos: Option<(f32, f32)>,
    /// Where in the panel the header was grabbed, in dp.
    grab: Option<(f32, f32)>,
    /// The slider being dragged.
    slider: Option<Hit>,
    /// The widgets' hit areas in dp, from the last frame.
    hits: Vec<(Hit, [f32; 4])>,
    panel: [f32; 4],
    /// GUI pixels to dp, and the screen's size in dp, from the last frame.
    to_dp: f32,
    screen: (f32, f32),
    last: Snapshot,
}

fn inside(r: [f32; 4], p: (f32, f32)) -> bool {
    p.0 >= r[0] && p.0 < r[0] + r[2] && p.1 >= r[1] && p.1 < r[1] + r[3]
}
fn rgb(argb: u32) -> u32 {
    color::hex(argb & 0x00FF_FFFF, 255)
}

/// Drawing helpers over the design system's `Ui`, with the hit areas of what it drew.
struct Pen<'a, 'b> {
    ui: &'a mut Ui<'b>,
    u: f32,
    hits: Vec<(Hit, [f32; 4])>,
    cursor: (f32, f32),
}
impl Pen<'_, '_> {
    fn over(&self, r: [f32; 4]) -> bool {
        inside(r, self.cursor)
    }
    fn fill(&mut self, x: f32, y: f32, w: f32, h: f32, r: f32, c: u32) {
        self.ui.rounded(x, y, w, h, r, c);
    }
    /// An outline `bw` dp thick, as nested one-pixel borders.
    fn ring(&mut self, x: f32, y: f32, w: f32, h: f32, r: f32, bw: f32, c: u32) {
        let d = self.ui.density;
        let n = (bw * d).round().max(1.0) as usize;
        for i in 0..n {
            let o = i as f32 / d;
            self.ui.border(
                x + o,
                y + o,
                (w - 2.0 * o).max(0.0),
                (h - 2.0 * o).max(0.0),
                (r - o).max(0.0),
                c,
            );
        }
    }
    /// A soft halo, strongest at the edge.
    fn glow(&mut self, x: f32, y: f32, w: f32, h: f32, r: f32, spread: f32, rgb: u32, alpha: u32) {
        for i in (1..=3u32).rev() {
            let g = spread * i as f32 / 3.0;
            let a = (alpha * (4 - i) / 6).min(255) as u8;
            self.ui
                .rounded(x - g, y - g, w + 2.0 * g, h + 2.0 * g, r + g, color::hex(rgb, a));
        }
    }
    /// Text in a box `h` tall whose top is `y`, shrunk to fit `max_w`.
    fn text(&mut self, face: Face, size: f32, x: f32, y: f32, h: f32, max_w: f32, c: u32, s: &str) {
        let mut size = size;
        let w = self.ui.measure(face, size, s);
        if w > max_w && w > 0.0 {
            size *= max_w / w;
        }
        self.ui.text_line(face, size, h, x, y, c, s);
    }
    fn width(&self, face: Face, size: f32, s: &str) -> f32 {
        self.ui.measure(face, size, s)
    }
    fn hit(&mut self, hit: Hit, r: [f32; 4]) -> bool {
        self.hits.push((hit, r));
        self.over(r)
    }

    /// The Vesper mark: a dark tile with the moon and the spark.
    fn logo(&mut self, x: f32, y: f32, s: f32) {
        self.glow(x, y, s, s, s * 0.22, s * 0.18, 0x8F7CFF, 0x2C);
        self.fill(x, y, s, s, s * 0.22, TILE);
        let k = s / 512.0;
        let moon = color::hex(0xA99BFF, 255);
        let (ar, br) = (149.3 * k, 122.8 * k);
        self.fill(x + 231.4 * k - ar, y + 279.5 * k - ar, 2.0 * ar, 2.0 * ar, ar, moon);
        self.fill(x + 337.7 * k - br, y + 205.4 * k - br, 2.0 * br, 2.0 * br, br, TILE);
        // the spark: two thin crossing bars
        let (cx, cy, l, t) = (x + 373.5 * k, y + 156.5 * k, 34.0 * k, 5.0 * k);
        let white = color::hex(0xFFFFFF, 255);
        self.fill(cx - l, cy - t / 2.0, 2.0 * l, t, t / 2.0, white);
        self.fill(cx - t / 2.0, cy - l, t, 2.0 * l, t / 2.0, white);
    }

    fn button(&mut self, hit: Hit, label: &str, r: [f32; 4], style: u8) -> bool {
        let over = self.hit(hit, r);
        let [x, y, w, h] = r;
        let u = self.u;
        let rad = (4.0 * u).min(h / 2.0);
        match style {
            2 => {
                self.glow(x, y, w, h, rad, if over { 3.0 * u } else { 2.0 * u }, 0x6F5CF2, if over { 0x70 } else { 0x40 });
                self.fill(x, y, w, h, rad, if over { color::hex(0xA597FF, 255) } else { ACCENT });
                self.fill(x + 0.5 * u, y + 0.5 * u, w - u, h * 0.45, rad, color::hex(0xFFFFFF, 0x38));
            }
            _ => {
                self.fill(x, y, w, h, rad, if style == 1 { SOFT } else if over { CTRL_HOVER } else { CTRL });
                if style == 1 {
                    self.ring(x, y, w, h, rad, 0.8 * u, color::hex(0x8F7CFF, 0xAA));
                }
            }
        }
        let face = if style == 2 { Face::SemiBold } else { Face::Medium };
        let size = 5.7 * u;
        let tw = self.width(face, size, label).min(w - 2.0 * u);
        let c = match style {
            2 => ON_ACCENT,
            1 => ACCENT2,
            _ => TEXT,
        };
        self.text(face, size, x + (w - tw) / 2.0, y, h, w - 2.0 * u, c, label);
        over
    }

    fn switch(&mut self, hit: Hit, x: f32, y: f32, on: bool) {
        let u = self.u;
        let (w, h) = (14.0 * u, 7.0 * u);
        self.hit(hit, [x, y, w, h]);
        self.fill(x, y, w, h, h / 2.0, if on { ACCENT } else { TRACK });
        let k = h - 1.6 * u;
        let kx = if on { x + w - k - 0.8 * u } else { x + 0.8 * u };
        self.fill(kx, y + 0.8 * u, k, k, k / 2.0, color::hex(0xFFFFFF, 255));
    }

    /// A labelled slider; `frac` is where the knob sits (0 to 1).
    fn slider(&mut self, hit: Hit, label: &str, value: &str, x: f32, y: f32, w: f32, frac: f32, grabbed: bool) {
        let u = self.u;
        let vw = self.width(Face::Medium, 5.9 * u, value);
        self.text(Face::Medium, 5.9 * u, x, y, 7.0 * u, w - vw - 3.0 * u, TEXT, label);
        self.text(Face::Medium, 5.9 * u, x + w - vw, y, 7.0 * u, vw + 1.0, ACCENT2, value);
        let (ty, th) = (y + 9.0 * u, 3.0 * u);
        self.hit(hit, [x, ty - 2.5 * u, w, th + 5.0 * u]);
        let f = frac.clamp(0.0, 1.0);
        self.fill(x, ty, w, th, th / 2.0, TRACK);
        self.fill(x, ty, (w * f).max(th), th, th / 2.0, ACCENT);
        let kx = x + w * f - 3.0 * u;
        if grabbed {
            self.glow(kx, ty - 1.5 * u, 6.0 * u, th + 3.0 * u, 3.0 * u, 2.0 * u, 0x8F7CFF, 0x60);
        }
        self.fill(kx, ty - 1.5 * u, 6.0 * u, th + 3.0 * u, 3.0 * u, color::hex(0xFFFFFF, 255));
    }
}

impl PanelState {
    /// Select widget `i` (a click on it on screen).
    pub fn select(&mut self, i: usize) {
        self.selected = i.min(12);
    }

    /// Draw the menu. `cursor` is in GUI pixels.
    pub fn draw(&mut self, atlas: &HudAtlas, batch: &mut HudBatch, cursor: (f32, f32), snap: &Snapshot) {
        self.last = snap.clone();
        let mut ui = Ui::new(atlas, batch, cursor);
        let u = (ui.w / 480.0).min(ui.h / 270.0);
        self.to_dp = ui.batch.scale / ui.density;
        self.screen = (ui.w, ui.h);
        let (pw, ph) = (138.0 * u, 200.0 * u);
        let (px, py) = self.place(pw, ph, u);
        self.panel = [px, py, pw, ph];
        let cur = ui.cursor;
        let mut pen = Pen { ui: &mut ui, u, hits: Vec::new(), cursor: cur };

        // card: halo, glass body, thin outline, an accent line along the top
        pen.glow(px, py, pw, ph, 6.0 * u, 4.0 * u, 0x8F7CFF, 0x30);
        pen.fill(px, py, pw, ph, 6.0 * u, PANEL);
        pen.ring(px, py, pw, ph, 6.0 * u, 0.8 * u, LINE);
        pen.fill(px + 8.0 * u, py, pw - 16.0 * u, 1.2 * u, 0.0, ACCENT);

        let pad = 7.0 * u;
        let (x, w) = (px + pad, pw - 2.0 * pad);
        let mut y = py + 7.0 * u;
        self.header(&mut pen, x, y, w, py, pw);
        y += 18.0 * u;

        // tabs: a dark track with the open one lit
        pen.fill(x, y, w, 12.5 * u, 5.0 * u, DARK);
        let tw = (w - 3.0 * u) / 4.0;
        for (i, name) in TABS.iter().enumerate() {
            let r = [x + 0.75 * u + i as f32 * (tw + 0.5 * u), y + 0.75 * u, tw, 11.0 * u];
            let on = i == self.tab;
            let over = pen.hit(Hit::Tab(i), r);
            if on {
                pen.fill(r[0], r[1], r[2], r[3], 4.2 * u, SOFT);
                pen.ring(r[0], r[1], r[2], r[3], 4.2 * u, 0.8 * u, color::hex(0x8F7CFF, 0xAA));
            } else if over {
                pen.fill(r[0], r[1], r[2], r[3], 4.2 * u, color::hex(0xAFA2FF, 0x26));
            }
            let size = 5.7 * u;
            let tx = r[0] + (r[2] - pen.width(Face::Medium, size, name).min(r[2] - 2.0 * u)) / 2.0;
            let c = if on { ACCENT2 } else if over { TEXT } else { MUTED };
            pen.text(Face::Medium, size, tx, r[1], r[3], r[2] - 2.0 * u, c, name);
        }
        y += 17.0 * u;

        match self.tab {
            0 => self.elements(&mut pen, x, y, w, snap),
            1 => self.looks(&mut pen, x, y, w, snap),
            2 => self.colors(&mut pen, x, y, w, snap),
            _ => self.boxes(&mut pen, x, y, w, snap),
        }

        // footer
        let fy = py + ph - 17.0 * u;
        let rw = w * 0.36;
        pen.button(Hit::Reset, "Reset", [x, fy, rw, 11.0 * u], 0);
        pen.button(Hit::Done, "Done", [x + rw + 3.0 * u, fy, w - rw - 3.0 * u, 11.0 * u], 2);

        self.hits = pen.hits;
        ui.finish();
    }

    /// The panel's top left: where the player left it, kept on the screen.
    fn place(&self, pw: f32, ph: f32, u: f32) -> (f32, f32) {
        let (sw, sh) = self.screen;
        let (x, y) = self.pos.unwrap_or((sw - pw - 4.0 * u, 6.0 * u));
        (x.clamp(0.0, (sw - pw).max(0.0)), y.clamp(0.0, (sh - ph).max(0.0)))
    }

    fn header(&self, pen: &mut Pen, x: f32, y: f32, w: f32, py: f32, pw: f32) {
        let u = pen.u;
        pen.logo(x, y, 13.0 * u);
        pen.text(Face::SemiBold, 8.2 * u, x + 17.0 * u, y - 1.0 * u, 8.0 * u, w - 42.0 * u, TEXT, "HUD editor");
        pen.text(Face::Regular, 5.0 * u, x + 17.0 * u, y + 7.0 * u, 6.0 * u, w - 40.0 * u, MUTED, "Drag items on screen to move them");
        // the header is the handle
        pen.hit(Hit::Drag, [x - 7.0 * u, py, pw - 22.0 * u, 19.0 * u]);
        let dragging = self.grab.is_some();
        for i in 0..6 {
            let c = if dragging { ACCENT2 } else { color::hex(0xA09FBA, 0x66) };
            pen.fill(x + w - 22.0 * u + (i % 2) as f32 * 2.6 * u, y + 2.0 * u + (i / 2) as f32 * 2.6 * u, 1.3 * u, 1.3 * u, 0.65 * u, c);
        }
        let r = [x + w - 11.0 * u, y, 11.0 * u, 11.0 * u];
        let over = pen.hit(Hit::Close, r);
        pen.fill(r[0], r[1], r[2], r[3], 4.0 * u, if over { CTRL_HOVER } else { CTRL });
        let xs = 6.5 * u;
        let xw = pen.width(Face::Medium, xs, "x");
        pen.text(Face::Medium, xs, r[0] + (r[2] - xw) / 2.0, r[1], r[3], r[2], if over { TEXT } else { MUTED }, "x");
    }

    fn elements(&self, pen: &mut Pen, x: f32, mut y: f32, w: f32, snap: &Snapshot) {
        let u = pen.u;
        let rh = 9.2 * u;
        for (i, name) in WIDGETS.iter().enumerate() {
            let on = i == self.selected;
            let row = [x, y, w - 18.0 * u, rh];
            let over = pen.hit(Hit::Row(i), row);
            if on {
                pen.fill(x, y, w, rh, 4.0 * u, SOFT);
                pen.fill(x, y + 1.5 * u, 1.6 * u, rh - 3.0 * u, 0.8 * u, ACCENT);
            } else if over {
                pen.fill(x, y, w, rh, 4.0 * u, color::hex(0xAFA2FF, 0x26));
            }
            let c = if snap.on[i] { TEXT } else { MUTED };
            pen.text(Face::Medium, 5.9 * u, x + 5.0 * u, y, rh, w - 30.0 * u, c, name);
            pen.switch(Hit::Switch(i), x + w - 17.0 * u, y + 1.1 * u, snap.on[i]);
            y += rh + 0.4 * u;
        }
        y += 3.0 * u;
        let i = self.selected.min(12);
        let s = snap.scale[i].clamp(50, 200);
        let frac = (s as f32 - 50.0) / 150.0;
        pen.slider(Hit::Size, &format!("Size of {}", WIDGETS[i]), &format!("{s}%"), x, y, w, frac, self.slider == Some(Hit::Size));
    }

    fn looks(&self, pen: &mut Pen, x: f32, y: f32, w: f32, snap: &Snapshot) {
        let u = pen.u;
        pen.text(Face::Medium, 5.9 * u, x, y - 1.0 * u, 8.0 * u, w, MUTED, "Pick a look for every widget");
        let (gap, th) = (3.0 * u, 26.0 * u);
        let tw = (w - gap) / 2.0;
        let y = y + 9.0 * u;
        for (i, name) in PRESETS.iter().enumerate() {
            let r = [x + (i % 2) as f32 * (tw + gap), y + (i / 2) as f32 * (th + gap), tw, th];
            let over = pen.hit(Hit::Preset(i), r);
            let (text, shadow, bg, op) = PRESET_LOOKS[i];
            let on = snap.text == text && snap.background == bg && (!bg || snap.opacity == op) && snap.shadow == shadow;
            pen.fill(r[0], r[1], r[2], r[3], 5.0 * u, if over { CTRL_HOVER } else { CTRL });
            // a small world behind the sample, so a see-through look can be judged
            let (wx, wy, ww, wh) = (r[0] + 1.5 * u, r[1] + 1.5 * u, r[2] - 3.0 * u, r[3] - 12.0 * u);
            pen.fill(wx, wy, ww, wh, 3.5 * u, color::hex(0x3F78B8, 255));
            pen.fill(wx, wy + wh * 0.62, ww, wh * 0.38, 0.0, color::hex(0x4C8A44, 255));
            // the sample: "FPS: 144" in the look's box and colour
            let size = 5.4 * u;
            let label = "FPS: 144";
            let sw = pen.width(Face::Medium, size, label);
            let (bw, bh) = (sw + 5.0 * u, 8.0 * u);
            let (bx, by) = (wx + (ww - bw) / 2.0, wy + (wh - bh) / 2.0);
            if bg {
                let a = (op as u32 * 255 / 100).min(255) as u8;
                pen.fill(bx, by, bw, bh, 2.0 * u, color::hex(0x0B0B17, a));
            }
            if shadow {
                pen.ui.text_line(Face::Medium, size, bh, bx + 2.5 * u + 0.5, by + 0.5, color::hex(0x000000, 0xAA), label);
            }
            pen.ui.text_line(Face::Medium, size, bh, bx + 2.5 * u, by, rgb(text.argb()), label);
            pen.text(Face::Medium, 5.6 * u, r[0] + 2.0 * u, r[1] + r[3] - 10.0 * u, 9.0 * u, r[2] - 4.0 * u, if on { ACCENT2 } else { TEXT }, name);
            if on {
                pen.ring(r[0], r[1], r[2], r[3], 5.0 * u, u, ACCENT);
            } else if over {
                pen.ring(r[0], r[1], r[2], r[3], 5.0 * u, 0.8 * u, color::hex(0x8F7CFF, 0x88));
            }
        }
    }

    fn colors(&self, pen: &mut Pen, x: f32, y: f32, w: f32, snap: &Snapshot) {
        let u = pen.u;
        pen.text(Face::Medium, 5.9 * u, x, y - 1.0 * u, 8.0 * u, w, MUTED, "Text colour of every widget");
        let y = y + 10.0 * u;
        let gap = u;
        let (sw, sh) = ((w - 4.0 * gap) / 5.0, 9.0 * u);
        for (i, c) in Colour::ALL.iter().enumerate() {
            let r = [x + (i % 5) as f32 * (sw + gap), y + (i / 5) as f32 * (sh + gap), sw, sh];
            let over = pen.hit(Hit::Swatch(*c), r);
            if *c == snap.text {
                pen.fill(r[0] - 0.9 * u, r[1] - 0.9 * u, r[2] + 1.8 * u, r[3] + 1.8 * u, 3.0 * u, color::hex(0xFFFFFF, 255));
            } else if over {
                pen.fill(r[0] - 0.6 * u, r[1] - 0.6 * u, r[2] + 1.2 * u, r[3] + 1.2 * u, 3.0 * u, color::hex(0xFFFFFF, 0x99));
            }
            pen.fill(r[0], r[1], r[2], r[3], 2.2 * u, rgb(c.argb()));
            if *c == Colour::Inverted {
                // the colour under the text, inverted: half light, half dark
                pen.fill(r[0] + r[2] / 2.0, r[1], r[2] / 2.0, r[3], 0.0, color::hex(0x000000, 255));
            }
        }
        let y = y + 2.0 * (sh + gap) + 3.0 * u;
        pen.text(Face::Medium, 5.9 * u, x, y, 7.0 * u, w, MUTED, &format!("Now: {}", snap.text.label()));
        let y = y + 12.0 * u;
        pen.text(Face::Medium, 5.9 * u, x, y, 7.0 * u, w - 16.0 * u, TEXT, "Text shadow");
        pen.switch(Hit::Shadow, x + w - 14.0 * u, y, snap.shadow);
    }

    fn boxes(&self, pen: &mut Pen, x: f32, y: f32, w: f32, snap: &Snapshot) {
        let u = pen.u;
        pen.text(Face::Medium, 5.9 * u, x, y, 7.0 * u, w - 16.0 * u, TEXT, "Dark box behind widgets");
        pen.switch(Hit::Background, x + w - 14.0 * u, y, snap.background);
        let y = y + 14.0 * u;
        let op = snap.opacity.min(100);
        pen.slider(Hit::Opacity, "Box opacity", &format!("{op}%"), x, y, w, op as f32 / 100.0, self.slider == Some(Hit::Opacity));
        let y = y + 22.0 * u;
        let c = if snap.background { MUTED } else { color::hex(0xA09FBA, 0x80) };
        pen.text(Face::Regular, 5.0 * u, x, y, 7.0 * u, w, c, "Opacity needs the box turned on.");
        pen.text(Face::Regular, 5.0 * u, x, y + 9.0 * u, 7.0 * u, w, MUTED, "Hover a widget and scroll to resize it.");
    }

    // ----- the pointer -----

    fn dp(&self, cursor: (f32, f32)) -> (f32, f32) {
        (cursor.0 * self.to_dp, cursor.1 * self.to_dp)
    }

    /// Whether the cursor (GUI pixels) is over the menu.
    pub fn contains(&self, cursor: (f32, f32)) -> bool {
        self.to_dp > 0.0 && inside(self.panel, self.dp(cursor))
    }

    /// A left press. `None` when it missed the menu (the game handles it); else the actions it caused.
    pub fn press(&mut self, cursor: (f32, f32)) -> Option<Vec<Action>> {
        if !self.contains(cursor) {
            return None;
        }
        let p = self.dp(cursor);
        let Some(&(hit, r)) = self.hits.iter().rev().find(|(_, r)| inside(*r, p)) else {
            return Some(Vec::new());
        };
        let snap = self.last.clone();
        let mut out = Vec::new();
        match hit {
            Hit::Close | Hit::Done => out.push(Action::Done),
            Hit::Drag => self.grab = Some((p.0 - self.panel[0], p.1 - self.panel[1])),
            Hit::Tab(i) => self.tab = i,
            Hit::Row(i) => self.selected = i,
            Hit::Switch(i) => out.push(Action::Toggle(i)),
            Hit::Size | Hit::Opacity => {
                self.slider = Some(hit);
                self.slide(hit, r, p, &mut out);
            }
            Hit::Preset(i) => out.push(Action::Preset(i)),
            Hit::Swatch(c) => out.push(Action::Text(c)),
            Hit::Shadow => out.push(Action::Shadow(!snap.shadow)),
            Hit::Background => out.push(Action::Background(!snap.background)),
            Hit::Reset => out.push(Action::ResetLayout),
        }
        Some(out)
    }

    fn slide(&self, hit: Hit, r: [f32; 4], p: (f32, f32), out: &mut Vec<Action>) {
        let f = ((p.0 - r[0]) / r[2]).clamp(0.0, 1.0);
        match hit {
            Hit::Size => {
                let v = (50.0 + f * 150.0 + 5.0).floor() as u32 / 10 * 10;
                out.push(Action::Scale(self.selected.min(12), v.clamp(50, 200) as u8));
            }
            Hit::Opacity => out.push(Action::Opacity(((f * 100.0 / 5.0).round() as u8) * 5)),
            _ => {}
        }
    }

    /// The cursor moved with the button held: the menu or a slider follows. `None` when neither is held.
    pub fn drag(&mut self, cursor: (f32, f32)) -> Option<Vec<Action>> {
        let p = self.dp(cursor);
        if let Some((gx, gy)) = self.grab {
            self.pos = Some((p.0 - gx, p.1 - gy));
            return Some(Vec::new());
        }
        let hit = self.slider?;
        let r = self.hits.iter().find(|(h, _)| *h == hit).map(|(_, r)| *r)?;
        let mut out = Vec::new();
        self.slide(hit, r, p, &mut out);
        Some(out)
    }

    pub fn release(&mut self) {
        self.grab = None;
        self.slider = None;
    }

    /// The look `i` of the Looks tab as (text colour, shadow, background, opacity).
    pub fn preset(i: usize) -> (Colour, bool, bool, u8) {
        PRESET_LOOKS[i.min(PRESET_LOOKS.len() - 1)]
    }
}

/// The icon of the pause menu's HUD editor button: the Vesper logo (crescent moon and spark), drawn in the 15x15 area
/// the game gives its own icons, but in quarter pixels so the curve and the four-pointed spark keep their shape.
/// `x`, `y` is the button's top left; the icon sits 3 in, like the other icons.
pub fn draw_pause_icon(batch: &mut HudBatch, atlas: &HudAtlas, x: f32, y: f32, hover: bool) {
    const N: usize = 60; // quarter pixels across the icon
    let cell = 15.0 / N as f32;
    // the part of the 512x512 logo that fills the icon (the moon and the spark, centred)
    let (lx, ly, span) = (74.5f32, 106.5f32, 340.0f32);
    let k = span / N as f32;
    let lift = if hover { 28.0 } else { 0.0 };
    let kind = |px: f32, py: f32| -> u8 {
        let in_moon = (px - 231.4).hypot(py - 279.5) <= 149.3 && (px - 337.7).hypot(py - 205.4) > 122.8;
        if in_moon {
            return 1;
        }
        let star = ((px - 373.5).abs() / 38.0).powf(0.65) + ((py - 156.5).abs() / 38.0).powf(0.65);
        if star <= 1.0 { 2 } else { 0 }
    };
    let (ox, oy) = (x + 3.0, y + 3.0);
    for row in 0..N {
        let py = ly + (row as f32 + 0.5) * k;
        let mut run: Option<(usize, u8)> = None;
        for col in 0..=N {
            let now = if col < N { kind(lx + (col as f32 + 0.5) * k, py) } else { 0 };
            if let Some((start, kd)) = run {
                if now != kd {
                    let c = if kd == 2 {
                        color::hex(0xFFFFFF, 255)
                    } else {
                        // the moon fades from light lilac (top left) to the accent (bottom right)
                        let t = (((start + col) as f32 / 2.0 + row as f32) / (2.0 * N as f32)).clamp(0.0, 1.0);
                        let mix = |a: f32, b: f32| ((a + (b - a) * t + lift).min(255.0)) as u32;
                        color::hex((mix(183.0, 133.0) << 16) | (mix(171.0, 116.0) << 8) | mix(255.0, 250.0), 255)
                    };
                    batch.fill(atlas, ox + start as f32 * cell, oy + row as f32 * cell, (col - start) as f32 * cell, cell, c);
                    run = None;
                }
            }
            if run.is_none() && now != 0 {
                run = Some((col, now));
            }
        }
    }
}
