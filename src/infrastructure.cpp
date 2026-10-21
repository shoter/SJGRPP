/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file infrastructure.cpp Implementation of infrastructure sharing */

#include "stdafx.h"
#include "infrastructure_func.h"
#include "train.h"
#include "aircraft.h"
#include "error.h"
#include "vehicle_func.h"
#include "station_base.h"
#include "depot_base.h"
#include "pbs.h"
#include "signal_func.h"
#include "window_func.h"
#include "gui.h"
#include "pathfinder/yapf/yapf_cache.h"
#include "company_base.h"
#include "string_func.h"
#include "scope_info.h"
#include "order_dest_func.h"
#include "strings_func.h"
#include "scope.h"
#include "economy_func.h"
#include "company_cmd.h"
#include "news_func.h"
#include "console_func.h"
#include "network/network.h"
#include "network/network_func.h"

#include "table/strings.h"

/**
 * Helper function for transferring sharing fees
 * @param v The vehicle involved
 * @param infra_owner The owner of the infrastructure
 * @param cost Amount to transfer as money fraction (shifted 8 bits to the left)
 */
static void PaySharingFee(Vehicle *v, Owner infra_owner, Money cost)
{
	Company *c = Company::Get(v->owner);
	if (!_settings_game.economy.sharing_payment_in_debt) {
		/* Do not allow fee payment to drop (money - loan) below 0. */
		cost = std::min(cost, (c->money - c->current_loan) << 8);
		if (cost <= 0) return;
	}
	v->profit_this_year -= cost;
	SubtractMoneyFromCompanyFract(v->owner, CommandCost(ExpensesType::SharingCost, cost));
	SubtractMoneyFromCompanyFract(infra_owner, CommandCost(ExpensesType::SharingRevenue, -cost));
}

/**
 * Scale a sharing fee setting with inflation.
 * @param fee The fee as set in the settings, in base (game start) prices.
 * @param inflation_prices Cumulated price inflation, 16 bit fractional part.
 * @param scale_with_inflation Whether the fee scales with inflation.
 * @return The fee as money fraction (shifted 8 bits to the left).
 */
Money ScaleSharingFeeFract(uint fee, uint64_t inflation_prices, bool scale_with_inflation)
{
	if (!scale_with_inflation) return static_cast<Money>(fee) << 8;
	return static_cast<Money>((static_cast<uint64_t>(fee) * inflation_prices) >> 8);
}

/**
 * Get the current sharing fee of a vehicle type, including inflation if enabled.
 * @param type The vehicle type.
 * @return The fee as money fraction (shifted 8 bits to the left): per day for road vehicles, ships and aircraft, per tile of train length per day for trains.
 */
Money GetSharingFeeFract(VehicleType type)
{
	return ScaleSharingFeeFract(_settings_game.economy.sharing_fee[to_underlying(type)], _economy.inflation_prices, _settings_game.economy.sharing_fee_inflation);
}

/**
 * Get the highest fee that companies can set for their own infrastructure, including inflation if enabled.
 * @param type The vehicle type.
 * @return The fee as money fraction (shifted 8 bits to the left), in the same units as #GetSharingFeeFract.
 */
Money GetSharingFeeMaxFract(VehicleType type)
{
	return ScaleSharingFeeFract(_settings_game.economy.sharing_fee_max[to_underlying(type)], _economy.inflation_prices, _settings_game.economy.sharing_fee_inflation);
}

/**
 * Get the fee that is charged with a company's sharing fee setting.
 * @param fee The fee setting of the company.
 * @param server_fee The fee set by the server, as money fraction.
 * @param max_fee The highest fee companies can set, as money fraction.
 * @return The fee as money fraction (shifted 8 bits to the left).
 */
Money GetEffectiveSharingFeeFract(const CompanySharingFee &fee, Money server_fee, Money max_fee)
{
	if (!fee.custom) return server_fee;
	/* The maximum may have been lowered since the company set its fee. */
	return std::min<Money>(fee.fee << 8, max_fee);
}

