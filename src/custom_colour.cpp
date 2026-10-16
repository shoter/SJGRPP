/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file custom_colour.cpp Implementation of custom (RGB) company colours.
 *
 * Custom colours are stored per livery next to the nearest preset colour. The preset colour is what NewGRFs and
 * all game logic see; the custom colour only changes the appearance. To draw with a custom colour, a recolour
 * table is generated at runtime: the preset recolour table is copied and its company colour ranges are replaced
 * by an eight shade ramp derived from the custom colour, quantised to the nearest palette entries.
 */

#include "stdafx.h"
#include "custom_colour.h"
#include "company_base.h"
#include "livery.h"
#include "spritecache.h"
#include "vehicle_func.h"
#include "window_func.h"
#include "gfx_func.h"
#include "table/sprites.h"

#include <map>

#include "safeguards.h"

/** Representative colour (the #Shade::Light shade in the DOS palette) of each preset colour. */
static constexpr EnumIndexArray<CustomColour, Colours, Colours::End> _preset_colour_rgb = {
	MakeCustomColour( 40,  92, 164), // DarkBlue
	MakeCustomColour( 96, 136, 108), // PaleGreen
	MakeCustomColour(204, 104, 124), // Pink
	MakeCustomColour(232, 184,  16), // Yellow
	MakeCustomColour(224,   0,   0), // Red
	MakeCustomColour( 80, 140, 160), // LightBlue
	MakeCustomColour( 92, 156,  52), // Green
	MakeCustomColour(104, 124,  76), // DarkGreen
	MakeCustomColour( 56, 144, 232), // Blue
	MakeCustomColour(204, 128,  96), // Cream
	MakeCustomColour(100, 100, 136), // Mauve
	MakeCustomColour(120,  88, 224), // Purple
	MakeCustomColour(252, 176,  48), // Orange
	MakeCustomColour(152, 132,  92), // Brown
	MakeCustomColour(148, 149, 148), // Grey
	MakeCustomColour(232, 232, 232), // White
};

/**
 * Get the representative RGB value of a preset colour.
 * @param colour The preset colour.
 * @return The RGB value as custom colour.
 */
CustomColour GetPresetColourRGB(Colours colour)
{
	return _preset_colour_rgb[colour < Colours::End ? colour : Colours::Begin];
}

/**
 * Perceptual distance between two custom colours.
 * Only integer arithmetic is used, as this is used by game logic and must be identical on all clients.
 * @param a First colour.
 * @param b Second colour.
 * @return Squared weighted distance.
 */
uint32_t GetCustomColourDistance(CustomColour a, CustomColour b)
{
	const int r1 = GetCustomColourRed(a), r2 = GetCustomColourRed(b);
	const int dr = r1 - r2;
	const int dg = GetCustomColourGreen(a) - GetCustomColourGreen(b);
	const int db = GetCustomColourBlue(a) - GetCustomColourBlue(b);
	const int avgr = (r1 + r2) / 2;
	/* "Redmean" approximation, scaled by 256. */
	return static_cast<uint32_t>((512 + avgr) * dr * dr + 1024 * dg * dg + (767 - avgr) * db * db);
}

/**
 * Find the preset colour which is closest to a custom colour.
 * This is deterministic and independent of the loaded graphics, so it may be used by game logic.
 * @param colour The custom colour.
 * @return The nearest preset colour.
 */
Colours GetNearestPresetColour(CustomColour colour)
{
	Colours best = Colours::Begin;
	uint32_t best_distance = UINT32_MAX;
	for (Colours c : EnumRange(Colours::End)) {
		uint32_t distance = GetCustomColourDistance(colour, _preset_colour_rgb[c]);
		if (distance < best_distance) {
			best = c;
			best_distance = distance;
		}
	}
	return best;
}

/**
 * Generate a saturated custom colour from a random seed.
 * Only integer arithmetic is used, as this is used by game logic and must be identical on all clients.
 * @param seed Random seed.
 * @return The generated colour.
 */
