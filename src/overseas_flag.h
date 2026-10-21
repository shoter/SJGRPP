/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file overseas_flag.h Drawing the flags of the countries of overseas stations. */

#ifndef OVERSEAS_FLAG_H
#define OVERSEAS_FLAG_H

#include <string_view>
#include <vector>

static constexpr uint OVERSEAS_FLAG_WIDTH = 120; ///< Width of the canvas flags are described on.
static constexpr uint OVERSEAS_FLAG_HEIGHT = 80; ///< Height of the canvas flags are described on.

bool IsValidOverseasFlag(std::string_view spec);
bool RenderOverseasFlag(std::string_view spec, uint width, uint height, std::vector<uint32_t> &pixels);

#endif /* OVERSEAS_FLAG_H */