/**
 * Get the fee that a company charges for the use of its infrastructure.
 * @param type The vehicle type.
 * @param infra_owner The owner of the infrastructure.
 * @return The fee as money fraction (shifted 8 bits to the left), in the same units as #GetSharingFeeFract.
 */
Money GetCompanySharingFeeFract(VehicleType type, Owner infra_owner)
{
	const Company *c = Company::GetIfValid(infra_owner);
	if (c == nullptr || !c->sharing_fees[to_underlying(type)].custom) return GetSharingFeeFract(type);
	return GetEffectiveSharingFeeFract(c->sharing_fees[to_underlying(type)], GetSharingFeeFract(type), GetSharingFeeMaxFract(type));
}

/**
 * Check whether a change of a company's sharing fee has to wait for the notice period.
 * @param old_fee The fee before the change.
 * @param new_fee The fee after the change.
 * @param notice_days The notice period in days.
 * @return True if the change takes effect after the notice period, false if immediately.
 */
bool IsSharingFeeChangeDelayed(Money old_fee, Money new_fee, uint notice_days)
{
	return notice_days > 0 && new_fee > old_fee;
}

/**
 * Pay the fee for spending a single tick inside a station.
 * @param v The vehicle that is using the station.
 * @param st The station that it uses.
 */
void PayStationSharingFee(Vehicle *v, const Station *st)
{
	if (v->owner == st->owner || st->owner == OWNER_NONE || v->type == VehicleType::Train) return;
	PaySharingFee(v, st->owner, GetCompanySharingFeeFract(v->type, st->owner) / DAY_TICKS);
}

/**
 * Calculate the track toll of a train.
 * @param fee The track toll per tile of train length per day, as money fraction (shifted 8 bits to the left).
 * @param length The length of the train, in vehicle length units (TILE_SIZE units per tile).
 * @param running_ticks The number of ticks the train was running since the toll was last paid.
 * @return The toll as money fraction (shifted 8 bits to the left).
 */
Money GetTrackTollFract(Money fee, uint length, uint running_ticks)
{
	/* Cost is calculated per tile of train length */
	Money cost = (fee * length) / TILE_SIZE;
	/* Only pay the required fraction */
	return (cost * running_ticks) / DAY_TICKS;
}

/**
 * Pay the daily fee for trains on foreign tracks.
 * @param v The vehicle to pay the fee for.
 */
void PayDailyTrackSharingFee(Train *v)
{
	Owner owner = GetTileOwner(v->tile);
	if (owner == v->owner) return;
	Money cost = GetTrackTollFract(GetCompanySharingFeeFract(VehicleType::Train, owner), v->gcache.cached_total_length, v->running_ticks);
	if (cost != 0) PaySharingFee(v, owner, cost);
}

/**
 * Check whether a vehicle is in an allowed position.
 * @param v     The vehicle to check.
 * @param owner Owner whose infrastructure is not allowed, because the company will be removed. Ignored if INVALID_OWNER.
 * @return      True if the vehicle is completely in an allowed position.
 */
