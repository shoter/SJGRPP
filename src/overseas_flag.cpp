/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/**
 * @file overseas_flag.cpp Drawing the flags of the countries of overseas stations.
 *
 * A flag is described by drawing instructions on a 120x80 canvas, separated by ';'. Later shapes are drawn over
 * earlier ones; parts of the canvas no shape covers stay transparent. Colours are RRGGBB hex values.
 *  - H c c ...                  horizontal stripes, top to bottom; a colour can be weighted as w:RRGGBB
 *  - V c c ...                  vertical stripes, left to right; weighted like H
 *  - R x0 y0 x1 y1 c            rectangle
 *  - C cx cy r c                disc
 *  - CB cx cy r c               lower half of a disc
 *  - E cx cy rx ry c            ellipse
 *  - S cx cy r c [n]            star with n points (default 5), one point up
 *  - T x0 y0 x1 y1 x2 y2 c      triangle
 *  - Q x0 y0 ... x3 y3 c        quadrilateral
 *  - M cx cy r dx r2 c          crescent: the disc minus a disc of radius r2 moved dx to the right
 *  - L x0 y0 x1 y1 w c          line of width w
 *  - G x0 y0 x1 y1 nx ny r c    grid of nx * ny discs filling the rectangle
 */

#include "stdafx.h"
#include "overseas.h"
#include "overseas_flag.h"
#include "gfx_func.h"
#include "palette_func.h"
#include "spritecache.h"
#include "blitter/factory.hpp"
#include "spriteloader/spriteloader.hpp"

#include <charconv>
#include <cmath>
#include <map>

#include "safeguards.h"

