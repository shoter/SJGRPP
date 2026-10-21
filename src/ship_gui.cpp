/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file ship_gui.cpp GUI for ships. */

#include "stdafx.h"
#include "vehicle_base.h"
#include "window_gui.h"
#include "gfx_func.h"
#include "vehicle_gui.h"
#include "vehicle_gui_base.h"
#include "strings_func.h"
#include "vehicle_func.h"
#include "zoom_func.h"
#include "ship.h"
#include "company_gui.h"
#include "car_ferry.h"
#include "roadveh.h"
#include "station_base.h"
#include "core/format.hpp"

#include "table/strings.h"

#include "safeguards.h"

/**
 * Draws an image of a ship
 * @param v         Front vehicle
 * @param r         Rect to draw at
 * @param selection Selected vehicle to draw a frame around
 * @param image_type Context where the image is being drawn.
 */
void DrawShipImage(const Vehicle *v, const Rect &r, VehicleID selection, EngineImageType image_type)
{
	bool rtl = _current_text_dir == TD_RTL;

	VehicleSpriteSeq seq;
	v->GetImage(rtl ? Direction::E : Direction::W, image_type, &seq);

	Rect rect = ConvertRect<Rect16, Rect>(seq.GetBounds());

	int width = UnScaleGUI(rect.Width());
	int x_offs = UnScaleGUI(rect.left);
	int x = rtl ? r.right - width - x_offs : r.left - x_offs;
	/* This magic -1 offset is related to the sprite_y_offsets in build_vehicle_gui.cpp */
	int y = ScaleSpriteTrad(-1) + CentreBounds(r.top, r.bottom, 0);

	seq.Draw(x, y, GetVehiclePalette(v), false);
	if (v->cargo_cap > 0) DrawCargoIconOverlay(x, y, v->cargo_type);

	if (v->index == selection) {
		x += x_offs;
		y += UnScaleGUI(rect.top);
		Rect hr = {x, y, x + width - 1, y + UnScaleGUI(rect.Height()) - 1};
		DrawFrameRect(hr.Expand(WidgetDimensions::scaled.bevel), Colours::White, FrameFlag::BorderOnly);
	}
}

/** A piece of the line of a road vehicle aboard a car ferry, see #GetCarFerryVehicleLine. */
struct CarFerryLinePiece {
	std::string text;           ///< Text of the piece.
	ExtendedTextColour colour;  ///< Colour of text without colour codes.
	bool space_before;          ///< Whether a space separates it from the previous piece.
};

/**
 * Get the line of a road vehicle aboard a car ferry in the details of the ship.
 * Road vehicles of other companies (infrastructure sharing) show their owner in the owner's colour, so the line is
 * made of pieces drawn one after the other.
 * @param ship The car ferry.
 * @param rv The road vehicle aboard.
 * @return The pieces of the line.
 */
static std::vector<CarFerryLinePiece> GetCarFerryVehicleLine(const Vehicle *ship, const RoadVehicle *rv)
{
	format_buffer cargo;
	for (const RoadVehicle *u = rv; u != nullptr; u = u->Next()) {
		if (u->cargo.StoredCount() == 0) continue;
		if (!cargo.empty()) cargo.append(GetListSeparator());
		AppendStringInPlace(cargo, STR_JUST_CARGO, u->cargo_type, u->cargo.StoredCount());
	}
	if (cargo.empty()) AppendStringInPlace(cargo, STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE_EMPTY);

	if (rv->owner == ship->owner) {
		if (Station::IsValidID(rv->ferry_dest)) return {{GetString(STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE, rv->index, rv->ferry_dest, cargo), TextColour::FromString, false}};
		return {{GetString(STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE_ANY, rv->index, cargo), TextColour::FromString, false}};
	}

	std::vector<CarFerryLinePiece> pieces;
	pieces.push_back({GetString(STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE_NAME, rv->index), TextColour::FromString, false});
	pieces.push_back({GetString(STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE_OWNER, rv->owner), GetDrawStringCompanyColour(rv->owner), true});
	if (Station::IsValidID(rv->ferry_dest)) {
		pieces.push_back({GetString(STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE_DEST, rv->ferry_dest, cargo), TextColour::FromString, true});
	} else {
		pieces.push_back({GetString(STR_VEHICLE_DETAILS_CAR_FERRY_VEHICLE_DEST_ANY, cargo), TextColour::FromString, false});
	}
	return pieces;
}

