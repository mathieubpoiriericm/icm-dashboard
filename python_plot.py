import csv
import math

# ---------- CONFIG ----------
WIDTH, HEIGHT = 800, 800
# X_OFFSET shifts all content right to avoid negative viewBox coordinates (Chromium iframe bug)
X_OFFSET = 100
CX, CY = WIDTH / 2 + X_OFFSET, HEIGHT / 2

# Rings for phases (inner radius, outer radius)
phase_rings = {
    "III": (0, 180),
    "II":  (180, 250),
    "I":   (250, 320),
}

# Order & colors for SVD populations (angular sectors)
populations = ["CAA", "Cognitive Impairment", "Stroke", "Any SVD (including monogenic)"]
pop_colors = {
    "CAA":                            "#440154",  # viridis purple
    "Cognitive Impairment":           "#31688e",  # viridis blue
    "Stroke":                         "#35b779",  # viridis green
    "Any SVD (including monogenic)":  "#fde725",  # viridis yellow
}

MARKER_R = 9
LABEL_D = 10

# Shadow configuration (manual shadows to avoid Chromium filter bugs)
SHADOW_OFFSET_X = 2
SHADOW_OFFSET_Y = 2
SHADOW_COLOR = "#000000"
SHADOW_OPACITY = 0.2

# Manual positioning adjustments for SVD Population labels
# Each population can have: h_offset, v_offset (distances from boundary)
pop_label_config = {
    "CAA": {"h_offset": 15, "v_offset": 0},
    "Cognitive Impairment": {"h_offset": 5, "v_offset": 0},
    "Stroke": {"h_offset": 0, "v_offset": -20},
    "Any SVD (including monogenic)": {"h_offset": 200, "v_offset": -35},
}

# ---------- HELPERS ----------

def estimate_text_width(text, font_size):
    """
    Estimate text width in pixels for Roboto font.
    Uses approximate character widths based on Roboto metrics.
    """
    # Approximate character widths as fraction of font size for Roboto
    char_widths = {
        'i': 0.25, 'l': 0.25, 'I': 0.25, 'j': 0.25, 't': 0.35, 'f': 0.35, 'r': 0.38,
        ' ': 0.25, '.': 0.25, ',': 0.25, ':': 0.25, ';': 0.25, '!': 0.25, '|': 0.25,
        'a': 0.55, 'c': 0.52, 'e': 0.55, 'g': 0.55, 'n': 0.55, 'o': 0.55, 's': 0.50,
        'b': 0.55, 'd': 0.55, 'h': 0.55, 'k': 0.50, 'p': 0.55, 'q': 0.55, 'u': 0.55,
        'v': 0.50, 'x': 0.50, 'y': 0.50, 'z': 0.50,
        'm': 0.85, 'w': 0.75,
        'A': 0.65, 'B': 0.65, 'C': 0.65, 'D': 0.68, 'E': 0.60, 'F': 0.58, 'G': 0.70,
        'H': 0.68, 'J': 0.50, 'K': 0.65, 'L': 0.55, 'M': 0.82, 'N': 0.68, 'O': 0.72,
        'P': 0.62, 'Q': 0.72, 'R': 0.65, 'S': 0.60, 'T': 0.60, 'U': 0.68, 'V': 0.65,
        'W': 0.90, 'X': 0.65, 'Y': 0.62, 'Z': 0.60,
        '0': 0.55, '1': 0.55, '2': 0.55, '3': 0.55, '4': 0.55,
        '5': 0.55, '6': 0.55, '7': 0.55, '8': 0.55, '9': 0.55,
        '-': 0.35, '–': 0.50, '—': 0.70, '(': 0.35, ')': 0.35,
    }

    width = 0
    for char in text:
        width += char_widths.get(char, 0.55) * font_size  # default to 0.55 for unknown chars

    return width

def pol2cart(r, theta):
    """Polar (r, theta in radians) -> Cartesian (x, y)"""
    return CX + r * math.cos(theta), CY + r * math.sin(theta)

def annular_sector_path(r_inner, r_outer, theta0, theta1):
    """SVG path for an annular sector between angles theta0, theta1 (radians)."""
    large_arc = 1 if (theta1 - theta0) > math.pi else 0

    # Outer arc endpoints
    x0o, y0o = pol2cart(r_outer, theta0)
    x1o, y1o = pol2cart(r_outer, theta1)

    if r_inner <= 0:
        # Wedge from center
        d = [
            f"M {CX:.2f} {CY:.2f}",
            f"L {x0o:.2f} {y0o:.2f}",
            f"A {r_outer:.2f} {r_outer:.2f} 0 {large_arc} 1 {x1o:.2f} {y1o:.2f}",
            "Z",
        ]
        return " ".join(d)

    # Normal annular sector (donut slice)
    x1i, y1i = pol2cart(r_inner, theta1)
    x0i, y0i = pol2cart(r_inner, theta0)

    d = [
        f"M {x0o:.2f} {y0o:.2f}",
        f"A {r_outer:.2f} {r_outer:.2f} 0 {large_arc} 1 {x1o:.2f} {y1o:.2f}",
        f"L {x1i:.2f} {y1i:.2f}",
        f"A {r_inner:.2f} {r_inner:.2f} 0 {large_arc} 0 {x0i:.2f} {y0i:.2f}",
        "Z",
    ]
    return " ".join(d)