static bool VehiclePositionIsAllowed(const Vehicle *v, Owner owner = INVALID_OWNER)
{
	switch (v->type) {
		case VehicleType::Train:
			if (HasBit(Train::From(v)->subtype, GVSF_VIRTUAL)) return true;
			for (const Vehicle *u = v; u != nullptr; u = u->Next()) {
				if (!IsValidTile(u->tile)) continue;
				if (!IsInfraTileUsageAllowed(VehicleType::Train, v->owner, u->tile) || GetTileOwner(u->tile) == owner) return false;
			}
			return true;
		case VehicleType::Road:
			for (const Vehicle *u = v; u != nullptr; u = u->Next()) {
				if (!IsValidTile(u->tile)) continue;
				if (IsRoadDepotTile(u->tile) || IsBayRoadStopTile(u->tile)) {
					if (!IsInfraTileUsageAllowed(VehicleType::Road, v->owner, u->tile) || GetTileOwner(u->tile) == owner) return false;
				}
			}
			return true;
		case VehicleType::Ship:
			if (!IsValidTile(v->tile)) return true;
			if (IsShipDepotTile(v->tile) && v->IsStoppedInDepot()) {
				if (!IsInfraTileUsageAllowed(VehicleType::Ship, v->owner, v->tile) || GetTileOwner(v->tile) == owner) return false;
			}
			return true;
		case VehicleType::Aircraft: {
			const Aircraft *a = Aircraft::From(v);
			if (a->state != FLYING && Station::IsValidID(a->targetairport)) {
				Owner station_owner = Station::Get(a->targetairport)->owner;
				if (!IsInfraUsageAllowed(VehicleType::Aircraft, a->owner, station_owner) || station_owner == owner) return false;
			}
			return true;
		}
		default: return true;
	}
}

/**
 * Check whether an order has a destination that is allowed.
 * I.e. it refers to a station/depot/waypoint the vehicle is allowed to visit.
 * @param order The order to check
 * @param v     The vehicle this order belongs to.
 * @param owner Owner whose infrastructure is not allowed, because the company will be removed. Ignored if INVALID_OWNER.
 * @return      True if the order has an allowed destination.
 */
static bool OrderDestinationIsAllowed(const Order *order, const Vehicle *v, Owner owner = INVALID_OWNER)
{
	Owner dest_owner;
	switch (order->GetType()) {
		case OT_IMPLICIT:
		case OT_GOTO_STATION:
		case OT_GOTO_WAYPOINT:
			dest_owner = BaseStation::Get(order->GetDestination().ToStationID())->owner;
			break;
		case OT_GOTO_DEPOT:
			if ((order->GetDepotActionType() & ODATFB_NEAREST_DEPOT) != 0) return true;
			dest_owner = (v->type == VehicleType::Aircraft) ? Station::Get(order->GetDestination().ToStationID())->owner : GetTileOwner(Depot::Get(order->GetDestination().ToDepotID())->xy);
			break;
		case OT_LOADING_ADVANCE:
		case OT_LOADING:
			dest_owner = Station::Get(v->last_station_visited)->owner;
			break;
		default:
			return true;
	}
	return dest_owner != owner && IsInfraUsageAllowed(v->type, v->owner, dest_owner);
}

/**
 * Sell a vehicle, no matter where it may be.
 * @param v The vehicle to sell
 * @param give_money Do we actually need to give money to the vehicle owner?
 */
static void RemoveAndSellVehicle(Vehicle *v, bool give_money)
{
	assert(v->Previous() == nullptr);

	if (give_money) {
		/* compute total value and give that to the owner */
		Money value = 0;
		for (Vehicle *u = v->First(); u != nullptr; u = u->Next()) {
			value += u->value;
		}
		SubtractMoneyFromCompany(v->owner, CommandCost(ExpensesType::NewVehicles, -value));
	}

	/* take special measures for trains, but not when sharing is disabled or when the train is a free wagon chain */
	if (IsInfrastructureSharingEnabled(VehicleType::Train) && v->type == VehicleType::Train && Train::From(v)->IsFrontEngine() && !Train::From(v)->IsVirtual()) {
 		DeleteVisibleTrain(Train::From(v));
	} else {
		delete v;
	}
}

void ConsoleRemoveVehicle(VehicleID id)
{
	Vehicle *v = Vehicle::GetIfValid(id);
	if (v->Previous() == nullptr) RemoveAndSellVehicle(v, false);
}

/**
 * Check all path reservations, and reserve a new path if the current path is invalid.
 */
