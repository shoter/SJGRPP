/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file debug_bridge_gui.cpp Opening game windows by name through the debug bridge, to exercise GUI code in tests. */

#include "stdafx.h"
#include "company_base.h"
#include "company_func.h"
#include "company_gui.h"
#include "framerate_type.h"
#include "goal_gui.h"
#include "graph_gui.h"
#include "gui.h"
#include "league_gui.h"
#include "news_gui.h"
#include "plans_func.h"
#include "sector_func.h"
#include "smallmap_gui.h"
#include "station_base.h"
#include "station_func.h"
#include "timetable.h"
#include "town.h"
#include "vehicle_base.h"
#include "vehicle_gui.h"
#include "linkgraph/linkgraph_gui.h"
#include "network/network_gui.h"

#include <functional>
#include <map>

#include "safeguards.h"

void ShowDeparturesWindow(StationID station); // departures_gui.h is not self-contained

namespace {

/** Resolve the optional id argument: a company (default local), or the n-th valid pool item (default 0, wraps). */
template <typename T>
const T *NthItem(int64_t arg)
{
	size_t count = T::GetNumItems();
	if (count == 0) return nullptr;
	size_t n = static_cast<size_t>(arg < 0 ? 0 : arg) % count;
	for (const T *item : T::Iterate()) {
		if (n-- == 0) return item;
	}
	return nullptr;
}

CompanyID CompanyArg(int64_t arg)
{
	if (arg < 0) return Company::IsValidID(_local_company) ? _local_company : CompanyID::Begin();
	const Company *c = NthItem<Company>(arg);
	return c != nullptr ? c->index : CompanyID::Begin();
}

const Vehicle *PrimaryVehicleArg(int64_t arg)
{
	std::vector<const Vehicle *> list;
	for (const Vehicle *v : Vehicle::Iterate()) {
		if (v->IsPrimaryVehicle()) list.push_back(v);
	}
	if (list.empty()) return nullptr;
	return list[static_cast<size_t>(arg < 0 ? 0 : arg) % list.size()];
}

using Opener = std::function<bool(int64_t)>;

const std::map<std::string, Opener, std::less<>> &Openers()
{
	auto station = [](auto fn) {
		return [fn](int64_t arg) { const Station *st = NthItem<Station>(arg); if (st == nullptr) return false; fn(st->index); return true; };
	};
	auto vehicle = [](auto fn) {
		return [fn](int64_t arg) { const Vehicle *v = PrimaryVehicleArg(arg); if (v == nullptr) return false; fn(v); return true; };
	};
	auto company = [](auto fn) {
		return [fn](int64_t arg) { if (Company::GetNumItems() == 0) return false; fn(CompanyArg(arg)); return true; };
	};
	auto plain = [](auto fn) {
		return [fn](int64_t) { fn(); return true; };
	};
	static const std::map<std::string, Opener, std::less<>> openers = {
		{ "game_options", plain(ShowGameOptions) },
		{ "smallmap", plain(ShowSmallMap) },
		{ "towns", plain(ShowTownDirectory) },
		{ "industries", plain(ShowIndustryDirectory) },
		{ "industry_cargoes", plain(ShowIndustryCargoesWindow) },
		{ "subsidies", plain(ShowSubsidiesList) },
		{ "messages", plain(ShowMessageHistory) },
		{ "last_news", plain(ShowLastNewsMessage) },
		{ "clients", plain(ShowClientList) },
		{ "framerate", plain(ShowFramerateWindow) },
		{ "linkgraph_legend", plain(ShowLinkGraphLegend) },
		{ "cargo_payment_rates", plain(ShowCargoPaymentRates) },
		{ "company_value_graph", plain(ShowCompanyValueGraph) },
		{ "income_graph", plain(ShowIncomeGraph) },
		{ "operating_profit_graph", plain(ShowOperatingProfitGraph) },
		{ "delivered_cargo_graph", plain(ShowDeliveredCargoGraph) },
		{ "performance_graph", plain(ShowPerformanceHistoryGraph) },
		{ "performance_detail", plain(ShowPerformanceRatingDetail) },
		{ "performance_league", plain(ShowPerformanceLeagueTable) },
		{ "league", plain(ShowFirstLeagueTable) },
		{ "plans", plain(ShowPlansWindow) },
		{ "sectors", plain(ShowSectorWindow) },
		{ "company", company(ShowCompany) },
		{ "finances", company(ShowCompanyFinances) },
		{ "company_stations", company(ShowCompanyStations) },
		{ "goals", company(ShowGoalsList) },
		{ "trains", company([](CompanyID c) { ShowVehicleListWindow(c, VehicleType::Train); }) },
		{ "road_vehicles", company([](CompanyID c) { ShowVehicleListWindow(c, VehicleType::Road); }) },
		{ "ships", company([](CompanyID c) { ShowVehicleListWindow(c, VehicleType::Ship); }) },
		{ "aircraft", company([](CompanyID c) { ShowVehicleListWindow(c, VehicleType::Aircraft); }) },
		{ "station", station(ShowStationViewWindow) },
		{ "station_cargo", station(ShowStationCargo) },
		{ "departures", station(ShowDeparturesWindow) },
		{ "station_vehicles", station([](StationID id) {
			const Station *st = Station::Get(id);
			ShowVehicleListWindow(Company::IsValidID(st->owner) ? st->owner : CompanyArg(-1), VehicleType::Road, id);
		}) },
		{ "town", [](int64_t arg) { const Town *t = NthItem<Town>(arg); if (t == nullptr) return false; ShowTownViewWindow(t->index); return true; } },
		{ "vehicle", vehicle(ShowVehicleViewWindow) },
		{ "orders", vehicle(ShowOrdersWindow) },
		{ "timetable", vehicle(ShowTimetableWindow) },
		{ "vehicle_group", vehicle([](const Vehicle *v) { ShowVehicleListWindow(v); }) },
		{ "viewport", [](int64_t arg) { ShowExtraViewportWindow(TileIndex(static_cast<uint32_t>(std::max<int64_t>(arg, 0)) % Map::Size())); return true; } },
		{ "land_info", [](int64_t arg) { ShowLandInfo(TileIndex(static_cast<uint32_t>(std::max<int64_t>(arg, 0)) % Map::Size())); return true; } },
	};
	return openers;
}

} // namespace

/** List the window names known to DebugBridgeShowWindow. */
std::vector<std::string> DebugBridgeWindowNames()
{
	std::vector<std::string> names;
	for (const auto &it : Openers()) names.push_back(it.first);
	return names;
}

/**
 * Open a window by name. arg selects the subject: n-th station/town/primary vehicle/company (wrapping), a tile for
 * viewport/land_info; -1 means the default (first item, local company).
 * @return Error message, empty on success.
 */
std::string DebugBridgeShowWindow(std::string_view name, int64_t arg)
{
	if (_game_mode != GameMode::Normal && !(_game_mode == GameMode::Editor && name == "sectors")) return "no game running";
	auto it = Openers().find(name);
	if (it == Openers().end()) return fmt::format("unknown window '{}', see 'show' without arguments", name);
	if (!it->second(arg)) return fmt::format("nothing to show for '{}'", name);
	return {};
}
