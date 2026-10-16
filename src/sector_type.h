/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_type.h Types related to sectors (building rights regions). */

#ifndef SECTOR_TYPE_H
#define SECTOR_TYPE_H

#include "core/pool_id_type.hpp"

/** The type of the IDs of sectors. 0xFF marks a tile which is not part of any sector. */
struct SectorIDTag : public PoolIDTraits<uint8_t, 0xFF, 0xFF> {};
using SectorID = PoolID<SectorIDTag>;

struct Sector;

static const uint MAX_LENGTH_SECTOR_NAME_CHARS = 64; ///< The maximum length of a sector name in characters including '\0'

#endif /* SECTOR_TYPE_H */