static void FixAllReservations()
{
	/* if this function is called, we can safely assume that sharing of rails is being switched off */
	assert(!IsInfrastructureSharingEnabled(VehicleType::Train));
	for (Train *v : Train::IterateFrontOnly()) {
		if (!v->IsPrimaryVehicle() || v->vehstatus.Test(VehState::Crashed) || HasBit(v->subtype, GVSF_VIRTUAL)) continue;
		/* It might happen that the train reserved additional tracks,
		 * but FollowTrainReservation can't detect those because they are no longer reachable.
		 * detect this by first finding the end of the reservation,
		 * then switch sharing on and try again. If these two ends differ,
		 * unreserve the path, switch sharing off and try to reserve a new path */
		PBSTileInfo end_tile_info = FollowTrainReservation(v, nullptr, { FollowTrainReservationFlag::IgnoreLookahead, FollowTrainReservationFlag::OkayUnused });

		/* first do a quick test to determine whether the next tile has any reservation at all */
		TileIndex next_tile = end_tile_info.tile + TileOffsByDiagDir(TrackdirToExitdir(end_tile_info.trackdir));
		/* If the next tile doesn't have a reservation at all, the reservation surely ends here. Thus all is well */
		if (GetReservedTrackbits(next_tile) == TRACK_BIT_NONE) continue;

		/* change sharing setting temporarily */
		_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Train)] = true;
		PBSTileInfo end_tile_info2 = FollowTrainReservation(v, nullptr, { FollowTrainReservationFlag::IgnoreLookahead, FollowTrainReservationFlag::OkayUnused });
		/* if these two reservation ends differ, unreserve the path and try to reserve a new path */
		if (end_tile_info.tile != end_tile_info2.tile || end_tile_info.trackdir != end_tile_info2.trackdir) {
			FreeTrainTrackReservation(v);
			_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Train)] = false;
			TryPathReserve(v, true);
		} else {
			_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Train)] = false;
		}
	}
}

/**
 * Check if a sharing change is possible.
 * If vehicles are still on others' infrastructure or using others' stations,
 * The change is not possible and false is returned.
 * @param type The type of vehicle whose setting will be changed.
 * @param new_value True if sharing will become enabled.
 * @return True if the change can take place, false otherwise.
 */
bool CheckSharingChangePossible(VehicleType type, bool new_value)
{
	if (type != VehicleType::Aircraft) YapfNotifyTrackLayoutChange(INVALID_TILE, INVALID_TRACK);
	/* Only do something when sharing is being disabled */
	if (!_settings_game.economy.infrastructure_sharing[to_underlying(type)] || new_value) return true;

	_settings_game.economy.infrastructure_sharing[to_underlying(type)] = false;
	auto guard = scope_guard([type]() {
		_settings_game.economy.infrastructure_sharing[to_underlying(type)] = true;
	});

	StringID error_message = STR_NULL;
	for (Vehicle *v : Vehicle::IterateTypeFrontOnly(type)) {
		if (HasBit(v->subtype, GVSF_VIRTUAL)) continue;

		/* Check vehicle positiion */
		if (!VehiclePositionIsAllowed(v)) {
			error_message = STR_CONFIG_SETTING_SHARING_USED_BY_VEHICLES;
			/* Break immediately, this error message takes precedence over the others. */
			break;
		}

		/* Check current order */
		if (!OrderDestinationIsAllowed(&v->current_order, v)) {
			error_message = STR_CONFIG_SETTING_SHARING_ORDERS_TO_OTHERS;
		}

		/* Check order list */
		if (v->FirstShared() != v) continue;
		for(const Order *o : v->Orders()) {
			if (!OrderDestinationIsAllowed(o, v)) {
				error_message = STR_CONFIG_SETTING_SHARING_ORDERS_TO_OTHERS;
			}
		}
	}

	if (type == VehicleType::Train && _settings_game.vehicle.train_braking_model == TBM_REALISTIC) {
		for (Train *v : Train::IterateFrontOnly()) {
			if (!v->IsPrimaryVehicle() || v->vehstatus.Test(VehState::Crashed) || HasBit(v->subtype, GVSF_VIRTUAL)) continue;
			/* It might happen that the train reserved additional tracks,
			 * but FollowTrainReservation can't detect those because they are no longer reachable.
			 * detect this by first finding the end of the reservation,
			 * then switch sharing on and try again. If these two ends differ,
			 * disallow changing the sharing state */
			PBSTileInfo end_tile_info = FollowTrainReservation(v, nullptr, { FollowTrainReservationFlag::IgnoreLookahead, FollowTrainReservationFlag::OkayUnused });

			/* first do a quick test to determine whether the next tile has any reservation at all */
			TileIndex next_tile = end_tile_info.tile + TileOffsByDiagDir(TrackdirToExitdir(end_tile_info.trackdir));
			/* If the next tile doesn't have a reservation at all, the reservation surely ends here. Thus all is well */
			if (GetReservedTrackbits(next_tile) == TRACK_BIT_NONE) continue;

			/* change sharing setting temporarily */
			_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Train)] = true;
			PBSTileInfo end_tile_info2 = FollowTrainReservation(v, nullptr, { FollowTrainReservationFlag::IgnoreLookahead, FollowTrainReservationFlag::OkayUnused });
			_settings_game.economy.infrastructure_sharing[to_underlying(VehicleType::Train)] = false;

			/* if these two reservation ends differ, disallow changing the sharing state */
			if (end_tile_info.tile != end_tile_info2.tile || end_tile_info.trackdir != end_tile_info2.trackdir) {
				error_message = STR_CONFIG_SETTING_SHARING_USED_BY_VEHICLES;
				break;
			}
		}
	}

	if (error_message != STR_NULL) {
		ShowErrorMessage(GetEncodedString(error_message), {}, WarningLevel::Error);
		return false;
	}

	if (type == VehicleType::Train) FixAllReservations();

	return true;
}

