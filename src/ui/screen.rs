use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;
use ratatui::widgets::Widget;

pub const IDEAL_COLS: u16 = 160;
pub const IDEAL_ROWS: u16 = 72;

/// Largest centered cell-rect inside `area` whose pixel grid (w, h*2)
/// preserves the src_w:src_h aspect ratio.
pub fn fit_rect(src_w: usize, src_h: usize, area: Rect) -> Rect {
    if area.width == 0 || area.height == 0 || src_w == 0 || src_h == 0 {
        return Rect::new(area.x, area.y, 0, 0);
    }
    let avail_px_w = area.width as f64;
    let avail_px_h = (area.height as f64) * 2.0;
    let scale = (avail_px_w / src_w as f64).min(avail_px_h / src_h as f64);
    let out_w = ((src_w as f64 * scale).round() as u16).clamp(1, area.width);
    let out_h_px = (src_h as f64 * scale).round() as u16;
    let out_h = (out_h_px.div_ceil(2)).clamp(1, area.height);
    let x = area.x + (area.width - out_w) / 2;
    let y = area.y + (area.height - out_h) / 2;
    Rect::new(x, y, out_w, out_h)
}

/// Draws an RGB888 frame as `▀` half-blocks: fg = top pixel, bg = bottom pixel.
pub struct GameScreen<'a> {
    pub rgb: &'a [u8],
    pub width: usize,
    pub height: usize,
}

impl GameScreen<'_> {
    fn pixel(&self, x: usize, y: usize) -> Color {
        let i = (y.min(self.height - 1) * self.width + x.min(self.width - 1)) * 3;
        Color::Rgb(self.rgb[i], self.rgb[i + 1], self.rgb[i + 2])
    }
}

impl Widget for GameScreen<'_> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        if self.rgb.len() < self.width * self.height * 3 {
            return;
        }
        let target = fit_rect(self.width, self.height, area);
        if target.width == 0 || target.height == 0 {
            return;
        }
        let px_h = (target.height as usize) * 2;
        for cy in 0..target.height {
            for cx in 0..target.width {
                let sx = (cx as usize) * self.width / target.width as usize;
                let top_py = (cy as usize * 2) * self.height / px_h;
                let bot_py = (cy as usize * 2 + 1) * self.height / px_h;
                let cell = &mut buf[(target.x + cx, target.y + cy)];
                cell.set_symbol("▀");
                cell.set_fg(self.pixel(sx, top_py));
                cell.set_bg(self.pixel(sx, bot_py));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::buffer::Buffer;
    use ratatui::layout::Rect;
    use ratatui::style::Color;
    use ratatui::widgets::Widget;

    #[test]
    fn fit_exact_terminal_is_full_res() {
        let r = fit_rect(160, 144, Rect::new(0, 0, 160, 72));
        assert_eq!((r.width, r.height), (160, 72));
        assert_eq!((r.x, r.y), (0, 0));
    }

    #[test]
    fn fit_scales_down_and_centers_preserving_aspect() {
        let r = fit_rect(160, 144, Rect::new(0, 0, 100, 72));
        // width-bound: scale = 100/160 = 0.625 -> 90px tall -> 45 cells
        assert_eq!((r.width, r.height), (100, 45));
        assert_eq!(r.x, 0);
        assert_eq!(r.y, (72 - 45) / 2);
        // height-bound case
        let r = fit_rect(160, 144, Rect::new(0, 0, 300, 36));
        assert_eq!((r.width, r.height), (80, 36));
        assert_eq!(r.x, (300 - 80) / 2);
    }

    #[test]
    fn fit_handles_degenerate_area() {
        let r = fit_rect(160, 144, Rect::new(0, 0, 0, 0));
        assert_eq!((r.width, r.height), (0, 0));
    }

    #[test]
    fn widget_renders_half_blocks_fg_top_bg_bottom() {
        // 2x2 frame: top row red|green, bottom row blue|white
        let rgb = [
            255, 0, 0, /**/ 0, 255, 0, // top
            0, 0, 255, /**/ 255, 255, 255, // bottom
        ];
        let area = Rect::new(0, 0, 2, 1);
        let mut buf = Buffer::empty(area);
        GameScreen {
            rgb: &rgb,
            width: 2,
            height: 2,
        }
        .render(area, &mut buf);
        let c00 = &buf[(0, 0)];
        assert_eq!(c00.symbol(), "▀");
        assert_eq!(c00.fg, Color::Rgb(255, 0, 0));
        assert_eq!(c00.bg, Color::Rgb(0, 0, 255));
        let c10 = &buf[(1, 0)];
        assert_eq!(c10.fg, Color::Rgb(0, 255, 0));
        assert_eq!(c10.bg, Color::Rgb(255, 255, 255));
    }

    #[test]
    fn widget_upscales_nearest_neighbor() {
        // 1x2 frame (red over blue) into a 4x2 area
        let rgb = [255, 0, 0, /**/ 0, 0, 255];
        let area = Rect::new(0, 0, 4, 2);
        let mut buf = Buffer::empty(area);
        GameScreen {
            rgb: &rgb,
            width: 1,
            height: 2,
        }
        .render(area, &mut buf);
        let top = &buf[(0, 0)];
        assert_eq!(top.fg, Color::Rgb(255, 0, 0));
        assert_eq!(top.bg, Color::Rgb(255, 0, 0));
        let bot = &buf[(0, 1)];
        assert_eq!(bot.fg, Color::Rgb(0, 0, 255));
        assert_eq!(bot.bg, Color::Rgb(0, 0, 255));
    }
}
