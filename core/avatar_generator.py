"""
core/avatar_generator.py

Reddit-Style Dynamic Vector SVG Avatar Generator:
- Generates zero-cost, crisp SVG avatars without external image hosting.
- Deterministic seeding based on username or user ID.
- User-customizable: primary color, background color, eyes, antenna, and mouth expressions.
"""

import hashlib
from typing import Dict, Any, Optional

PALETTES = [
    {"bg": "#EEF2FF", "primary": "#6366F1", "screen": "#1E1B4B", "glow": "#38BDF8"},  # Indigo / Sky
    {"bg": "#FEF3C7", "primary": "#D97706", "screen": "#451A03", "glow": "#FBBF24"},  # Amber / Gold
    {"bg": "#FCE7F3", "primary": "#DB2777", "screen": "#500724", "glow": "#F472B6"},  # Pink / Rose
    {"bg": "#ECFDF5", "primary": "#059669", "screen": "#064E3B", "glow": "#34D399"},  # Emerald / Mint
    {"bg": "#F3E8FF", "primary": "#9333EA", "screen": "#3B0764", "glow": "#C084FC"},  # Purple / Violet
    {"bg": "#EFF6FF", "primary": "#2563EB", "screen": "#172554", "glow": "#60A5FA"},  # Blue / Azure
    {"bg": "#FFF1F2", "primary": "#E11D48", "screen": "#4C0519", "glow": "#FB7185"},  # Crimson / Ruby
    {"bg": "#F0FDF4", "primary": "#16A34A", "screen": "#14532D", "glow": "#4ADE80"},  # Green / Lime
]


def _hash_seed(seed: str) -> int:
    h = hashlib.md5((seed or "peculiar").encode("utf-8")).hexdigest()
    return int(h, 16)


def generate_avatar_svg(username: str, config: Optional[Dict[str, Any]] = None) -> str:
    """
    Renders an SVG string for Reddit-style avatar customization.
    """
    cfg = config or {}
    val = _hash_seed(username)
    palette = PALETTES[val % len(PALETTES)]

    bg_color = cfg.get("bg_color") or palette["bg"]
    primary_color = cfg.get("primary_color") or palette["primary"]
    screen_color = cfg.get("screen_color") or palette["screen"]
    glow_color = cfg.get("glow_color") or palette["glow"]

    eye_variant = cfg.get("eye_style") or (["round", "glow", "wink"][val % 3])
    antenna_variant = cfg.get("antenna_style") or (["round", "star", "ring"][val % 3])

    # Antenna element
    if antenna_variant == "star":
        antenna_svg = f"""
        <line x1="60" y1="26" x2="60" y2="38" stroke="{primary_color}" stroke-width="4" stroke-linecap="round" />
        <polygon points="60,14 63,22 71,22 65,27 67,35 60,30 53,35 55,27 49,22 57,22" fill="{primary_color}" />
        """
    elif antenna_variant == "ring":
        antenna_svg = f"""
        <line x1="60" y1="28" x2="60" y2="38" stroke="{primary_color}" stroke-width="4" stroke-linecap="round" />
        <circle cx="60" cy="20" r="8" fill="none" stroke="{primary_color}" stroke-width="4" />
        """
    else:  # round
        antenna_svg = f"""
        <line x1="60" y1="26" x2="60" y2="38" stroke="{primary_color}" stroke-width="4" stroke-linecap="round" />
        <circle cx="60" cy="20" r="7" fill="{primary_color}" />
        """

    # Eyes element
    if eye_variant == "wink":
        eyes_svg = f"""
        <circle cx="48" cy="64" r="5" fill="{glow_color}" />
        <path d="M 68 64 Q 73 59 78 64" stroke="{glow_color}" stroke-width="3" fill="none" stroke-linecap="round" />
        """
    elif eye_variant == "round":
        eyes_svg = f"""
        <circle cx="48" cy="64" r="5.5" fill="{glow_color}" />
        <circle cx="72" cy="64" r="5.5" fill="{glow_color}" />
        """
    else:  # glow
        eyes_svg = f"""
        <rect x="44" y="60" width="9" height="9" rx="3" fill="{glow_color}" />
        <rect x="67" y="60" width="9" height="9" rx="3" fill="{glow_color}" />
        """

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120">
  <defs>
    <filter id="soft-shadow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="4" stdDeviation="3" flood-opacity="0.12" />
    </filter>
  </defs>

  <!-- Background Base -->
  <rect width="120" height="120" rx="28" fill="{bg_color}" />

  <!-- Antenna -->
  {antenna_svg}

  <!-- Ears / Headsets -->
  <rect x="22" y="54" width="8" height="22" rx="4" fill="{primary_color}" />
  <rect x="90" y="54" width="8" height="22" rx="4" fill="{primary_color}" />

  <!-- Main Head Body -->
  <rect x="28" y="38" width="64" height="54" rx="16" fill="{primary_color}" filter="url(#soft-shadow)" />

  <!-- Inner Visor / Screen -->
  <rect x="36" y="48" width="48" height="34" rx="10" fill="{screen_color}" />

  <!-- Eyes -->
  {eyes_svg}

  <!-- Cute Mouth Expression -->
  <rect x="52" y="74" width="16" height="3" rx="1.5" fill="{glow_color}" opacity="0.8" />
</svg>"""
    return svg.strip()

