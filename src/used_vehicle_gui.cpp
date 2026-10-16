/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file used_vehicle_gui.cpp GUI for buying used vehicles. */

#include "stdafx.h"
#include "used_vehicle.h"
#include "command_func.h"
#include "company_func.h"
#include "company_base.h"
#include "date_func.h"
#include "depot_map.h"
#include "engine_base.h"
#include "engine_gui.h"
#include "gfx_func.h"
#include "rail.h"
#include "road.h"
#include "road_map.h"
#include "strings_func.h"
#include "tile_map.h"
#include "vehicle_cmd.h"
#include "vehicle_func.h"
#include "vehicle_gui.h"
#include "widget_type.h"
#include "window_func.h"
#include "window_gui.h"

#include "widgets/used_vehicle_widget.h"

#include "table/strings.h"

#include "safeguards.h"

uint GetEngineListHeight(VehicleType type);

static constexpr uint USED_VEHICLE_DETAILS_LINES = 13;      ///< Height of the details panel in lines.
static constexpr uint USED_VEHICLE_MAX_HISTORY_LINES = 4;   ///< Maximum number of previous owners shown in the details panel.

static constexpr std::initializer_list<NWidgetPart> _nested_buy_used_vehicle_widgets = {
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_CLOSEBOX, Colours::Grey),
		NWidget(WWT_CAPTION, Colours::Grey, WID_UV_CAPTION),
		NWidget(WWT_SHADEBOX, Colours::Grey),
		NWidget(WWT_DEFSIZEBOX, Colours::Grey),
		NWidget(WWT_STICKYBOX, Colours::Grey),
	EndContainer(),
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_MATRIX, Colours::Grey, WID_UV_LIST), SetResize(1, 1), SetFill(1, 0), SetMatrixDataTip(1, 0, STR_USED_VEHICLE_LIST_TOOLTIP), SetScrollbar(WID_UV_SCROLLBAR),
		NWidget(NWID_VSCROLLBAR, Colours::Grey, WID_UV_SCROLLBAR),
	EndContainer(),
	NWidget(WWT_PANEL, Colours::Grey, WID_UV_PANEL), SetMinimalSize(300, 0), SetResize(1, 0), EndContainer(),
	NWidget(NWID_HORIZONTAL),
		NWidget(NWID_SELECTION, Colours::Invalid, WID_UV_BUY_SEL),
			NWidget(WWT_PUSHTXTBTN, Colours::Grey, WID_UV_BUY), SetResize(1, 0), SetFill(1, 0), SetStringTip(STR_USED_VEHICLE_BUY_BUTTON, STR_USED_VEHICLE_BUY_TOOLTIP),
		EndContainer(),
		NWidget(WWT_PANEL, Colours::Grey), SetResize(1, 0), SetFill(1, 1), EndContainer(),
		NWidget(WWT_RESIZEBOX, Colours::Grey),
	EndContainer(),
};

/** Window listing the used vehicles of one vehicle type. */
struct BuyUsedVehicleWindow : Window {
	VehicleType vehicle_type;              ///< Type of vehicles shown.
	TileIndex tile;                        ///< Depot to place bought vehicles in, or #INVALID_TILE when only viewing.
	RailType railtype = INVALID_RAILTYPE;  ///< Rail type of the depot.
	RoadType roadtype = INVALID_ROADTYPE;  ///< Road type of the depot.
	std::vector<uint32_t> listings;        ///< Identifiers of the shown listings, in display order.
	uint32_t selected = 0;                 ///< Identifier of the selected listing, 0 when none.
	Scrollbar *vscroll = nullptr;

	BuyUsedVehicleWindow(WindowDesc &desc, TileIndex tile, VehicleType type) : Window(desc), vehicle_type(type), tile(tile)
	{
		if (tile != INVALID_TILE) {
			if (type == VehicleType::Train) this->railtype = GetRailType(tile);
			if (type == VehicleType::Road) {
				this->roadtype = GetRoadTypeRoad(tile);
				if (this->roadtype == INVALID_ROADTYPE) this->roadtype = GetRoadTypeTram(tile);
			}
		}

		this->CreateNestedTree();
		this->vscroll = this->GetScrollbar(WID_UV_SCROLLBAR);
		if (tile == INVALID_TILE) this->GetWidget<NWidgetStacked>(WID_UV_BUY_SEL)->SetDisplayedPlane(SZSP_NONE);
		this->FinishInitNested(tile == INVALID_TILE ? static_cast<WindowNumber>(type) : static_cast<WindowNumber>(tile.base()));
		this->owner = (tile != INVALID_TILE) ? GetTileOwner(tile) : _local_company;

		this->BuildList();
	}