CustomColour GenerateDistinctCustomColour(uint32_t seed)
{
	const uint hue = GB(seed, 0, 16) % 360;
	const uint sat = 55 + GB(seed, 16, 8) % 46;  // 55..100 %
	const uint val = 60 + GB(seed, 24, 8) % 41;  // 60..100 %

	const uint c = val * sat * 255 / 10000;       // chroma, 0..255
	const uint h = hue % 120;
	const uint x = c * (60 - (h > 60 ? h - 60 : 60 - h)) / 60;
	const uint m = val * 255 / 100 - c;

	uint r = 0, g = 0, b = 0;
	switch (hue / 60) {
		case 0: r = c; g = x; break;
		case 1: r = x; g = c; break;
		case 2: g = c; b = x; break;
		case 3: g = x; b = c; break;
		case 4: r = x; b = c; break;
		default: r = c; b = x; break;
	}
	return MakeCustomColour(r + m, g + m, b + m);
}

/** Palette indices of the primary company colour range. */
static constexpr uint8_t PRIMARY_CC_START = 0xC6;
/** Palette indices of the secondary company colour range. */
static constexpr uint8_t SECONDARY_CC_START = 0x50;

/** Cache of shade ramps of custom colours. */
static std::map<CustomColour, std::array<uint8_t, to_underlying(Shade::End)>> _custom_colour_ramps;

/** Cache of generated palettes, keyed by base palette and custom colours. */
static std::map<std::tuple<PaletteID, CustomColour, CustomColour>, PaletteID> _custom_colour_palettes;

/** First sprite ID used for generated palettes, or 0 if not determined yet. */
static SpriteID _custom_colour_palette_base = 0;

/** Maximum number of generated palettes before the cache is flushed. */
static constexpr uint MAX_CUSTOM_COLOUR_PALETTES = 4096;

/** Maximum number of cached shade ramps before the cache is flushed. */
static constexpr uint MAX_CUSTOM_COLOUR_RAMPS = 4096;

/**
 * Get the eight shade palette ramp of a custom colour.
 * The custom colour is used as the #Shade::Light shade, darker shades are scaled towards black and lighter shades towards white.
 * @param colour The custom colour.
 * @return The palette indices of each shade.
 */
static std::array<uint8_t, to_underlying(Shade::End)> GetCustomColourRamp(CustomColour colour)
{
	auto it = _custom_colour_ramps.find(colour);
	if (it != _custom_colour_ramps.end()) return it->second;

	/* Don't let the cache grow without bounds, e.g. while dragging the sliders of the colour picker. */
	if (_custom_colour_ramps.size() >= MAX_CUSTOM_COLOUR_RAMPS) _custom_colour_ramps.clear();

	/* Scale factors (in 1/256) of the darker shades and the lighter shades, followed by how far the lighter shades are mixed towards white (in 1/256). */
	static constexpr std::array<uint, to_underlying(Shade::End)> scale = { 100, 140, 180, 218, 256, 320, 400, 460 };
	static constexpr std::array<uint, to_underlying(Shade::End)> whiten = { 0, 0, 0, 0, 0, 20, 50, 100 };

	std::array<uint8_t, to_underlying(Shade::End)> ramp{};
	const uint rgb[3] = { GetCustomColourRed(colour), GetCustomColourGreen(colour), GetCustomColourBlue(colour) };
	for (uint shade = 0; shade < ramp.size(); shade++) {
		uint out[3];
		for (uint i = 0; i < 3; i++) {
			uint v = std::min<uint>(255, rgb[i] * scale[shade] / 256);
			v += (255 - v) * whiten[shade] / 256;
			out[i] = v;
		}
		ramp[shade] = GetNearestColourIndex(out[0], out[1], out[2]);
	}

	_custom_colour_ramps.emplace(colour, ramp);
	return ramp;
}

/**
 * Get the palette colour of a shade of a custom colour, for drawing in the user interface.
 * @param colour The custom colour.
 * @param shade The shade.
 * @return Palette colour.
 */
PixelColour GetCustomColourGradient(CustomColour colour, Shade shade)
{
	return PixelColour{GetCustomColourRamp(colour)[to_underlying(shade) % to_underlying(Shade::End)]};
}

/**
 * Forget all generated palettes, e.g. because the sprite cache was reset.
 */
