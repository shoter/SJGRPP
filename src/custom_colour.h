/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file custom_colour.h Functions related to custom (RGB) company colours. */

#ifndef CUSTOM_COLOUR_H
#define CUSTOM_COLOUR_H

#include "custom_colour_type.h"
#include "company_type.h"
#include "gfx_type.h"
#include "palette_func.h"

struct Livery;

CustomColour GetPresetColourRGB(Colours colour);
Colours GetNearestPresetColour(CustomColour colour);
CustomColour GenerateDistinctCustomColour(uint32_t seed);
uint32_t GetCustomColourDistance(CustomColour a, CustomColour b);

PaletteID GetCustomColourPalette(PaletteID base, CustomColour primary, CustomColour secondary);
PixelColour GetCustomColourGradient(CustomColour colour, Shade shade);
void ResetCustomColourPalettes();

PaletteID GetLiveryPalette(const Livery &livery, bool twocc, PaletteID base = 0);
PixelColour GetLiveryColourGradient(const Livery &livery, bool primary, Shade shade);
PixelColour GetCompanyColourGradient(CompanyID company, Shade shade);

#endif /* CUSTOM_COLOUR_H */