	/** Whether a listing can be bought into this window's depot. */
	bool CanBuyHere(const UsedVehicleListing &l) const
	{
		if (this->tile == INVALID_TILE) return false;
		if (!IsUsedEngineBuildable(l.engine, this->vehicle_type, _local_company)) return false;
		const Engine *e = Engine::Get(l.engine);
		if (this->railtype != INVALID_RAILTYPE && !HasPowerOnRail(e->VehInfo<RailVehicleInfo>().railtypes, this->railtype)) return false;
		if (this->roadtype != INVALID_ROADTYPE && !HasPowerOnRoad(e->VehInfo<RoadVehicleInfo>().roadtype, this->roadtype)) return false;
		return true;
	}

	void BuildList()
	{
		struct Entry {
			uint32_t id;
			std::string name;
			int32_t age;
		};
		std::vector<Entry> entries;
		for (const UsedVehicleListing &l : _used_vehicle_market.listings) {
			const Engine *e = Engine::GetIfValid(l.engine);
			if (e == nullptr || e->type != this->vehicle_type || !IsUsedVehicleMarketEngine(l.engine)) continue;
			entries.push_back({l.id, GetString(STR_ENGINE_NAME, PackEngineNameDParam(l.engine, EngineNameContext::PurchaseList)), l.age.base()});
		}
		std::sort(entries.begin(), entries.end(), [](const Entry &a, const Entry &b) {
			int r = StrNaturalCompare(a.name, b.name);
			if (r != 0) return r < 0;
			if (a.age != b.age) return a.age < b.age;
			return a.id < b.id;
		});

		this->listings.clear();
		for (const Entry &e : entries) this->listings.push_back(e.id);
		if (std::ranges::find(this->listings, this->selected) == this->listings.end()) this->selected = 0;
		this->vscroll->SetCount(this->listings.size());
	}

	const UsedVehicleListing *GetSelected() const
	{
		return this->selected != 0 ? GetUsedVehicleListing(this->selected) : nullptr;
	}

	std::string GetWidgetString(WidgetID widget, StringID stringid) const override
	{
		if (widget == WID_UV_CAPTION) return GetString(STR_USED_VEHICLE_CAPTION_TRAIN + to_underlying(this->vehicle_type));
		return this->Window::GetWidgetString(widget, stringid);
	}

	void UpdateWidgetSize(WidgetID widget, Dimension &size, const Dimension &padding, Dimension &fill, Dimension &resize) override
	{
		switch (widget) {
			case WID_UV_LIST:
				fill.height = resize.height = GetEngineListHeight(this->vehicle_type);
				size.height = 8 * resize.height;
				break;

			case WID_UV_PANEL:
				size.height = GetCharacterHeight(FontSize::Normal) * USED_VEHICLE_DETAILS_LINES + padding.height;
				break;
		}
	}