def shade_hex(hex_color, factor):
    """Darkens a hex color by factor in [0,1]."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    r2 = int(r * (1 - factor))
    g2 = int(g * (1 - factor))
    b2 = int(b * (1 - factor))

    return f"#{r2:02x}{g2:02x}{b2:02x}"

# ---------- LOAD DATA ----------
rows = []
with open("./data/csv/table2_for_py.csv", newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

# Map CSV population names to display names
pop_name_mapping = {
    "SVD": "Any SVD (including monogenic)",
    "CAA": "CAA",
    "Cognitive Impairment": "Cognitive Impairment",
    "Stroke": "Stroke"
}

# Group rows by population to distribute angles & markers
pop_to_rows = {p: [] for p in populations}
for r in rows:
    csv_pop = r["SVD Population"]
    display_pop = pop_name_mapping.get(csv_pop, csv_pop)
    pop_to_rows[display_pop].append(r)

# ---------- ANGLES PROPORTIONAL TO UNIQUE DRUG COUNTS ----------
pop_counts = {}
for pop in populations:
    drugs = set()
    for r in pop_to_rows[pop]:
        name = (r.get("Drug") or r.get("\ufeffDrug") or "").strip()
        if name:
            drugs.add(name)
    pop_counts[pop] = len(drugs)

total = sum(pop_counts.values()) or 1  # avoid div-by-zero if empty

# Angular span for each population
pop_angles = {
    pop: (count / total) * 2 * math.pi
    for pop, count in pop_counts.items()
}

# Cumulative angles (before rotation)
pop_theta0 = {}
pop_theta1 = {}
cum = 0.0
for pop in populations:
    theta0 = cum
    theta1 = cum + pop_angles[pop]
    pop_theta0[pop] = theta0
    pop_theta1[pop] = theta1
    cum = theta1

# Rotate so first population starts at the top (12 o'clock)
ROTATE = -math.pi / 2
for pop in populations:
    pop_theta0[pop] += ROTATE
    pop_theta1[pop] += ROTATE

# ---------- BUILD SVG ----------
svg_parts = []

# SVG header + defs
svg_parts.append(
    f'<svg width="1420" height="{HEIGHT}" '
    f'viewBox="0 0 1520 {HEIGHT}" '
    f'xmlns="http://www.w3.org/2000/svg">'
)
svg_parts.append("<defs>")
svg_parts.append(
    f'<symbol id="marker" overflow="visible">'
    f'<circle cx="0" cy="0" r="{MARKER_R}" fill="currentColor"/>'
    f'</symbol>'
)
svg_parts.append(
    f'<symbol id="marker_tri" overflow="visible">'
    f'<polygon points="0,-{MARKER_R} {MARKER_R},{MARKER_R} -{MARKER_R},{MARKER_R}" fill="currentColor"/>'
    f'</symbol>'
)
svg_parts.append("</defs>")


# Background (shifted by X_OFFSET)
svg_parts.append(
    f'<rect x="{X_OFFSET}" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#ffffff" fill-opacity="0"/>'
)

global_r_outer = max(r[1] for r in phase_rings.values())

# ---------- POPULATION × PHASE WEDGES ----------
for pop in populations:
    theta0 = pop_theta0[pop]
    theta1 = pop_theta1[pop]

    pop_rows = pop_to_rows.get(pop, [])
    phases_with_drugs = set()
    for r in pop_rows:
        phases_with_drugs.add(r["Clinical Trial Phase"].strip())

    for phase, (r_inner, r_outer) in phase_rings.items():
        if phase not in phases_with_drugs:
            color = "#c8c8c8"
            grey_opacity = 0.4
        else:
            # Keep base saturated color, apply opacity gradient instead of shading
            color = pop_colors.get(pop, "#444")
            grey_opacity = None

        d = annular_sector_path(r_inner, r_outer, theta0, theta1)

        phase_opacity = {"I": 0.35, "II": 0.6, "III": 1.0}
        if grey_opacity is not None:
            opacity = grey_opacity
        else:
            opacity = phase_opacity.get(phase, 0.6)

        # Shadow for wedge
        svg_parts.append(
            f'<path d="{d}" fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}" '
            f'transform="translate({SHADOW_OFFSET_X},{SHADOW_OFFSET_Y})"/>'
        )
        # Main wedge
        svg_parts.append(
            f'<path class="wedge" '
            f'data-pop="{pop}" data-phase="{phase}" '
            f'data-pop-color="{pop_colors.get(pop, "#444")}" '
            f'd="{d}" fill="{color}" fill-opacity="{opacity}"/>'
        )

    # population label with background rect - position outside plot touching at corner
    label_theta = (theta0 + theta1) / 2

    # Calculate position on the outer boundary
    boundary_x, boundary_y = pol2cart(global_r_outer, label_theta)

    # Determine text anchor based on which side of the plot
    cos_theta = math.cos(label_theta)
    sin_theta = math.sin(label_theta)

    # Position label offset from boundary, with anchor appropriate for the side
    # Add padding to account for label box (8px padding in adjustLabelBackgrounds)
    box_padding = 10

    # Get manual positioning config for this population
    config = pop_label_config.get(pop, {"h_offset": 5, "v_offset": 5})
    h_offset = config["h_offset"] + box_padding  # horizontal distance from boundary to label box edge
    v_offset = config["v_offset"] + box_padding  # vertical distance from boundary to label box edge

    if cos_theta >= 0:
        # Right side - anchor at start (left edge of text)
        anchor = "start"
        # Position text so that box edge (text start - padding) is at offset from boundary
        lx = boundary_x + h_offset
    else:
        # Left side - anchor at end (right edge of text)
        anchor = "end"
        # Position text so that box edge (text end + padding) is at offset from boundary
        lx = boundary_x - h_offset

    # Vertical positioning
    if sin_theta > 0:
        # Upper half - position above the boundary
        # Use middle baseline for consistent cross-browser rendering
        # Adjust ly to account for text height (half of two-line text box)
        ly = boundary_y - v_offset
        v_baseline = "middle"
    else:
        # Lower half - position below the boundary
        # Use middle baseline for consistent cross-browser rendering
        ly = boundary_y + v_offset
        v_baseline = "middle"

    base_col = pop_colors.get(pop, "#444")

    # Default text position (will be adjusted for two-line labels)
    text_ly = ly

    # Estimate text dimensions for initial box rendering
    if pop == "Cognitive Impairment":
        # Two-line text with 1.2em line spacing
        est_width = max(estimate_text_width("Cognitive", 18), estimate_text_width("Impairment", 18))
        # Height calculation: first line (18px) + dy spacing (18*1.2) + descent of second line
        # Total: 18 + 21.6 + 4 (descent) ≈ 43.6px
        est_height = 40  # Tighter height for two-line text

        # For two-line text, use minimal padding for tighter fit
        padX, padY = 6, 4  # Reduced padding for tighter box borders

        # Position box relative to text anchor
        if anchor == "start":
            box_x = lx - padX
        else:
            box_x = lx - est_width - padX

        # For two-line text with middle baseline:
        # The first tspan is centered at ly, but we want the whole two-line block centered
        # Adjust ly upward by half the spacing between lines
        text_ly = ly - (18 * 1.2) / 2  # Move up by half of the dy spacing

        # Position box to contain the adjusted text
        box_y = text_ly - 18 / 2 - padY
    elif pop == "Any SVD (including monogenic)":
        # Two-line text with 1.2em line spacing
        est_width = max(estimate_text_width("Any SVD", 18), estimate_text_width("(including monogenic)", 18))
        # Height calculation: first line (18px) + dy spacing (18*1.2) + descent of second line
        # Total: 18 + 21.6 + 4 (descent) ≈ 43.6px
        est_height = 40  # Tighter height for two-line text

        # For two-line text, use minimal padding for tighter fit
        padX, padY = 8, 4  # Reduced padding for tighter box borders

        # Special positioning: move label completely outside plot
        # Position the center of the box at the offset distance
        extra_offset = (est_width + padX * 2) / 2
        lx_center = lx + extra_offset

        # Store the centered x position for the text
        lx = lx_center

        # Position box relative to centered text
        box_x = lx_center - est_width / 2 - padX

        # For two-line text with middle baseline:
        # The first tspan is centered at ly, but we want the whole two-line block centered
        # Adjust ly upward by half the spacing between lines
        text_ly = ly - (18 * 1.2) / 2  # Move up by half of the dy spacing

        # Position box to contain the adjusted text
        box_y = text_ly - 18 / 2 - padY
    else:
        est_width = estimate_text_width(pop, 18)
        est_height = 18  # Single line height

        padX, padY = 8, 4
        # Position box relative to text anchor
        if anchor == "start":
            box_x = lx - padX
        else:
            box_x = lx - est_width - padX

        # Position box relative to vertical baseline (middle)
        # For middle baseline, text is vertically centered at ly
        # Box should be centered on ly as well
        box_y = ly - est_height / 2 - padY

    svg_parts.append(
        f'<g class="pop-label" data-pop="{pop}">' +
        # Shadow for label box
        f'<rect x="{box_x + SHADOW_OFFSET_X:.2f}" y="{box_y + SHADOW_OFFSET_Y:.2f}" width="{est_width + padX*2:.2f}" height="{est_height + padY*2:.2f}" rx="6" ' +
        f'fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}" pointer-events="none"/>' +
        # Main label box
        f'<rect class="label-bg pop-bg" x="{box_x:.2f}" y="{box_y:.2f}" width="{est_width + padX*2:.2f}" height="{est_height + padY*2:.2f}" rx="6" ' +
        f'fill="{base_col}" fill-opacity="0.6" pointer-events="none"/>' +
        (
            f'<text x="{lx:.2f}" y="{text_ly:.2f}" fill="#000" font-size="18" ' +
            f'text-anchor="{anchor}" dominant-baseline="{v_baseline}">{pop}</text>'
            if pop not in ["Cognitive Impairment", "Any SVD (including monogenic)"] else
            (
                f'<text x="{lx:.2f}" y="{text_ly:.2f}" fill="#000" font-size="18" text-anchor="{anchor}" dominant-baseline="{v_baseline}">' +
                f'<tspan x="{lx:.2f}" dy="0">Cognitive</tspan>' +
                f'<tspan x="{lx:.2f}" dy="1.2em">Impairment</tspan></text>'
                if pop == "Cognitive Impairment" else
                f'<text x="{lx:.2f}" y="{text_ly:.2f}" fill="#000" font-size="18" text-anchor="middle" dominant-baseline="{v_baseline}">' +
                f'<tspan x="{lx:.2f}" dy="0">Any SVD</tspan>' +
                f'<tspan x="{lx:.2f}" dy="1.2em">(including monogenic)</tspan></text>'
            )
        ) +
        '</g>'
    )

boundary_angle = pop_theta1["Any SVD (including monogenic)"]

# ---------- PHASE RINGS + LABELS ----------
for phase, (r0, r1) in phase_rings.items():
    r_mid = (r0 + r1) / 2
    # Phase ring circles removed - boundaries are now defined by wedge shadows only
    lx, ly = pol2cart(r_mid, boundary_angle)

    # Estimate text dimensions for phase labels (16px font)
    phase_text = f"Phase {phase}"
    est_width = estimate_text_width(phase_text, 16)
    est_height = 16 * 1.2  # Single line height with spacing
    padX, padY = 8, 4
    # Middle anchor, middle baseline
    box_x = lx - est_width / 2 - padX
    box_y = ly - est_height / 2 - padY

    svg_parts.append(
        f'<g class="phase-label" data-phase="{phase}">'
        # Shadow for phase label box
        f'<rect x="{box_x + SHADOW_OFFSET_X:.2f}" y="{box_y + SHADOW_OFFSET_Y:.2f}" width="{est_width + padX*2:.2f}" height="{est_height + padY*2:.2f}" rx="6" '
        f'fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}" pointer-events="none"/>'
        # Main phase label box
        f'<rect class="label-bg phase-bg" x="{box_x:.2f}" y="{box_y:.2f}" width="{est_width + padX*2:.2f}" height="{est_height + padY*2:.2f}" rx="6" '
        f'fill="#ffffff" fill-opacity="0.6" pointer-events="none"/>'
        f'<text x="{lx:.2f}" y="{ly:.2f}" fill="#000" font-size="16" '
        f'text-anchor="middle" dominant-baseline="middle">'
        f'Phase {phase}</text>'
        f'</g>'
    )

# ---------- BRIGHT, SATURATED COLORS FOR MECHANISM OF ACTION ----------
# Collect unique mechanisms
mechanisms = []
for r in rows:
    mech = (r.get("Mechanism of Action") or "").strip()
    if mech and mech not in mechanisms:
        mechanisms.append(mech)

# Bright, saturated, easily distinguishable colors for drug markers
marker_palette = [
    "#e41a1c",  # red
    "#377eb8",  # blue
    "#4daf4a",  # green
    "#984ea3",  # purple
    "#ff7f00",  # orange
    "#ffff33",  # yellow
    "#a65628",  # brown
    "#f781bf",  # pink
    "#999999",  # grey
]

# Map mechanisms to colors (cycle if more mechanisms than colors)
mech_colors = {mech: marker_palette[i % len(marker_palette)] for i, mech in enumerate(mechanisms)}

# ---------- DRUG MARKERS ----------
svg_parts.append(
    '<g id="drugs" font-family="Roboto" font-size="12" fill="#000">'
)

for pop in populations:
    pop_rows = pop_to_rows[pop]
    if not pop_rows:
        continue

    theta0 = pop_theta0[pop]
    theta1 = pop_theta1[pop]

    phase_to_rows = {}
    for row in pop_rows:
        ph = row["Clinical Trial Phase"].strip()
        phase_to_rows.setdefault(ph, []).append(row)

    for phase, rows_in_phase in phase_to_rows.items():
        r0, r1 = phase_rings[phase]
        r = (r0 + r1) / 2

        m = len(rows_in_phase)
        for j, row in enumerate(rows_in_phase):
            drug = (row.get("Drug") or row.get("\ufeffDrug") or "").strip()
            if not drug:
                continue

            frac = (j + 1) / (m + 1)
            theta = theta0 + frac * (theta1 - theta0)

            x, y = pol2cart(r, theta)
            # Position labels at consistent horizontal distance from markers
            horizontal_distance = 10  # pixels from marker edge
            anchor = "start" if math.cos(theta) >= 0 else "end"

            # Calculate label position: marker position + horizontal offset
            if math.cos(theta) >= 0:
                # Right side: label to the right of marker
                lx = x + MARKER_R + horizontal_distance
            else:
                # Left side: label to the left of marker
                lx = x - MARKER_R - horizontal_distance
            ly = y

            tooltip = f"{drug} — Phase {phase}, {pop}"

            # Set background color based on genetic evidence
            has_genetic_evidence = row.get("Genetic Evidence", "").strip() == "Yes"
            label_bg_color = "#90ee90" if has_genetic_evidence else "#ffffff"  # darker green if Yes, white otherwise

            # Estimate text dimensions for drug labels (12px font)
            est_width = estimate_text_width(drug, 12)
            est_height = 12 * 1.3  # Single line height with spacing
            padX, padY = 4, 2
            # Position box relative to text anchor
            if anchor == "start":
                box_x = lx - padX
            else:
                box_x = lx - est_width - padX
            box_y = ly - est_height / 2 - padY  # middle baseline

            svg_parts.append(
                f'<g class="drug" '
                f'data-drug="{drug}" '
                f'data-phase="{phase}" '
                f'data-pop="{pop}" '
                f'data-pop-color="{pop_colors.get(pop, "#444")}" '
                f'data-mech="{(row.get("Mechanism of Action") or "").strip()}" '
                f'data-gtarget="{(row.get("Genetic Target") or "").strip()}" '
                f'data-ge="{(row.get("Genetic Evidence") or "").strip()}" '
                f'data-tname="{(row.get("Trial Name") or "").strip()}" '
                f'data-regid="{(row.get("Registry ID") or "").strip()}" '
                f'data-svdpopd="{(row.get("SVD Population Details") or "").strip()}" '
                f'data-ssize="{(row.get("Target Sample Size") or "").strip()}" '
                f'data-estcomp="{(row.get("Estimated Completion Date") or "").strip()}" '
                f'data-pco="{(row.get("Primary Outcome") or "").strip()}" '
                f'data-sptype="{(row.get("Sponsor Type") or "").strip()}">'
                f'<title>{tooltip}</title>'
                # Shadow for drug marker
                f'<circle cx="{x + SHADOW_OFFSET_X:.2f}" cy="{y + SHADOW_OFFSET_Y:.2f}" r="{MARKER_R}" '
                f'fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}"/>'
                # Main drug marker
                f'<use href="#marker" '
                f'x="{x:.2f}" y="{y:.2f}" color="{mech_colors.get(row.get("Mechanism of Action","").strip(), "#ffffff")}"/>'
                # Shadow for drug label box
                f'<rect x="{box_x + SHADOW_OFFSET_X:.2f}" y="{box_y + SHADOW_OFFSET_Y:.2f}" width="{est_width + padX*2:.2f}" height="{est_height + padY*2:.2f}" rx="6" '
                f'fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}" pointer-events="none"/>'
                # Main drug label box
                f'<rect class="label-bg drug-bg" x="{box_x:.2f}" y="{box_y:.2f}" width="{est_width + padX*2:.2f}" height="{est_height + padY*2:.2f}" rx="6" '
                f'fill="{label_bg_color}" fill-opacity="0.6" pointer-events="none"/>'
                f'<text x="{lx:.2f}" y="{ly:.2f}" '
                f'text-anchor="{anchor}" dominant-baseline="middle">'
                f'{drug}</text>'
                f'</g>'
            )

svg_parts.append("</g>")
# ---------- MARKER LEGEND ----------
# Position legend at top-right of plot
legend_x = CX + global_r_outer + 150
legend_y = CY - global_r_outer + 20

# Calculate Genetic Evidence legend box dimensions
legend_title_width = estimate_text_width("Genetic Evidence", 15)
legend_item_width = 40 + 18 + 8  # Yes/No box + marker + spacing
legend_width = max(legend_title_width, legend_item_width) + 40  # Add padding
legend_height = 90  # Fixed height for two items

# Center the Yes/No boxes within the legend
legend_center_x = legend_x + legend_width/2 - 20  # Center of legend box
box_width = 40
yes_box_x = legend_center_x - box_width/2
no_box_x = legend_center_x - box_width/2

svg_parts.append(
    f'<g id="legend" font-size="14" fill="#000" font-family="Roboto">'
    # Shadow for legend background
    f'<rect x="{legend_x - 20 + SHADOW_OFFSET_X}" y="{legend_y - 30 + SHADOW_OFFSET_Y}" width="{legend_width:.2f}" height="{legend_height:.2f}" '
    f'rx="10" fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}"/>'
    # Main legend background
    f'<rect id="legend-bg" x="{legend_x - 20}" y="{legend_y - 30}" width="{legend_width:.2f}" height="{legend_height:.2f}" '
    f'rx="10" fill="#c8c8c8" fill-opacity="0.4"/>'
    f'<text x="{legend_center_x:.2f}" y="{legend_y - 12}" text-anchor="middle" font-family="Roboto" font-size="15" font-weight="500">Genetic Evidence</text>'
    f'<g class="legend-item">'
    # Shadow for Yes box
    f'<rect x="{yes_box_x + SHADOW_OFFSET_X:.2f}" y="{legend_y + 2 + SHADOW_OFFSET_Y}" width="{box_width}" height="20" rx="6" '
    f'fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}"/>'
    # Main Yes box
    f'<rect x="{yes_box_x:.2f}" y="{legend_y + 2}" width="{box_width}" height="20" rx="6" '
    f'fill="#90ee90" fill-opacity="0.6"/>'
    f'<text x="{legend_center_x:.2f}" y="{legend_y + 12}" text-anchor="middle" dominant-baseline="middle" font-family="Roboto">Yes</text>'
    f'</g>'
    f'<g class="legend-item">'
    # Shadow for No box
    f'<rect x="{no_box_x + SHADOW_OFFSET_X:.2f}" y="{legend_y + 30 + SHADOW_OFFSET_Y}" width="{box_width}" height="20" rx="6" '
    f'fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}"/>'
    # Main No box
    f'<rect x="{no_box_x:.2f}" y="{legend_y + 30}" width="{box_width}" height="20" rx="6" '
    f'fill="#ffffff" fill-opacity="0.6"/>'
    f'<text x="{legend_center_x:.2f}" y="{legend_y + 40}" text-anchor="middle" dominant-baseline="middle" font-family="Roboto">No</text>'
    f'</g>'
    f'</g>'
)

# ---------- MECHANISM OF ACTION LEGEND ----------
# Move MOA legend directly underneath the Genetic Evidence legend
legend_moa_x = legend_x
legend_moa_y = legend_y + 110

# Calculate MOA legend box height based on number of mechanisms
num_mechanisms = len(mechanisms)
moa_legend_height = 36 + num_mechanisms * 36 + 5  # header + items + padding (increased spacing)

# Calculate maximum text width for mechanisms
max_mech_width = 0
for mech in mechanisms:
    # Estimate based on longest line in mechanism text
    if "(" in mech:
        lines = [mech[:mech.find("(")].rstrip(), mech[mech.find("("):]]
        max_line_width = max(estimate_text_width(line, 14) for line in lines)
    else:
        max_line_width = estimate_text_width(mech, 14)
    max_mech_width = max(max_mech_width, max_line_width)

# Also account for title width
title_width = estimate_text_width("Mechanism of Action", 15)
moa_legend_width = max(max_mech_width + 50, title_width + 40)  # +50 for marker and padding

svg_parts.append(
    f'<g id="legend-moa" font-size="14" fill="#000" font-family="Roboto">'
    # Shadow for MOA legend background
    f'<rect x="{legend_moa_x - 20 + SHADOW_OFFSET_X}" y="{legend_moa_y - 30 + SHADOW_OFFSET_Y}" width="{moa_legend_width:.2f}" height="{moa_legend_height:.2f}" '
    f'rx="10" fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}"/>'
    # Main MOA legend background
    f'<rect id="legend-moa-bg" x="{legend_moa_x - 20}" y="{legend_moa_y - 30}" width="{moa_legend_width:.2f}" height="{moa_legend_height:.2f}" '
    f'rx="10" fill="#c8c8c8" fill-opacity="0.4"/>'
    f'<text x="{legend_moa_x + moa_legend_width/2 - 20:.2f}" y="{legend_moa_y - 12}" text-anchor="middle" font-family="Roboto" font-size="15" font-weight="500">Mechanism of Action</text>'
)

# Add one entry per mechanism
for i, mech in enumerate(mechanisms):
    y = legend_moa_y + 16 + i * 36  # Increased spacing from title
    color = mech_colors.get(mech, "#000000")
    # Split mechanism into before/after parenthesis
    paren_index = mech.find("(")
    if paren_index != -1:
        mech_main = mech[:paren_index].rstrip()
        mech_paren = mech[paren_index:].strip()
        text_block = (
            f'<text x="{legend_moa_x + 24}" y="{y}" font-family="Roboto">'
            f'{mech_main}'
            f'<tspan x="{legend_moa_x + 24}" dy="1.2em">{mech_paren}</tspan>'
            f'</text>'
        )
    else:
        text_block = (
            f'<text x="{legend_moa_x + 24}" y="{y}" dominant-baseline="middle" font-family="Roboto">{mech}</text>'
        )

    svg_parts.append(
        f'<g class="legend-item">'
        # Shadow for mechanism circle
        f'<circle cx="{legend_moa_x + SHADOW_OFFSET_X}" cy="{y + SHADOW_OFFSET_Y}" r="9" fill="{SHADOW_COLOR}" fill-opacity="{SHADOW_OPACITY}"/>'
        # Main mechanism circle
        f'<circle cx="{legend_moa_x}" cy="{y}" r="9" fill="{color}"/>'
        f'{text_block}'
        f'</g>'
    )

svg_parts.append("</g>")
svg_parts.append("</svg>")

svg_html = "\n".join(svg_parts)

# ---------- HTML ----------
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Cache-Control" content="public, max-age=86400">
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  body {{
    margin: 0;
    padding: 0;
    background: #ffffff;
    overflow: hidden;
  }}
  body.svg-loading .fig {{
    opacity: 0;
    visibility: hidden;
  }}
  body.svg-ready .fig {{
    opacity: 1;
    visibility: visible;
    transition: opacity 0.35s ease-in-out;
  }}
  .fig {{
      display: flex;
      align-items: flex-start;
  }}
  .fig-c {{
      position: relative;
      z-index: 1;
  }}
  #sidebar {{
    position: absolute;
    left: 0;
    top: 0;
    height: 100%;
    z-index: 1000;
    display: inline-block;
    width: 0;
    background: rgba(200, 200, 200, 0.4);
    color: #000;
    font-family: "Roboto", Arial, sans-serif;
    overflow-x: hidden;
    overflow-y: auto;
    transition: width 0.3s ease, padding 0.3s ease;
    padding: 0;
    box-shadow: 2px 0 8px rgba(0,0,0,0.15);
    border-radius: 0 20px 20px 0;
  }}
  #sidebar.open {{
    width: auto;
    min-width: 179px;
    max-width: 319px;
    padding: 16px;
  }}
  #sidebar-content {{
    white-space: normal;
    word-wrap: break-word;
    max-width: 298px;
    opacity: 1;
    font-size: 12px;
    line-height: 1.55;
  }}
  svg {{
    font-family: "Roboto", Arial, sans-serif;
  }}
  g.drug text {{
    visibility: visible;
    opacity: 1;
    pointer-events: auto;
    cursor: pointer;
  }}
  g.drug rect.drug-bg {{
    visibility: visible;
    opacity: 1;
  }}
  #tooltip {{
    --tip-color-rgba: rgba(255,255,255,0.25);
    position: fixed;
    padding: 9px 10px;
    border-radius: 20px;
    min-width: 240px;
    max-width: 320px;
    font-family: "Roboto", Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    pointer-events: none;
    display: flex;
    flex-direction: column;
    box-shadow:
      0 18px 35px rgba(0,0,0,0.55),
      0 0 22px var(--tip-color-rgba);
    border: 1px solid rgba(255,255,255,0.12);
    backdrop-filter: blur(10px);
    color: #fff;
    z-index: 9999;
    opacity: 0;
    transform: scale(0.94);
    transition:
      opacity 0.35s ease-in-out,
      transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    overflow: hidden;
    background: var(--tip-color-rgba);
  }}
  #tooltip > * {{
    position: relative;
    z-index: 2;
  }}
  #tooltip::before {{
    content: "";
    position: absolute;
    top: 0;
    left: -35%;
    width: 170%;
    height: 170%;
    background: radial-gradient(
      circle at top left,
      rgba(255,255,255,0.22),
      rgba(255,255,255,0.05) 40%,
      rgba(255,255,255,0) 70%
    );
    pointer-events: none;
    transform: rotate(25deg);
    z-index: 3;
  }}
  #tooltip::after {{
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(140deg, var(--tip-color-rgba), rgba(255,255,255,0));
    opacity: 0.9;
    pointer-events: none;
    z-index: 1;
  }}
  #tooltip.show {{
    opacity: 1;
    transform: scale(1);
  }}
</style>
</head>
<body class="svg-loading">
<!-- Mask for Chromium iframe rendering artifact -->
<div style="position:fixed;top:0;left:0;width:40px;height:40px;background:#fff;z-index:9999;pointer-events:none;"></div>
<div class="fig">
  <div id="sidebar">
    <div id="sidebar-content"></div>
  </div>
  <div class="fig-c">
    {svg_html}
  </div>
</div>
<script src="python_plot.js"></script>
</body>
</html>
"""

