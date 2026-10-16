/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file debug_bridge_cmd.cpp Issuing game commands through the debug bridge, exactly like the GUI does. */

#include "stdafx.h"
#include "command_func.h"
#include "company_func.h"
#include "core/backup_type.hpp"
#include "core/serialisation.hpp"
#include "network/network.h"
#include "order_base.h"
#include "order_cmd.h"
#include "cargotype.h"
#include "engine_base.h"
#include "engine_func.h"
#include "rail_map.h"
#include "road_map.h"
#include "station_map.h"
#include "strings_func.h"
#include "tile_map.h"
#include "vehicle_base.h"
#include "water_map.h"

#include "table/strings.h"

#include "3rdparty/nlohmann/json.hpp"

#include <charconv>
#include <map>

#include "safeguards.h"

using json = nlohmann::json;

bool GetSimpleCommandPayloadFieldTypes(Commands cmd, std::vector<std::string_view> &types);

/** Results of commands sent to the server, by callback parameter (sequence number), until collected. */
static std::map<uint32_t, json> _debug_bridge_cmd_results;

static json CommandResultToJson(Commands cmd, TileIndex tile, const CommandCost &res, bool executed)
{
	json j = json::object();
	j["command"] = std::string(GetCommandName(cmd));
	j["tile"] = tile.base();
	j["success"] = res.Succeeded();
	j["executed"] = executed;
	j["cost"] = static_cast<int64_t>(res.GetCost());
	if (res.Failed()) j["error"] = res.SummaryMessage();
	if (res.HasAnyResultData()) j["result_data"] = res.GetUntypedResultData();
	/* Station building commands do not return the station: report the one now on the command tile. */
	if (executed && res.Succeeded() && tile < Map::Size() && IsTileType(tile, TileType::Station)) j["station"] = GetStationIndex(tile).base();
	return j;
}

/** Command callback: a command issued through the debug bridge came back from the server and was executed. */
void CcDebugBridge(const CommandCost &result, Commands cmd, TileIndex tile, const CommandPayloadBase &, CallbackParameter param)
{
	_debug_bridge_cmd_results[param] = CommandResultToJson(cmd, tile, result, true);
}

static std::string_view StripCmdPrefix(std::string_view name)
{
	if (name.size() > 3 && name.starts_with("Cmd")) name.remove_prefix(3);
	return name;
}

static bool EqualsIgnoreCase(std::string_view a, std::string_view b)
{
	return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) { return std::tolower(x) == std::tolower(y); });
}

static std::optional<Commands> FindCommand(std::string_view name)
{
	name = StripCmdPrefix(name);
	for (uint i = 0; i < to_underlying(Commands::End); i++) {
		Commands cmd = static_cast<Commands>(i);
		if (EqualsIgnoreCase(StripCmdPrefix(GetCommandName(cmd)), name)) return cmd;
	}
	return std::nullopt;
}

/** List the commands (optionally only those whose name contains filter) with their payload field types. */
json DebugBridgeCommandList(std::string_view filter)
{
	json list = json::array();
	std::vector<std::string_view> types;
	for (uint i = 0; i < to_underlying(Commands::End); i++) {
		Commands cmd = static_cast<Commands>(i);
		std::string_view name = StripCmdPrefix(GetCommandName(cmd));
		if (!filter.empty()) {
			auto it = std::search(name.begin(), name.end(), filter.begin(), filter.end(), [](char x, char y) { return std::tolower(x) == std::tolower(y); });
			if (it == name.end()) continue;
		}
		json entry = { { "name", std::string(name) }, { "id", i } };
		if (GetSimpleCommandPayloadFieldTypes(cmd, types)) {
			entry["fields"] = json::array();
			for (std::string_view t : types) entry["fields"].push_back(std::string(t));
		} else {
			entry["fields"] = nullptr; // Custom payload: only usable with docmd_hex.
		}
		list.push_back(std::move(entry));
	}
	return list;
}