	void DrawList(const Rect &r) const
	{
		const bool rtl = _current_text_dir == TD_RTL;
		const int step_size = GetEngineListHeight(this->vehicle_type);
		const VehicleCellSize cell = GetVehicleImageCellSize(this->vehicle_type, EngineImageType::Purchase);
		const int sprite_width = cell.extend_left + cell.extend_right;

		if (this->listings.empty()) {
			const Rect tr = r.Shrink(WidgetDimensions::scaled.matrix).Shrink(WidgetDimensions::scaled.framerect, RectPadding::zero);
			DrawString(tr, IsUsedVehicleMarketEnabled() ? STR_USED_VEHICLE_EMPTY : STR_USED_VEHICLE_DISABLED);
			return;
		}

		int age_width = GetStringBoundingBox(GetString(STR_USED_VEHICLE_ROW_AGE, GetParamMaxValue(100))).width;
		Money max_price = 0;
		auto [first, last] = this->vscroll->GetVisibleRangeIterators(this->listings);
		for (auto it = first; it != last; ++it) {
			if (const UsedVehicleListing *l = GetUsedVehicleListing(*it); l != nullptr) max_price = std::max(max_price, l->GetPrice());
		}
		int price_width = GetStringBoundingBox(GetString(STR_USED_VEHICLE_ROW_PRICE, max_price)).width;

		Rect ir = r.WithHeight(step_size).Shrink(WidgetDimensions::scaled.matrix, RectPadding::zero);
		for (auto it = first; it != last; ++it, ir = ir.Translate(0, step_size)) {
			const UsedVehicleListing *l = GetUsedVehicleListing(*it);
			if (l == nullptr) continue;

			const bool shaded = this->tile != INVALID_TILE && !this->CanBuyHere(*l);
			if (l->id == this->selected) GfxFillRect(ir, PC_DARK_GREY);

			Rect tr = ir.Shrink(WidgetDimensions::scaled.matrix);
			const PaletteID pal = shaded ? PALETTE_CRASH : GetEnginePalette(l->engine, _local_company);
			const int sprite_x = tr.WithWidth(sprite_width, rtl).left + cell.extend_left;
			DrawVehicleEngine(r.left, r.right, sprite_x, ir.top + ir.Height() / 2, l->engine, pal, EngineImageType::Purchase);
			tr = tr.Indent(sprite_width + WidgetDimensions::scaled.hsep_wide, rtl);

			const int text_y = tr.top + (tr.Height() - GetCharacterHeight(FontSize::Normal)) / 2;
			Rect price_rect = tr.WithWidth(price_width, !rtl);
			tr = tr.Indent(price_width + WidgetDimensions::scaled.hsep_wide, !rtl);
			Rect age_rect = tr.WithWidth(age_width, !rtl);
			tr = tr.Indent(age_width + WidgetDimensions::scaled.hsep_wide, !rtl);

			const ExtendedTextColour tc = shaded ? ExtendedTextColour{TextColour::Grey, {ExtendedTextColourFlag::Forced, ExtendedTextColourFlag::NoShade}} : ExtendedTextColour{TextColour::Black};
			DrawString(price_rect.left, price_rect.right, text_y, GetString(STR_USED_VEHICLE_ROW_PRICE, l->GetPrice()), tc, AlignmentH::ForceRight);
			DrawString(age_rect.left, age_rect.right, text_y, GetString(STR_USED_VEHICLE_ROW_AGE, DateDeltaToYearDelta(l->age)), tc, AlignmentH::ForceRight);
			const uint64_t engine_param = PackEngineNameDParam(l->engine, EngineNameContext::PurchaseList);
			DrawString(tr.left, tr.right, text_y, l->name.empty() ? GetString(STR_USED_VEHICLE_ROW_NAME, engine_param) : GetString(STR_USED_VEHICLE_ROW_NAME_CUSTOM, engine_param, l->name), tc);
		}
	}

