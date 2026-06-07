from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, JpegImagePlugin  # noqa: F401


ROOT = Path(__file__).resolve().parent
SLIDE_DIR = ROOT / "slides"
PDF_PATH = ROOT / "decisions_that_cannot_be_wrong_carousel.pdf"
CONTACT_SHEET_PATH = ROOT / "decisions_that_cannot_be_wrong_contact_sheet.png"

W, H = 1080, 1350
MARGIN = 92

INK = "#14171a"
CREAM = "#f4f0e8"
RED = "#b51f32"
RED_DARK = "#6d0020"
MUTED = "#6f6f6f"
LINE = "#d9d1c2"
WHITE = "#ffffff"
BLUE = "#306385"
GREEN = "#2d7d4f"
GOLD = "#a57924"

FONT_SANS = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
FONT_SANS_BOLD = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
FONT_SERIF = "/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf"
FONT_SERIF_BOLD = "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


F = {
    "eyebrow": font(FONT_SANS_BOLD, 24),
    "title": font(FONT_SERIF_BOLD, 66),
    "title_small": font(FONT_SERIF_BOLD, 56),
    "body": font(FONT_SANS, 31),
    "body_bold": font(FONT_SANS_BOLD, 31),
    "small": font(FONT_SANS, 22),
    "small_bold": font(FONT_SANS_BOLD, 22),
    "tiny": font(FONT_SANS, 17),
    "number": font(FONT_SERIF_BOLD, 58),
    "diagram": font(FONT_SANS_BOLD, 20),
}


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        words = para.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if text_size(draw, candidate, fnt)[0] <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    fnt: ImageFont.FreeTypeFont,
    fill: str,
    width: int,
    leading: int,
) -> int:
    x, y = xy
    for line in wrap(draw, text, fnt, width):
        if line:
            draw.text((x, y), line, font=fnt, fill=fill)
        y += leading
    return y