/**
 * Get the width of the line of a road vehicle aboard a car ferry.
 * @param pieces The pieces of the line.
 * @return The width in pixels.
 */
static uint GetCarFerryVehicleLineWidth(const std::vector<CarFerryLinePiece> &pieces)
{
	uint width = 0;
	for (const CarFerryLinePiece &piece : pieces) {
		if (piece.space_before) width += GetCharacterWidth(FontSize::Normal, ' ');
		width += GetStringBoundingBox(piece.text).width;
	}
	return width;
}

/**
 * Draw the line of a road vehicle aboard a car ferry.
 * @param r The rect to draw in, at the top of the line.
 * @param pieces The pieces of the line.
 */
static void DrawCarFerryVehicleLine(Rect r, const std::vector<CarFerryLinePiece> &pieces)
{
	const bool rtl = _current_text_dir == TD_RTL;
	for (const CarFerryLinePiece &piece : pieces) {
		if (piece.space_before) r = r.Indent(GetCharacterWidth(FontSize::Normal, ' '), rtl);
		if (r.left > r.right) return;
		DrawString(r.left, r.right, r.top, piece.text, piece.colour);
		r = r.Indent(GetStringBoundingBox(piece.text).width, rtl);
	}
}

/**
 * Get the width of the widest line of the road vehicles listed in the details of a car ferry.
 * @param v The car ferry.
 * @return The width in pixels.
 */
uint GetCarFerryVehicleLinesWidth(const Vehicle *v)
{
	const Ship *ship = Ship::From(v);
	const size_t shown = std::min(ship->ferry_vehicles.size(), MAX_CAR_FERRY_DETAILS_LINES);
	uint width = 0;
	for (size_t i = 0; i < shown; i++) {
		width = std::max(width, GetCarFerryVehicleLineWidth(GetCarFerryVehicleLine(v, RoadVehicle::Get(ship->ferry_vehicles[i]))));
	}
	return width;
}

/**
 * Draw the details for the given vehicle at the given position
 *
 * @param v     current vehicle
 * @param r     the Rect to draw within
 */