	void DrawDetails(const Rect &r) const
	{
		const UsedVehicleListing *l = this->GetSelected();
		if (l == nullptr) return;
		const Engine *e = Engine::Get(l->engine);
		const int line = GetCharacterHeight(FontSize::Normal);

		Rect tr = r.Shrink(WidgetDimensions::scaled.framerect);
		auto draw = [&](std::string &&str) {
			if (tr.top + line > tr.bottom + 1) return;
			DrawString(tr, str);
			tr.top += line;
		};

		draw(GetString(STR_USED_VEHICLE_INFO_ENGINE, PackEngineNameDParam(l->engine, EngineNameContext::PurchaseList)));
		if (!l->name.empty()) draw(GetString(STR_USED_VEHICLE_INFO_NAME, l->name));
		draw(GetString(STR_USED_VEHICLE_INFO_AGE, DateDeltaToYearDelta(l->age), DateDeltaToYearDelta(e->GetLifeLengthInDays())));
		draw(GetString(STR_USED_VEHICLE_INFO_PRICE, l->GetPrice(), e->GetCost()));
		draw(GetString(STR_USED_VEHICLE_INFO_RELIABILITY, ToPercent16(l->GetReliability())));
		draw(GetString(EconTime::UsingWallclockUnits() ? STR_USED_VEHICLE_INFO_RUNNING_COST_PERIOD : STR_USED_VEHICLE_INFO_RUNNING_COST, e->GetDisplayRunningCost()));
		if (!e->flags.Test(EngineFlag::Available) || e->company_avail.None()) draw(GetString(STR_USED_VEHICLE_INFO_NOT_ON_SALE));
		if (this->tile != INVALID_TILE && !this->CanBuyHere(*l)) draw(GetString(STR_USED_VEHICLE_INFO_NOT_HERE));

		if (l->history.empty()) {
			draw(GetString(STR_USED_VEHICLE_INFO_PREVIOUS_OWNERS_UNKNOWN));
			return;
		}
		draw(GetString(STR_USED_VEHICLE_INFO_PREVIOUS_OWNERS));
		tr = tr.Indent(WidgetDimensions::scaled.hsep_indent, _current_text_dir == TD_RTL);
		const size_t shown = std::min<size_t>(l->history.size(), USED_VEHICLE_MAX_HISTORY_LINES);
		for (size_t i = l->history.size() - shown; i < l->history.size(); i++) {
			const VehicleOwnerRecord &rec = l->history[i];
			draw(GetString(STR_VEHICLE_INFO_OWNER_HISTORY_ENTRY, rec.GetCompanyName(), rec.start, rec.end));
		}
		if (l->history.size() > shown) draw(GetString(STR_VEHICLE_INFO_OWNER_HISTORY_MORE, l->history.size() - shown));
	}

	void DrawWidget(const Rect &r, WidgetID widget) const override
	{
		switch (widget) {
			case WID_UV_LIST: this->DrawList(r); break;
			case WID_UV_PANEL: this->DrawDetails(r); break;
		}
	}

	void OnPaint() override
	{
		const UsedVehicleListing *l = this->GetSelected();
		this->SetWidgetDisabledState(WID_UV_BUY, l == nullptr || !this->CanBuyHere(*l) || !Company::IsValidID(_local_company) || this->owner != _local_company);
		this->DrawWidgets();
	}

	void Buy()
	{
		const UsedVehicleListing *l = this->GetSelected();
		if (l == nullptr || this->tile == INVALID_TILE) return;
		Command<Commands::BuyUsedVehicle>::Post(STR_ERROR_CAN_T_BUY_USED_VEHICLE, CommandCallback::BuildPrimaryVehicle, this->tile, l->id, ClientID::Invalid);
	}

	void OnClick([[maybe_unused]] Point pt, WidgetID widget, int click_count) override
	{
		switch (widget) {
			case WID_UV_LIST: {
				auto it = this->vscroll->GetScrolledItemFromWidget(this->listings, pt.y, this, WID_UV_LIST);
				this->selected = (it != this->listings.end()) ? *it : 0;
				this->SetDirty();
				if (click_count > 1 && this->selected != 0 && !this->IsWidgetDisabled(WID_UV_BUY)) this->Buy();
				break;
			}

			case WID_UV_BUY:
				this->Buy();
				break;
		}
	}

	void OnInvalidateData([[maybe_unused]] int data = 0, [[maybe_unused]] bool gui_scope = true) override
	{
		if (!gui_scope) return;
		this->BuildList();
		this->SetDirty();
	}

	void OnResize() override
	{
		this->vscroll->SetCapacityFromWidget(this, WID_UV_LIST);
	}
};

static WindowDesc _buy_used_vehicle_desc(__FILE__, __LINE__,
	WindowPosition::Automatic, "buy_used_vehicle", 340, 300,
	WindowClass::BuyUsedVehicle, WindowClass::None,
	WindowDefaultFlag::Construction,
	_nested_buy_used_vehicle_widgets
);

/**
 * Show the used vehicle market for a vehicle type.
 * @param tile Depot to buy vehicles into, or #INVALID_TILE to only view the market.
 * @param type Vehicle type.
 */
void ShowBuyUsedVehicleWindow(TileIndex tile, VehicleType type)
{
	assert(IsCompanyBuildableVehicleType(type));
	const WindowNumber num = (tile == INVALID_TILE) ? static_cast<WindowNumber>(type) : static_cast<WindowNumber>(tile.base());
	CloseWindowById(WindowClass::BuyUsedVehicle, num);
	new BuyUsedVehicleWindow(_buy_used_vehicle_desc, tile, type);
}