/**
 * Handle the removal (through reset_company or bankruptcy) of a company.
 * i.e. remove all vehicles owned by that company or on its infrastructure,
 * and delete all now-invalid orders.
 * @param Owner the company to be removed.
 */
void HandleSharingCompanyDeletion(Owner owner)
{
	YapfNotifyTrackLayoutChange(INVALID_TILE, INVALID_TRACK);

	Vehicle *si_v = nullptr;
	SCOPE_INFO_FMT([&si_v], "HandleSharingCompanyDeletion: veh: {}", VehicleInfoDumper(si_v));
	for (Vehicle *v : Vehicle::IterateFrontOnly()) {
		si_v = v;
		if (!IsCompanyBuildableVehicleType(v)) continue;
		/* vehicle position */
		if (v->owner == owner || !VehiclePositionIsAllowed(v, owner)) {
			RemoveAndSellVehicle(v, v->owner != owner);
			continue;
		}
		/* current order */
		if (!OrderDestinationIsAllowed(&v->current_order, v, owner)) {
			if (v->current_order.IsAnyLoadingType()) {
				v->LeaveStation();
			} else {
				v->current_order.MakeDummy();
			}
			SetWindowDirty(WindowClass::VehicleView, v->index);
		}

		/* order list */
		if (v->FirstShared() != v) continue;

		RemoveVehicleOrdersIf(v, [&](const Order *o) {
			if (o->GetType() == OT_GOTO_DEPOT && (o->GetDepotActionType() & ODATFB_NEAREST_DEPOT) != 0) return false;
			return !OrderDestinationIsAllowed(o, v, owner);
		});
	}

	if (_settings_game.vehicle.train_braking_model == TBM_REALISTIC && IsInfrastructureSharingEnabled(VehicleType::Train)) {
		for (TileIndex t(0); t < Map::Size(); t++) {
			switch (GetTileType(t)) {
				case TileType::Railway:
				case TileType::Road:
				case TileType::Station:
				case TileType::TunnelBridge:
					if (GetTileOwner(t) == owner) {
						TrackBits bits = GetReservedTrackbits(t);
						if (bits != TRACK_BIT_NONE) {
							/* Vehicles of this company and vehicles physically on tiles of this company have all been removed, but this tile is still reserved.
							 * The reservation may belong to a train of another company which is still on another company's infrastructure, remove it.
							 */
							for (Track track : SetTrackBitIterator(bits)) {
								Train *v = GetTrainForReservation(t, track);
								if (v != nullptr) RemoveAndSellVehicle(v, v->owner != owner);
							}
						}
					}
					break;

				default:
					break;
			}
		}

	}
}