void ResetCustomColourPalettes()
{
	_custom_colour_ramps.clear();
	_custom_colour_palettes.clear();
	_custom_colour_palette_base = 0;
}

/**
 * Get a palette which applies custom colours on top of a base recolour palette.
 * @param base The base palette, i.e. the recolour palette of the preset colours.
 * @param primary Custom primary colour, or #CUSTOM_COLOUR_NONE.
 * @param secondary Custom secondary colour, or #CUSTOM_COLOUR_NONE.
 * @return The palette to draw with.
 */
PaletteID GetCustomColourPalette(PaletteID base, CustomColour primary, CustomColour secondary)
{
	if (!IsCustomColour(primary) && !IsCustomColour(secondary)) return base;
	if (base == PAL_NONE) return base;

	auto key = std::make_tuple(base, primary, secondary);
	auto it = _custom_colour_palettes.find(key);
	if (it != _custom_colour_palettes.end()) return it->second;

	if (_custom_colour_palette_base == 0) _custom_colour_palette_base = GetMaxSpriteID();
	if (_custom_colour_palettes.size() >= MAX_CUSTOM_COLOUR_PALETTES) {
		/* Too many different palettes, start again. Cached vehicle palettes have to be recalculated. */
		_custom_colour_palettes.clear();
		ResetVehicleColourMap();
		MarkWholeScreenDirty();
	}

	const SpriteID id = _custom_colour_palette_base + static_cast<SpriteID>(_custom_colour_palettes.size());
	if (id >= MAX_PALETTES) return base;

	std::array<uint8_t, 256> remap;
	const uint8_t *src = GetNonSprite(GB(base, 0, PALETTE_WIDTH), SpriteType::Recolour);
	std::copy_n(src, remap.size(), remap.begin());

	if (IsCustomColour(primary)) {
		const auto ramp = GetCustomColourRamp(primary);
		std::copy(ramp.begin(), ramp.end(), remap.begin() + PRIMARY_CC_START);
	}
	if (IsCustomColour(secondary)) {
		const auto ramp = GetCustomColourRamp(secondary);
		std::copy(ramp.begin(), ramp.end(), remap.begin() + SECONDARY_CC_START);
	}

	SetGeneratedRecolourSprite(id, remap);
	_custom_colour_palettes.emplace(key, id);
	return id;
}

/**
 * Get the palette to draw something with the colours of a livery.
 * @param livery The livery.
 * @param twocc Whether the palette is for a two company colour sprite.
 * @param base Base palette to which the recolour offset of the livery is added, or #PAL_NONE (0) for the default company colour palettes.
 * @return The palette to draw with.
 */
PaletteID GetLiveryPalette(const Livery &livery, bool twocc, PaletteID base)
{
	if (base == PAL_NONE) base = twocc ? static_cast<PaletteID>(SPR_2CCMAP_BASE) : static_cast<PaletteID>(PALETTE_RECOLOUR_START);
	PaletteID pal = base + livery.GetRecolourOffset(twocc);
	return GetCustomColourPalette(pal, livery.custom1, twocc ? livery.custom2 : CUSTOM_COLOUR_NONE);
}

/**
 * Get the palette colour of a shade of a livery colour, for drawing in the user interface.
 * @param livery The livery.
 * @param primary Whether to use the primary or secondary colour.
 * @param shade The shade.
 * @return Palette colour.
 */
PixelColour GetLiveryColourGradient(const Livery &livery, bool primary, Shade shade)
{
	CustomColour custom = primary ? livery.custom1 : livery.custom2;
	if (IsCustomColour(custom)) return GetCustomColourGradient(custom, shade);
	return GetColourGradient(primary ? livery.colour1 : livery.colour2, shade);
}

/**
 * Get the palette colour of a shade of the primary colour of a company, for drawing in the user interface.
 * @param company The company.
 * @param shade The shade.
 * @return Palette colour.
 */
PixelColour GetCompanyColourGradient(CompanyID company, Shade shade)
{
	const Company *c = Company::GetIfValid(company);
	if (c == nullptr) return GetColourGradient(Colours::White, shade);
	return GetLiveryColourGradient(c->livery[LiveryScheme::Default], true, shade);
}