namespace {

/** One shape of a flag. */
struct FlagShape {
	char kind;                 ///< 'R', 'C', 'B' (lower half disc), 'E', 'P' (polygon), 'M', 'L', 'G'.
	std::vector<float> values; ///< Coordinates and sizes of the shape; a polygon has x, y pairs.
	uint32_t colour;           ///< Colour as 0xRRGGBB.
};

std::vector<std::string_view> SplitFlagTokens(std::string_view text)
{
	std::vector<std::string_view> tokens;
	size_t pos = 0;
	while (pos < text.size()) {
		while (pos < text.size() && text[pos] == ' ') pos++;
		size_t end = pos;
		while (end < text.size() && text[end] != ' ') end++;
		if (end > pos) tokens.push_back(text.substr(pos, end - pos));
		pos = end;
	}
	return tokens;
}

bool ParseFlagColour(std::string_view token, uint32_t &colour)
{
	if (token.size() != 6) return false;
	auto res = std::from_chars(token.data(), token.data() + token.size(), colour, 16);
	return res.ec == std::errc{} && res.ptr == token.data() + token.size();
}

bool ParseFlagNumber(std::string_view token, float &value)
{
	auto res = std::from_chars(token.data(), token.data() + token.size(), value);
	return res.ec == std::errc{} && res.ptr == token.data() + token.size();
}

/** Parse weighted or plain stripe colours and turn them into rectangles. */
bool ParseFlagStripes(std::span<const std::string_view> args, bool horizontal, std::vector<FlagShape> &shapes)
{
	if (args.empty()) return false;
	std::vector<std::pair<float, uint32_t>> stripes;
	float total = 0;
	for (std::string_view arg : args) {
		float weight = 1;
		size_t colon = arg.find(':');
		if (colon != std::string_view::npos) {
			if (!ParseFlagNumber(arg.substr(0, colon), weight) || weight <= 0) return false;
			arg = arg.substr(colon + 1);
		}
		uint32_t colour;
		if (!ParseFlagColour(arg, colour)) return false;
		stripes.emplace_back(weight, colour);
		total += weight;
	}
	const float length = horizontal ? OVERSEAS_FLAG_HEIGHT : OVERSEAS_FLAG_WIDTH;
	float pos = 0;
	for (const auto &[weight, colour] : stripes) {
		float next = pos + weight * length / total;
		if (horizontal) {
			shapes.push_back({'R', {0, pos, static_cast<float>(OVERSEAS_FLAG_WIDTH), next}, colour});
		} else {
			shapes.push_back({'R', {pos, 0, next, static_cast<float>(OVERSEAS_FLAG_HEIGHT)}, colour});
		}
		pos = next;
	}
	return true;
}

/** Parse one drawing instruction. */
bool ParseFlagCommand(std::string_view command, std::vector<FlagShape> &shapes)
{
	std::vector<std::string_view> tokens = SplitFlagTokens(command);
	if (tokens.empty()) return true;
	std::string_view name = tokens[0];
	std::span<const std::string_view> args(tokens.data() + 1, tokens.size() - 1);

	if (name == "H" || name == "V") return ParseFlagStripes(args, name == "H", shapes);

	/* Every other instruction is a list of numbers followed by a colour, a star can also have a point count. */
	size_t numbers;
	char kind;
	if (name == "R") { numbers = 4; kind = 'R'; }
	else if (name == "C") { numbers = 3; kind = 'C'; }
	else if (name == "CB") { numbers = 3; kind = 'B'; }
	else if (name == "E") { numbers = 4; kind = 'E'; }
	else if (name == "S") { numbers = 3; kind = 'S'; }
	else if (name == "T") { numbers = 6; kind = 'P'; }
	else if (name == "Q") { numbers = 8; kind = 'P'; }
	else if (name == "M") { numbers = 5; kind = 'M'; }
	else if (name == "L") { numbers = 5; kind = 'L'; }
	else if (name == "G") { numbers = 7; kind = 'G'; }
	else return false;

	const size_t extra = (kind == 'S') ? 1 : 0;
	if (args.size() != numbers + 1 && args.size() != numbers + 1 + extra) return false;

	FlagShape shape{kind, {}, 0};
	for (size_t i = 0; i < numbers; i++) {
		float value;
		if (!ParseFlagNumber(args[i], value)) return false;
		shape.values.push_back(value);
	}
	if (!ParseFlagColour(args[numbers], shape.colour)) return false;

	if (kind == 'S') {
		float points = 5;
		if (args.size() == numbers + 2 && (!ParseFlagNumber(args[numbers + 1], points) || points < 3 || points > 32)) return false;
		/* Turn the star into a polygon, one point up. */
		const float cx = shape.values[0], cy = shape.values[1], r = shape.values[2];
		const int n = static_cast<int>(points);
		const float inner = r * (n == 5 ? 0.382f : 0.55f);
		shape.kind = 'P';
		shape.values.clear();
		for (int i = 0; i < n * 2; i++) {
			const float angle = static_cast<float>(-M_PI / 2 + i * M_PI / n);
			const float radius = (i % 2 == 0) ? r : inner;
			shape.values.push_back(cx + radius * std::cos(angle));
			shape.values.push_back(cy + radius * std::sin(angle));
		}
	}
	shapes.push_back(std::move(shape));
	return true;
}

bool ParseFlag(std::string_view spec, std::vector<FlagShape> &shapes)
{
	shapes.clear();
	size_t pos = 0;
	while (pos <= spec.size()) {
		size_t end = spec.find(';', pos);
		if (end == std::string_view::npos) end = spec.size();
		if (!ParseFlagCommand(spec.substr(pos, end - pos), shapes)) return false;
		pos = end + 1;
	}
	return !shapes.empty();
}

bool PolygonContains(const std::vector<float> &v, float x, float y)
{
	bool inside = false;
	const size_t n = v.size() / 2;
	for (size_t i = 0, j = n - 1; i < n; j = i++) {
		const float xi = v[i * 2], yi = v[i * 2 + 1], xj = v[j * 2], yj = v[j * 2 + 1];
		if ((yi > y) != (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
	}
	return inside;
}

bool ShapeContains(const FlagShape &s, float x, float y)
{
	const std::vector<float> &v = s.values;
	auto in_disc = [](float cx, float cy, float r, float x, float y) {
		return (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r * r;
	};
	switch (s.kind) {
		case 'R': return x >= v[0] && x < v[2] && y >= v[1] && y < v[3];
		case 'C': return in_disc(v[0], v[1], v[2], x, y);
		case 'B': return y >= v[1] && in_disc(v[0], v[1], v[2], x, y);
		case 'E': {
			const float dx = (x - v[0]) / v[2], dy = (y - v[1]) / v[3];
			return dx * dx + dy * dy <= 1;
		}
		case 'P': return PolygonContains(v, x, y);
		case 'M': return in_disc(v[0], v[1], v[2], x, y) && !in_disc(v[0] + v[3], v[1], v[4], x, y);
		case 'L': {
			const float dx = v[2] - v[0], dy = v[3] - v[1];
			const float len2 = dx * dx + dy * dy;
			if (len2 == 0) return false;
			const float t = std::clamp(((x - v[0]) * dx + (y - v[1]) * dy) / len2, 0.0f, 1.0f);
			const float px = v[0] + t * dx - x, py = v[1] + t * dy - y;
			return px * px + py * py <= v[4] * v[4] / 4;
		}
		case 'G': {
			const int nx = static_cast<int>(v[4]), ny = static_cast<int>(v[5]);
			if (nx <= 0 || ny <= 0) return false;
			const float cw = (v[2] - v[0]) / nx, ch = (v[3] - v[1]) / ny;
			const int ix = static_cast<int>(std::floor((x - v[0]) / cw)), iy = static_cast<int>(std::floor((y - v[1]) / ch));
			if (ix < 0 || ix >= nx || iy < 0 || iy >= ny) return false;
			return in_disc(v[0] + (ix + 0.5f) * cw, v[1] + (iy + 0.5f) * ch, v[6], x, y);
		}
	}
	return false;
}

} // namespace

bool IsValidOverseasFlag(std::string_view spec)
{
	std::vector<FlagShape> shapes;
	return ParseFlag(spec, shapes);
}

bool RenderOverseasFlag(std::string_view spec, uint width, uint height, std::vector<uint32_t> &pixels)
{
	std::vector<FlagShape> shapes;
	if (width == 0 || height == 0 || !ParseFlag(spec, shapes)) return false;

	/* Average SAMPLES x SAMPLES points per pixel for smooth edges. */
	constexpr int SAMPLES = 4;
	pixels.assign(static_cast<size_t>(width) * height, 0);
	for (uint py = 0; py < height; py++) {
		for (uint px = 0; px < width; px++) {
			uint32_t sum[4] = {};
			for (int sy = 0; sy < SAMPLES; sy++) {
				for (int sx = 0; sx < SAMPLES; sx++) {
					const float x = (px + (sx + 0.5f) / SAMPLES) * OVERSEAS_FLAG_WIDTH / width;
					const float y = (py + (sy + 0.5f) / SAMPLES) * OVERSEAS_FLAG_HEIGHT / height;
					for (auto it = shapes.rbegin(); it != shapes.rend(); ++it) {
						if (!ShapeContains(*it, x, y)) continue;
						sum[0] += GB(it->colour, 16, 8);
						sum[1] += GB(it->colour, 8, 8);
						sum[2] += GB(it->colour, 0, 8);
						sum[3] += 255;
						break;
					}
				}
			}
			constexpr uint32_t n = SAMPLES * SAMPLES;
			if (sum[3] == 0) continue;
			/* Colour is averaged over the covered samples only, the alpha over all of them. */
			const uint32_t covered = sum[3] / 255;
			pixels[static_cast<size_t>(py) * width + px] = (sum[3] / n) << 24 | (sum[0] / covered) << 16 | (sum[1] / covered) << 8 | (sum[2] / covered);
		}
	}
	return true;
}

/** Encoded flag sprites, keyed by country and size. A null pointer means the flag could not be drawn. */
static std::map<std::tuple<uint8_t, uint, uint>, std::unique_ptr<std::byte[]>> _flag_cache;
static const Blitter *_flag_cache_blitter = nullptr; ///< Blitter the cached sprites were encoded for.

void ClearOverseasFlagCache()
{
	_flag_cache.clear();
	_flag_cache_blitter = nullptr;
}

static std::unique_ptr<std::byte[]> EncodeFlagSprite(uint8_t country, uint width, uint height)
{
	std::vector<uint32_t> pixels;
	if (!RenderOverseasFlag(GetOverseasCountry(country).flag, width, height, pixels)) return nullptr;

	Blitter *blitter = BlitterFactory::GetCurrentBlitter();
	const bool palette_only = blitter->GetScreenDepth() == 8;

	SpriteLoader::SpriteCollection collection;
	SpriteLoader::Sprite &sprite = collection[ZoomLevel::Min];
	sprite.AllocateData(ZoomLevel::Min, static_cast<size_t>(width) * height);
	sprite.colours = palette_only ? SpriteComponents{SpriteComponent::Palette} : SpriteComponents{SpriteComponent::RGB, SpriteComponent::Alpha};
	sprite.width = width;
	sprite.height = height;
	sprite.x_offs = 0;
	sprite.y_offs = 0;
	for (size_t i = 0; i < pixels.size(); i++) {
		SpriteLoader::CommonPixel &px = sprite.data[i];
		px.r = GB(pixels[i], 16, 8);
		px.g = GB(pixels[i], 8, 8);
		px.b = GB(pixels[i], 0, 8);
		px.a = GB(pixels[i], 24, 8);
		px.m = 0;
		if (palette_only) px.m = px.a < 128 ? 0 : GetNearestColourIndex(px.r, px.g, px.b);
	}

	UniquePtrSpriteAllocator allocator;
	blitter->Encode(SpriteType::Font, collection, allocator);
	return std::move(allocator.data);
}

/**
 * Draw the flag of a country filling a rectangle.
 * @param country The country.
 * @param r The rectangle.
 * @return False if nothing was drawn.
 */
bool DrawOverseasFlag(uint8_t country, const Rect &r)
{
	if (country >= NUM_OVERSEAS_COUNTRIES || r.Width() <= 0 || r.Height() <= 0) return false;

	const Blitter *blitter = BlitterFactory::GetCurrentBlitter();
	if (blitter != _flag_cache_blitter) {
		_flag_cache.clear();
		_flag_cache_blitter = blitter;
	}

	auto key = std::make_tuple(country, static_cast<uint>(r.Width()), static_cast<uint>(r.Height()));
	auto it = _flag_cache.find(key);
	if (it == _flag_cache.end()) it = _flag_cache.emplace(key, EncodeFlagSprite(country, r.Width(), r.Height())).first;
	if (it->second == nullptr) return false;

	DrawEncodedSprite(reinterpret_cast<const Sprite *>(it->second.get()), r.left, r.top);
	return true;
}
