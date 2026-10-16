/*
 * This file is part of OpenTTD.
 * OpenTTD is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, version 2.
 * OpenTTD is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with OpenTTD. If not, see <https://www.gnu.org/licenses/old-licenses/gpl-2.0>.
 */

/** @file custom_colour_gui.h GUI for picking a custom (RGB) company colour. */

#ifndef CUSTOM_COLOUR_GUI_H
#define CUSTOM_COLOUR_GUI_H

#include "custom_colour_type.h"
#include "window_type.h"

#include <optional>

/**
 * Callback of the custom colour picker.
 * @param parent The window the colour was picked for.
 * @param parent_widget The widget of the parent window the colour was picked for.
 * @param colour The chosen colour.
 * @param starting_colour Whether the starting colour toggle is set. Always false if the toggle was not offered.
 */
using CustomColourPickerCallback = void(Window *parent, WidgetID parent_widget, CustomColour colour, bool starting_colour);

void ShowCustomColourPicker(Window *parent, WidgetID parent_widget, CustomColour initial, CustomColourPickerCallback *callback, std::optional<bool> starting_colour = std::nullopt);

#endif /* CUSTOM_COLOUR_GUI_H */