static bool ParseInteger(std::string_view str, int64_t &value, uint64_t &uvalue, bool &is_negative)
{
	if (str == "true") str = "1";
	if (str == "false") str = "0";
	is_negative = str.starts_with('-');
	if (is_negative) str.remove_prefix(1);
	int base = 10;
	if (str.starts_with("0x") || str.starts_with("0X")) {
		str.remove_prefix(2);
		base = 16;
	}
	if (str.empty()) return false;
	auto [ptr, ec] = std::from_chars(str.data(), str.data() + str.size(), uvalue, base);
	if (ec != std::errc{} || ptr != str.data() + str.size()) return false;
	if (is_negative) {
		if (uvalue > static_cast<uint64_t>(INT64_MAX) + 1) return false;
		value = static_cast<int64_t>(0 - uvalue);
	} else {
		value = static_cast<int64_t>(uvalue);
	}
	return true;
}

/** Serialise one text argument as a payload field of the given wire type. Values may be given in the signed or unsigned range of the field width. */
static bool SerialiseField(BufferSerialisationRef &out, std::string_view type, const std::string &arg, std::string &error)
{
	if (type == "string" || type == "encoded_string") {
		out.Send_string(arg);
		return true;
	}

	int64_t value;
	uint64_t uvalue;
	bool negative;
	if (!ParseInteger(arg, value, uvalue, negative)) {
		error = fmt::format("invalid integer '{}' for field of type {}", arg, type);
		return false;
	}
	auto check_range = [&](int64_t min, uint64_t max) {
		if (negative ? value < min : uvalue > max) {
			error = fmt::format("value {} out of range for field of type {}", arg, type);
			return false;
		}
		return true;
	};

	if (type == "bool") {
		if (!check_range(0, 1)) return false;
		out.Send_generic(value != 0);
	} else if (type == "u8" || type == "i8") {
		if (!check_range(INT8_MIN, UINT8_MAX)) return false;
		out.Send_uint8(static_cast<uint8_t>(value));
	} else if (type == "u16" || type == "i16") {
		if (!check_range(INT16_MIN, UINT16_MAX)) return false;
		out.Send_uint16(static_cast<uint16_t>(value));
	} else if (type == "u32") {
		if (!check_range(INT32_MIN, UINT32_MAX)) return false;
		out.Send_generic(static_cast<uint32_t>(value));
	} else if (type == "i32") {
		if (!check_range(INT32_MIN, UINT32_MAX)) return false;
		out.Send_generic(static_cast<int32_t>(value));
	} else if (type == "u64") {
		out.Send_generic(negative ? static_cast<uint64_t>(value) : uvalue);
	} else if (type == "i64") {
		if (!negative && uvalue > static_cast<uint64_t>(INT64_MAX)) {
			error = fmt::format("value {} out of range for field of type {}", arg, type);
			return false;
		}
		out.Send_generic(value);
	} else {
		error = fmt::format("unsupported field type {}", type);
		return false;
	}
	return true;
}

/** Build a command container through the same deserialisation path as commands received over the network. */
template <typename F>
static bool BuildContainer(Commands cmd, TileIndex tile, F write_payload, DynBaseCommandContainer &out, std::string &error)
{
	std::vector<uint8_t> buffer;
	BufferSerialisationRef serialiser(buffer, SHRT_MAX);
	serialiser.Send_uint16(to_underlying(cmd));
	serialiser.Send_uint16(0); // error_msg
	serialiser.Send_uint32(tile.base());
	size_t size_pos = buffer.size();
	serialiser.Send_uint16(0);
	if (!write_payload(serialiser)) return false;
	serialiser.SendAtOffset_uint16(size_pos, static_cast<uint16_t>(buffer.size() - size_pos - 2));

	DeserialisationBuffer deserialiser(buffer.data(), buffer.size());
	const char *err = out.Deserialise(deserialiser);
	if (err == nullptr && deserialiser.error) err = "payload too short for this command";
	if (err == nullptr && deserialiser.pos != buffer.size()) err = "payload too long for this command";
	if (err != nullptr) {
		error = err;
		return false;
	}
	return true;
}

static bool ParseCommandAndTile(const std::vector<std::string> &args, size_t first, Commands &cmd, TileIndex &tile, std::string &error)
{
	if (args.size() < first + 2) {
		error = "usage: <command name> <tile> [fields...]";
		return false;
	}
	std::optional<Commands> found = FindCommand(args[first]);
	if (!found.has_value()) {
		error = fmt::format("unknown command '{}', see 'cmds'", args[first]);
		return false;
	}
	cmd = *found;
	int64_t value;
	uint64_t uvalue;
	bool negative;
	if (!ParseInteger(args[first + 1], value, uvalue, negative) || negative || uvalue > UINT32_MAX) {
		error = fmt::format("invalid tile '{}'", args[first + 1]);
		return false;
	}
	tile = TileIndex(static_cast<uint32_t>(uvalue));
	return true;
}

