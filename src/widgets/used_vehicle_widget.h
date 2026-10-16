/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file used_vehicle_widget.h Types related to the used vehicle market widgets. */

#ifndef WIDGETS_USED_VEHICLE_WIDGET_H
#define WIDGETS_USED_VEHICLE_WIDGET_H

/** Widgets of the #BuyUsedVehicleWindow class. */
enum BuyUsedVehicleWidgets : WidgetID {
	WID_UV_CAPTION,   ///< Caption of window.
	WID_UV_LIST,      ///< List of used vehicles.
	WID_UV_SCROLLBAR, ///< Scrollbar of the list.
	WID_UV_PANEL,     ///< Details of the selected used vehicle.
	WID_UV_BUY_SEL,   ///< Selection to hide the buy button when not in a depot.
	WID_UV_BUY,       ///< Buy button.
};

#endif /* WIDGETS_USED_VEHICLE_WIDGET_H */