/**
 * Update all block signals on the map.
 * To be called after the setting for sharing of rails changes.
 * @param owner Owner whose signals to update. If INVALID_OWNER, update everything.
 */
void UpdateAllBlockSignals(Owner owner)
{
	Owner last_owner = INVALID_OWNER;
	auto check_owner = [&](Owner track_owner) -> bool {
		if (owner != INVALID_OWNER && track_owner != owner) return true;

		if (!IsOneSignalBlock(track_owner, last_owner)) {
			/* Cannot update signals of two different companies in one run,
			 * if these signal blocks are not joined */
			UpdateSignalsInBuffer();
			last_owner = track_owner;
		}
		return false;
	};
	TileIndex tile(0);
	do {
		if (IsTileType(tile, TileType::Railway) && HasSignals(tile)) {
			Owner track_owner = GetTileOwner(tile);
			if (check_owner(track_owner)) continue;
			for (Track track : SetTrackBitIterator(GetTrackBits(tile))) {
				if (IsSignalPresent(tile, SignalOnTrack(track))) AddTrackToSignalBuffer(tile, track, track_owner);
			}
		} else if (IsLevelCrossingTile(tile) && (owner == INVALID_OWNER || GetTileOwner(tile) == owner)) {
			UpdateLevelCrossing(tile);
		} else if (IsTunnelBridgeWithSignalSimulation(tile)) {
			Owner track_owner = GetTileOwner(tile);
			if (check_owner(track_owner)) continue;
			if (IsTunnelBridgeSignalSimulationExit(tile)) {
				AddSideToSignalBuffer(tile, DiagDirection::Invalid, track_owner);
			}
			if (_extra_aspects > 0 && IsTunnelBridgeSignalSimulationEntrance(tile) && GetTunnelBridgeEntranceSignalState(tile) == SignalState::Green) {
				SetTunnelBridgeEntranceSignalAspect(tile, 0);
				UpdateAspectDeferred(tile, GetTunnelBridgeEntranceTrackdir(tile));
			}
		}
	} while (++tile != Map::Size());

	UpdateSignalsInBuffer();
	FlushDeferredAspectUpdates();
}

/** Name of the sharing fee of each vehicle type, used in messages. */
static const StringID _sharing_fee_name_strings[] = {
	STR_SHARING_FEE_NAME_RAIL,
	STR_SHARING_FEE_NAME_ROAD,
	STR_SHARING_FEE_NAME_WATER,
	STR_SHARING_FEE_NAME_AIR,
};

/**
 * Get the unit of the sharing fee of a vehicle type, to be shown after the amount.
 * @param type The vehicle type.
 * @return The string of the unit.
 */
StringID GetSharingFeeUnitString(VehicleType type)
{
	return type == VehicleType::Train ? STR_COMPANY_SETTINGS_SHARING_UNIT_RAIL : STR_COMPANY_SETTINGS_SHARING_UNIT_OTHER;
}

/**
 * Tell all players about a change of a company's sharing fee: in the chat in multiplayer, as news otherwise.
 * @param message The message.
 */
static void AnnounceSharingFeeChange(const std::string &message)
{
	if (_networking) {
		const std::string text = GetString(STR_NETWORK_SERVER_MESSAGE, message);
		IConsolePrint(CC_DEFAULT, text);
		NetworkAddChatMessage(CC_DEFAULT, _settings_client.gui.network_chat_timeout, text);
	} else {
		AddNewsItem(GetEncodedString(STR_SHARING_FEE_NEWS, message), NewsType::Economy, NewsStyle::Normal, {});
	}
}