/** docmd <name> <tile> <field>...: the payload is given as one text argument per field (simple tuple payloads only). */
bool DebugBridgeBuildCommand(const std::vector<std::string> &args, size_t first, DynBaseCommandContainer &out, std::string &error)
{
	Commands cmd;
	TileIndex tile;
	if (!ParseCommandAndTile(args, first, cmd, tile, error)) return false;

	std::vector<std::string_view> types;
	if (!GetSimpleCommandPayloadFieldTypes(cmd, types)) {
		error = fmt::format("{} has a custom payload, use docmd_hex", GetCommandName(cmd));
		return false;
	}
	size_t given = args.size() - first - 2;
	if (given != types.size()) {
		std::string expected;
		for (std::string_view t : types) expected += fmt::format(" {}", t);
		error = fmt::format("{} expects {} fields:{}, got {}", GetCommandName(cmd), types.size(), expected, given);
		return false;
	}
	return BuildContainer(cmd, tile, [&](BufferSerialisationRef &s) {
		for (size_t i = 0; i < types.size(); i++) {
			if (!SerialiseField(s, types[i], args[first + 2 + i], error)) return false;
		}
		return true;
	}, out, error);
}

/** docmd_hex <name> <tile> <hex>: the payload is given in its network serialisation. */
bool DebugBridgeBuildRawCommand(const std::vector<std::string> &args, size_t first, DynBaseCommandContainer &out, std::string &error)
{
	Commands cmd;
	TileIndex tile;
	if (!ParseCommandAndTile(args, first, cmd, tile, error)) return false;
	std::string hex = args.size() > first + 2 ? args[first + 2] : std::string{};
	if (hex.size() % 2 != 0) {
		error = "hex payload must have an even number of digits";
		return false;
	}
	return BuildContainer(cmd, tile, [&](BufferSerialisationRef &s) {
		for (size_t i = 0; i < hex.size(); i += 2) {
			uint8_t byte;
			auto [ptr, ec] = std::from_chars(hex.data() + i, hex.data() + i + 2, byte, 16);
			if (ec != std::errc{} || ptr != hex.data() + i + 2) {
				error = "invalid hex payload";
				return false;
			}
			s.Send_uint8(byte);
		}
		return true;
	}, out, error);
}

/**
 * order <vehicle> station|waypoint|depot <dest> [nonstop|stop_everywhere] [full_load|full_load_any|no_load] [unload|transfer|no_unload]
 * Appends an order, like clicking a destination in the orders window.
 */
