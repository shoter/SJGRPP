/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file sjgrpp_info_gui.cpp Window with information about features of sJGRPP, such as the current infrastructure sharing fees. */

#include "stdafx.h"
#include "economy_func.h"
#include "gui.h"
#include "infrastructure_func.h"
#include "settings_type.h"
#include "strings_func.h"
#include "window_func.h"
#include "window_gui.h"
#include "zoom_func.h"
#include "core/geometry_func.hpp"

#include "widgets/sjgrpp_info_widget.h"

#include "table/strings.h"

#include "safeguards.h"

static constexpr NWidgetPart _nested_sjgrpp_info_widgets[] = {
	NWidget(NWID_HORIZONTAL),
		NWidget(WWT_CLOSEBOX, Colours::Grey),
		NWidget(WWT_CAPTION, Colours::Grey, WID_SJI_CAPTION), SetStringTip(STR_SJGRPP_INFO_CAPTION, STR_TOOLTIP_WINDOW_TITLE_DRAG_THIS),
		NWidget(WWT_SHADEBOX, Colours::Grey),
		NWidget(WWT_STICKYBOX, Colours::Grey),
	EndContainer(),
	NWidget(WWT_PANEL, Colours::Grey, WID_SJI_INFO), SetFill(1, 1),
	EndContainer(),
};

static WindowDesc _sjgrpp_info_desc(__FILE__, __LINE__,
	WindowPosition::Center, "sjgrpp_info", 0, 0,
	WindowClass::SjgrppInfo, WindowClass::None,
	{},
	_nested_sjgrpp_info_widgets
);

/** String of the sharing fee line of each vehicle type. */
static const StringID _sharing_fee_strings[] = {
	STR_SJGRPP_INFO_SHARING_RAIL,
	STR_SJGRPP_INFO_SHARING_ROAD,
	STR_SJGRPP_INFO_SHARING_WATER,
	STR_SJGRPP_INFO_SHARING_AIR,
};

/** Window with information about features of sJGRPP. */
struct SjgrppInfoWindow : Window {
	SjgrppInfoWindow(WindowDesc &desc, WindowNumber window_number) : Window(desc)
	{
		this->InitNested(window_number);
	}

	/** Get the lines of text shown in the window. */
	std::vector<std::pair<std::string, bool>> GetLines() const
	{
		std::vector<std::pair<std::string, bool>> lines; ///< Text and whether it is a title.
		lines.emplace_back(GetString(STR_SJGRPP_INFO_SHARING_TITLE), true);
		if (_settings_game.economy.sharing_fee_inflation) {
			lines.emplace_back(GetString(STR_SJGRPP_INFO_SHARING_INFLATION_ON, _economy.inflation_prices * 100 >> 16, 2), false);
		} else {
			lines.emplace_back(GetString(STR_SJGRPP_INFO_SHARING_INFLATION_OFF), false);
		}
		for (VehicleType type : { VehicleType::Train, VehicleType::Road, VehicleType::Ship, VehicleType::Aircraft }) {
			lines.emplace_back(GetString(_sharing_fee_strings[to_underlying(type)], GetSharingFeeFract(type) >> 8,
					IsInfrastructureSharingEnabled(type) ? STR_EMPTY : STR_SJGRPP_INFO_SHARING_DISABLED), false);
		}
		return lines;
	}

	void UpdateWidgetSize(WidgetID widget, Dimension &size, [[maybe_unused]] const Dimension &padding, [[maybe_unused]] Dimension &fill, [[maybe_unused]] Dimension &resize) override
	{
		if (widget != WID_SJI_INFO) return;
		Dimension d{};
		for (const auto &[text, title] : this->GetLines()) {
			d.width = std::max(d.width, GetStringBoundingBox(text).width);
			d.height += GetCharacterHeight(FontSize::Normal) + (title ? WidgetDimensions::scaled.vsep_normal : 0);
		}
		/* Room for larger amounts after inflation. */
		d.width += ScaleGUITrad(40);
		d.width += WidgetDimensions::scaled.framerect.Horizontal();
		d.height += WidgetDimensions::scaled.framerect.Vertical();
		size = maxdim(size, d);
	}

	void DrawWidget(const Rect &r, WidgetID widget) const override
	{
		if (widget != WID_SJI_INFO) return;
		Rect ir = r.Shrink(WidgetDimensions::scaled.framerect);
		for (const auto &[text, title] : this->GetLines()) {
			DrawString(ir.left, ir.right, ir.top, text);
			ir.top += GetCharacterHeight(FontSize::Normal) + (title ? WidgetDimensions::scaled.vsep_normal : 0);
		}
	}

	void OnHundredthTick() override
	{
		/* Fees change with inflation and settings, which can be changed by others in multiplayer. */
		this->ReInit();
	}
};

/** Show the sJGRPP info window. */
void ShowSjgrppInfoWindow()
{
	AllocateWindowDescFront<SjgrppInfoWindow>(_sjgrpp_info_desc, 0);
}