def bg(dark: bool = False) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), INK if dark else CREAM)
    draw = ImageDraw.Draw(img)
    if dark:
        for r in [760, 930, 1110, 1300]:
            draw.ellipse((W - r // 3, H - r // 2, W + r, H + r), outline="#23272b", width=2)
    else:
        draw.rectangle((0, 0, W, H), fill=CREAM)
    return img, draw


def header(draw: ImageDraw.ImageDraw, label: str, dark: bool = False) -> None:
    color = RED if not dark else RED
    draw.line((MARGIN, 96, MARGIN + 40, 96), fill=color, width=4)
    draw.text((MARGIN + 56, 82), label.upper(), font=F["eyebrow"], fill=color)


def footer(draw: ImageDraw.ImageDraw, n: int, dark: bool = False) -> None:
    color = "#8d8d8d" if dark else MUTED
    draw.text((MARGIN, H - 74), "DECISIONS THAT CANNOT BE WRONG", font=F["tiny"], fill=color)
    draw.text((W - MARGIN - 56, H - 74), f"{n:02d} / 09", font=F["tiny"], fill=color)


def title_block(
    draw: ImageDraw.ImageDraw,
    label: str,
    title: str,
    body: str,
    n: int,
    dark: bool = False,
    y: int = 170,
    title_font: ImageFont.FreeTypeFont | None = None,
) -> int:
    header(draw, label, dark=dark)
    fill_title = WHITE if dark else INK
    fill_body = "#d7d7d7" if dark else "#3f3f3f"
    y = draw_wrapped(draw, title, (MARGIN, y), title_font or F["title"], fill_title, W - 2 * MARGIN, 74)
    y += 30
    y = draw_wrapped(draw, body, (MARGIN, y), F["body"], fill_body, W - 2 * MARGIN, 43)
    footer(draw, n, dark=dark)
    return y


def rounded_box(draw, box, fill, outline=LINE, width=2, radius=18):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def slide_1() -> Image.Image:
    img, draw = bg(dark=True)
    header(draw, "Field notes - trustworthy AI", dark=True)
    draw_wrapped(draw, "The System That\nKnows When to Stop", (MARGIN, 435), F["title"], WHITE, 740, 74)
    draw_wrapped(
        draw,
        "Building clinical AI for decisions where being almost right is not enough.",
        (MARGIN, 705),
        F["body"],
        "#d7d7d7",
        720,
        43,
    )
    draw.line((MARGIN, 1120, MARGIN + 180, 1120), fill=RED, width=5)
    draw.text((MARGIN, 1160), "SWIPE TO EXPLORE THE ARCHITECTURE", font=F["small_bold"], fill="#b5b5b5")
    footer(draw, 1, dark=True)
    return img


def slide_2() -> Image.Image:
    img, draw = bg()
    y = title_block(
        draw,
        "The problem",
        "The weakness is not intelligence. It is authority.",
        "Modern models are excellent readers. They can search guidelines, algorithms, fact sheets, and test directories in seconds.\n\nThe risk is different: a fluent answer can look authoritative before it has earned the right to be trusted.",
        2,
        y=150,
        title_font=F["title_small"],
    )
    box_y = max(y + 70, 930)
    rounded_box(draw, (MARGIN, box_y, W - MARGIN, box_y + 135), INK, INK, radius=14)
    draw_wrapped(
        draw,
        "The job is not to make AI sound smarter. It is to make authority inspectable.",
        (MARGIN + 32, box_y + 35),
        F["body_bold"],
        WHITE,
        W - 2 * MARGIN - 64,
        42,
    )
    return img


def slide_3() -> Image.Image:
    img, draw = bg()
    title_block(
        draw,
        "Pillar one - hybrid architecture",
        "Control the data boundary before the first prompt.",
        "The model never roams the data. It requests what it needs through a governed API layer, retrieval plan, and evidence bundle.",
        3,
        y=150,
        title_font=F["title_small"],
    )
    x1, x2 = MARGIN + 25, W - MARGIN - 25
    y = 585
    rounded_box(draw, (x1, y, x2, y + 90), WHITE, INK, radius=12)
    draw.text((x1 + 330, y + 28), "AI LAYER", font=F["diagram"], fill=INK)
    y += 128
    rounded_box(draw, (x1, y, x2, y + 90), WHITE, INK, radius=12)
    draw.text((x1 + 276, y + 28), "GOVERNED API LAYER", font=F["diagram"], fill=INK)
    draw.line((W // 2, y - 38, W // 2, y), fill=INK, width=3)
    y += 150
    labels = ["Test data", "Encounter history", "Algorithms", "Consult topics"]
    bw = 185
    gap = 22
    start = MARGIN + 38
    for i, label in enumerate(labels):
        bx = start + i * (bw + gap)
        rounded_box(draw, (bx, y, bx + bw, y + 70), "#eee8dc", LINE, radius=10)
        draw.text((bx + 22, y + 23), label, font=F["small_bold"], fill=INK)
        draw.line((bx + bw // 2, y - 64, W // 2, y - 25), fill=LINE, width=2)
    draw_wrapped(
        draw,
        "Trust begins when the organization controls what can be searched, cited, and exposed.",
        (MARGIN, 1030),
        F["body_bold"],
        RED_DARK,
        W - 2 * MARGIN,
        43,
    )
    return img


def slide_4() -> Image.Image:
    img, draw = bg()
    title_block(
        draw,
        "Pillar two - agentic farm",
        "Split intelligence into jobs you can audit.",
        "The system is not one giant prompt. Each agent has one job and one artifact to hand forward.",
        4,
        y=150,
        title_font=F["title_small"],
    )
    labels = [
        ("Intent", "understand the ask"),
        ("Retrieval", "find governed sources"),
        ("Evidence", "package the case"),
        ("Response", "draft with citations"),
        ("Critic", "challenge the answer"),
        ("Confidence", "score with rules"),
        ("Render", "draw pathways"),
        ("Format", "choose UI schema"),
    ]
    start_y = 550
    box_w, box_h = 200, 104
    gap_x, gap_y = 34, 58
    for i, (name, sub) in enumerate(labels):
        col, row = i % 4, i // 4
        x = MARGIN + col * (box_w + gap_x)
        y = start_y + row * (box_h + gap_y)
        rounded_box(draw, (x, y, x + box_w, y + box_h), WHITE, INK, radius=14)
        draw.text((x + 20, y + 22), name, font=F["small_bold"], fill=INK)
        draw.text((x + 20, y + 58), sub, font=F["tiny"], fill=MUTED)
        if i not in (3, 7):
            draw.line((x + box_w, y + box_h // 2, x + box_w + gap_x, y + box_h // 2), fill=RED, width=3)
    rounded_box(draw, (MARGIN, 930, W - MARGIN, 1038), INK, INK, radius=14)
    draw_wrapped(draw, "Small jobs create inspectable handoffs.", (MARGIN + 34, 965), F["body_bold"], WHITE, W - 2 * MARGIN - 68, 43)
    return img


def slide_5() -> Image.Image:
    img, draw = bg()
    title_block(
        draw,
        "Pillar three - debating cycle",
        "Make uncertainty visible before it becomes risk.",
        "The critic checks stewardship, safety, evidence gaps, and missed alternatives. Consensus is deterministic. Confidence is rule-based.",
        5,
        y=150,
        title_font=F["title_small"],
    )
    y = 560
    rounded_box(draw, (MARGIN + 130, y, W - MARGIN - 130, y + 70), INK, INK, radius=14)
    draw.text((MARGIN + 296, y + 21), "RESPONSE UNDER REVIEW", font=F["small_bold"], fill=WHITE)
    y += 132
    roles = [("Stewardship", BLUE), ("Safety", RED), ("Skeptic", GOLD)]
    bw = 255
    for i, (role, color) in enumerate(roles):
        x = MARGIN + i * (bw + 26)
        rounded_box(draw, (x, y, x + bw, y + 132), WHITE, color, radius=12, width=3)
        draw.text((x + 22, y + 24), role, font=F["small_bold"], fill=color)
        draw_wrapped(draw, "Find gaps. Challenge assumptions.", (x + 22, y + 61), F["tiny"], INK, bw - 44, 25)
        draw.line((x + bw // 2, y - 62, W // 2, y - 8), fill=LINE, width=2)
    y += 210
    rounded_box(draw, (MARGIN + 150, y, W - MARGIN - 150, y + 70), INK, INK, radius=14)
    draw.text((MARGIN + 318, y + 21), "DETERMINISTIC CONSENSUS", font=F["small_bold"], fill=WHITE)
    y += 125
    outcomes = [("Approve", GREEN), ("Revise once", GOLD), ("Escalate", RED)]
    for i, (outcome, color) in enumerate(outcomes):
        x = MARGIN + i * (245 + 33)
        rounded_box(draw, (x, y, x + 245, y + 78), "#fff8f0", color, radius=12, width=3)
        draw.text((x + 28, y + 24), outcome, font=F["small_bold"], fill=color)
    draw.text((MARGIN + 226, y + 118), "The system escalates instead of improvising.", font=F["small_bold"], fill=RED_DARK)
    return img


def slide_6() -> Image.Image:
    img, draw = bg()
    title_block(
        draw,
        "Pillar four - formatting agent",
        "The right answer still needs the right shape.",
        "A diagnostic pathway should not read like a paragraph. A comparison should not hide inside prose.",
        6,
        y=150,
        title_font=F["title_small"],
    )
    y = 560
    rounded_box(draw, (MARGIN + 230, y, W - MARGIN - 230, y + 66), INK, INK, radius=14)
    draw.text((MARGIN + 354, y + 19), "CLINICAL JSON", font=F["small_bold"], fill=WHITE)
    draw.line((W // 2, y + 66, W // 2, y + 112), fill=RED, width=3)
    y += 112
    rounded_box(draw, (MARGIN + 172, y, W - MARGIN - 172, y + 78), RED, RED, radius=14)
    draw.text((MARGIN + 308, y + 25), "FORMATTING AGENT", font=F["small_bold"], fill=WHITE)
    draw.line((W // 2, y + 78, W // 2, y + 130), fill=RED, width=3)
    y += 140
    components = [
        ("Cards", BLUE),
        ("Tables", GOLD),
        ("Citations", GREEN),
        ("Warnings", RED),
        ("Pathways", INK),
        ("PDF split-view", RED_DARK),
    ]
    bw, bh = 252, 80
    for i, (label, color) in enumerate(components):
        col, row = i % 3, i // 3
        x = MARGIN + col * (bw + 38)
        yy = y + row * (bh + 34)
        rounded_box(draw, (x, yy, x + bw, yy + bh), WHITE, color, radius=12, width=3)
        draw.text((x + 30, yy + 25), label, font=F["small_bold"], fill=color)
    draw_wrapped(
        draw,
        "The interface becomes governed output, not decoration added at the end.",
        (MARGIN, 1060),
        F["body_bold"],
        RED_DARK,
        W - 2 * MARGIN,
        43,
    )
    return img


def slide_7() -> Image.Image:
    img, draw = bg()
    title_block(
        draw,
        "The scale",
        "From one test question to every ordering moment.",
        "Today: diagnostic test selection grounded in ARUP content.\n\nFinal phase: a provider copilot that reads lab history, encounter context, prior orders, and trusted guidance to draft cited care-plan building blocks.",
        7,
        y=150,
        title_font=F["title_small"],
    )
    y = 730
    steps = [
        ("POC", "test selection"),
        ("Copilot", "care-plan blocks"),
        ("Health system", "thousands of decisions/month"),
    ]
    bw = 270
    for i, (name, sub) in enumerate(steps):
        x = MARGIN + i * (bw + 42)
        rounded_box(draw, (x, y, x + bw, y + 138), WHITE, RED if i == 1 else INK, radius=18, width=3)
        draw.text((x + 30, y + 30), name, font=F["body_bold"], fill=RED if i == 1 else INK)
        draw_wrapped(draw, sub, (x + 30, y + 76), F["small"], MUTED, bw - 60, 27)
        if i < 2:
            draw.line((x + bw + 10, y + 69, x + bw + 36, y + 69), fill=RED, width=4)
    rounded_box(draw, (MARGIN, 990, W - MARGIN, 1118), INK, INK, radius=14)
    draw_wrapped(
        draw,
        "A surface area measured in thousands of provider decisions per health system each month - and millions nationally.",
        (MARGIN + 34, 1024),
        F["body_bold"],
        WHITE,
        W - 2 * MARGIN - 68,
        42,
    )
    return img


def slide_8() -> Image.Image:
    img, draw = bg()
    title_block(
        draw,
        "The takeaways",
        "Three rules for AI that cannot bluff.",
        "",
        8,
        y=150,
        title_font=F["title_small"],
    )
    items = [
        ("01", "Own the boundary, or the model owns you.", "Trust starts with governed access, not a better prompt."),
        ("02", "Design agents small enough to review.", "When each job has an artifact, the system can be audited."),
        ("03", "Treat uncertainty as a workflow, not a weakness.", "Escalation is a feature when the decision carries risk."),
    ]
    y = 500
    for num, head, body in items:
        draw.text((MARGIN, y), num, font=F["number"], fill=RED)
        head_end = draw_wrapped(draw, head, (MARGIN + 145, y + 6), F["body_bold"], INK, W - 2 * MARGIN - 145, 42)
        body_end = draw_wrapped(draw, body, (MARGIN + 145, head_end + 10), F["small"], MUTED, W - 2 * MARGIN - 145, 30)
        y = max(y + 185, body_end + 54)
    return img


def slide_9() -> Image.Image:
    img, draw = bg(dark=True)
    header(draw, "The bar for clinical AI", dark=True)
    draw_wrapped(
        draw,
        "Zero ungrounded claims.\nVisible uncertainty.\nHuman handoff.",
        (MARGIN, 390),
        F["title_small"],
        RED,
        W - 2 * MARGIN,
        68,
    )
    draw_wrapped(
        draw,
        "This series will unpack the frameworks behind it: Hybrid Architecture, the Agentic Farm, the Debating Cycle, and the Formatting Agent.",
        (MARGIN, 720),
        F["body"],
        "#d7d7d7",
        W - 2 * MARGIN,
        43,
    )
    rounded_box(draw, (MARGIN, 980, MARGIN + 330, 1048), RED, RED, radius=34)
    draw.text((MARGIN + 44, 1001), "ANCHOR ARTICLE", font=F["small_bold"], fill=WHITE)
    footer(draw, 9, dark=True)
    return img


SLIDES = [
    slide_1,
    slide_2,
    slide_3,
    slide_4,
    slide_5,
    slide_6,
    slide_7,
    slide_8,
    slide_9,
]


def build_contact_sheet(images: list[Image.Image]) -> None:
    thumb_w, thumb_h = 216, 270
    pad, label_h = 22, 28
    cols = 3
    rows = 3
    sheet = Image.new("RGB", (cols * thumb_w + (cols + 1) * pad, rows * (thumb_h + label_h) + (rows + 1) * pad), WHITE)
    d = ImageDraw.Draw(sheet)
    for i, img in enumerate(images):
        thumb = img.copy()
        thumb.thumbnail((thumb_w, thumb_h))
        col = i % cols
        row = i // cols
        x = pad + col * (thumb_w + pad)
        y = pad + row * (thumb_h + label_h + pad)
        d.text((x, y), f"Slide {i + 1}", font=F["tiny"], fill=INK)
        sheet.paste(thumb, (x, y + label_h))
    sheet.save(CONTACT_SHEET_PATH, quality=95)


def main() -> None:
    SLIDE_DIR.mkdir(parents=True, exist_ok=True)
    images = [fn() for fn in SLIDES]
    for i, image in enumerate(images, start=1):
        image.save(SLIDE_DIR / f"slide_{i:02d}.png", quality=95)
    images[0].save(PDF_PATH, save_all=True, append_images=images[1:], resolution=100.0)
    build_contact_sheet(images)
    print(f"Wrote {len(images)} slides to {SLIDE_DIR}")
    print(f"Wrote {PDF_PATH}")
    print(f"Wrote {CONTACT_SHEET_PATH}")


if __name__ == "__main__":
    main()