bool DebugBridgeBuildOrder(const std::vector<std::string> &args, size_t first, DynBaseCommandContainer &out, std::string &error)
{
	if (args.size() < first + 3) {
		error = "usage: order <vehicle> station|waypoint|depot <dest> [nonstop|stop_everywhere] [full_load|full_load_any|no_load] [unload|transfer|no_unload]";
		return false;
	}
	int64_t veh_value, dest_value;
	uint64_t uvalue;
	bool negative;
	if (!ParseInteger(args[first], veh_value, uvalue, negative) || negative || !ParseInteger(args[first + 2], dest_value, uvalue, negative) || negative) {
		error = "invalid vehicle or destination id";
		return false;
	}
	const Vehicle *v = Vehicle::GetIfValid(static_cast<VehicleID>(veh_value));
	if (v == nullptr) {
		error = fmt::format("no vehicle {}", veh_value);
		return false;
	}

	const std::string &kind = args[first + 1];
	OrderNonStopFlags non_stop = v->IsGroundVehicle() ? ONSF_NO_STOP_AT_INTERMEDIATE_STATIONS : ONSF_STOP_EVERYWHERE;
	Order order;
	if (kind == "station") {
		order.MakeGoToStation(static_cast<StationID>(dest_value));
		order.SetStopLocation(OrderStopLocation::FarEnd);
	} else if (kind == "waypoint") {
		order.MakeGoToWaypoint(static_cast<StationID>(dest_value));
		if (v->IsGroundVehicle()) non_stop = ONSF_NO_STOP_AT_ANY_STATION;
	} else if (kind == "depot") {
		order.MakeGoToDepot(static_cast<DepotID>(dest_value), OrderDepotTypeFlags{OrderDepotTypeFlag::PartOfOrders}, non_stop);
	} else {
		error = fmt::format("unknown order kind '{}'", kind);
		return false;
	}

	for (size_t i = first + 3; i < args.size(); i++) {
		const std::string &opt = args[i];
		if (opt == "nonstop") {
			non_stop = kind == "waypoint" ? ONSF_NO_STOP_AT_ANY_STATION : ONSF_NO_STOP_AT_INTERMEDIATE_STATIONS;
		} else if (opt == "stop_everywhere") {
			non_stop = ONSF_STOP_EVERYWHERE;
		} else if (opt == "full_load") {
			order.SetLoadType(OrderLoadType::FullLoad);
		} else if (opt == "full_load_any") {
			order.SetLoadType(OrderLoadType::FullLoadAny);
		} else if (opt == "no_load") {
			order.SetLoadType(OrderLoadType::NoLoad);
		} else if (opt == "unload") {
			order.SetUnloadType(OrderUnloadType::Unload);
		} else if (opt == "transfer") {
			order.SetUnloadType(OrderUnloadType::Transfer);
		} else if (opt == "no_unload") {
			order.SetUnloadType(OrderUnloadType::NoUnload);
		} else {
			error = fmt::format("unknown order option '{}'", opt);
			return false;
		}
	}
	order.SetNonStopType(non_stop);

	InsertOrderCmdData payload(v->index, INVALID_VEH_ORDER_ID, order);
	out = DynBaseCommandContainer(Commands::InsertOrder, STR_NULL, v->tile, payload.Clone());
	return true;
}

/**
 * Issue a command for the local company, through the same network-safe path the GUI and scripts use.
 * @return The result if it is already known (single player, or the local test run failed),
 *         std::nullopt if the command was sent to the server: collect it with DebugBridgeTakeCommandResult.
 */
std::optional<json> DebugBridgeSubmitCommand(const DynBaseCommandContainer &container, uint32_t seq, CompanyID company)
{
	Backup<CompanyID> cur_company(_current_company, company, FILE_LINE);
	CommandCost res = DoCommandPScript(container.cmd, container.tile, *container.payload, CommandCallback::DebugBridge, seq, DCIF_NONE, false, false);
	cur_company.Restore();
	if (_networking && res.Succeeded()) return std::nullopt;
	return CommandResultToJson(container.cmd, container.tile, res, !_networking && res.Succeeded());
}

std::optional<json> DebugBridgeTakeCommandResult(uint32_t seq)
{
	auto it = _debug_bridge_cmd_results.find(seq);
	if (it == _debug_bridge_cmd_results.end()) return std::nullopt;
	json result = std::move(it->second);
	_debug_bridge_cmd_results.erase(it);
	return result;
}

void DebugBridgeForgetCommandResult(uint32_t seq)
{
	_debug_bridge_cmd_results.erase(seq);
}

/** Run only the test part of a command for the local company: nothing is executed or sent to the server. */
json DebugBridgeTestCommand(const DynBaseCommandContainer &container, CompanyID company)
{
	Backup<CompanyID> cur_company(_current_company, company, FILE_LINE);
	CommandCost res = DoCommandPScript(container.cmd, container.tile, *container.payload, CommandCallback::None, 0, DCIF_NONE, true, false);
	cur_company.Restore();
	return CommandResultToJson(container.cmd, container.tile, res, false);
}