# ---------- WRITE FILES ----------
with open("www/python_plot.html", "w", encoding="utf-8") as f:
    f.write(html)

js_code = r"""
(function() {
  document.body.classList.add('svg-loading');

  const readyTimer = setTimeout(markReady, 2000);

  function markReady() {
    clearTimeout(readyTimer);
    document.body.classList.remove('svg-loading');
    document.body.classList.add('svg-ready');
  }

  function waitForFonts() {
    if (document.fonts && document.fonts.ready) {
      return document.fonts.ready.catch(() => {});
    }
    return Promise.resolve();
  }

  function nextFrame() {
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

    // Prevent label boxes from overlapping their own drug markers
  function adjustMarkerLabelOverlap() {
    const markers = document.querySelectorAll('g.drug use');
    markers.forEach(mk => {
      const g = mk.closest('g.drug');
      const text = g.querySelector('text');
      const rect = g.querySelector('rect.label-bg');
      if (!text || !rect) return;

      const mkX = parseFloat(mk.getAttribute('x'));
      const mkY = parseFloat(mk.getAttribute('y'));
      const mkR = 9; // marker radius

      const rx = parseFloat(rect.getAttribute('x'));
      const ry = parseFloat(rect.getAttribute('y'));
      const rw = parseFloat(rect.getAttribute('width'));
      const rh = parseFloat(rect.getAttribute('height'));

      // Check if circle (marker) intersects rectangle (label background)
      const overlap =
        mkX + mkR > rx &&
        mkX - mkR < rx + rw &&
        mkY + mkR > ry &&
        mkY - mkR < ry + rh;

      if (overlap) {
        // Nudge label outward radially
        const svg = document.querySelector('svg');
        const cx = parseFloat(svg.getAttribute('width')) / 2;
        const cy = parseFloat(svg.getAttribute('height')) / 2;

        const bbox = text.getBBox();
        const dx = bbox.x - cx;
        const dy = bbox.y - cy;
        const len = Math.sqrt(dx*dx + dy*dy) || 1;

        const nudgedX = bbox.x + dx/len * 10;
        const nudgedY = bbox.y + dy/len * 10;

        text.setAttribute('x', (nudgedX + bbox.width/2).toFixed(2));
        text.setAttribute('y', (nudgedY + bbox.height/2).toFixed(2));

        const newBox = text.getBBox();
        const padX = 8, padY = 4;
        rect.setAttribute('x', (newBox.x - padX).toFixed(2));
        rect.setAttribute('y', (newBox.y - padY).toFixed(2));
        rect.setAttribute('width', (newBox.width + padX*2).toFixed(2));
        rect.setAttribute('height', (newBox.height + padY*2).toFixed(2));
      }
    });
  }

  // --- Inserted functions for Cognitive Impairment overlap ---
  function boxesOverlap(a, b) {
    return (
      a.x < b.x + b.width &&
      a.x + a.width > b.x &&
      a.y < b.y + b.height &&
      a.y + a.height > b.y
    );
  }

  // Fix two-line population label boxes - DISABLED due to Chromium getBBox() bug in iframes
  // Rects are pre-rendered with correct dimensions
  function adjustTwoLinePopLabels() {
    return; // Disabled - getBBox() returns incorrect values in Chromium iframes
  }

  function avoidCognitiveOverlap() {
    const popLabel = document.querySelector('g.pop-label[data-pop="Cognitive Impairment"]');
    const legend = document.getElementById('legend-moa-bg');
    if (!popLabel || !legend) return;

    const popBox = popLabel.getBBox();
    const legBox = legend.getBBox();

    if (boxesOverlap(popBox, legBox)) {
      const text = popLabel.querySelector('text');
      if (text) {
        let x = parseFloat(text.getAttribute("x"));
        x -= 30;
        text.setAttribute("x", x.toFixed(2));

        const rect = popLabel.querySelector('rect.label-bg');
        if (rect) {
          const nb = text.getBBox();
          const padX = 8, padY = 4;
          rect.setAttribute('x', (nb.x - padX).toFixed(2));
          rect.setAttribute('y', (nb.y - padY).toFixed(2));
          rect.setAttribute('width', (nb.width + padX * 2).toFixed(2));
          rect.setAttribute('height', (nb.height + padY * 2).toFixed(2));
        }
      }
    }
  }
  // --- End inserted functions ---

  // Fine-tune label background rectangles (boxes are pre-rendered with estimated dimensions)
  function adjustLabelBackgrounds() {
    // DISABLED: Boxes are fully pre-rendered with proper dimensions
    // This function is intentionally disabled to avoid Chromium getBBox() bugs in iframes
    // If you need fine-tuning, uncomment the code below, but the pre-rendered dimensions
    // should be accurate enough for production use.
    return;

    /* OPTIONAL FINE-TUNING (disabled to avoid Chromium bugs):
    const groups = document.querySelectorAll('g.pop-label, g.phase-label, g.drug');
    groups.forEach(g => {
      const rect = g.querySelector('rect.label-bg');
      const text = g.querySelector('text');
      if (!rect || !text) return;

      try {
        const bbox = text.getBBox();
        const padX = 8;
        const padY = 4;
        rect.setAttribute('x', (bbox.x - padX).toFixed(2));
        rect.setAttribute('y', (bbox.y - padY).toFixed(2));
        rect.setAttribute('width', (bbox.width + padX * 2).toFixed(2));
        rect.setAttribute('height', (bbox.height + padY * 2).toFixed(2));
      } catch (e) {
        console.warn('getBBox failed for label, keeping pre-rendered dimensions:', e);
      }
    });
    */
  }

  // Auto-size legend box (kept for fine-tuning, but legends are pre-rendered)
  function adjustLegendBox() {
      // Legend boxes are pre-rendered with proper dimensions
      // This function kept for compatibility but does minimal work
      const bg = document.getElementById('legend-bg');
      if (!bg) return;

      // Check if already has proper dimensions
      const currentWidth = parseFloat(bg.getAttribute('width'));
      if (currentWidth > 0) return; // Already properly sized, skip adjustment
  }

  // Auto-size MOA legend box (kept for fine-tuning, but legend is pre-rendered)
  function adjustLegendBoxMOA() {
    // MOA legend box is pre-rendered with proper dimensions
    // This function kept for compatibility but does minimal work
    const bg = document.getElementById('legend-moa-bg');
    if (!bg) return;

    // Check if already has proper dimensions
    const currentWidth = parseFloat(bg.getAttribute('width'));
    if (currentWidth > 0) return; // Already properly sized, skip adjustment
  }

  // Minimal tooltip-only JS
  function hexToRgb(hex) {
    if (!hex) return null;
    let clean = hex.replace('#', '');
    if (clean.length === 3) {
      clean = clean.split('').map(ch => ch + ch).join('');
    }
    if (clean.length !== 6) return null;
    const num = parseInt(clean, 16);
    return {
      r: (num >> 16) & 255,
      g: (num >> 8) & 255,
      b: num & 255
    };
  }

  function colorChannels(color) {
    if (!color) return null;
    const trimmed = color.trim().toLowerCase();
    if (trimmed.startsWith('#')) {
      return hexToRgb(trimmed);
    }
    const match = trimmed.match(/rgba?\(([^)]+)\)/);
    if (match) {
      const parts = match[1].split(',').map(v => parseFloat(v.trim()));
      return {
        r: parts[0] ?? 0,
        g: parts[1] ?? 0,
        b: parts[2] ?? 0
      };
    }
    return null;
  }

  function toRgba(color, alpha = 1) {
    const rgb = colorChannels(color);
    if (!rgb) return `rgba(255,255,255,${alpha})`;
    return `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`;
  }

  function isLightColor(color) {
    const rgb = colorChannels(color);
    if (!rgb) return false;
    const luminance = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
    return luminance >= 185;
  }

  function ensureTooltip() {
    let tip = document.getElementById('tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'tooltip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  function showTooltip(html, x, y, accentColor) {
    const tip = ensureTooltip();
    tip.innerHTML = html;

    // Set color properties
    if (accentColor) {
      tip.style.setProperty('--tip-color-rgba', toRgba(accentColor, 0.45));
    } else {
      tip.style.setProperty('--tip-color-rgba', 'rgba(255,255,255,0.25)');
    }
    const dividerColor =
      accentColor && isLightColor(accentColor)
        ? '#000'
        : 'rgba(255,255,255,0.35)';
    tip.style.setProperty('--tip-divider-color', dividerColor);

    // Temporarily show to measure dimensions
    tip.style.visibility = 'hidden';
    tip.style.display = 'flex';

    const tipWidth = tip.offsetWidth;
    const tipHeight = tip.offsetHeight;
    const margin = 10; // margin from viewport edges
    const cursorOffset = 5; // offset from cursor

    // Get viewport dimensions
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // Calculate initial position (centered above cursor)
    let finalX = x - tipWidth / 2;
    let finalY = y - tipHeight - cursorOffset;

    // Check if tooltip fits above cursor
    const fitsAbove = finalY >= margin;

    if (fitsAbove) {
      // Tooltip fits above - adjust horizontal position to stay within viewport
      if (finalX < margin) {
        finalX = margin;
      } else if (finalX + tipWidth > viewportWidth - margin) {
        finalX = viewportWidth - tipWidth - margin;
      }
    } else {
      // Doesn't fit above - show to the right of cursor (avoid below due to
      // iframe clipping issues)
      finalX = x + cursorOffset + 15;
      finalY = y - tipHeight / 2; // vertically centered on cursor

      // Clamp vertical position
      if (finalY < margin) {
        finalY = margin;
      } else if (finalY + tipHeight > viewportHeight - margin) {
        finalY = viewportHeight - tipHeight - margin;
      }

      // If right side doesn't fit, try left side
      if (finalX + tipWidth > viewportWidth - margin) {
        finalX = x - tipWidth - cursorOffset - 15;
        if (finalX < margin) {
          finalX = margin;
        }
      }
    }

    tip.style.left = finalX + 'px';
    tip.style.top = finalY + 'px';
    tip.style.visibility = '';

    tip.classList.remove('show');
    void tip.offsetWidth;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        tip.classList.add('show');
      });
    });
  }
  function hideTooltip() {
    const tip = document.getElementById('tooltip');
    if (!tip) return;
    tip.classList.remove('show');
  }

  function updateTooltipPosition(x, y) {
    const tip = document.getElementById('tooltip');
    if (!tip) return;

    const tipWidth = tip.offsetWidth;
    const tipHeight = tip.offsetHeight;
    const margin = 10;
    const cursorOffset = 5;

    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // Calculate initial position (centered above cursor)
    let finalX = x - tipWidth / 2;
    let finalY = y - tipHeight - cursorOffset;

    // Check if tooltip fits above cursor
    const fitsAbove = finalY >= margin;

    if (fitsAbove) {
      // Tooltip fits above - adjust horizontal position to stay within viewport
      if (finalX < margin) {
        finalX = margin;
      } else if (finalX + tipWidth > viewportWidth - margin) {
        finalX = viewportWidth - tipWidth - margin;
      }
    } else {
      // Doesn't fit above - show to the right of cursor (avoid below due to
      // iframe clipping issues)
      finalX = x + cursorOffset + 15;
      finalY = y - tipHeight / 2; // vertically centered on cursor

      // Clamp vertical position
      if (finalY < margin) {
        finalY = margin;
      } else if (finalY + tipHeight > viewportHeight - margin) {
        finalY = viewportHeight - tipHeight - margin;
      }

      // If right side doesn't fit, try left side
      if (finalX + tipWidth > viewportWidth - margin) {
        finalX = x - tipWidth - cursorOffset - 15;
        if (finalX < margin) {
          finalX = margin;
        }
      }
    }

    tip.style.left = finalX + 'px';
    tip.style.top = finalY + 'px';
  }

  function initTooltipHandlers() {
    const nodes = document.querySelectorAll('.drug');

    // Add click handlers to drug labels (text elements)
    nodes.forEach(node => {
      const textEl = node.querySelector('text');
      if (textEl) {
        textEl.addEventListener('click', (ev) => {
          ev.stopPropagation();
          // Trigger the same click handler as the marker
          node.dispatchEvent(new MouseEvent('click', {
            bubbles: false,
            cancelable: true,
            view: window
          }));
        });
      }
    });

    // Wedge tooltips
    const wedges = document.querySelectorAll('.wedge');
    wedges.forEach(w => {
      w.addEventListener('mouseover', (ev) => {
        const pop = w.getAttribute('data-pop') || '–';
        const phase = w.getAttribute('data-phase') || '–';

        // Read exact wedge fill & opacity from the SVG element
        const fill = w.getAttribute('fill') || '#000';
        const op = parseFloat(w.getAttribute('fill-opacity') || '1');

        // Use wedge opacity instead of a fixed tooltipOpacity
        let bg = fill;
        if (fill.startsWith('#') && (fill.length === 7 || fill.length === 4)) {
          const hex = fill.length === 7 ? fill.slice(1) : (
            fill[1] + fill[1] + fill[2] + fill[2] + fill[3] + fill[3]
          );
          const r = parseInt(hex.slice(0,2),16);
          const g = parseInt(hex.slice(2,4),16);
          const b = parseInt(hex.slice(4,6),16);
          bg = `rgba(${r},${g},${b},1)`;
        }

        const html = `
  <div style="padding:2px 4px;">
    <div style="font-size:14px; font-weight:600; margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid var(--tip-divider-color, #ddd);">
      ${pop}
    </div>
    <div style="font-size:12px; line-height:1.6;">
      <div><strong>Phase:</strong> ${phase}</div>
    </div>
  </div>`;
        const tip = ensureTooltip();
        tip.style.color = isLightColor(bg) ? "#000" : "#fff";

        showTooltip(html, ev.clientX, ev.clientY, bg);

      });
      w.addEventListener('mousemove', (ev) => {
        updateTooltipPosition(ev.clientX, ev.clientY);
      });
      w.addEventListener('mouseout', () => {
        hideTooltip();
      });
    });
    nodes.forEach(node => {
      node.addEventListener('click', (ev) => {
        const name    = node.getAttribute('data-drug')    || 'Unknown';
        const phase   = node.getAttribute('data-phase')    || '–';
        const pop     = node.getAttribute('data-pop')      || '–';
        const mech    = node.getAttribute('data-mech')     || '–';
        const gtarget = node.getAttribute('data-gtarget')  || '–';
        const gevid   = node.getAttribute('data-ge')       || '–';
        const tname   = node.getAttribute('data-tname')    || '–';
        const regid   = node.getAttribute('data-regid')    || '–';
        const svdpopd = node.getAttribute('data-svdpopd')  || '–';
        const ssize   = node.getAttribute('data-ssize')    || '–';
        const estcomp = node.getAttribute('data-estcomp')  || '–';
        const pco     = node.getAttribute('data-pco')      || '–';
        const sptype  = node.getAttribute('data-sptype')   || '–';

        const html = `
          <h2 style="margin-top:0; font-size:16px;">${name}</h2>
          <hr>
          <p><strong>Mechanism of Action:</strong> ${mech}</p>
          <p><strong>Genetic Target:</strong> ${gtarget}</p>
          <p><strong>Genetic Evidence:</strong> ${gevid}</p>
          <p><strong>Clinical Trial Name:</strong> ${tname}</p>
          <p><strong>Registry ID:</strong> ${regid}</p>
          <p><strong>Clinical Trial Phase:</strong> ${phase}</p>
          <p><strong>SVD Population Details:</strong> ${svdpopd}</p>
          <p><strong>Target Sample Size:</strong> ${ssize}</p>
          <p><strong>Estimated Completion Date:</strong> ${estcomp}</p>
          <p><strong>Primary Outcome:</strong> ${pco}</p>
          <p><strong>Sponsor Type:</strong> ${sptype}</p>
        `;

        const sb = document.getElementById('sidebar');
        const sbc = document.getElementById('sidebar-content');
        sbc.innerHTML = html;
        sb.classList.add('open');

        // Wait for sidebar to render and adjust plot margin
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            const sidebarWidth = sb.offsetWidth;
            const figC = document.querySelector('.fig-c');
            if (figC) {
              figC.style.marginLeft = sidebarWidth + 'px';
              figC.style.transition = 'margin-left 0.3s ease';
            }
          });
        });
      });
      node.addEventListener('mouseover', (ev) => {
        const name    = node.getAttribute('data-drug')    || 'Unknown';
        const phase   = node.getAttribute('data-phase')    || '–';
        const pop     = node.getAttribute('data-pop')      || '–';
        const mech    = node.getAttribute('data-mech')     || '–';
        const gtarget = node.getAttribute('data-gtarget')  || '–';
        const gevid   = node.getAttribute('data-ge')       || '–';
        const tname   = node.getAttribute('data-tname')    || '–';
        const regid   = node.getAttribute('data-regid')    || '–';
        const svdpopd = node.getAttribute('data-svdpopd')  || '–';
        const ssize   = node.getAttribute('data-ssize')    || '–';
        const estcomp = node.getAttribute('data-estcomp')  || '–';
        const pco     = node.getAttribute('data-pco')      || '–';
        const sptype  = node.getAttribute('data-sptype')   || '–';

        const html = `
  <div style="padding:2px 4px;">
    <div style="font-size:14px; font-weight:600; margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid var(--tip-divider-color, #ddd);">
      ${name}
    </div>
    <div style="font-size:12px; line-height:1.6;">
      <div><strong>Mechanism of Action:</strong> ${mech}</div>
      <div><strong>Genetic Target:</strong> ${gtarget}</div>
      <div><strong>Genetic Evidence:</strong> ${gevid}</div>
      <div><strong>Clinical Trial Name:</strong> ${tname}</div>
      <div><strong>Registry ID:</strong> ${regid}</div>
      <div><strong>Clinical Trial Phase:</strong> ${phase}</div>
      <div><strong>SVD Population Details:</strong> ${svdpopd}</div>
      <div><strong>Target Sample Size:</strong> ${ssize}</div>
      <div><strong>Estimated Completion Date:</strong> ${estcomp}</div>
      <div><strong>Primary Outcome:</strong> ${pco}</div>
      <div><strong>Sponsor Type:</strong> ${sptype}</div>
    </div>
  </div>`;
        const popColor = node.getAttribute('data-pop-color') || '#444444';
        const tip = ensureTooltip();
        tip.style.color = isLightColor(popColor) ? "#000" : "#fff";
        showTooltip(html, ev.clientX, ev.clientY, popColor);
      });
      node.addEventListener('mousemove', (ev) => {
        updateTooltipPosition(ev.clientX, ev.clientY);
      });
      node.addEventListener('mouseout', () => {
        hideTooltip();
      });
    });
  }

  async function renderWhenReady() {
    try {
      await waitForFonts();
      await nextFrame();
      adjustTwoLinePopLabels();
      adjustLabelBackgrounds();
      adjustMarkerLabelOverlap();
      adjustLegendBox();
      adjustLegendBoxMOA();
      if (typeof adjustDrugLabelCollisions === 'function') {
        adjustDrugLabelCollisions();
      }
      avoidCognitiveOverlap();
      await nextFrame();
      setTimeout(() => {
        adjustTwoLinePopLabels();
        adjustLabelBackgrounds();
        adjustMarkerLabelOverlap();
        adjustLegendBox();
        adjustLegendBoxMOA();
        if (typeof adjustDrugLabelCollisions === 'function') {
          adjustDrugLabelCollisions();
        }
        avoidCognitiveOverlap();
      }, 140);
      initTooltipHandlers();
    } finally {
      markReady();
    }
  }

  window.addEventListener('load', () => {
    renderWhenReady().catch(() => markReady());
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === "Escape") {
      const sb = document.getElementById('sidebar');
      const figC = document.querySelector('.fig-c');
      sb.classList.remove('open');
      if (figC) {
        figC.style.marginLeft = '0';
      }
    }
  });
})();
"""  # noqa: E501

with open("www/python_plot.js", "w", encoding="utf-8") as f_js:
    f_js.write(js_code)

print("Wrote python_plot.html and python_plot.js")
