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

DEF_CMD_TUPLE_NT(Commands::BuySectorRights,     CmdBuySectorRights,     {}, CommandType::MoneyManagement,       CmdDataT<SectorID>)
DEF_CMD_TUPLE   (Commands::SectorPaint,         CmdSectorPaint,         {}, CommandType::LandscapeConstruction, CmdDataT<TileIndex, SectorID, bool>)
DEF_CMD_TUPLE   (Commands::SectorCreate,        CmdSectorCreate,        {}, CommandType::LandscapeConstruction, CmdDataT<>)
DEF_CMD_TUPLE_NT(Commands::SectorRename,        CmdSectorRename,        {}, CommandType::OtherManagement,       CmdDataT<SectorID, std::string>)
DEF_CMD_TUPLE_NT(Commands::SectorSetProperties, CmdSectorSetProperties, {}, CommandType::OtherManagement,       CmdDataT<SectorID, Money, bool>)
DEF_CMD_TUPLE_NT(Commands::SectorGenerate,      CmdSectorGenerate,      {}, CommandType::LandscapeConstruction, CmdDataT<uint32_t>)
DEF_CMD_TUPLE_NT(Commands::SectorMerge,         CmdSectorMerge,         {}, CommandType::LandscapeConstruction, CmdDataT<SectorID, SectorID>)

#endif /* SECTOR_CMD_H */
