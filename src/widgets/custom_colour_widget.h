/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file custom_colour_widget.h Types related to the custom colour picker widgets. */

#ifndef WIDGETS_CUSTOM_COLOUR_WIDGET_H
#define WIDGETS_CUSTOM_COLOUR_WIDGET_H

/** Widgets of the #CustomColourPickerWindow class. */
enum CustomColourPickerWidgets : WidgetID {
	WID_CCP_CAPTION,        ///< Caption of the window.
	WID_CCP_PREVIEW,        ///< Preview of the colour and its shades.
	WID_CCP_RED_LABEL,      ///< Label of the red component.
	WID_CCP_RED,            ///< Slider of the red component.
	WID_CCP_RED_VALUE,      ///< Value of the red component.
	WID_CCP_GREEN_LABEL,    ///< Label of the green component.
	WID_CCP_GREEN,          ///< Slider of the green component.
	WID_CCP_GREEN_VALUE,    ///< Value of the green component.
	WID_CCP_BLUE_LABEL,     ///< Label of the blue component.
	WID_CCP_BLUE,           ///< Slider of the blue component.
	WID_CCP_BLUE_VALUE,     ///< Value of the blue component.
	WID_CCP_HEX,            ///< Hexadecimal entry of the colour.
	WID_CCP_PRESETS,        ///< Preset colours to start from.
	WID_CCP_STARTING_SEL,   ///< Selection to show or hide the starting colour toggle.
	WID_CCP_STARTING,       ///< Toggle to use the colour as starting colour of new companies.
	WID_CCP_OK,             ///< Apply the colour.
	WID_CCP_CANCEL,         ///< Close without applying.
};

#endif /* WIDGETS_CUSTOM_COLOUR_WIDGET_H */
