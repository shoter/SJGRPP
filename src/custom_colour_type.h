/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file custom_colour_type.h Types related to custom (RGB) company colours. */

#ifndef CUSTOM_COLOUR_TYPE_H
#define CUSTOM_COLOUR_TYPE_H

#include "core/bitmath_func.hpp"

/**
 * A custom RGB company colour.
 * Bits 0..23 hold the colour as 0xRRGGBB, bit 24 is set when the custom colour is in use.
 * A value of #CUSTOM_COLOUR_NONE means that the preset colour is used instead.
 */
using CustomColour = uint32_t;

static constexpr CustomColour CUSTOM_COLOUR_NONE = 0; ///< No custom colour, use the preset colour.
static constexpr uint8_t CUSTOM_COLOUR_VALID_BIT = 24; ///< Bit which is set when a custom colour is in use.
static constexpr CustomColour CUSTOM_COLOUR_MASK = (1U << (CUSTOM_COLOUR_VALID_BIT + 1)) - 1; ///< All bits which may be set in a #CustomColour.

/**
 * Make a custom colour from its components.
 * @param r Red component.
 * @param g Green component.
 * @param b Blue component.
 * @return The custom colour.
 */
constexpr CustomColour MakeCustomColour(uint8_t r, uint8_t g, uint8_t b)
{
	return (1U << CUSTOM_COLOUR_VALID_BIT) | (static_cast<uint32_t>(r) << 16) | (static_cast<uint32_t>(g) << 8) | b;
}

/**
 * Check whether a custom colour is in use.
 * @param c The custom colour.
 * @return True iff the custom colour is set.
 */
constexpr bool IsCustomColour(CustomColour c) { return HasBit(c, CUSTOM_COLOUR_VALID_BIT); }

/**
 * Check whether a value is a valid custom colour (or #CUSTOM_COLOUR_NONE).
 * @param c The value to check.
 * @return True iff the value is valid.
 */
constexpr bool IsValidCustomColour(CustomColour c) { return c == CUSTOM_COLOUR_NONE || (IsCustomColour(c) && (c & ~CUSTOM_COLOUR_MASK) == 0); }

constexpr uint8_t GetCustomColourRed(CustomColour c) { return GB(c, 16, 8); }   ///< Red component of a custom colour.
constexpr uint8_t GetCustomColourGreen(CustomColour c) { return GB(c, 8, 8); }  ///< Green component of a custom colour.
constexpr uint8_t GetCustomColourBlue(CustomColour c) { return GB(c, 0, 8); }   ///< Blue component of a custom colour.

#endif /* CUSTOM_COLOUR_TYPE_H */
