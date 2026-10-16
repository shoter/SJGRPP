/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sector_cmd.h Command definitions related to sectors. */

#ifndef SECTOR_CMD_H
#define SECTOR_CMD_H

#include "command_type.h"
#include "economy_type.h"
#include "sector_type.h"
#include "tile_type.h"

#include <vector>

/** Payload of #CmdSectorPaintTiles: a list of tiles or the corners of a polygon to assign to a sector. */
struct SectorPaintTilesCmdData final : public CommandPayloadSerialisable<SectorPaintTilesCmdData> {
	static constexpr bool HasStringSanitiser = false;
	SectorID sector = SectorID::Invalid(); ///< The sector the tiles are assigned to.
	bool polygon = false;                  ///< Whether #tiles are the corners of a polygon whose inside is assigned, instead of the tiles themselves.
	std::vector<TileIndex> tiles;          ///< The tiles, or the corners of the polygon.

	void SerialisePayload(BufferSerialisationRef buffer) const;
	bool Deserialise(DeserialisationBuffer &buffer, StringValidationSettings default_string_validation);
	void FormatDebugSummary(format_target &output) const;
};

/** Maximum number of tiles or polygon corners in a #SectorPaintTilesCmdData. */
static constexpr size_t MAX_SECTOR_PAINT_TILES = 16384;

DEF_CMD_TUPLE_NT(Commands::BuySectorRights,     CmdBuySectorRights,     {}, CommandType::MoneyManagement,       CmdDataT<SectorID>)
DEF_CMD_TUPLE   (Commands::SectorPaint,         CmdSectorPaint,         {}, CommandType::LandscapeConstruction, CmdDataT<TileIndex, SectorID, bool>)
DEF_CMD_TUPLE   (Commands::SectorCreate,        CmdSectorCreate,        {}, CommandType::LandscapeConstruction, CmdDataT<>)
DEF_CMD_TUPLE_NT(Commands::SectorRename,        CmdSectorRename,        {}, CommandType::OtherManagement,       CmdDataT<SectorID, std::string>)
DEF_CMD_TUPLE_NT(Commands::SectorSetProperties, CmdSectorSetProperties, {}, CommandType::OtherManagement,       CmdDataT<SectorID, Money, bool>)
DEF_CMD_TUPLE_NT(Commands::SectorGenerate,      CmdSectorGenerate,      {}, CommandType::LandscapeConstruction, CmdDataT<uint32_t>)
DEF_CMD_TUPLE_NT(Commands::SectorMerge,         CmdSectorMerge,         {}, CommandType::LandscapeConstruction, CmdDataT<SectorID, SectorID>)
DEF_CMD_DIRECT_NT(Commands::SectorPaintTiles,    CmdSectorPaintTiles,    {}, CommandType::LandscapeConstruction, SectorPaintTilesCmdData)
DEF_CMD_TUPLE_NT(Commands::SectorSetLocked,     CmdSectorSetLocked,     {}, CommandType::OtherManagement,       CmdDataT<SectorID, bool>)
DEF_CMD_TUPLE_NT(Commands::SectorRemoveEmpty,   CmdSectorRemoveEmpty,   {}, CommandType::LandscapeConstruction, CmdDataT<>)

#endif /* SECTOR_CMD_H */