void DrawShipDetails(const Vehicle *v, const Rect &r)
{
	int y = r.top;

	DrawString(r.left, r.right, y, GetString(STR_VEHICLE_INFO_BUILT_VALUE, PackEngineNameDParam(v->engine_type, EngineNameContext::VehicleDetails), v->build_year, v->value));
	y += GetCharacterHeight(FontSize::Normal);

	Money feeder_share = 0;

	if (v->Next() != nullptr) {
		CargoArray max_cargo{};
		StringID subtype_text[NUM_CARGO]{};

		for (const Vehicle *u = v; u != nullptr; u = u->Next()) {
			max_cargo[u->cargo_type] += u->cargo_cap;
			if (u->cargo_cap > 0) {
				StringID text = GetCargoSubtypeText(u);
				if (text != STR_EMPTY) subtype_text[u->cargo_type] = text;
			}
		}

		{
			format_buffer capacity;
			std::string_view list_separator = GetListSeparator();

			bool first = true;
			for (CargoType i{}; i < NUM_CARGO; i++) {
				if (max_cargo[i] > 0) {
					if (!first) capacity.append(list_separator);
					AppendStringInPlace(capacity, STR_JUST_CARGO, i, max_cargo[i]);

					if (subtype_text[i] != 0) {
						AppendStringInPlace(capacity, subtype_text[i]);
					}

					first = false;
				}
			}

			format_buffer capacity_str;
			AppendStringInPlace(capacity_str, STR_VEHICLE_DETAILS_TRAIN_ARTICULATED_RV_CAPACITY, capacity);
			DrawString(r.left, r.right, y, capacity_str, TextColour::Blue);
			y += GetCharacterHeight(FontSize::Normal) + WidgetDimensions::scaled.vsep_normal;
		}

		for (const Vehicle *u = v; u != nullptr; u = u->Next()) {
			if (u->cargo_cap == 0) continue;

			if (u->cargo.StoredCount() > 0) {
				DrawString(r.left, r.right, y, GetString(STR_VEHICLE_DETAILS_CARGO_FROM, u->cargo_type, u->cargo.StoredCount(), u->cargo.GetFirstStation()));
				feeder_share += u->cargo.GetFeederShare();
			} else {
				DrawString(r.left, r.right, y, STR_VEHICLE_DETAILS_CARGO_EMPTY);
			}
			y += GetCharacterHeight(FontSize::Normal);
		}
		y += WidgetDimensions::scaled.vsep_normal;
	} else if (IsCarFerry(v)) {
		const Ship *ship = Ship::From(v);
		DrawString(r.left, r.right, y, GetString(STR_VEHICLE_DETAILS_CAR_FERRY, ship->ferry_vehicles.size(), GetCarFerryCapacity(v)));
		y += GetCharacterHeight(FontSize::Normal) + WidgetDimensions::scaled.vsep_normal;

		if (ship->ferry_vehicles.empty()) {
			DrawString(r.left, r.right, y, STR_VEHICLE_DETAILS_CARGO_EMPTY);
			y += GetCharacterHeight(FontSize::Normal);
		}
		const size_t shown = std::min(ship->ferry_vehicles.size(), MAX_CAR_FERRY_DETAILS_LINES);
		for (size_t i = 0; i < shown; i++) {
			DrawCarFerryVehicleLine(r.WithY(y, r.bottom), GetCarFerryVehicleLine(v, RoadVehicle::Get(ship->ferry_vehicles[i])));
			y += GetCharacterHeight(FontSize::Normal);
		}
		if (ship->ferry_vehicles.size() > shown) {
			DrawString(r.left, r.right, y, GetString(STR_VEHICLE_DETAILS_CAR_FERRY_MORE, ship->ferry_vehicles.size() - shown));
			y += GetCharacterHeight(FontSize::Normal);
		}
		y += WidgetDimensions::scaled.vsep_normal;
	} else {
		DrawString(r.left, r.right, y, GetString(STR_VEHICLE_INFO_CAPACITY, v->cargo_type, v->cargo_cap, GetCargoSubtypeText(v)));
		y += GetCharacterHeight(FontSize::Normal) + WidgetDimensions::scaled.vsep_normal;

		if (v->cargo.StoredCount() > 0) {
			DrawString(r.left, r.right, y, GetString(STR_VEHICLE_DETAILS_CARGO_FROM, v->cargo_type, v->cargo.StoredCount(), v->cargo.GetFirstStation()));
			feeder_share += v->cargo.GetFeederShare();
		} else {
			DrawString(r.left, r.right, y, STR_VEHICLE_DETAILS_CARGO_EMPTY);
		}
		y += GetCharacterHeight(FontSize::Normal) + WidgetDimensions::scaled.vsep_normal;
	}

	/* Draw Transfer credits text */
	DrawString(r.left, r.right, y, GetString(STR_VEHICLE_INFO_FEEDER_CARGO_VALUE, feeder_share));
	y += GetCharacterHeight(FontSize::Normal) + WidgetDimensions::scaled.vsep_normal;

	if (Ship::From(v)->critical_breakdown_count > 0) {
		DrawString(r.left, r.right, y, GetString(STR_NEED_REPAIR, Ship::From(v)->GetDisplayEffectiveMaxSpeed()));
	}
}