/** Engines the local company can build, optionally of one vehicle type (rail, road, ship, aircraft). */
json DebugBridgeEngineList(std::string_view type)
{
	static const std::pair<std::string_view, VehicleType> types[] = {
		{ "rail", VehicleType::Train }, { "road", VehicleType::Road }, { "ship", VehicleType::Ship }, { "aircraft", VehicleType::Aircraft },
	};
	json list = json::array();
	for (const auto &[name, vt] : types) {
		if (!type.empty() && type != name) continue;
		for (const Engine *e : Engine::IterateType(vt)) {
			if (!IsEngineBuildable(e->index, vt, _local_company)) continue;
			json j = json::object();
			j["id"] = e->index.base();
			j["type"] = std::string(name);
			j["name"] = GetString(STR_ENGINE_NAME, PackEngineNameDParam(e->index, EngineNameContext::Generic));
			CargoType cargo = e->GetDefaultCargoType();
			j["cargo"] = cargo;
			const CargoSpec *cs = IsValidCargoType(cargo) ? CargoSpec::Get(cargo) : nullptr;
			j["cargo_label"] = cs != nullptr ? std::string(cs->label.begin(), cs->label.end()) : std::string{};
			j["capacity"] = e->GetDisplayDefaultCapacity();
			j["max_speed"] = e->GetDisplayMaxSpeed();
			j["cost"] = static_cast<int64_t>(e->GetCost());
			j["intro_year"] = CalTime::ConvertDateToYMD(e->intro_date).year.base();
			if (vt == VehicleType::Train) {
				const RailVehicleInfo &rvi = e->VehInfo<RailVehicleInfo>();
				j["wagon"] = rvi.railveh_type == RailVehicleType::Wagon;
				j["multihead"] = rvi.railveh_type == RailVehicleType::Multihead;
				j["railtypes"] = rvi.railtypes.base();
				j["power"] = e->GetPower();
			} else if (vt == VehicleType::Road) {
				j["roadtype"] = e->VehInfo<RoadVehicleInfo>().roadtype;
				j["power"] = e->GetPower();
			} else if (vt == VehicleType::Aircraft) {
				const AircraftVehicleInfo &avi = e->VehInfo<AircraftVehicleInfo>();
				j["helicopter"] = avi.subtype == AIR_HELI;
				j["fast"] = (avi.subtype & AIR_FAST) != 0;
			}
			list.push_back(std::move(j));
		}
	}
	return list;
}

/**
 * Per-tile data of a map area, as parallel row-major arrays: type (TileType), height (lowest corner), slope (Slope bits),
 * owner (Owner, 255 where the tile type has none), sub (water/station/road/rail tile subtype), bits (road bits / track bits)
 * and road_owner (owner of the road on road, crossing, road stop and road tunnel/bridge tiles, else 255).
 */
json DebugBridgeTiles(uint x0, uint y0, uint w, uint h)
{
	std::vector<uint8_t> type, height, slope, owner, sub, bits, road_owner;
	const size_t count = static_cast<size_t>(w) * h;
	for (auto *v : { &type, &height, &slope, &owner, &sub, &bits, &road_owner }) v->reserve(count);
	for (uint y = y0; y < y0 + h; y++) {
		for (uint x = x0; x < x0 + w; x++) {
			TileIndex t = TileXY(x, y);
			TileType tt = GetTileType(t);
			auto [s, z] = GetTileSlopeZ(t);
			type.push_back(to_underlying(tt));
			height.push_back(static_cast<uint8_t>(z));
			slope.push_back(static_cast<uint8_t>(s));
			bool has_owner = tt != TileType::House && tt != TileType::Industry && tt != TileType::Void;
			owner.push_back(has_owner ? static_cast<uint8_t>(GetTileOwner(t).base()) : 255);
			uint8_t sub_value = 0, bits_value = 0;
			switch (tt) {
				case TileType::Water: sub_value = to_underlying(GetWaterTileType(t)); break;
				case TileType::Station: sub_value = to_underlying(GetStationType(t)); break;
				case TileType::Road:
					sub_value = to_underlying(GetRoadTileType(t));
					if (IsNormalRoad(t)) bits_value = GetRoadBits(t, RoadTramType::Road).base();
					break;
				case TileType::Railway:
					sub_value = to_underlying(GetRailTileType(t));
					if (!IsRailDepot(t)) bits_value = static_cast<uint8_t>(GetTrackBits(t));
					break;
				default: break;
			}
			sub.push_back(sub_value);
			bits.push_back(bits_value);
			road_owner.push_back(MayHaveRoad(t) && HasTileRoadType(t, RoadTramType::Road) ? static_cast<uint8_t>(GetRoadOwner(t, RoadTramType::Road).base()) : 255);
		}
	}
	return { { "x", x0 }, { "y", y0 }, { "w", w }, { "h", h }, { "type", type }, { "height", height }, { "slope", slope },
			{ "owner", owner }, { "sub", sub }, { "bits", bits }, { "road_owner", road_owner } };
}