/**
 * Change the fee that the current company charges for the use of its infrastructure.
 * A higher fee takes effect after the notice period, a lower fee immediately.
 * @param flags Type of operation.
 * @param type Vehicle type of the fee.
 * @param custom Charge an own fee instead of the server's fee.
 * @param fee The own fee, a fixed amount per day (trains: per tile of train length per day). Ignored if \p custom is false.
 * @return The cost of this operation or an error.
 */
CommandCost CmdSetCompanySharingFee(DoCommandFlags flags, VehicleType type, bool custom, Money fee)
{
	if (type >= VehicleType::CompanyEnd) return CMD_ERROR;
	Company *c = Company::GetIfValid(_current_company);
	if (c == nullptr) return CMD_ERROR;

	if (!custom) fee = 0;
	if (fee < 0) return CMD_ERROR;
	const Money max_fee = GetSharingFeeMaxFract(type);
	if (fee > (max_fee >> 8)) return CommandCostWithParam(STR_ERROR_SHARING_FEE_ABOVE_MAXIMUM, max_fee >> 8);

	if (flags.Test(DoCommandFlag::Execute)) {
		CompanySharingFee &current = c->sharing_fees[to_underlying(type)];
		const Money server_fee = GetSharingFeeFract(type);
		const Money old_fee = GetEffectiveSharingFeeFract(current, server_fee, max_fee);
		CompanySharingFee requested{};
		requested.custom = custom;
		requested.fee = fee;
		const Money new_fee = GetEffectiveSharingFeeFract(requested, server_fee, max_fee);
		const StringID name = _sharing_fee_name_strings[to_underlying(type)];
		const StringID unit = GetSharingFeeUnitString(type);

		if (current.custom == custom && current.fee == fee) {
			/* Back to the fee that is in effect: drop an announced rise. */
			if (!current.pending) return CommandCost();
			current.pending = false;
			AnnounceSharingFeeChange(GetString(STR_SHARING_FEE_MESSAGE_CANCELLED, c->index, name, old_fee >> 8, unit));
		} else if (IsSharingFeeChangeDelayed(old_fee, new_fee, _settings_game.economy.sharing_fee_notice_days)) {
			/* Announcing the same rise again does not postpone it. */
			if (current.pending && current.pending_custom == custom && current.pending_fee == fee) return CommandCost();
			current.pending = true;
			current.pending_custom = custom;
			current.pending_fee = fee;
			current.pending_date = EconTime::CurDate() + EconTime::DateDelta{_settings_game.economy.sharing_fee_notice_days};
			AnnounceSharingFeeChange(GetString(STR_SHARING_FEE_MESSAGE_ANNOUNCED, c->index, name, old_fee >> 8, new_fee >> 8, unit, _settings_game.economy.sharing_fee_notice_days));
		} else {
			current.custom = custom;
			current.fee = fee;
			current.pending = false;
			AnnounceSharingFeeChange(GetString(STR_SHARING_FEE_MESSAGE_CHANGED, c->index, name, old_fee >> 8, new_fee >> 8, unit));
		}
		InvalidateWindowData(WindowClass::CompanySettings, c->index);
	}

	return CommandCost();
}

/** Put the announced rises of the companies' sharing fees into effect when their notice period is over. */
void CompaniesSharingFeesDailyLoop()
{
	for (Company *c : Company::Iterate()) {
		for (CompanySharingFee &fee : c->sharing_fees) {
			if (!fee.pending || EconTime::CurDate() < fee.pending_date) continue;
			fee.custom = fee.pending_custom;
			fee.fee = fee.pending_fee;
			fee.pending = false;
			InvalidateWindowData(WindowClass::CompanySettings, c->index);
		}
	}
}
